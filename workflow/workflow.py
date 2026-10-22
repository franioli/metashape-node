"""Dense processing of an already aligned Metashape chunk, from the command line.

Alignment, GCPs/CPs and camera calibration are assumed to be done already.
Pipeline: depth maps -> dense cloud -> mesh -> texture -> DSM -> orthomosaic -> exports.

Settings are grouped per processing stage (project, depth, cloud, mesh, texture,
dem, ortho, export, hardware) and resolved in this order, later wins:
    1. defaults in the dataclasses of the CONFIGURATION section (fallback)
    2. processing_config.toml next to the script (the file you normally edit)
    3. --preset NAME  ([presets.NAME.<group>] tables of the toml file)
    4. command line options, named <group>-<field>: --depth-downscale 1 ...
Use --print-config to see the effective settings, --help for all options.

Run it through run_processing.bat (uv), or with any interpreter that has the
Metashape module installed:
    python process.py --help
"""

import argparse
import os
import sys
import time
import tomllib  # Python >= 3.11
import types
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Literal, Union, get_args, get_origin

# Metashape 2.x reads this variable (NOT the old 1.x "agisoft_LICENSE").
# Folder containing license_2.x.lic (floating license server).
os.environ.setdefault("AGISOFT_LICENSE_PATH", r"C:\Program Files\Agisoft\Metashape Pro")

try:
    import Metashape
except ImportError:
    sys.exit(
        f"Cannot import Metashape with interpreter:\n  {sys.executable}\n\n"
        "Use the uv environment, e.g.:\n"
        "  run_processing.bat                       (default env C:\\venvs\\metashape-2.3)\n"
        "  set METASHAPE_VENV=C:\\venvs\\other & run_processing.bat\n"
        "  set METASHAPE_PYTHON=C:\\path\\to\\python.exe & run_processing.bat\n"
        "or install it:\n"
        "  uv venv C:\\venvs\\metashape-2.3 --python 3.13\n"
        "  uv pip install --python C:\\venvs\\metashape-2.3 <path to metashape-*.whl>"
    )

ROOT = (
    Path(__file__).resolve().parent
)  # project folder, relative paths in the toml are relative to it
DEFAULT_CONFIG_FILE = Path(__file__).resolve().parent / "processing_config.toml"


# ==========================================================================
#  CONFIGURATION - edit the defaults here, or override from the command line
#  Each group has a CLI prefix (empty = option name is just the field name).
# ==========================================================================
def opt(default, help, **kw):
    """Dataclass field with a help text (used for --help and as documentation)."""
    return field(default=default, metadata={"help": help, **kw})


BlendingName = Literal["mosaic", "average", "max", "min", "disabled"]


@dataclass
class ProjectCfg:
    prefix = ""
    source: Path | None = opt(
        None, "REQUIRED: source project (opened read-only, never modified)"
    )
    processing_project: Path | None = opt(
        None,
        "NEW project holding only the chunk to process, where all results are saved "
        "(default: <source name>_processing.psx next to the source)",
    )
    chunk: str | None = opt(
        None, "label of the chunk to process (default: the last chunk of the project)"
    )
    in_place: bool = opt(False, "process the source project directly, no new project")


@dataclass
class RunCfg:
    prefix = ""
    steps: str = opt(
        "depth,cloud,mesh,texture,dsm,ortho,export",
        "comma separated steps to run, or 'none'",
    )
    force: bool = opt(False, "rebuild steps even if their result already exists")


@dataclass
class HardwareCfg:
    prefix = ""
    gpu: str = opt(
        "nvidia", "use only GPUs whose name contains this text ('all' = every GPU)"
    )
    cpu_cores_inactive: int | None = opt(
        None, "CPU cores left free (default: Metashape's choice)"
    )


@dataclass
class DepthMapsCfg:
    prefix = "depth"
    downscale: Literal[1, 2, 4, 8, 16] = opt(
        2, "1=Ultra, 2=High, 4=Medium, 8=Low, 16=Lowest"
    )
    filter: Literal["none", "mild", "moderate", "aggressive"] = opt(
        "mild", "depth filtering"
    )
    max_neighbors: int = opt(
        16, "max neighbor images per depth map (-1 = no limit; more = slower, cleaner)"
    )
    reuse: bool = opt(False, "reuse existing depth maps where possible")
    subdivide_task: bool = opt(True, "split in batches to limit memory use")
    workitem_size_cameras: int = opt(20, "cameras per batch")
    max_workgroup_size: int = opt(100, "max cameras per group processed together")


