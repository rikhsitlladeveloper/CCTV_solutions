"""Contracts for the scene editor, visual placement, functions and sessions."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import (
    ConfigSpace, FunctionKind, GeometryProvenance, MountType, SceneAssetKind, SceneObjectKind,
)

Finite = Annotated[float, Field(allow_inf_nan=False)]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# -- scene ---------------------------------------------------------------

class SceneIn(BaseModel):
    workspace_id: int
    name: str = Field(min_length=1, max_length=160)
    building_width_m: Finite | None = Field(default=None, gt=0.5, le=5000)
    building_length_m: Finite | None = Field(default=None, gt=0.5, le=5000)
    notes: str | None = None


class SceneUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    building_width_m: Finite | None = Field(default=None, gt=0.5, le=5000)
    building_length_m: Finite | None = Field(default=None, gt=0.5, le=5000)
    notes: str | None = None


class SceneObjectIn(BaseModel):
    kind: SceneObjectKind
    name: str = Field(min_length=1, max_length=160)
    x: Finite = 0.0
    y: Finite = 0.0
    z: Finite = 0.0
    rotation_deg: Finite = Field(default=0.0, ge=-360, le=360)
    width_m: Finite = Field(default=1.0, gt=0.01, le=1000)
    depth_m: Finite = Field(default=1.0, gt=0.01, le=1000)
    height_m: Finite = Field(default=1.0, gt=0.0, le=200)
    points: list[list[Finite]] | None = None
    provenance: GeometryProvenance = GeometryProvenance.estimated
    colour: str | None = Field(default=None, max_length=20)
    notes: str | None = None

    @model_validator(mode="after")
    def _points_shape(self):
        if self.points is not None:
            if len(self.points) < 2:
                raise ValueError("An outline needs at least two points.")
            for p in self.points:
                if len(p) != 2:
                    raise ValueError("Each outline vertex must be an [x, y] pair.")
        return self


class SceneObjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    x: Finite | None = None
    y: Finite | None = None
    z: Finite | None = None
    rotation_deg: Finite | None = Field(default=None, ge=-360, le=360)
    width_m: Finite | None = Field(default=None, gt=0.01, le=1000)
    depth_m: Finite | None = Field(default=None, gt=0.01, le=1000)
    height_m: Finite | None = Field(default=None, gt=0.0, le=200)
    points: list[list[Finite]] | None = None
    provenance: GeometryProvenance | None = None
    colour: str | None = Field(default=None, max_length=20)
    notes: str | None = None


class SceneObjectBulkIn(BaseModel):
    """The editor saves the whole working set, which makes undo/redo trivial."""
    objects: list[SceneObjectIn] = Field(default_factory=list, max_length=2000)


class SceneObjectOut(BaseModel):
    id: int
    kind: SceneObjectKind
    name: str
    x: float
    y: float
    z: float
    rotation_deg: float
    width_m: float
    depth_m: float
    height_m: float
    points: list[list[float]] | None = None
    footprint: list[list[float]] = []
    provenance: GeometryProvenance
    colour: str
    notes: str | None = None


class SceneAssetOut(ORMModel):
    id: int
    kind: SceneAssetKind
    original_filename: str
    content_type: str
    size_bytes: int
    width_px: int | None
    height_px: int | None
    metres_per_pixel: float | None
    origin_x: float | None
    origin_y: float | None
    rotation_deg: float | None
    model_scale: float | None
    model_up_axis: str | None
    model_floor_offset_m: float | None
    model_rotation_deg: float | None
    is_reference_only: bool
    notes: str | None
    url: str = ""


class SceneOut(BaseModel):
    id: int
    workspace_id: int
    workspace_name: str
    name: str
    building_width_m: float | None
    building_length_m: float | None
    geometry_provenance: GeometryProvenance
    notes: str | None
    draft_revision: int
    published_revision: int | None
    published_at: datetime | None
    published_by: str | None
    has_unpublished_changes: bool
    objects: list[SceneObjectOut] = []
    assets: list[SceneAssetOut] = []
    grid: dict[str, float] = {}
    conventions: dict[str, str] = {}


class PlanScaleIn(BaseModel):
    """Two points on the image plus the real distance between them."""
    point_a: list[Finite] = Field(min_length=2, max_length=2)
    point_b: list[Finite] = Field(min_length=2, max_length=2)
    distance_m: Finite = Field(gt=0.01, le=10000)
    origin_x: Finite = 0.0
    origin_y: Finite = 0.0
    rotation_deg: Finite = Field(default=0.0, ge=-360, le=360)


class ModelAlignIn(BaseModel):
    scale: Finite = Field(default=1.0, gt=1e-6, le=10000)
    up_axis: str = Field(default="Y", pattern="^[YZ]$")
    floor_offset_m: Finite = 0.0
    rotation_deg: Finite = Field(default=0.0, ge=-360, le=360)


class PublishIn(BaseModel):
    confirm: bool = True
    notes: str | None = None


# -- visual placement ----------------------------------------------------

class VisualPlacementIn(BaseModel):
    """What the installer did on screen. No angles are typed in."""
    workspace_id: int
    x: Finite = Field(ge=-100000, le=100000)
    y: Finite = Field(ge=-100000, le=100000)
    height_m: Finite = Field(gt=0.1, le=100, description="Mounting height above the floor")
    target_x: Finite = Field(ge=-100000, le=100000)
    target_y: Finite = Field(ge=-100000, le=100000)
    target_z: Finite = Field(default=0.0, ge=-100, le=100)
    mount_type: MountType = MountType.wall
    roll_deg: Finite = Field(default=0.0, ge=-180, le=180)
    # Illustrative only. Drawing a wider cone must never touch a real camera's zoom.
    illustrative_hfov_deg: Finite | None = Field(default=None, gt=1, lt=179)
    illustrative_range_m: Finite | None = Field(default=None, gt=0.1, le=500)
    notes: str | None = None
    activate: bool = True


class PlacementOut(BaseModel):
    camera_id: int
    revision_id: int
    revision_number: int
    status: str
    position: dict[str, float]
    aim: dict[str, Any]
    frustum_floor_polygon: list[list[float]] | None = None
    frustum_clipped: bool = False
    is_approximate: bool = True
    fov_source: str
    #: Whether this placement is the geometry now in use. False when a solved
    #: calibration was already active and was deliberately left alone.
    activated: bool = True
    warnings: list[str] = []


# -- functions -----------------------------------------------------------

class FunctionIn(BaseModel):
    kind: FunctionKind
    name: str = Field(min_length=1, max_length=160)
    enabled: bool = True
    space: ConfigSpace = ConfigSpace.image
    config: dict[str, Any] = Field(default_factory=dict)
    image_width: int | None = Field(default=None, ge=2, le=20000)
    image_height: int | None = Field(default=None, ge=2, le=20000)
    zone_id: int | None = None
    scene_object_id: int | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _config_shape(self):
        cfg = self.config or {}
        if self.kind in (FunctionKind.people_counting, FunctionKind.product_counting):
            line = cfg.get("line")
            if not line or len(line) != 2 or any(len(p) != 2 for p in line):
                raise ValueError(
                    "Counting needs a line: two points, as \"line\": [[x1, y1], [x2, y2]].")
            if cfg.get("direction") not in ("a_to_b", "b_to_a", "both"):
                raise ValueError('Counting needs "direction": "a_to_b", "b_to_a" or "both".')
        if self.kind in (FunctionKind.restricted_zone, FunctionKind.ppe_monitoring,
                         FunctionKind.workstation_occupancy):
            polygon = cfg.get("polygon")
            if not self.zone_id and (not polygon or len(polygon) < 3):
                raise ValueError(
                    "This function needs an area: either an existing zone, or a "
                    "\"polygon\" of at least three points.")
        if self.kind == FunctionKind.ppe_monitoring and not cfg.get("required_ppe"):
            raise ValueError(
                'PPE monitoring needs "required_ppe", for example ["hi_vis", "helmet"].')
        if self.kind == FunctionKind.workstation_occupancy and not (
                self.scene_object_id or cfg.get("workstation_name")):
            raise ValueError(
                "Workstation occupancy needs a workstation: link a scene object, or give "
                '"workstation_name".')
        if self.space == ConfigSpace.image and (self.image_width is None or self.image_height is None):
            raise ValueError(
                "Picture-space configuration must record the image size its coordinates "
                "belong to.")
        return self


class FunctionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    enabled: bool | None = None
    config: dict[str, Any] | None = None
    zone_id: int | None = None
    scene_object_id: int | None = None
    notes: str | None = None


class FunctionOut(BaseModel):
    id: int
    camera_id: int
    kind: FunctionKind
    label: str
    name: str
    enabled: bool
    space: ConfigSpace
    config: dict[str, Any]
    image_width: int | None
    image_height: int | None
    zone_id: int | None
    scene_object_id: int | None
    notes: str | None
    status: dict[str, Any]
    updated_at: datetime


# -- commissioning sessions ---------------------------------------------

class SessionIn(BaseModel):
    workspace_id: int
    name: str = Field(min_length=1, max_length=160)
    camera_ids: list[int] = Field(default_factory=list, max_length=100)
    notes: str | None = None


class CheckpointIn(BaseModel):
    camera_id: int | None = None
    label: str = Field(min_length=1, max_length=200)
    pixel_u: Finite | None = None
    pixel_v: Finite | None = None
    image_width: int | None = Field(default=None, ge=2, le=20000)
    image_height: int | None = Field(default=None, ge=2, le=20000)
    measured_x: Finite | None = None
    measured_y: Finite | None = None
    observation: str | None = None

    @model_validator(mode="after")
    def _pixel_pairs(self):
        if (self.pixel_u is None) != (self.pixel_v is None):
            raise ValueError("Give both pixel_u and pixel_v, or neither.")
        if self.pixel_u is not None and (self.image_width is None or self.image_height is None):
            raise ValueError("A picture point must record the image size it came from.")
        if (self.measured_x is None) != (self.measured_y is None):
            raise ValueError("Give both measured_x and measured_y, or neither.")
        return self


class CheckpointOut(ORMModel):
    id: int
    session_id: int
    camera_id: int | None
    camera_name: str | None = None
    recorded_at: datetime
    label: str
    pixel_u: float | None
    pixel_v: float | None
    projected_x: float | None
    projected_y: float | None
    measured_x: float | None
    measured_y: float | None
    error_m: float | None
    observation: str | None


class SessionOut(BaseModel):
    id: int
    workspace_id: int
    name: str
    camera_ids: list[int]
    started_at: datetime
    ended_at: datetime | None
    operator: str | None
    notes: str | None
    checkpoints: list[CheckpointOut] = []
    accuracy: dict[str, Any] = {}
