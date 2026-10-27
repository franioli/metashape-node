"""Configuration structure of the workflow, with defaults.

The values of a project are in its processing_config.toml (in the project folder,
see processing_config.example.toml); load them with
    cfg = config.load()                 # or load("other.toml")
Any key missing from the file keeps the default below. The result is plain
dataclasses, so a script or notebook can still change them before a step:
    cfg.buildModel.face_count_custom = 8_000_000

Naming mirrors the Metashape Python API, so its reference documents the options:
- a section named like a Chunk method (matchPhotos, buildModel, ...) holds keyword
  arguments of that method, with the same names, passed to it as they are;
- enum values are the Metashape enum name in snake_case without its type suffix:
  MildFiltering -> "mild", PointCloudData -> "point_cloud", HighFaceCount -> "high";
- projection is given as a CRS string ("EPSG::32632"), None = the chunk CRS.
Only the options useful here are mirrored; the others keep the Metashape default.
Sections in snake_case (project, run, ...) are settings of this script.
"""

import os
import tomllib  # Python >= 3.11
import types
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Literal, Union, get_args, get_origin

# settings file of the project: in the current folder, unless $MSWORKFLOW_CONFIG or
# --config say otherwise
CONFIG_FILE = Path(os.environ.get("MSWORKFLOW_CONFIG", "processing_config.toml")).resolve()

Blending = Literal["mosaic", "average", "max", "min", "disabled", "natural"]
Interpolation = Literal["disabled", "enabled", "extrapolated"]


# ==========================================================================
#  settings of this script
# ==========================================================================
@dataclass
class ProjectCfg:
    # source project (opened read-only); None = start a new, empty project at
    # processing_project (then run the photos and align steps first)
    source: Path | None = None
    # NEW project holding only the chunk to process, where all results are saved
    # (None = <source name>_processing.psx next to the source)
    processing_project: Path | None = None
    chunk: str | None = None  # label of the chunk to process, None = the last chunk
    in_place: bool = False  # process the source project directly, no new project


@dataclass
class RunCfg:
    # steps to run, always in pipeline order (each is skipped if already built):
    #   photos, align, optimize, depth, cloud, filter, mesh, texture, dsm, ortho, export
    # by default only the dense part: alignment is usually checked by hand (GCPs)
    steps: tuple[str, ...] = (
        "depth", "cloud", "filter", "mesh", "texture", "dsm", "ortho", "export"
    )
    force: bool = False  # rebuild products that already exist
    heartbeat_minutes: float = 5.0  # log RSS / free RAM every N minutes in a step, 0 = off


@dataclass
class HardwareCfg:
    gpu: str = "nvidia"  # use only GPUs whose name contains this, "all" = every GPU
    # CPU cores left unused, through OMP_NUM_THREADS (None = all cores). Applied when
    # helpers.py imports Metashape, from the default config file only.
    inactive_cores: int | None = None
    # folder with license_2.x.lic (Metashape 2.x reads AGISOFT_LICENSE_PATH, not the
    # old 1.x "agisoft_LICENSE"); an AGISOFT_LICENSE_PATH already set wins
    license_path: str = r"C:\Program Files\Agisoft\Metashape Pro"


@dataclass
class PhotosCfg:
    """Step photos: Chunk.addPhotos(filenames) with the files of folder."""

    folder: Path | None = None  # folder with the photos to add (not recursive)
    extensions: tuple[str, ...] = (".jpg", ".jpeg", ".tif", ".tiff", ".png", ".dng")


@dataclass
class GradualSelectionCfg:
    """Step optimize: TiePoints.Filter thresholds applied before optimizeCameras.

    Tie points with a criterion value above its threshold are removed (0 = off).
    reprojection_error is applied after a first optimization, followed by a second.
    """

    reconstruction_uncertainty: float = 0.0  # Filter.ReconstructionUncertainty, e.g. 50
    projection_accuracy: float = 0.0  # Filter.ProjectionAccuracy, e.g. 10
    reprojection_error: float = 0.0  # Filter.ReprojectionError, pixels, e.g. 1


@dataclass
class ConfidenceFilterCfg:
    """Step filter: PointCloud.setConfidenceFilter + removePoints (+ compactPoints)."""

    min_confidence: int = 0
    max_confidence: int = 2  # points with min <= confidence <= max are removed
    # PointCloud.compactPoints: purge them from disk, lighter project but IRREVERSIBLE
    # (otherwise PointCloud.restorePoints() can bring them back)
    compact: bool = False


@dataclass
class ExportCfg:
    folder: Path = Path("output")  # exported files and processing.log
    overwrite: bool = False  # re-export files that already exist
    model_name: str = "model"  # file name of the mesh, without extension