@dataclass
class DenseCloudCfg:
    prefix = "cloud"
    colors: bool = opt(True, "compute point colors")
    confidence: bool = opt(True, "compute point confidence")
    keep_depth: bool = opt(True, "keep the depth maps after building the cloud")
    max_neighbors: int = opt(100, "max neighbor images per point")
    uniform_sampling: bool = opt(
        False, "decimate to a regular spacing (points_spacing)"
    )
    points_spacing: float = opt(0.1, "point spacing in metres if uniform_sampling")


@dataclass
class MeshCfg:
    prefix = "mesh"
    surface: Literal["arbitrary", "height_field"] = opt("arbitrary", "surface type")
    face_count: Literal["low", "medium", "high"] = opt("high", "target face count")
    face_count_custom: int | None = opt(
        None, "exact target face count, overrides face_count"
    )
    interpolation: Literal["disabled", "enabled", "extrapolated"] = opt(
        "enabled", "hole filling"
    )
    vertex_colors: bool = opt(True, "compute vertex colors")
    vertex_confidence: bool = opt(True, "compute vertex confidence")
    keep_depth: bool = opt(True, "keep the depth maps after building the mesh")
    split_in_blocks: bool = opt(False, "build in tiles (very large models)")
    blocks_size: int = opt(250, "tile size in metres if split_in_blocks")


@dataclass
class TextureCfg:
    prefix = "texture"
    size: int = opt(8192, "px per texture page")
    pages: int = opt(1, "number of texture pages")
    mapping: Literal["generic", "orthophoto", "adaptive_orthophoto"] = opt(
        "generic", "UV mapping mode"
    )
    blending: BlendingName = opt("mosaic", "color blending")
    downscale: int = opt(2, "image downscale used for texturing (1 = full resolution)")
    fill_holes: bool = opt(True, "fill texture holes")
    ghosting_filter: bool = opt(True, "ghosting filter")


@dataclass
class DemCfg:
    prefix = "dem"
    resolution: float = opt(0.0, "DSM pixel size in metres, 0 = automatic")
    interpolation: Literal["disabled", "enabled", "extrapolated"] = opt(
        "enabled", "hole filling"
    )
    crs: str | None = opt(
        None, "output CRS, e.g. 'EPSG::32632' (default: the chunk CRS)"
    )


@dataclass
class OrthoCfg:
    prefix = "ortho"
    resolution: float = opt(0.0, "orthomosaic pixel size in metres, 0 = automatic")
    surface: Literal["dsm", "mesh"] = opt("dsm", "surface used to orthorectify")
    blending: BlendingName = opt("mosaic", "color blending")
    fill_holes: bool = opt(True, "fill holes")
    ghosting_filter: bool = opt(False, "ghosting filter")
    cull_faces: bool = opt(False, "ignore back-facing mesh faces (surface = mesh)")
    refine_seamlines: bool = opt(False, "refine seamlines to hide visible joins")
    crs: str | None = opt(
        None, "output CRS, e.g. 'EPSG::32632' (default: the chunk CRS)"
    )


@dataclass
class ExportCfg:
    prefix = "export"
    folder: Path = opt(ROOT / "output", "folder for exported files and processing.log")
    cloud_format: Literal["laz", "las", "ply", "e57", "xyz"] = opt(
        "laz", "dense cloud format"
    )
    model_format: Literal["obj", "ply", "fbx", "glb"] = opt("obj", "mesh format")
    texture_format: Literal["jpeg", "png", "tiff"] = opt("jpeg", "mesh texture format")
    nodata_value: float = opt(-32767.0, "no-data value of the exported DSM")


