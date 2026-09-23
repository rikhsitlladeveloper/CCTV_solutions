"""Request and response contracts for positioning and calibration.

Validation here is deliberately strict: malformed matrices, non-finite numbers,
invalid rotations and under-constrained point sets are rejected at the edge
rather than producing a confident but meaningless result downstream.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import CalibrationMethod, CalibrationStatus, ObservationRole, PixelConvention

Finite = Annotated[float, Field(allow_inf_nan=False)]


def _check_finite(values, label: str):
    for v in values:
        if v is None or not math.isfinite(float(v)):
            raise ValueError(f"{label} must contain only finite numbers.")
    return values


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# -- coordinate systems --------------------------------------------------

class CoordinateSystemIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    site_id: int | None = None
    origin_description: str | None = None
    notes: str | None = None
    floor_plane_z: Finite = 0.0
    grid_min_x: Finite = -10.0
    grid_max_x: Finite = 60.0
    grid_min_y: Finite = -10.0
    grid_max_y: Finite = 60.0
    grid_spacing_m: Finite = Field(default=1.0, gt=0.01, le=100.0)
    max_reprojection_error_px: Finite = Field(default=3.0, gt=0, le=1000)
    max_ground_error_m: Finite = Field(default=0.25, gt=0, le=100)
    min_reference_points: int = Field(default=6, ge=4, le=200)

    @model_validator(mode="after")
    def _grid_is_sane(self):
        if self.grid_max_x <= self.grid_min_x or self.grid_max_y <= self.grid_min_y:
            raise ValueError("Grid maximum values must be greater than the minimums.")
        span = min(self.grid_max_x - self.grid_min_x, self.grid_max_y - self.grid_min_y)
        if self.grid_spacing_m > span:
            raise ValueError("Grid spacing cannot be larger than the grid itself.")
        return self


class CoordinateSystemUpdate(BaseModel):
    """Edits to a frame. Changing its physical meaning needs explicit consent."""
    name: str | None = Field(default=None, min_length=1, max_length=120)
    site_id: int | None = None
    origin_description: str | None = None
    notes: str | None = None
    floor_plane_z: Finite | None = None
    grid_min_x: Finite | None = None
    grid_max_x: Finite | None = None
    grid_min_y: Finite | None = None
    grid_max_y: Finite | None = None
    grid_spacing_m: Finite | None = Field(default=None, gt=0.01, le=100.0)
    max_reprojection_error_px: Finite | None = Field(default=None, gt=0, le=1000)
    max_ground_error_m: Finite | None = Field(default=None, gt=0, le=100)
    min_reference_points: int | None = Field(default=None, ge=4, le=200)
    # Required to change anything that redefines the frame physically.
    confirm_redefinition: bool = False
    redefinition_reason: str | None = None


class CoordinateSystemOut(ORMModel):
    id: int
    name: str
    site_id: int | None
    origin_description: str | None
    notes: str | None
    origin_photo_path: str | None
    floor_plane_z: float
    grid_min_x: float
    grid_max_x: float
    grid_min_y: float
    grid_max_y: float
    grid_spacing_m: float
    max_reprojection_error_px: float
    max_ground_error_m: float
    min_reference_points: int
    definition_revision: int
    created_at: datetime
    updated_at: datetime
    reference_point_count: int = 0
    camera_count: int = 0
    conventions: dict[str, str] = {}


# -- reference points ----------------------------------------------------

class ReferencePointIn(BaseModel):
    coordinate_system_id: int
    code: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=160)
    x: Finite
    y: Finite
    z: Finite = 0.0
    description: str | None = None
    measurement_notes: str | None = None
    uncertainty_m: Finite | None = Field(default=None, ge=0, le=100)
    aruco_dictionary: str | None = Field(default=None, max_length=40)
    aruco_marker_id: int | None = Field(default=None, ge=0, le=100000)
    aruco_marker_size_m: Finite | None = Field(default=None, gt=0, le=10)
    aruco_corner_index: int | None = Field(default=None, ge=0, le=3)

    @model_validator(mode="after")
    def _aruco_needs_size(self):
        if self.aruco_marker_id is not None and not self.aruco_marker_size_m:
            raise ValueError(
                "An ArUco marker ID alone does not fix anything in the world. Give the marker's "
                "physical size as well, and keep the surveyed X/Y/Z as the authority."
            )
        return self


class ReferencePointUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=60)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    x: Finite | None = None
    y: Finite | None = None
    z: Finite | None = None
    description: str | None = None
    measurement_notes: str | None = None
    uncertainty_m: Finite | None = Field(default=None, ge=0, le=100)


class ReferencePointOut(ORMModel):
    id: int
    coordinate_system_id: int
    code: str
    name: str
    x: float
    y: float
    z: float
    description: str | None
    measurement_notes: str | None
    uncertainty_m: float | None
    photo_path: str | None
    aruco_dictionary: str | None
    aruco_marker_id: int | None
    aruco_marker_size_m: float | None
    aruco_corner_index: int | None
    observation_count: int = 0


# -- intrinsics ----------------------------------------------------------

class IntrinsicsImport(BaseModel):
    label: str = Field(default="Imported calibration", max_length=160)
    model: str = "pinhole"
    camera_matrix: list[list[Finite]] | list[Finite]
    distortion_coefficients: list[Finite] = []
    image_width: int = Field(ge=2, le=20000)
    image_height: int = Field(ge=2, le=20000)
    image_rotation_deg: Literal[0, 90, 180, 270] = 0
    crop: list[int] | None = None
    lens_description: str | None = Field(default=None, max_length=160)
    zoom_state: str | None = Field(default=None, max_length=80)
    rms_reprojection_error_px: Finite | None = Field(default=None, ge=0)
    calibrated_at: datetime | None = None
    notes: str | None = None
    activate: bool = True

    @field_validator("camera_matrix")
    @classmethod
    def _matrix_shape(cls, v):
        flat = [x for row in v for x in row] if v and isinstance(v[0], list) else list(v)
        if len(flat) != 9:
            raise ValueError("The camera matrix K must have nine elements (3x3).")
        _check_finite(flat, "The camera matrix")
        return v

    @field_validator("crop")
    @classmethod
    def _crop_shape(cls, v):
        if v is not None and len(v) != 4:
            raise ValueError("Crop must be [x, y, width, height].")
        return v


class IntrinsicsOut(ORMModel):
    id: int
    camera_id: int
    label: str
    model: str
    camera_matrix: list[list[float]] = []
    distortion_coefficients: list[float] = []
    width: int
    height: int
    image_rotation_deg: int
    crop: list[int] | None = None
    geometry_key: str = ""
    lens_description: str | None
    zoom_state: str | None
    source: str
    rms_reprojection_error_px: float | None
    view_count: int | None
    calibrated_at: datetime | None
    notes: str | None
    is_active: bool
    created_at: datetime
    horizontal_fov_deg: float | None = None
    vertical_fov_deg: float | None = None


class CheckerboardConfig(BaseModel):
    inner_cols: int = Field(ge=3, le=40, description="Inner corners across")
    inner_rows: int = Field(ge=3, le=40, description="Inner corners down")
    square_size_m: Finite = Field(gt=0.001, le=1.0)
    label: str = Field(default="Checkerboard calibration", max_length=160)
    lens_description: str | None = None
    zoom_state: str | None = None
    activate: bool = False

    @model_validator(mode="after")
    def _not_square_pattern(self):
        if self.inner_cols == self.inner_rows:
            raise ValueError(
                "A square inner-corner count (for example 6x6) is rotationally ambiguous. "
                "Use a board with different counts, such as 9x6."
            )
        return self


# -- observations --------------------------------------------------------

class ObservationIn(BaseModel):
    reference_point_id: int
    pixel_u: Finite = Field(ge=-10000, le=100000)
    pixel_v: Finite = Field(ge=-10000, le=100000)
    image_width: int = Field(ge=2, le=20000)
    image_height: int = Field(ge=2, le=20000)
    role: ObservationRole = ObservationRole.fit
    source: str = Field(default="manual", max_length=30)
    notes: str | None = None

    @model_validator(mode="after")
    def _pixel_inside_image(self):
        if not (0 <= self.pixel_u <= self.image_width - 1):
            raise ValueError(
                f"Pixel u={self.pixel_u} is outside a {self.image_width}px-wide image.")
        if not (0 <= self.pixel_v <= self.image_height - 1):
            raise ValueError(
                f"Pixel v={self.pixel_v} is outside a {self.image_height}px-tall image.")
        return self


class ObservationOut(ORMModel):
    id: int
    camera_id: int
    reference_point_id: int
    pixel_u: float
    pixel_v: float
    image_width: int
    image_height: int
    role: ObservationRole
    source: str
    notes: str | None
    reference_point: ReferencePointOut | None = None


class ObservationBulkIn(BaseModel):
    observations: list[ObservationIn] = Field(min_length=1, max_length=500)
    replace_existing: bool = False


# -- solving -------------------------------------------------------------

class ManualPoseIn(BaseModel):
    coordinate_system_id: int
    x: Finite = Field(ge=-100000, le=100000)
    y: Finite = Field(ge=-100000, le=100000)
    z: Finite = Field(ge=-1000, le=1000)
    roll_deg: Finite = Field(ge=-360, le=360)
    pitch_deg: Finite = Field(ge=-360, le=360)
    yaw_deg: Finite = Field(ge=-360, le=360)
    approx_hfov_deg: Finite | None = Field(default=None, gt=1, lt=179)
    approx_vfov_deg: Finite | None = Field(default=None, gt=1, lt=179)
    approx_range_m: Finite | None = Field(default=None, gt=0, le=500)
    source_image_width: int | None = Field(default=None, ge=2, le=20000)
    source_image_height: int | None = Field(default=None, ge=2, le=20000)
    notes: str | None = None
    activate: bool = True


class HomographySolveIn(BaseModel):
    coordinate_system_id: int
    image_width: int = Field(ge=2, le=20000)
    image_height: int = Field(ge=2, le=20000)
    plane_z: Finite = 0.0
    use_intrinsics: bool = True
    ransac_threshold_px: Finite = Field(default=3.0, gt=0, le=200)
    holdout_reference_point_ids: list[int] = []
    notes: str | None = None
    activate: bool = False


class PoseSolveIn(BaseModel):
    coordinate_system_id: int
    image_width: int = Field(ge=2, le=20000)
    image_height: int = Field(ge=2, le=20000)
    use_ransac: bool = True
    reprojection_threshold_px: Finite = Field(default=4.0, gt=0, le=200)
    holdout_reference_point_ids: list[int] = []
    notes: str | None = None
    activate: bool = False


class PoseAdjustIn(BaseModel):
    """A hand correction on top of a solved pose. Saved as a new, unvalidated revision."""
    x: Finite
    y: Finite
    z: Finite
    roll_deg: Finite
    pitch_deg: Finite
    yaw_deg: Finite
    reason: str = Field(min_length=1, max_length=500)


class RevisionOut(ORMModel):
    id: int
    camera_id: int
    coordinate_system_id: int
    coordinate_system_revision: int
    revision_number: int
    parent_revision_id: int | None
    method: CalibrationMethod
    status: CalibrationStatus
    position: dict[str, float] | None = None
    rpy_deg: dict[str, float] | None = None
    quaternion_wxyz: list[float] | None = None
    T_world_camera: list[list[float]] | None = None
    map_heading_deg: float | None = None
    has_homography: bool = False
    plane_z: float
    pixel_convention: PixelConvention
    distortion_corrected: bool
    source_image_width: int | None
    source_image_height: int | None
    intrinsics_id: int | None
    approx_hfov_deg: float | None
    approx_vfov_deg: float | None
    approx_range_m: float | None
    solver: str | None
    metrics: dict[str, Any] | None = None
    warnings: list[str] = []
    caveats: list[str] = []
    notes: str | None
    is_active: bool
    activated_at: datetime | None
    created_by: str | None
    created_at: datetime
    latest_validation: dict[str, Any] | None = None


class ValidationIn(BaseModel):
    reviewer: str | None = Field(default=None, max_length=120)
    notes: str | None = None


class ActivateIn(BaseModel):
    confirm: bool = True
    notes: str | None = None


# -- projection testing --------------------------------------------------

class ImageToWorldIn(BaseModel):
    pixel_u: Finite
    pixel_v: Finite
    plane_z: Finite | None = None


class WorldToImageIn(BaseModel):
    x: Finite
    y: Finite
    z: Finite = 0.0


class GroundMarkIn(BaseModel):
    camera_id: int
    pixel_u: Finite
    pixel_v: Finite


class MultiCameraCheckIn(BaseModel):
    coordinate_system_id: int
    label: str = Field(default="Ground point", max_length=160)
    plane_z: Finite = 0.0
    marks: list[GroundMarkIn] = Field(min_length=2, max_length=20)


# -- map -----------------------------------------------------------------

class MapCameraOut(BaseModel):
    camera_id: int
    name: str
    calibration_status: CalibrationStatus
    method: CalibrationMethod | None = None
    connection_status: str
    position: dict[str, float] | None = None
    rpy_deg: dict[str, float] | None = None
    map_heading_deg: float | None = None
    approx_hfov_deg: float | None = None
    approx_range_m: float | None = None
    horizontal_fov_deg: float | None = None
    vertical_fov_deg: float | None = None
    floor_polygon: list[list[float]] | None = None
    floor_polygon_clipped: bool = False
    # True when the polygon is the floor area a homography covers rather than a
    # projected view frustum. Such a camera has no known physical position.
    area_is_mapped_coverage: bool = False
    is_approximate: bool = True
    revision_id: int | None = None
    area: str | None = None


class FactoryMapOut(BaseModel):
    coordinate_system: CoordinateSystemOut
    cameras: list[MapCameraOut]
    reference_points: list[ReferencePointOut]
    floor_plan: dict[str, Any] | None = None
    conventions: dict[str, str]


class FloorPlanAlignmentIn(BaseModel):
    coordinate_system_id: int
    metres_per_pixel: Finite = Field(gt=1e-6, le=10)
    origin_x: Finite
    origin_y: Finite
    rotation_deg: Finite = Field(default=0.0, ge=-360, le=360)
