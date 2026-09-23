"""Contracts for the guided setup flow, zones and camera relationships."""
from __future__ import annotations

import math
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import PointRole, RelationshipKind, VerificationStatus, ZoneKind

Finite = Annotated[float, Field(allow_inf_nan=False)]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# -- workspaces ----------------------------------------------------------

class WorkspaceIn(BaseModel):
    """Step B: create the monitored area."""
    name: str = Field(min_length=1, max_length=120)
    floor_id: int
    width_m: Finite = Field(gt=0.5, le=5000, description="Approximate workspace width")
    length_m: Finite = Field(gt=0.5, le=5000, description="Approximate workspace length")
    origin_description: str = Field(min_length=1, max_length=2000)
    x_axis_description: str = Field(min_length=1, max_length=2000)
    surface_description: str | None = None
    grid_spacing_m: Finite = Field(default=1.0, gt=0.01, le=100)
    max_ground_error_m: Finite = Field(default=0.25, gt=0, le=100)
    min_reference_points: int = Field(default=6, ge=4, le=200)


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    # Frames created before floors were recorded can be attached to one.
    floor_id: int | None = None
    width_m: Finite | None = Field(default=None, gt=0.5, le=5000)
    length_m: Finite | None = Field(default=None, gt=0.5, le=5000)
    origin_description: str | None = None
    x_axis_description: str | None = None
    surface_description: str | None = None
    grid_spacing_m: Finite | None = Field(default=None, gt=0.01, le=100)
    max_ground_error_m: Finite | None = Field(default=None, gt=0, le=100)
    min_reference_points: int | None = Field(default=None, ge=4, le=200)
    confirm_redefinition: bool = False
    redefinition_reason: str | None = None


class WorkspaceOut(BaseModel):
    id: int
    name: str
    floor_id: int | None
    floor_label: str | None = None
    origin_description: str | None
    x_axis_description: str | None
    surface_description: str | None
    width_m: float | None
    length_m: float | None
    grid_min_x: float
    grid_max_x: float
    grid_min_y: float
    grid_max_y: float
    grid_spacing_m: float
    floor_plane_z: float
    max_ground_error_m: float
    min_reference_points: int
    definition_revision: int
    camera_count: int = 0
    point_count: int = 0
    calibration_point_count: int = 0
    validation_point_count: int = 0
    created_at: datetime
    updated_at: datetime


# -- floor points --------------------------------------------------------

class FloorPointIn(BaseModel):
    """Step C: a measured floor point."""
    workspace_id: int
    code: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=160)
    x: Finite = Field(ge=-100000, le=100000)
    y: Finite = Field(ge=-100000, le=100000)
    role: PointRole = PointRole.calibration
    description: str | None = None
    measurement_notes: str | None = None
    uncertainty_m: Finite | None = Field(default=None, ge=0, le=100)
    aruco_dictionary: str | None = Field(default=None, max_length=40)
    aruco_marker_id: int | None = Field(default=None, ge=0, le=100000)
    aruco_marker_size_m: Finite | None = Field(default=None, gt=0, le=10)
    aruco_corner_index: int | None = Field(default=None, ge=0, le=3)

    @model_validator(mode="after")
    def _marker_needs_size(self):
        if self.aruco_marker_id is not None and not self.aruco_marker_size_m:
            raise ValueError(
                "A marker ID identifies which marker it is, not where it is. Record the marker's "
                "printed size as well, and keep the measured X/Y above as the authority."
            )
        return self


class FloorPointUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=60)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    x: Finite | None = None
    y: Finite | None = None
    role: PointRole | None = None
    description: str | None = None
    measurement_notes: str | None = None
    uncertainty_m: Finite | None = Field(default=None, ge=0, le=100)


class FloorPointOut(BaseModel):
    id: int
    workspace_id: int
    code: str
    name: str
    x: float
    y: float
    z: float
    role: PointRole
    description: str | None
    measurement_notes: str | None
    uncertainty_m: float | None
    aruco_marker_id: int | None
    aruco_marker_size_m: float | None
    aruco_corner_index: int | None
    observed_by_camera_ids: list[int] = []