@dataclass
class Config:
    project: ProjectCfg = field(default_factory=ProjectCfg)
    run: RunCfg = field(default_factory=RunCfg)
    hardware: HardwareCfg = field(default_factory=HardwareCfg)
    depth: DepthMapsCfg = field(default_factory=DepthMapsCfg)
    cloud: DenseCloudCfg = field(default_factory=DenseCloudCfg)
    mesh: MeshCfg = field(default_factory=MeshCfg)
    texture: TextureCfg = field(default_factory=TextureCfg)
    dem: DemCfg = field(default_factory=DemCfg)
    ortho: OrthoCfg = field(default_factory=OrthoCfg)
    export: ExportCfg = field(default_factory=ExportCfg)


# ---- command line generated from the config dataclasses --------------------
def _arg_type(tp):
    """Return (python type, choices) of a field annotation for argparse."""
    if get_origin(tp) is Literal:
        choices = list(get_args(tp))
        return type(choices[0]), choices
    if get_origin(tp) in (Union, getattr(types, "UnionType", Union)):  # Optional[X]
        return _arg_type(next(a for a in get_args(tp) if a is not type(None)))
    return tp, None


def _dest(group_name, f):
    return f"{group_name}__{f.name}"


# ---- validation of values coming from the TOML file -------------------------
def _coerce(tp, value):
    """Validate/convert a TOML value to the field type; raise ValueError if wrong."""
    if get_origin(tp) is Literal:
        if value not in get_args(tp):
            raise ValueError(f"must be one of {list(get_args(tp))}")
        return value
    if get_origin(tp) in (Union, getattr(types, "UnionType", Union)):  # Optional[X]
        return _coerce(next(a for a in get_args(tp) if a is not type(None)), value)
    if tp is bool:
        if not isinstance(value, bool):
            raise ValueError("must be true or false")
        return value
    if tp is Path:
        p = Path(value)
        return (
            p if p.is_absolute() else ROOT / p
        )  # relative paths are relative to the project folder
    if tp is float and isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if tp in (int, str) and type(value) is tp:
        return value
    raise ValueError(f"must be of type {tp.__name__}")


def apply_values(cfg, data, source):
    """Apply {group: {field: value}} onto cfg, with clear errors for unknown/invalid entries."""
    for group_name, values in data.items():
        if not hasattr(cfg, group_name) or not isinstance(values, dict):
            sys.exit(
                f"{source}: unknown section [{group_name}]. valid: {[f.name for f in fields(cfg)]}"
            )
        group = getattr(cfg, group_name)
        valid = {f.name: f for f in fields(group)}
        for key, value in values.items():
            if key not in valid:
                sys.exit(
                    f"{source}: unknown key '{key}' in [{group_name}]. valid: {list(valid)}"
                )
            try:
                setattr(group, key, _coerce(valid[key].type, value))
            except ValueError as e:
                sys.exit(f"{source}: [{group_name}] {key} = {value!r}: {e}")


def format_config(cfg):
    """Effective configuration as TOML-like text (None values are omitted)."""
    lines = []
    for group_name in (f.name for f in fields(cfg)):
        lines.append(f"[{group_name}]")
        for f in fields(getattr(cfg, group_name)):
            v = getattr(getattr(cfg, group_name), f.name)
            if v is None:
                continue
            v = str(v).replace("\\", "/") if isinstance(v, Path) else v
            lines.append(
                f"{f.name} = {str(v).lower() if isinstance(v, bool) else repr(v).replace(chr(39), chr(34))}"
            )
        lines.append("")
    return "\n".join(lines)


