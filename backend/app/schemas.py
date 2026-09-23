"""API contracts.

No schema in this file has a field that can carry a camera password or a
credential-bearing URL outward.  ``CameraOut`` exposes ``has_password`` only.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import ConnectionType, ReviewStatus, TestStatus


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# -- auth ---------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: int
    username: str


# -- locations ----------------------------------------------------------

class NameIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name cannot be blank.")
        return v


class SiteIn(NameIn):
    pass


class BuildingIn(NameIn):
    site_id: int


class FloorIn(NameIn):
    building_id: int


class AreaIn(NameIn):
    floor_id: int


class AreaOut(ORMModel):
    id: int
    name: str
    floor_id: int
    camera_count: int = 0


class FloorOut(ORMModel):
    id: int
    name: str
    building_id: int
    areas: list[AreaOut] = []
    has_floor_plan: bool = False
    floor_plan_id: int | None = None


class BuildingOut(ORMModel):
    id: int
    name: str
    site_id: int
    floors: list[FloorOut] = []


class SiteOut(ORMModel):
    id: int
    name: str
    buildings: list[BuildingOut] = []


class LocationPathOut(BaseModel):
    site_id: int | None = None
    site: str | None = None
    building_id: int | None = None
    building: str | None = None
    floor_id: int | None = None
    floor: str | None = None
    area_id: int | None = None
    area: str | None = None


class InlineLocationIn(BaseModel):
    """Lets the wizard create missing location names in one call."""
    site: str | None = None
    building: str | None = None
    floor: str | None = None
    area: str | None = None
    site_id: int | None = None
    building_id: int | None = None
    floor_id: int | None = None
    area_id: int | None = None


# -- cameras ------------------------------------------------------------

class CameraBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    host: str = Field(min_length=1, max_length=255)
    connection_type: ConnectionType = ConnectionType.onvif
    onvif_port: int | None = Field(default=80, ge=1, le=65535)
    onvif_path: str | None = "/onvif/device_service"
    rtsp_port: int | None = Field(default=554, ge=1, le=65535)
    stream_path: str | None = None
    username: str | None = Field(default=None, max_length=120)
    manufacturer: str | None = Field(default=None, max_length=120)
    model: str | None = Field(default=None, max_length=120)
    notes: str | None = None
    area_id: int | None = None
    installation_description: str | None = None
    mounting_height_m: float | None = Field(default=None, ge=0, le=100)
    selected_profile_token: str | None = None
    selected_profile_name: str | None = None
    profile_resolution: str | None = None
    profile_encoding: str | None = None

    @field_validator("name", "host")
    @classmethod
    def _strip_required(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("This field is required.")
        return v

    @field_validator("host")
    @classmethod
    def _bare_host(cls, v: str) -> str:
        if "://" in v:
            raise ValueError("Enter a bare IP address or hostname, without a URL scheme.")
        if "/" in v or "@" in v or " " in v:
            raise ValueError("Enter a bare IP address or hostname.")
        return v


class CameraCreate(CameraBase):
    password: str | None = None


class CameraUpdate(BaseModel):
    """All fields optional. An omitted/empty password keeps the stored one."""
    name: str | None = None
    host: str | None = None
    connection_type: ConnectionType | None = None
    onvif_port: int | None = Field(default=None, ge=1, le=65535)
    onvif_path: str | None = None
    rtsp_port: int | None = Field(default=None, ge=1, le=65535)
    stream_path: str | None = None
    username: str | None = None
    password: str | None = None          # empty/omitted => keep existing
    clear_password: bool = False         # explicit removal
    manufacturer: str | None = None
    model: str | None = None
    notes: str | None = None
    area_id: int | None = None
    installation_description: str | None = None
    mounting_height_m: float | None = Field(default=None, ge=0, le=100)
    selected_profile_token: str | None = None
    selected_profile_name: str | None = None
    profile_resolution: str | None = None
    profile_encoding: str | None = None

    @field_validator("host")
    @classmethod
    def _bare_host(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if "://" in v or "/" in v or "@" in v or " " in v:
            raise ValueError("Enter a bare IP address or hostname.")
        return v


class PlacementOut(ORMModel):
    id: int
    camera_id: int
    floor_plan_id: int
    norm_x: float
    norm_y: float
    heading_deg: float
    mounting_height_m: float | None = None
    fov_deg: float | None = None
    view_distance_m: float | None = None
    review_status: ReviewStatus
    updated_at: datetime


class CameraOut(ORMModel):
    id: int
    name: str
    host: str
    connection_type: ConnectionType
    onvif_port: int | None
    onvif_path: str | None
    rtsp_port: int | None
    stream_path: str | None
    username: str | None
    has_password: bool = False
    manufacturer: str | None
    model: str | None
    notes: str | None
    installation_description: str | None
    mounting_height_m: float | None
    installation_photo_path: str | None
    selected_profile_token: str | None
    selected_profile_name: str | None
    profile_resolution: str | None
    profile_encoding: str | None
    snapshot_supported: bool
    last_test_status: TestStatus
    last_test_at: datetime | None
    last_test_error: str | None
    last_test_detail: str | None
    created_at: datetime
    updated_at: datetime
    # Which factory/workspace frame this camera is positioned in, if any.
    coordinate_system_id: int | None = None
    location: LocationPathOut = LocationPathOut()
    placement: PlacementOut | None = None


class CameraSummary(BaseModel):
    total: int
    reachable: int
    failed: int
    untested: int
    awaiting_placement: int
    placements_need_review: int


# -- connection tests ---------------------------------------------------

class ProfileOut(BaseModel):
    token: str
    name: str
    encoding: str | None = None
    resolution: str | None = None
    fps: int | None = None


class TestConnectionRequest(BaseModel):
    """Test an unsaved camera from the wizard, or override a saved camera's fields."""
    host: str | None = None
    connection_type: ConnectionType | None = None
    onvif_port: int | None = Field(default=None, ge=1, le=65535)
    onvif_path: str | None = None
    rtsp_port: int | None = Field(default=None, ge=1, le=65535)
    stream_path: str | None = None
    username: str | None = None
    password: str | None = None
    profile_token: str | None = None
    use_stored_password: bool = False

    @field_validator("host")
    @classmethod
    def _bare_host(cls, v: str | None) -> str | None:
        if v and ("://" in v or "/" in v or "@" in v or " " in v):
            raise ValueError("Enter a bare IP address or hostname.")
        return v.strip() if v else v