# -- matching ------------------------------------------------------------

class MatchIn(BaseModel):
    reference_point_id: int
    pixel_u: Finite
    pixel_v: Finite

    @field_validator("pixel_u", "pixel_v")
    @classmethod
    def _finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("Pixel coordinates must be finite numbers.")
        return v


class MatchDraftIn(BaseModel):
    """Step D: the installer's matches, in source-image pixels."""
    image_width: int = Field(ge=2, le=20000)
    image_height: int = Field(ge=2, le=20000)
    matches: list[MatchIn] = Field(default_factory=list, max_length=500)
    replace_existing: bool = True

    @model_validator(mode="after")
    def _pixels_inside_image(self):
        for m in self.matches:
            if not (0 <= m.pixel_u <= self.image_width - 1):
                raise ValueError(
                    f"A match at u={m.pixel_u:.0f} is outside the {self.image_width}px-wide image.")
            if not (0 <= m.pixel_v <= self.image_height - 1):
                raise ValueError(
                    f"A match at v={m.pixel_v:.0f} is outside the {self.image_height}px-tall image.")
        return self


class MatchOut(BaseModel):
    observation_id: int
    reference_point_id: int
    code: str
    name: str
    role: PointRole
    world_x: float
    world_y: float
    pixel_u: float
    pixel_v: float
    image_width: int
    image_height: int


class MatchStatusOut(BaseModel):
    camera_id: int
    workspace_id: int | None
    image_width: int | None
    image_height: int | None
    matches: list[MatchOut] = []
    unmatched_points: list[FloorPointOut] = []
    calibration_matched: int = 0
    validation_matched: int = 0
    issues: list[dict[str, Any]] = []
    ready_to_calculate: bool = False


# -- mapping -------------------------------------------------------------

class CalculateMappingIn(BaseModel):
    """Step E. No solver choice: the pipeline is fixed and documented."""
    image_width: int = Field(ge=2, le=20000)
    image_height: int = Field(ge=2, le=20000)
    # Units follow the pixel space the pipeline selects, which is reported back.
    tolerance_px: Finite = Field(default=3.0, gt=0.1, le=200)
    notes: str | None = None


class CheckAccuracyIn(BaseModel):
    """Step F."""
    acceptance_threshold_m: Finite = Field(default=0.25, gt=0.001, le=100)
    reviewer: str | None = Field(default=None, max_length=120)
    notes: str | None = None


class ActivateMappingIn(BaseModel):
    confirm: bool = True
    notes: str | None = None


class ProjectPointIn(BaseModel):
    pixel_u: Finite | None = None
    pixel_v: Finite | None = None
    x: Finite | None = None
    y: Finite | None = None

    @model_validator(mode="after")
    def _one_direction(self):
        has_pixel = self.pixel_u is not None and self.pixel_v is not None
        has_world = self.x is not None and self.y is not None
        if has_pixel == has_world:
            raise ValueError("Give either a pixel (pixel_u, pixel_v) or a floor position (x, y).")
        return self


# -- markers -------------------------------------------------------------

class DetectMarkersIn(BaseModel):
    dictionary: str = Field(default="DICT_4X4_50", max_length=40)


class AcceptMarkersIn(BaseModel):
    image_width: int = Field(ge=2, le=20000)
    image_height: int = Field(ge=2, le=20000)
    accept: list[MatchIn] = Field(min_length=1, max_length=200)


# -- zones ---------------------------------------------------------------

class ZoneIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: ZoneKind = ZoneKind.monitored
    image_polygon: list[list[Finite]] = Field(min_length=3, max_length=200)
    image_width: int = Field(ge=2, le=20000)
    image_height: int = Field(ge=2, le=20000)
    notes: str | None = None

    @model_validator(mode="after")
    def _polygon_is_sane(self):
        for point in self.image_polygon:
            if len(point) != 2:
                raise ValueError("Each polygon vertex must be a [u, v] pair.")
            u, v = point
            if not (0 <= u <= self.image_width - 1 and 0 <= v <= self.image_height - 1):
                raise ValueError(
                    f"Vertex ({u:.0f}, {v:.0f}) lies outside the "
                    f"{self.image_width}x{self.image_height} image.")
        return self


class ZoneUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    kind: ZoneKind | None = None
    image_polygon: list[list[Finite]] | None = Field(default=None, min_length=3, max_length=200)
    image_width: int | None = Field(default=None, ge=2, le=20000)
    image_height: int | None = Field(default=None, ge=2, le=20000)
    notes: str | None = None


class ZoneOut(ORMModel):
    id: int
    camera_id: int
    camera_name: str | None = None
    name: str
    kind: ZoneKind
    image_polygon: list[list[float]] = []
    image_width: int
    image_height: int
    world_polygon: list[list[float]] | None = None
    world_is_stale: bool = False
    notes: str | None
    updated_at: datetime


# -- relationships -------------------------------------------------------

class RelationshipIn(BaseModel):
    kind: RelationshipKind
    camera_a_id: int
    camera_b_id: int
    zone_a_id: int | None = None
    zone_b_id: int | None = None
    overlap_polygon: list[list[Finite]] | None = None
    min_travel_seconds: Finite | None = Field(default=None, ge=0, le=86400)
    max_travel_seconds: Finite | None = Field(default=None, ge=0, le=86400)
    notes: str | None = None
    verification: VerificationStatus = VerificationStatus.unverified

    @model_validator(mode="after")
    def _shape(self):
        if self.camera_a_id == self.camera_b_id:
            raise ValueError("A camera cannot be related to itself.")
        if (self.min_travel_seconds is not None and self.max_travel_seconds is not None
                and self.min_travel_seconds > self.max_travel_seconds):
            raise ValueError(
                f"Minimum travel time ({self.min_travel_seconds} s) exceeds the maximum "
                f"({self.max_travel_seconds} s).")
        return self


class RelationshipUpdate(BaseModel):
    zone_a_id: int | None = None
    zone_b_id: int | None = None
    overlap_polygon: list[list[Finite]] | None = None
    min_travel_seconds: Finite | None = Field(default=None, ge=0, le=86400)
    max_travel_seconds: Finite | None = Field(default=None, ge=0, le=86400)
    notes: str | None = None
    verification: VerificationStatus | None = None


class RelationshipOut(ORMModel):
    id: int
    kind: RelationshipKind
    camera_a_id: int
    camera_a_name: str | None = None
    camera_b_id: int
    camera_b_name: str | None = None
    coordinate_system_id: int | None
    zone_a_id: int | None
    zone_a_name: str | None = None
    zone_b_id: int | None
    zone_b_name: str | None = None
    overlap_polygon: list[list[float]] | None = None
    overlap_area_m2: float | None = None
    min_travel_seconds: float | None
    max_travel_seconds: float | None
    suggested_by_geometry: bool
    verification: VerificationStatus
    verified_by: str | None
    verified_at: datetime | None
    review_reason: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
    warnings: list[str] = []


class RelationshipGraphOut(BaseModel):
    cameras: list[dict[str, Any]]
    relationships: list[RelationshipOut]
    summary: dict[str, Any]
    suggestions: list[dict[str, Any]] = []


# -- consistency check ---------------------------------------------------

class ConsistencyMarkIn(BaseModel):
    camera_id: int
    pixel_u: Finite
    pixel_v: Finite


class ConsistencyCheckIn(BaseModel):
    workspace_id: int
    label: str = Field(default="Floor point", max_length=160)
    marks: list[ConsistencyMarkIn] = Field(min_length=2, max_length=20)
    ground_truth_x: Finite | None = None
    ground_truth_y: Finite | None = None
    reference_point_id: int | None = None