def parse_config():
    """Resolve settings: script defaults < config file < preset < command line."""
    cfg = Config()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    meta = parser.add_argument_group("configuration")
    meta.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_FILE,
        help=f"TOML settings file (default: {DEFAULT_CONFIG_FILE.name} next to the script, if present)",
    )
    meta.add_argument(
        "--preset", help="named preset from the config file, e.g. draft or final"
    )
    meta.add_argument(
        "--print-config",
        action="store_true",
        help="print the effective settings and exit",
    )

    for group_name in (f.name for f in fields(cfg)):
        group = getattr(cfg, group_name)
        section = parser.add_argument_group(group_name)
        prefix = type(group).prefix
        for f in fields(group):
            flag = "--" + "-".join(filter(None, [prefix, f.name.replace("_", "-")]))
            help_text = f.metadata["help"]
            if f.type is bool:
                section.add_argument(
                    flag,
                    dest=_dest(group_name, f),
                    action=argparse.BooleanOptionalAction,  # --flag / --no-flag
                    default=argparse.SUPPRESS,
                    help=f"{help_text} (default: {f.default})",
                )
                continue
            tp, choices = _arg_type(f.type)
            section.add_argument(
                flag,
                dest=_dest(group_name, f),
                type=tp,
                choices=choices,
                metavar=None if choices else f.name.upper(),
                default=argparse.SUPPRESS,
                help=f"{help_text} (default: {f.default})",
            )
    args = vars(parser.parse_args())

    # 1) config file (explicit --config must exist, the default one is optional)
    config_file = args.pop("config")
    preset = args.pop("preset")
    print_config = args.pop("print_config")
    presets = {}
    if config_file.exists():
        with open(config_file, "rb") as fh:
            data = tomllib.load(fh)
        presets = data.pop("presets", {})
        apply_values(cfg, data, str(config_file))
    elif config_file != DEFAULT_CONFIG_FILE:
        sys.exit(f"config file not found: {config_file}")

    # 2) preset
    if preset:
        if preset not in presets:
            sys.exit(
                f"preset '{preset}' not found in {config_file}. available: {list(presets)}"
            )
        apply_values(cfg, presets[preset], f"{config_file} [presets.{preset}]")

    # 3) command line (already validated by argparse)
    for dest, value in args.items():
        group_name, field_name = dest.split("__")
        setattr(getattr(cfg, group_name), field_name, value)

    if print_config:
        print(format_config(cfg))
        sys.exit(0)
    return cfg


# ==========================================================================
#  helpers
# ==========================================================================
_log_file = None


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    if _log_file:
        _log_file.write(line + "\n")
        _log_file.flush()


def progress(label):
    """Progress callback printing every 10 %."""
    state = {"last": -10}

    def callback(p):
        if p - state["last"] >= 10 or p >= 100:
            state["last"] = p
            print(f"    {label}: {p:5.1f}%", flush=True)

    return callback


def setup_gpu(hw):
    devices = Metashape.app.enumGPUDevices()
    for i, d in enumerate(devices):
        log(f"GPU {i}: {d['name']} ({d['mem_size'] / 2**30:.1f} GB)")
    if hw.gpu.lower() != "all":
        mask = sum(
            1 << i for i, d in enumerate(devices) if hw.gpu.lower() in d["name"].lower()
        )
        if mask:
            Metashape.app.gpu_mask = mask
            log(f"gpu_mask = {mask:#b}")
        else:
            log(f"WARNING: no GPU matches '{hw.gpu}', leaving Metashape defaults")
    if hw.cpu_cores_inactive is not None:
        Metashape.app.cpu_cores_inactive = hw.cpu_cores_inactive


def projection_kwargs(crs):
    """{'projection': ...} for the given CRS text, or {} to use the chunk CRS."""
    if not crs:
        return {}
    proj = Metashape.OrthoProjection()
    proj.crs = Metashape.CoordinateSystem(crs)
    return {"projection": proj}


def tiff_compression():
    c = Metashape.ImageCompression()
    c.tiff_compression = Metashape.ImageCompression.TiffCompressionLZW
    c.tiff_big = True
    c.tiff_tiled = True
    c.tiff_overviews = True
    return c


FILTERS = {
    "none": Metashape.NoFiltering,
    "mild": Metashape.MildFiltering,
    "moderate": Metashape.ModerateFiltering,
    "aggressive": Metashape.AggressiveFiltering,
}
FACES = {
    "low": Metashape.LowFaceCount,
    "medium": Metashape.MediumFaceCount,
    "high": Metashape.HighFaceCount,
}
INTERPOLATIONS = {
    "disabled": Metashape.DisabledInterpolation,
    "enabled": Metashape.EnabledInterpolation,
    "extrapolated": Metashape.Extrapolated,
}
BLENDINGS = {
    "mosaic": Metashape.MosaicBlending,
    "average": Metashape.AverageBlending,
    "max": Metashape.MaxBlending,
    "min": Metashape.MinBlending,
    "disabled": Metashape.DisabledBlending,
}
MAPPINGS = {
    "generic": Metashape.GenericMapping,
    "orthophoto": Metashape.OrthophotoMapping,
    "adaptive_orthophoto": Metashape.AdaptiveOrthophotoMapping,
}
IMAGE_FORMATS = {
    "jpeg": Metashape.ImageFormatJPEG,
    "png": Metashape.ImageFormatPNG,
    "tiff": Metashape.ImageFormatTIFF,
}
SURFACES = {"arbitrary": Metashape.Arbitrary, "height_field": Metashape.HeightField}