# ==========================================================================
#  Metashape Chunk methods: keyword arguments, same names
# ==========================================================================
@dataclass
class MatchPhotosCfg:
    downscale: Literal[0, 1, 2, 4, 8] = 1  # 0=Highest 1=High 2=Medium 4=Low 8=Lowest
    generic_preselection: bool = True  # find overlapping pairs on downscaled images
    reference_preselection: bool = True  # find overlapping pairs from camera positions
    # ReferencePreselection*: source = reference coordinates, estimated = current
    # alignment (re-matching), sequential = by photo order (strips)
    reference_preselection_mode: Literal["source", "estimated", "sequential"] = "source"
    filter_mask: bool = False  # ignore feature points in masked areas
    mask_tiepoints: bool = True  # apply the masks to the tie points too (if filter_mask)
    filter_stationary_points: bool = True  # drop points fixed in all images (e.g. fiducials)
    keypoint_limit: int = 40_000  # feature points per image (0 = unlimited)
    keypoint_limit_per_mpx: int = 1_000  # feature points per megapixel
    tiepoint_limit: int = 4_000  # tie points per image (0 = unlimited)
    keep_keypoints: bool = True  # allows adding photos later without re-matching
    guided_matching: bool = False  # more matches on repetitive texture, slower
    reset_matches: bool = False  # discard existing matches (e.g. with --force)
    subdivide_task: bool = True  # split in batches to limit memory use
    workitem_size_cameras: int = 20
    workitem_size_pairs: int = 80
    max_workgroup_size: int = 100


@dataclass
class AlignCamerasCfg:
    adaptive_fitting: bool = False  # fit more lens parameters when well constrained
    reset_alignment: bool = False  # align all cameras again, not only the unaligned ones
    subdivide_task: bool = True


@dataclass
class OptimizeCamerasCfg:
    # lens parameters to fit
    fit_f: bool = True
    fit_cx: bool = True
    fit_cy: bool = True
    fit_b1: bool = False
    fit_b2: bool = False
    fit_k1: bool = True
    fit_k2: bool = True
    fit_k3: bool = True
    fit_k4: bool = False
    fit_p1: bool = True
    fit_p2: bool = True
    fit_corrections: bool = False  # additional corrections
    adaptive_fitting: bool = False
    tiepoint_covariance: bool = True  # compute tie point covariances (for the report)


@dataclass
class BuildDepthMapsCfg:
    downscale: Literal[1, 2, 4, 8, 16] = 2  # 1=Ultra 2=High 4=Medium 8=Low 16=Lowest
    filter_mode: Literal["no", "mild", "moderate", "aggressive"] = "mild"  # *Filtering
    max_neighbors: int = 16  # neighbor photos per depth map (-1 = no limit)
    reuse_depth: bool = False  # reuse existing depth maps where possible
    subdivide_task: bool = True  # split in batches to limit memory use
    workitem_size_cameras: int = 20  # cameras per batch
    max_workgroup_size: int = 100  # max cameras per group processed together


@dataclass
class BuildPointCloudCfg:
    point_colors: bool = True
    point_confidence: bool = True  # needed by the filter step
    keep_depth: bool = True  # keep the depth maps after building the cloud
    max_neighbors: int = 100  # max neighbor images per point
    uniform_sampling: bool = False  # decimate the cloud to a regular spacing
    points_spacing: float = 0.1  # metres, used if uniform_sampling
    subdivide_task: bool = True
    workitem_size_cameras: int = 20
    max_workgroup_size: int = 100


@dataclass
class BuildModelCfg:
    source_data: Literal["point_cloud", "depth_maps"] = "point_cloud"
    surface_type: Literal["arbitrary", "height_field"] = "arbitrary"
    face_count: Literal["low", "medium", "high", "custom"] = "custom"
    face_count_custom: int = 15_000_000  # used if face_count = "custom"
    interpolation: Interpolation = "enabled"  # hole filling
    vertex_colors: bool = False  # separate pass over all photos; the texture has the colour
    vertex_confidence: bool = True
    keep_depth: bool = True  # keep the depth maps after building the mesh
    split_in_blocks: bool = False  # build in tiles (very large models)
    blocks_size: int = 250  # tile size in metres if split_in_blocks
    subdivide_task: bool = True  # split in batches to limit memory use
    workitem_size_cameras: int = 20
    max_workgroup_size: int = 100


@dataclass
class BuildUVCfg:
    mapping_mode: Literal["generic", "orthophoto", "adaptive_orthophoto"] = "generic"
    page_count: int = 8  # number of texture pages (more = sharper)
    texture_size: int = 16384  # px per page; keep equal to buildTexture.texture_size


@dataclass
class BuildTextureCfg:
    blending_mode: Blending = "mosaic"
    texture_size: int = 16384  # px per page
    downscale: int = 2  # image downscale used for texturing (1 = full resolution)
    fill_holes: bool = True
    ghosting_filter: bool = True
    out_of_focus_filter: bool = False  # prefer sharp photos for each texel
    color_enhancement: bool = False  # correct the brightness/colour of the result
    workitem_size_cameras: int = 20
    max_workgroup_size: int = 100


@dataclass
class BuildDemCfg:
    source_data: Literal["point_cloud", "tie_points"] = "point_cloud"
    interpolation: Interpolation = "enabled"  # hole filling
    projection: str | None = None  # output CRS, e.g. "EPSG::32632", None = the chunk CRS
    resolution: float = 0.0  # metres per pixel, 0 = automatic


