"""Metashape processing workflow, from the command line or step by step.

Pipeline: photos -> align -> optimize -> depth maps -> dense cloud -> confidence
filter -> mesh -> texture -> DSM -> orthomosaic -> exports.
GCPs/CPs are placed by hand: run up to "align", place the markers in the GUI,
then run from "optimize" on. Each step is skipped if its product exists, and the
project is saved after each step; a failed step is logged with its traceback.
Settings: processing_config.toml in the project folder (values), config.py
(structure and defaults); sections named like a Metashape method hold its keyword
arguments.

Command line, from the project folder (`run` is the launcher in the container):
    run                                 # nothing is run: shows this help
    run --all                           # all the steps listed in [run] steps (or: run all)
    run mesh texture                    # only these steps (still in pipeline order)
    run mesh --force                    # rebuild the mesh even if it exists
    run --config other.toml
    (without the launcher: python -m msworkflow ..., with /opt/workflow on PYTHONPATH)

Script or notebook, one step at a time (from the project folder):
    from msworkflow import config
    from msworkflow import process as p   # imports Metashape

    cfg = config.load()                 # processing_config.toml, or load("other.toml")
    cfg.buildModel.face_count_custom = 8_000_000   # change settings as needed
    doc = p.start(cfg)                  # license, GPUs, open project (doc.chunk)
    p.run_step("mesh", doc, cfg)        # skip if built, check inputs, log memory, save
    # or the bare step, no checks, no save:
    p.build_mesh(doc, cfg)
    doc.save()
    # or Metashape directly, with a config section as keyword arguments:
    doc.chunk.buildModel(**p.api_kwargs(cfg.buildModel), build_texture=False)
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import config
from .helpers import (  # helpers sets the Metashape environment before importing it
    IMAGE_FORMATS,
    MODEL_FORMATS,
    POINT_CLOUD_FORMATS,
    Heartbeat,
    Metashape,
    api_kwargs,
    log,
    log_error,
    memory_status,
    omp_threads,
    open_project,
    progress,
    set_log_file,
    setup_gpu,
    tiff_compression,
)

ALL_POINT_CLASSES = list(range(128))
# metadata keys stored in the project (names kept from the former process.py script)
FILTER_META = "process.py/removed_max_confidence"  # set on the cloud once filtered
OPTIMIZE_META = "process.py/optimized"  # set on the chunk once optimized


# ---- "is this product already built?" checks --------------------------------
def has_photos(chunk):
    return len(chunk.cameras) > 0


def is_aligned(chunk):
    return any(c.transform for c in chunk.cameras)


def is_optimized(chunk):
    return bool(chunk.meta[OPTIMIZE_META])


def has_depth(chunk):
    return chunk.depth_maps is not None


def has_cloud(chunk):
    return chunk.point_cloud is not None


def is_filtered(chunk):
    return chunk.point_cloud is not None and bool(chunk.point_cloud.meta[FILTER_META])


def has_mesh(chunk):
    return chunk.model is not None


def has_texture(chunk):
    return chunk.model is not None and bool(chunk.model.textures)


def has_dsm(chunk):
    return chunk.elevation is not None


def has_ortho(chunk):
    return chunk.orthomosaic is not None


HAS_DATA = {  # source_data / surface_data value: built-check
    "point_cloud": has_cloud,
    "depth_maps": has_depth,
    "tie_points": is_aligned,
    "elevation": has_dsm,
    "model": has_mesh,
}


def require_data(chunk, section, key):
    """Raise if the data selected by section.<key> (e.g. source_data) is not built."""
    value = getattr(section, key)
    if not HAS_DATA[value](chunk):
        raise RuntimeError(f"{key} = {value!r}, but there is no {value} in the chunk")


# ==========================================================================
#  processing steps: step(doc, cfg), working on doc.chunk
# ==========================================================================
def add_photos(doc, cfg):
    c = cfg.photos
    if c.folder is None:
        raise RuntimeError("set [photos] folder")
    files = sorted(
        str(f) for f in c.folder.iterdir() if f.suffix.lower() in c.extensions
    )
    if not files:
        raise RuntimeError(f"no {'/'.join(c.extensions)} files in {c.folder}")
    doc.chunk.addPhotos(files, progress=progress("add photos"))
    log(f"    {len(doc.chunk.cameras)} photos in the chunk")


def align_photos(doc, cfg):
    chunk = doc.chunk
    chunk.matchPhotos(**api_kwargs(cfg.matchPhotos), progress=progress("match photos"))
    chunk.alignCameras(**api_kwargs(cfg.alignCameras), progress=progress("align"))
    aligned = sum(1 for cam in chunk.cameras if cam.transform)
    log(f"    {aligned}/{len(chunk.cameras)} cameras aligned")


def count_tie_points(chunk):
    """Number of valid tie points (removed ones stay in the list, flagged invalid)."""
    return sum(pt.valid for pt in chunk.tie_points.points)


def remove_tie_points(chunk, criterion, threshold):
    """Gradual selection: remove the tie points whose criterion is above threshold.

    criterion: name of a Metashape.TiePoints.Filter criterion, e.g. "ReprojectionError".
    threshold 0 = off.
    """
    if not threshold:
        return
    f = Metashape.TiePoints.Filter()
    f.init(
        chunk,
        criterion=getattr(Metashape.TiePoints.Filter, criterion),
        progress=progress(criterion),
    )
    before = count_tie_points(chunk)
    f.removePoints(threshold)
    removed = before - count_tie_points(chunk)
    log(
        f"    {criterion} > {threshold} (max {f.max_value:.3g}): removed {removed:,} of "
        f"{before:,} tie points ({removed / max(before, 1) * 100:.1f}%)"
    )


def optimize_cameras(doc, cfg):
    """Optional gradual selection on the tie points, then camera optimization."""
    g = cfg.gradual_selection
    chunk = doc.chunk

    def optimize():
        chunk.optimizeCameras(**api_kwargs(cfg.optimizeCameras), progress=progress("optimize"))

    remove_tie_points(chunk, "ReconstructionUncertainty", g.reconstruction_uncertainty)
    remove_tie_points(chunk, "ProjectionAccuracy", g.projection_accuracy)
    optimize()
    if g.reprojection_error:
        remove_tie_points(chunk, "ReprojectionError", g.reprojection_error)
        optimize()
    chunk.meta[OPTIMIZE_META] = "1"


def build_depth_maps(doc, cfg):
    doc.chunk.buildDepthMaps(**api_kwargs(cfg.buildDepthMaps), progress=progress("depth maps"))


def build_dense_cloud(doc, cfg):
    doc.chunk.buildPointCloud(
        **api_kwargs(cfg.buildPointCloud),
        source_data=Metashape.DepthMapsData,
        replace_asset=True,
        progress=progress("dense cloud"),
    )


def filter_dense_cloud(doc, cfg):
    """Remove low-confidence points (confidence = number of agreeing depth maps).

    removePoints() only marks the points as removed: restorePoints() undoes it
    and the project files keep their size, until compactPoints() purges them.
    """
    c = cfg.confidence_filter
    pc = doc.chunk.point_cloud
    if cfg.buildModel.source_data != "point_cloud":
        log("    note: buildModel.source_data is not point_cloud, the filter does not affect the mesh")
    pc.updateStatistics()
    before = pc.point_count
    pc.setConfidenceFilter(c.min_confidence, c.max_confidence)
    try:
        pc.removePoints(ALL_POINT_CLASSES, progress=progress("remove points"))
    finally:
        pc.resetFilters()
    if c.compact:
        pc.compactPoints(progress=progress("compact points"))
    pc.updateStatistics()
    pc.meta[FILTER_META] = str(c.max_confidence)
    log(
        f"    confidence {c.min_confidence}-{c.max_confidence}: {before:,} -> "
        f"{pc.point_count:,} points (-{(before - pc.point_count) / max(before, 1) * 100:.1f}%)"
        + (", compacted" if c.compact else ", restorable with restorePoints()")
    )


def build_mesh(doc, cfg):
    c = cfg.buildModel
    chunk = doc.chunk
    require_data(chunk, c, "source_data")
    target = f"{c.face_count_custom:,}" if c.face_count == "custom" else c.face_count
    log(f"    from {c.source_data}, target face count {target}")
    chunk.buildModel(
        **api_kwargs(c), build_texture=False, replace_asset=True, progress=progress("mesh")
    )
    m = chunk.model
    log(f"    mesh: {len(m.faces):,} faces, {len(m.vertices):,} vertices")


def build_texture(doc, cfg):
    uv, tex = cfg.buildUV, cfg.buildTexture
    log(
        f"    {uv.page_count} pages of {uv.texture_size} px, {uv.mapping_mode} mapping, "
        f"{tex.blending_mode} blending"
    )
    doc.chunk.buildUV(**api_kwargs(uv), progress=progress("UV"))
    doc.save()  # cheap, and keeps the UVs if buildTexture is killed
    doc.chunk.buildTexture(**api_kwargs(tex), progress=progress("texture"))


def build_dsm(doc, cfg):
    require_data(doc.chunk, cfg.buildDem, "source_data")
    doc.chunk.buildDem(**api_kwargs(cfg.buildDem), replace_asset=True, progress=progress("DSM"))


def build_orthomosaic(doc, cfg):
    require_data(doc.chunk, cfg.buildOrthomosaic, "surface_data")
    doc.chunk.buildOrthomosaic(
        **api_kwargs(cfg.buildOrthomosaic), replace_asset=True, progress=progress("orthomosaic")
    )


# ==========================================================================
#  exports: export(doc, cfg, path)
# ==========================================================================
def export_cloud(doc, cfg, path):
    doc.chunk.exportPointCloud(
        str(path),
        format=POINT_CLOUD_FORMATS[cfg.exportPointCloud.format],
        source_data=Metashape.PointCloudData,
        crs=doc.chunk.crs,
        save_point_color=True,
        save_point_confidence=True,
        progress=progress("export cloud"),
    )


def model_shift(chunk, shift):
    """Local origin subtracted from the exported vertices (CRS units).

    UTM coordinates (~5e6 m) exceed float32 precision in Blender (jitter,
    z-fighting): export relative to this origin and keep it to go back.
    """
    if shift is not None:
        return Metashape.Vector(shift)
    centre = chunk.crs.project(chunk.transform.matrix.mulp(chunk.region.center))
    return Metashape.Vector([round(v, -2) for v in centre])  # to 100 m


def export_model(doc, cfg, path):
    c = cfg.exportModel
    chunk = doc.chunk
    shift = model_shift(chunk, c.shift)
    # exportModel subtracts shift: exported = CRS coordinate - shift (checked on 2.3.2)
    info = {
        "crs": chunk.crs.name,
        "crs_wkt": chunk.crs.wkt,
        "shift": list(shift),
        "note": "CRS coordinate = model coordinate + shift",
    }
    log(f"    shift (x0, y0, z0) = {tuple(shift)} in {chunk.crs.name}")
    path.with_name(path.stem + "_shift.json").write_text(json.dumps(info, indent=2))
    chunk.exportModel(
        str(path),
        format=MODEL_FORMATS[c.format],
        texture_format=IMAGE_FORMATS[c.texture_format],
        crs=chunk.crs,
        shift=shift,
        save_texture=True,
        save_uv=True,
        save_normals=True,
        save_colors=False,
        save_cameras=False,
        save_markers=False,
        progress=progress("export model"),
    )


def export_dsm(doc, cfg, path):
    doc.chunk.exportRaster(
        str(path),
        source_data=Metashape.ElevationData,
        nodata_value=cfg.exportRaster.nodata_value,
        image_format=Metashape.ImageFormatTIFF,
        image_compression=tiff_compression(),
        progress=progress("export DSM"),
    )


def export_orthomosaic(doc, cfg, path):
    doc.chunk.exportRaster(
        str(path),
        source_data=Metashape.OrthomosaicData,
        image_format=Metashape.ImageFormatTIFF,
        image_compression=tiff_compression(),
        save_alpha=True,
        white_background=False,
        progress=progress("export ortho"),
    )


def export_report(doc, cfg, path):
    # needs fonts in the image (fontconfig, fonts-dejavu-core, fonts-liberation),
    # otherwise it floods the log with "PDF error: error_no=..."
    doc.chunk.exportReport(str(path), title=doc.chunk.label, progress=progress("report"))


EXPORTS = [  # (label, available-check, file name from cfg, function)
    ("dense cloud", has_cloud, lambda cfg: f"dense_cloud.{cfg.exportPointCloud.format}", export_cloud),
    ("model + texture", has_texture, lambda cfg: f"{cfg.export.model_name}.{cfg.exportModel.format}", export_model),
    ("DSM (GeoTIFF)", has_dsm, lambda cfg: "dsm.tif", export_dsm),
    ("orthomosaic (GeoTIFF)", has_ortho, lambda cfg: "orthomosaic.tif", export_orthomosaic),
    ("report (PDF)", lambda chunk: True, lambda cfg: "report.pdf", export_report),
]


def export_all(doc, cfg):
    """Run every export whose product is built; a failure does not stop the others.

    An existing file is not exported again unless cfg.export.overwrite is set.
    """
    c = cfg.export
    c.folder.mkdir(parents=True, exist_ok=True)
    failed = []
    for label, available, filename, func in EXPORTS:
        if not available(doc.chunk):
            log(f"    skip {label}: not built")
            continue
        path = c.folder / filename(cfg)
        if path.exists() and not c.overwrite:
            log(f"    skip {label}: {path.name} exists (set export.overwrite to redo)")
            continue
        t0 = time.time()
        try:
            func(doc, cfg, path)
            log(f"    {label} -> {path.name} done in {(time.time() - t0) / 60:.1f} min")
        except Exception as e:
            log_error(label)
            if "cancel" in str(e).lower():
                raise
            failed.append(label)
    if failed:
        raise RuntimeError(f"failed exports: {', '.join(failed)}")


# ==========================================================================
#  pipeline
# ==========================================================================
STEPS = {  # name: (function, built-check, prerequisite steps), in pipeline order
    "photos": (add_photos, has_photos, ()),
    "align": (align_photos, is_aligned, ("photos",)),
    "optimize": (optimize_cameras, is_optimized, ("align",)),
    "depth": (build_depth_maps, has_depth, ("align",)),
    "cloud": (build_dense_cloud, has_cloud, ("depth",)),
    "filter": (filter_dense_cloud, is_filtered, ("cloud",)),
    "mesh": (build_mesh, has_mesh, ()),  # input: buildModel.source_data
    "texture": (build_texture, has_texture, ("mesh",)),
    "dsm": (build_dsm, has_dsm, ()),  # input: buildDem.source_data
    "ortho": (build_orthomosaic, has_ortho, ()),  # input: buildOrthomosaic.surface_data
    "export": (export_all, lambda chunk: False, ()),
}


def run_step(name, doc, cfg):
    """Run one step, skipped if already built (unless cfg.run.force) or if a
    prerequisite is missing; save the project after it.

    Returns False if it failed (logged with its traceback, project not saved).
    """
    func, built, requires = STEPS[name]
    if built(doc.chunk) and not cfg.run.force:
        log(f"skip {name}: already built (use --force to rebuild)")
        return True
    for req in requires:
        if not STEPS[req][1](doc.chunk):
            log(f"skip {name}: needs '{req}', which is not built")
            return False
    log(f"--- {name} | {memory_status()}")
    t0 = time.time()
    try:
        with Heartbeat(name, cfg.run.heartbeat_minutes):
            func(doc, cfg)
        doc.save()
    except Exception as e:
        log_error(name)
        if "cancel" in str(e).lower():  # kill -INT: stop the run, not just this step
            raise KeyboardInterrupt from e
        return False
    log(
        f"    {name} done in {(time.time() - t0) / 60:.1f} min (project saved) | "
        f"{memory_status()}"
    )
    return True


def start(cfg):
    """Check the license, select the GPUs and open the project.

    Returns the document, with doc.chunk set to the chunk to process.
    """
    set_log_file(cfg.export.folder / "processing.log")
    log(
        f"Metashape {Metashape.app.version} | license valid: {Metashape.license.valid} "
        f"| pid {os.getpid()}"
    )
    if not Metashape.license.valid:
        sys.exit(
            "No valid Metashape license (check AGISOFT_LICENSE_PATH / license server)."
        )
    threads = os.environ.get("OMP_NUM_THREADS", "all")
    log(f"CPU threads: {threads}/{os.cpu_count()} | {memory_status()}")
    if omp_threads(cfg.hardware) not in (None, threads):
        log(
            "WARNING: hardware.inactive_cores is not applied: it is read from "
            f"{config.CONFIG_FILE.name} when Metashape is imported"
        )
    setup_gpu(cfg.hardware.gpu)

    doc = open_project(cfg.project)
    chunk = doc.chunk
    aligned = sum(1 for c in chunk.cameras if c.transform)
    log(
        f"chunk '{chunk.label}': {len(chunk.cameras)} cameras ({aligned} aligned), "
        f"{len(chunk.markers)} markers, CRS: {chunk.crs.name if chunk.crs else None}"
    )
    return doc


def main():
    parser = argparse.ArgumentParser(
        prog="run",
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "steps",
        nargs="*",
        help=f"steps to run: {', '.join(STEPS)}; or 'all' (same as --all)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="run all the steps listed in [run] steps of the settings file",
    )
    parser.add_argument(
        "--force", action="store_true", help="rebuild products that already exist"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=config.CONFIG_FILE,
        help=f"TOML settings file (default: {config.CONFIG_FILE.name})",
    )
    args = parser.parse_args()

    explicit = [s for s in args.steps if s != "all"]
    run_all = args.all or "all" in args.steps
    if run_all and explicit:
        parser.error("'all' / --all cannot be combined with explicit steps")
    if not run_all and not explicit:  # nothing asked for: do not run anything
        parser.print_help()
        sys.exit(2)

    try:
        cfg = config.load(args.config)
    except (OSError, ValueError) as e:
        sys.exit(f"cannot load config: {e}")
    if explicit:
        cfg.run.steps = tuple(explicit)
    cfg.run.force = cfg.run.force or args.force
    bad = [s for s in cfg.run.steps if s not in STEPS]
    if bad:
        sys.exit(f"unknown step(s): {bad}. valid: {list(STEPS)}")

    doc = start(cfg)
    failed = []
    for name in STEPS:  # pipeline order, whatever the order given
        if name in cfg.run.steps and not run_step(name, doc, cfg):
            failed.append(name)  # later steps needing it are skipped by run_step
    if failed:
        log(f"finished with errors in: {', '.join(failed)} (see the tracebacks above)")
        sys.exit(1)
    log("all done")