@dataclass
class Context:
    """What every step receives."""

    cfg: Config
    doc: Metashape.Document
    chunk: Metashape.Chunk


# ---- "is this product already built?" checks --------------------------------
def has_depth(chunk):
    return chunk.depth_maps is not None


def has_cloud(chunk):
    return chunk.point_cloud is not None


def has_mesh(chunk):
    return chunk.model is not None


def has_texture(chunk):
    return chunk.model is not None and bool(chunk.model.textures)


def has_dsm(chunk):
    return chunk.elevation is not None


def has_ortho(chunk):
    return chunk.orthomosaic is not None


# ==========================================================================
#  step registry: the decorators handle skip / prerequisites / timing / saving
# ==========================================================================
STEPS = {}  # name -> (function, built-check, prerequisites), in pipeline order
EXPORTS = []  # (label, available-check, function)


def step(name, built, requires=()):
    """Register a processing step.

    built:    function(chunk) -> True if the product already exists (step skipped)
    requires: names of earlier steps whose product must exist
    """

    def decorator(func):
        STEPS[name] = (func, built, requires)
        return func

    return decorator


def export(label, available):
    """Register an export; a failure is logged and does not stop the others."""

    def decorator(func):
        EXPORTS.append((label, available, func))
        return func

    return decorator


def run_step(name, ctx):
    func, built, requires = STEPS[name]
    if built(ctx.chunk) and not ctx.cfg.run.force:
        log(f"skip {name}: already built (use --force to rebuild)")
        return
    for req in requires:
        if not STEPS[req][1](ctx.chunk):
            sys.exit(f"step '{name}' needs '{req}' (run it first)")
    log(f"--- {name}")
    t0 = time.time()
    func(ctx)
    ctx.doc.save()
    log(f"    {name} done in {(time.time() - t0) / 60:.1f} min (project saved)")


def run_exports(ctx):
    log("--- exports")
    ctx.cfg.export.folder.mkdir(parents=True, exist_ok=True)
    for label, available, func in EXPORTS:
        if not available(ctx.chunk):
            log(f"    skip {label}: not built")
            continue
        t0 = time.time()
        try:
            func(ctx)
            log(f"    {label} -> done in {(time.time() - t0) / 60:.1f} min")
        except Exception as e:  # one failed export must not lose the others
            log(f"    {label} FAILED: {e}")


# ==========================================================================
#  processing steps
# ==========================================================================
@step("depth", built=has_depth)
def build_depth_maps(ctx):
    c = ctx.cfg.depth
    ctx.chunk.buildDepthMaps(
        downscale=c.downscale,
        filter_mode=FILTERS[c.filter],
        max_neighbors=c.max_neighbors,
        reuse_depth=c.reuse,
        subdivide_task=c.subdivide_task,
        workitem_size_cameras=c.workitem_size_cameras,
        max_workgroup_size=c.max_workgroup_size,
        progress=progress("depth maps"),
    )


@step("cloud", built=has_cloud, requires=("depth",))
def build_dense_cloud(ctx):
    c = ctx.cfg.cloud
    ctx.chunk.buildPointCloud(
        source_data=Metashape.DepthMapsData,
        point_colors=c.colors,
        point_confidence=c.confidence,
        keep_depth=c.keep_depth,
        max_neighbors=c.max_neighbors,
        uniform_sampling=c.uniform_sampling,
        points_spacing=c.points_spacing,
        replace_asset=True,
        progress=progress("dense cloud"),
    )


