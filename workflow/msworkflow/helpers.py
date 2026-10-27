"""Metashape helpers for the workflow: logging, memory monitoring, GPUs, project opening.

Importing this module first sets the Metashape environment from [hardware] of
the default config file (license_path, inactive_cores), then imports Metashape:
import Metashape through it (from .helpers import Metashape), or after it.
"""

import os
import socket
import sys
import threading
import time
import traceback
from dataclasses import fields
from pathlib import Path

from . import config


def omp_threads(hardware):
    """OMP_NUM_THREADS for config [hardware], or None to leave it alone."""
    if hardware.inactive_cores is None:
        return None
    return str(max(1, os.cpu_count() - hardware.inactive_cores))


try:
    _hardware = config.load().hardware
except (OSError, ValueError):  # missing/invalid config: process.main reports it properly
    _hardware = config.HardwareCfg()
os.environ.setdefault("AGISOFT_LICENSE_PATH", _hardware.license_path)
# Metashape's memory planner ("using 15/30 threads") reads OMP_NUM_THREADS when the
# module is loaded, so it must be set before the import below.
if omp_threads(_hardware):
    os.environ["OMP_NUM_THREADS"] = omp_threads(_hardware)

try:
    import Metashape
except ImportError:
    sys.exit(
        f"Cannot import Metashape with interpreter {sys.executable}\n"
        "Use an environment with the metashape wheel installed (the container image has one)."
    )


# ---- logging ----------------------------------------------------------------
_log_file = None


def set_log_file(path):
    """Also append log lines to this file."""
    global _log_file
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    _log_file = open(path, "a", encoding="utf-8")


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    if _log_file:
        _log_file.write(line + "\n")
        _log_file.flush()


def log_error(what):
    """Log the current exception with its traceback (to the log file too)."""
    log(f"    {what} FAILED: {sys.exc_info()[1]!r}")
    for line in traceback.format_exc().rstrip().splitlines():
        log(f"    | {line}")


def progress(label):
    """Progress callback printing every 10 %."""
    state = {"last": -10}

    def callback(p):
        if p - state["last"] >= 10 or p >= 100:
            state["last"] = p
            print(f"    {label}: {p:5.1f}%", flush=True)

    return callback


# ---- memory -----------------------------------------------------------------
def memory_status():
    """'RSS x GB (peak y GB), available z GB' for this process and the machine."""

    def read_kb(path, keys):
        values = {}
        with open(path) as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                if key in keys:
                    values[key] = int(rest.split()[0]) / 2**20  # kB -> GB
        return values

    proc = read_kb("/proc/self/status", ("VmRSS", "VmHWM"))
    mem = read_kb("/proc/meminfo", ("MemAvailable", "MemTotal"))
    return (
        f"RSS {proc['VmRSS']:.1f} GB (peak {proc['VmHWM']:.1f} GB), "
        f"available {mem['MemAvailable']:.1f}/{mem['MemTotal']:.0f} GB"
    )