@dataclass
class BuildOrthomosaicCfg:
    surface_data: Literal["elevation", "model"] = "elevation"  # DSM or mesh
    blending_mode: Blending = "mosaic"
    fill_holes: bool = True
    ghosting_filter: bool = False
    color_enhancement: bool = False  # correct the brightness/colour of the result
    cull_faces: bool = False  # ignore back-facing mesh faces (surface_data = "model")
    refine_seamlines: bool = False  # hide visible joins between photos (slower)
    projection: str | None = None  # output CRS, e.g. "EPSG::32632", None = the chunk CRS
    resolution: float = 0.0  # metres per pixel, 0 = automatic


@dataclass
class ExportPointCloudCfg:
    format: Literal["laz", "las", "ply", "e57", "xyz"] = "laz"  # PointCloudFormat*


@dataclass
class ExportModelCfg:
    format: Literal["obj", "ply", "fbx", "gltf"] = "obj"  # ModelFormat*
    texture_format: Literal["jpeg", "png", "tiff"] = "jpeg"  # ImageFormat*
    # local origin (x0, y0, z0) in CRS units subtracted from the mesh vertices
    # (None = chunk region centre rounded to 100 m)
    shift: tuple[float, float, float] | None = None


@dataclass
class ExportRasterCfg:
    nodata_value: float = -32767.0  # no-data value of the exported DSM


@dataclass
class Config:
    # this script
    project: ProjectCfg = field(default_factory=ProjectCfg)
    run: RunCfg = field(default_factory=RunCfg)
    hardware: HardwareCfg = field(default_factory=HardwareCfg)
    photos: PhotosCfg = field(default_factory=PhotosCfg)
    gradual_selection: GradualSelectionCfg = field(default_factory=GradualSelectionCfg)
    confidence_filter: ConfidenceFilterCfg = field(default_factory=ConfidenceFilterCfg)
    export: ExportCfg = field(default_factory=ExportCfg)
    # Metashape Chunk methods, in pipeline order
    matchPhotos: MatchPhotosCfg = field(default_factory=MatchPhotosCfg)
    alignCameras: AlignCamerasCfg = field(default_factory=AlignCamerasCfg)
    optimizeCameras: OptimizeCamerasCfg = field(default_factory=OptimizeCamerasCfg)
    buildDepthMaps: BuildDepthMapsCfg = field(default_factory=BuildDepthMapsCfg)
    buildPointCloud: BuildPointCloudCfg = field(default_factory=BuildPointCloudCfg)
    buildModel: BuildModelCfg = field(default_factory=BuildModelCfg)
    buildUV: BuildUVCfg = field(default_factory=BuildUVCfg)
    buildTexture: BuildTextureCfg = field(default_factory=BuildTextureCfg)
    buildDem: BuildDemCfg = field(default_factory=BuildDemCfg)
    buildOrthomosaic: BuildOrthomosaicCfg = field(default_factory=BuildOrthomosaicCfg)
    exportPointCloud: ExportPointCloudCfg = field(default_factory=ExportPointCloudCfg)
    exportModel: ExportModelCfg = field(default_factory=ExportModelCfg)
    exportRaster: ExportRasterCfg = field(default_factory=ExportRasterCfg)


# ---- loading the TOML file ----------------------------------------------------
def _convert(tp, value, root):
    """Check/convert a TOML value to the field type tp; raise ValueError if wrong."""
    origin = get_origin(tp)
    if origin is Literal:
        if value not in get_args(tp):
            raise ValueError(f"must be one of {list(get_args(tp))}")
        return value
    if origin in (Union, types.UnionType):  # e.g. Path | None: first match wins
        errors = []
        for member in get_args(tp):
            try:
                return _convert(member, value, root)
            except ValueError as e:
                errors.append(str(e))
        raise ValueError(" or ".join(errors))
    if origin is tuple:
        if not isinstance(value, list):
            raise ValueError("must be a list, e.g. [1, 2, 3]")
        return tuple(_convert(get_args(tp)[0], v, root) for v in value)
    if tp is Path and isinstance(value, str):
        return root / value  # relative paths are relative to the config file
    if tp is float and type(value) in (int, float):
        return float(value)
    if type(value) is tp:  # bool, int, str (type() so that true is not an int)
        return value
    raise ValueError("must be None" if tp is type(None) else f"must be {tp.__name__}")


def load(path=CONFIG_FILE):
    """Config with the values of the TOML file at path over the defaults."""
    path = Path(path).resolve()
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    cfg = Config()
    for group_name, values in data.items():
        if not hasattr(cfg, group_name) or not isinstance(values, dict):
            raise ValueError(f"{path}: unknown section [{group_name}]")
        group = getattr(cfg, group_name)
        valid = {f.name: f.type for f in fields(group)}
        for key, value in values.items():
            if key not in valid:
                raise ValueError(f"{path}: unknown key '{key}' in [{group_name}]")
            try:
                setattr(group, key, _convert(valid[key], value, path.parent))
            except ValueError as e:
                raise ValueError(f"{path}: [{group_name}] {key} = {value!r}: {e}") from None
    if not cfg.export.folder.is_absolute():  # the default too
        cfg.export.folder = path.parent / cfg.export.folder
    return cfg