@step("mesh", built=has_mesh, requires=("depth",))
def build_mesh(ctx):
    c = ctx.cfg.mesh
    custom_faces = (
        {}
        if c.face_count_custom is None
        else {"face_count_custom": c.face_count_custom}
    )
    ctx.chunk.buildModel(
        surface_type=SURFACES[c.surface],
        source_data=Metashape.DepthMapsData,
        interpolation=INTERPOLATIONS[c.interpolation],
        face_count=FACES[c.face_count],
        **custom_faces,
        vertex_colors=c.vertex_colors,
        vertex_confidence=c.vertex_confidence,
        keep_depth=c.keep_depth,
        split_in_blocks=c.split_in_blocks,
        blocks_size=c.blocks_size,
        build_texture=False,
        replace_asset=True,
        progress=progress("mesh"),
    )


@step("texture", built=has_texture, requires=("mesh",))
def build_texture(ctx):
    c = ctx.cfg.texture
    ctx.chunk.buildUV(
        mapping_mode=MAPPINGS[c.mapping],
        page_count=c.pages,
        texture_size=c.size,
        progress=progress("UV"),
    )
    ctx.chunk.buildTexture(
        blending_mode=BLENDINGS[c.blending],
        texture_size=c.size,
        downscale=c.downscale,
        fill_holes=c.fill_holes,
        ghosting_filter=c.ghosting_filter,
        progress=progress("texture"),
    )


@step("dsm", built=has_dsm, requires=("cloud",))
def build_dsm(ctx):
    c = ctx.cfg.dem
    ctx.chunk.buildDem(
        source_data=Metashape.PointCloudData,
        interpolation=INTERPOLATIONS[c.interpolation],
        resolution=c.resolution,
        **projection_kwargs(c.crs),
        replace_asset=True,
        progress=progress("DSM"),
    )


@step("ortho", built=has_ortho)
def build_orthomosaic(ctx):
    c = ctx.cfg.ortho
    if c.surface == "dsm":
        if not has_dsm(ctx.chunk):
            sys.exit("ortho on DSM needs the DSM (run the 'dsm' step first)")
        surface = Metashape.ElevationData
    else:
        if not has_mesh(ctx.chunk):
            sys.exit("ortho on mesh needs the mesh (run the 'mesh' step first)")
        surface = Metashape.ModelData
    ctx.chunk.buildOrthomosaic(
        surface_data=surface,
        blending_mode=BLENDINGS[c.blending],
        fill_holes=c.fill_holes,
        ghosting_filter=c.ghosting_filter,
        cull_faces=c.cull_faces,
        refine_seamlines=c.refine_seamlines,
        resolution=c.resolution,
        **projection_kwargs(c.crs),
        replace_asset=True,
        progress=progress("orthomosaic"),
    )


# ==========================================================================
#  exports
# ==========================================================================
@export("dense cloud", available=has_cloud)
def export_cloud(ctx):
    ctx.chunk.exportPointCloud(
        str(ctx.cfg.export.folder / f"dense_cloud.{ctx.cfg.export.cloud_format}"),
        source_data=Metashape.PointCloudData,
        crs=ctx.chunk.crs,
        save_point_color=True,
        save_point_confidence=True,
        progress=progress("export cloud"),
    )


@export("model + texture", available=has_mesh)
def export_model(ctx):
    c = ctx.cfg.export
    ctx.chunk.exportModel(
        str(c.folder / f"model.{c.model_format}"),
        crs=ctx.chunk.crs,
        save_texture=True,
        save_uv=True,
        texture_format=IMAGE_FORMATS[c.texture_format],
        progress=progress("export model"),
    )


@export("DSM (GeoTIFF)", available=has_dsm)
def export_dsm(ctx):
    ctx.chunk.exportRaster(
        str(ctx.cfg.export.folder / "dsm.tif"),
        source_data=Metashape.ElevationData,
        nodata_value=ctx.cfg.export.nodata_value,
        image_format=Metashape.ImageFormatTIFF,
        image_compression=tiff_compression(),
        progress=progress("export DSM"),
    )