class Heartbeat:
    """Log memory_status() every `minutes` while a step runs.

    Metashape releases the GIL while processing, so a Python thread keeps
    running; the last line before an OOM kill shows how close it got.
    """

    def __init__(self, label, minutes):
        self.label, self.seconds = label, minutes * 60
        self.stop = threading.Event()

    def __enter__(self):
        if self.seconds > 0:
            threading.Thread(target=self._run, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.stop.set()

    def _run(self):
        while not self.stop.wait(self.seconds):
            log(f"    [{self.label}] {memory_status()}")


# ---- hardware ---------------------------------------------------------------
def setup_gpu(name_filter):
    """Enable only the GPUs whose name contains name_filter ('all' = every GPU)."""
    devices = Metashape.app.enumGPUDevices()
    for i, d in enumerate(devices):
        log(f"GPU {i}: {d['name']} ({d['mem_size'] / 2**30:.1f} GB)")
    if name_filter.lower() == "all":
        return
    mask = sum(
        1 << i for i, d in enumerate(devices) if name_filter.lower() in d["name"].lower()
    )
    if mask:
        Metashape.app.gpu_mask = mask
        log(f"gpu_mask = {mask:#b}")
    else:
        log(f"WARNING: no GPU matches '{name_filter}', leaving Metashape defaults")


# ---- config sections -> Metashape keyword arguments ---------------------------
DATA_SOURCES = {
    "point_cloud": Metashape.PointCloudData,
    "depth_maps": Metashape.DepthMapsData,
    "tie_points": Metashape.TiePointsData,
    "elevation": Metashape.ElevationData,
    "model": Metashape.ModelData,
}
ENUMS = {  # keyword argument name: {config value: Metashape enum}
    "reference_preselection_mode": {
        "source": Metashape.ReferencePreselectionSource,
        "estimated": Metashape.ReferencePreselectionEstimated,
        "sequential": Metashape.ReferencePreselectionSequential,
    },
    "filter_mode": {
        "no": Metashape.NoFiltering,
        "mild": Metashape.MildFiltering,
        "moderate": Metashape.ModerateFiltering,
        "aggressive": Metashape.AggressiveFiltering,
    },
    "face_count": {
        "low": Metashape.LowFaceCount,
        "medium": Metashape.MediumFaceCount,
        "high": Metashape.HighFaceCount,
        "custom": Metashape.CustomFaceCount,
    },
    "interpolation": {
        "disabled": Metashape.DisabledInterpolation,
        "enabled": Metashape.EnabledInterpolation,
        "extrapolated": Metashape.Extrapolated,
    },
    "blending_mode": {
        "mosaic": Metashape.MosaicBlending,
        "average": Metashape.AverageBlending,
        "max": Metashape.MaxBlending,
        "min": Metashape.MinBlending,
        "disabled": Metashape.DisabledBlending,
        "natural": Metashape.NaturalBlending,
    },
    "mapping_mode": {
        "generic": Metashape.GenericMapping,
        "orthophoto": Metashape.OrthophotoMapping,
        "adaptive_orthophoto": Metashape.AdaptiveOrthophotoMapping,
    },
    "surface_type": {"arbitrary": Metashape.Arbitrary, "height_field": Metashape.HeightField},
    "source_data": DATA_SOURCES,
    "surface_data": DATA_SOURCES,
}
# export formats: the config value is also the file extension
POINT_CLOUD_FORMATS = {
    "laz": Metashape.PointCloudFormatLAZ,
    "las": Metashape.PointCloudFormatLAS,
    "ply": Metashape.PointCloudFormatPLY,
    "e57": Metashape.PointCloudFormatE57,
    "xyz": Metashape.PointCloudFormatXYZ,
}
MODEL_FORMATS = {
    "obj": Metashape.ModelFormatOBJ,
    "ply": Metashape.ModelFormatPLY,
    "fbx": Metashape.ModelFormatFBX,
    "gltf": Metashape.ModelFormatGLTF,
}
IMAGE_FORMATS = {
    "jpeg": Metashape.ImageFormatJPEG,
    "png": Metashape.ImageFormatPNG,
    "tiff": Metashape.ImageFormatTIFF,
}


def api_kwargs(section):
    """Keyword arguments for the Metashape method mirrored by a config section.

    Enum names become Metashape enums, a projection CRS string an OrthoProjection,
    and None values are left out (Metashape default).
    """
    kwargs = {}
    for f in fields(section):
        value = getattr(section, f.name)
        if value is None:
            continue
        if f.name == "projection":
            value = Metashape.OrthoProjection()
            value.crs = Metashape.CoordinateSystem(getattr(section, f.name))
        elif f.name in ENUMS:
            value = ENUMS[f.name][value]
        kwargs[f.name] = value
    return kwargs


def tiff_compression():
    c = Metashape.ImageCompression()
    c.tiff_compression = Metashape.ImageCompression.TiffCompressionLZW
    c.tiff_big = True
    c.tiff_tiled = True
    c.tiff_overviews = True
    return c


# ---- opening the project ----------------------------------------------------
def find_chunk(doc, label):
    """Chunk with the given label, or the last chunk if label is None; exits if none."""
    if not doc.chunks:
        sys.exit("the project has no chunks")
    if label is None:
        return doc.chunks[-1]
    chunk = next((c for c in doc.chunks if c.label == label), None)
    if chunk is None:
        sys.exit(f"chunk '{label}' not found. available: {[c.label for c in doc.chunks]}")
    return chunk


def stale_lock(path):
    """True if the project lock was left by a dead run on this machine.

    The lock (<name>.files/lock) holds only the host name, and stays behind when
    a run is OOM-killed: the next run would then open the project read-only.
    """
    lock = Path(path).with_suffix(".files") / "lock"
    try:
        host = lock.read_text().strip()
    except OSError:
        return False
    if host != socket.gethostname():
        return False  # another machine (e.g. a workstation on the share)
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            cmd = (proc / "cmdline").read_bytes().replace(b"\0", b" ")
        except OSError:
            continue
        if b"msworkflow" in cmd and int(proc.name) != os.getpid():
            return False  # another run is alive
    return True


def open_doc(path, read_only=False):
    doc = Metashape.Document()
    try:
        doc.open(str(path), read_only=read_only)
        if not read_only and doc.read_only and stale_lock(path):
            log(f"WARNING: stale lock from a dead run on this host, ignoring it ({path})")
            doc = Metashape.Document()
            doc.open(str(path), ignore_lock=True)
    except Exception as e:  # Metashape raises OSError/RuntimeError
        sys.exit(f"cannot open project {path}: {e}")
    # Metashape silently falls back to read-only when the project is in use:
    # fail now instead of after hours of processing, when save() is refused.
    if not read_only and doc.read_only:
        sys.exit(
            f"project {path} is in use (opened read-only): close Metashape or stop "
            "the other run, then retry."
        )
    return doc


def open_project(p):
    """Open the project to process (p = config.ProjectCfg), with doc.chunk set.

    By default only the chunk to process is copied into a NEW project, which
    isolates the processing (small file, other chunks and the source untouched).
    Without a source, a new project with one empty chunk is created.
    """
    if p.source is None and (p.in_place or p.processing_project is None):
        sys.exit(
            "set [project] source, or only processing_project to start a new project"
        )
    if p.processing_project is None:
        p.processing_project = p.source.with_name(p.source.stem + "_processing.psx")
    if p.in_place:
        doc = open_doc(p.source)
    elif p.processing_project.exists():
        log(f"resuming existing processing project {p.processing_project}")
        doc = open_doc(p.processing_project)
    elif p.source is None:
        log(f"creating new project {p.processing_project}")
        doc = Metashape.Document()
        chunk = doc.addChunk()
        if p.chunk:
            chunk.label = p.chunk
        doc.save(str(p.processing_project))
    else:
        if not p.source.is_file():
            sys.exit(f"source project not found: {p.source}")
        log(f"opening {p.source} read-only")
        source = open_doc(p.source, read_only=True)
        chunk = find_chunk(source, p.chunk)
        log(f"copying chunk '{chunk.label}' to new project {p.processing_project}")
        doc = Metashape.Document()
        try:
            doc.append(source, chunks=[chunk])
        except Exception as e:
            sys.exit(
                f"Metashape cannot copy chunk '{chunk.label}' into a new project ({e}).\n"
                "This happens when the chunk holds a DSM/orthomosaic that append() cannot "
                "copy. Work on a copy of the project instead: duplicate the .psx and its "
                ".files folder, point source to the copy and set in_place = true."
            )
        doc.save(str(p.processing_project))
    doc.chunk = find_chunk(doc, p.chunk)
    log(f"project: {doc.path}")
    return doc