class TestConnectionResult(BaseModel):
    status: TestStatus
    ok: bool
    login_ok: bool
    stream_ok: bool
    snapshot_supported: bool
    summary: str
    error: str | None = None
    tested_at: datetime
    profiles: list[ProfileOut] = []
    selected_profile_token: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    firmware: str | None = None


# -- floor plans --------------------------------------------------------

class FloorPlanOut(ORMModel):
    id: int
    floor_id: int
    original_filename: str
    content_type: str
    width_px: int
    height_px: int
    scale_px_per_metre: float | None
    scale_point_a_x: float | None
    scale_point_a_y: float | None
    scale_point_b_x: float | None
    scale_point_b_y: float | None
    scale_distance_m: float | None
    version: int
    updated_at: datetime
    image_url: str = ""
    location: LocationPathOut = LocationPathOut()
    placement_count: int = 0
    needs_review_count: int = 0


class ScaleIn(BaseModel):
    point_a_x: float = Field(ge=0, le=1)
    point_a_y: float = Field(ge=0, le=1)
    point_b_x: float = Field(ge=0, le=1)
    point_b_y: float = Field(ge=0, le=1)
    distance_m: float = Field(gt=0, le=10000)


class PlacementIn(BaseModel):
    floor_plan_id: int
    norm_x: float = Field(ge=0, le=1)
    norm_y: float = Field(ge=0, le=1)
    heading_deg: float = Field(ge=0, lt=360)
    mounting_height_m: float | None = Field(default=None, ge=0, le=100)
    fov_deg: float | None = Field(default=None, gt=0, le=360)
    view_distance_m: float | None = Field(default=None, gt=0, le=500)
    review_status: ReviewStatus = ReviewStatus.confirmed


# -- preview ------------------------------------------------------------

class PreviewSessionOut(BaseModel):
    session_id: str
    camera_id: int
    stream_url: str
    expires_in_s: float
    note: str