@export("orthomosaic (GeoTIFF)", available=has_ortho)
def export_orthomosaic(ctx):
    ctx.chunk.exportRaster(
        str(ctx.cfg.export.folder / "orthomosaic.tif"),
        source_data=Metashape.OrthomosaicData,
        image_format=Metashape.ImageFormatTIFF,
        image_compression=tiff_compression(),
        save_alpha=True,
        white_background=False,
        progress=progress("export ortho"),
    )


def always(chunk):
    return True


@export("report (PDF)", available=always)
def export_report(ctx):
    ctx.chunk.exportReport(
        str(ctx.cfg.export.folder / "report.pdf"),
        title=ctx.chunk.label,
        progress=progress("report"),
    )


# ==========================================================================
#  main
# ==========================================================================
def find_chunk(doc, label):
    """Chunk with the given label, or the last chunk if label is None; exits if none."""
    if not doc.chunks:
        sys.exit("the project has no chunks")
    if label is None:
        return doc.chunks[-1]
    chunk = next((c for c in doc.chunks if c.label == label), None)
    if chunk is None:
        sys.exit(
            f"chunk '{label}' not found. available: {[c.label for c in doc.chunks]}"
        )
    return chunk


def open_doc(path, **kw):
    doc = Metashape.Document()
    try:
        doc.open(str(path), **kw)
    except Exception as e:  # Metashape raises OSError/RuntimeError
        sys.exit(f"cannot open project {path}: {e}")
    # Metashape silently falls back to read-only when the project is in use:
    # fail now instead of after hours of processing, when save() is refused.
    if not kw.get("read_only") and doc.read_only:
        sys.exit(
            f"project {path} is in use (opened read-only): close Metashape or stop "
            "the other process.py run, then retry."
        )
    return doc


def open_project(p):
    """Open the project to process.

    By default only the chunk to process is exported into a NEW project, which
    isolates the processing (small file, other chunks and the source untouched).
    """
    if p.in_place:
        return open_doc(p.source), p.source
    if p.processing_project.exists():
        log(f"resuming existing processing project {p.processing_project}")
        return open_doc(p.processing_project), p.processing_project

    log(f"opening {p.source} read-only")
    source = open_doc(p.source, read_only=True)
    chunk = find_chunk(source, p.chunk)
    log(f"exporting chunk '{chunk.label}' to new project {p.processing_project}")
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
    return doc, p.processing_project


def main():
    global _log_file
    cfg = parse_config()

    p = cfg.project
    if p.source is None:
        sys.exit(
            "no source project: set [project] source in the config file or use --source"
        )
    if not p.source.is_file():
        sys.exit(f"source project not found: {p.source}")
    if p.processing_project is None:
        p.processing_project = p.source.with_name(p.source.stem + "_processing.psx")

    steps = [
        s.strip() for s in cfg.run.steps.split(",") if s.strip() not in ("", "none")
    ]
    bad = [s for s in steps if s not in STEPS and s != "export"]
    if bad:
        sys.exit(f"unknown step(s): {bad}. valid: {[*STEPS, 'export']}")

    cfg.export.folder.mkdir(parents=True, exist_ok=True)
    _log_file = open(cfg.export.folder / "processing.log", "a", encoding="utf-8")

    log(f"Metashape {Metashape.app.version} | license valid: {Metashape.license.valid}")
    if not Metashape.license.valid:
        sys.exit(
            "No valid Metashape license (check AGISOFT_LICENSE_PATH / license server)."
        )
    setup_gpu(cfg.hardware)

    doc, path = open_project(cfg.project)
    log(f"project: {path}")

    chunk = find_chunk(doc, cfg.project.chunk)
    doc.chunk = chunk
    aligned = sum(1 for c in chunk.cameras if c.transform)
    log(
        f"chunk '{chunk.label}': {len(chunk.cameras)} cameras ({aligned} aligned), "
        f"{len(chunk.markers)} markers, CRS: {chunk.crs.name if chunk.crs else None}"
    )
    if not aligned:
        sys.exit("chunk has no aligned cameras")

    ctx = Context(cfg, doc, chunk)
    for name in STEPS:  # pipeline order, whatever the order given on the command line
        if name in steps:
            run_step(name, ctx)
    if "export" in steps:
        run_exports(ctx)
    log("all done")


if __name__ == "__main__":
    main()
