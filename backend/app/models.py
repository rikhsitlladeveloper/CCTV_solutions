"""Persistent records for cameras, locations, floor plans and placements.

Two independent notions of "verified" are tracked and never conflated:

* ``Camera.last_test_status``  - connection verified: the backend actually
  reached the device.
* ``CameraPlacement.review_status`` - location verified: a human confirmed the
  marker position against the current floor-plan image.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ConnectionType(str, enum.Enum):
    onvif = "onvif"
    rtsp = "rtsp"


class TestStatus(str, enum.Enum):
    untested = "untested"       # never probed
    online = "online"           # device answered and a stream/snapshot was confirmed
    partial = "partial"         # logged in, but the video stream did not verify
    auth_failed = "auth_failed"
    unreachable = "unreachable"
    timeout = "timeout"
    error = "error"


class ReviewStatus(str, enum.Enum):
    confirmed = "confirmed"
    needs_review = "needs_review"


class Site(Base):
    __tablename__ = "sites"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    buildings: Mapped[list["Building"]] = relationship(
        back_populates="site", cascade="all, delete-orphan", order_by="Building.name"
    )


class Building(Base):
    __tablename__ = "buildings"
    __table_args__ = (UniqueConstraint("site_id", "name", name="uq_building_per_site"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    site: Mapped[Site] = relationship(back_populates="buildings")
    floors: Mapped[list["Floor"]] = relationship(
        back_populates="building", cascade="all, delete-orphan", order_by="Floor.name"
    )


class Floor(Base):
    __tablename__ = "floors"
    __table_args__ = (UniqueConstraint("building_id", "name", name="uq_floor_per_building"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    building_id: Mapped[int] = mapped_column(ForeignKey("buildings.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    building: Mapped[Building] = relationship(back_populates="floors")
    areas: Mapped[list["Area"]] = relationship(
        back_populates="floor", cascade="all, delete-orphan", order_by="Area.name"
    )
    floor_plan: Mapped["FloorPlan | None"] = relationship(
        back_populates="floor", cascade="all, delete-orphan", uselist=False
    )


class Area(Base):
    __tablename__ = "areas"
    __table_args__ = (UniqueConstraint("floor_id", "name", name="uq_area_per_floor"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    floor_id: Mapped[int] = mapped_column(ForeignKey("floors.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    floor: Mapped[Floor] = relationship(back_populates="areas")
    cameras: Mapped[list["Camera"]] = relationship(back_populates="area")


class FloorPlan(Base):
    __tablename__ = "floor_plans"
    id: Mapped[int] = mapped_column(primary_key=True)
    floor_id: Mapped[int] = mapped_column(
        ForeignKey("floors.id", ondelete="CASCADE"), unique=True, index=True
    )
    image_path: Mapped[str] = mapped_column(String(255))     # filename under data/floorplans
    original_filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(64))
    width_px: Mapped[int] = mapped_column(Integer)
    height_px: Mapped[int] = mapped_column(Integer)
    # Optional scale: two points in normalized coords plus their real distance.
    scale_px_per_metre: Mapped[float | None] = mapped_column(Float, nullable=True)
    scale_point_a_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    scale_point_a_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    scale_point_b_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    scale_point_b_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    scale_distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)

    # Optional placement of this image inside a factory world frame, so it can be
    # drawn as a backdrop behind metric camera positions. World coordinates are
    # never derived from the image: this only positions the picture.
    world_coordinate_system_id: Mapped[int | None] = mapped_column(
        ForeignKey("coordinate_systems.id", ondelete="SET NULL"), nullable=True
    )
    world_metres_per_pixel: Mapped[float | None] = mapped_column(Float, nullable=True)
    world_origin_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    world_origin_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    world_rotation_deg: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    floor: Mapped[Floor] = relationship(back_populates="floor_plan")
    placements: Mapped[list["CameraPlacement"]] = relationship(
        back_populates="floor_plan", cascade="all, delete-orphan"
    )


class Camera(Base):
    __tablename__ = "cameras"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    host: Mapped[str] = mapped_column(String(255))            # IP address or hostname
    connection_type: Mapped[ConnectionType] = mapped_column(
        Enum(ConnectionType, native_enum=False), default=ConnectionType.onvif
    )
    onvif_port: Mapped[int | None] = mapped_column(Integer, nullable=True, default=80)
    onvif_path: Mapped[str | None] = mapped_column(String(255), nullable=True, default="/onvif/device_service")
    rtsp_port: Mapped[int | None] = mapped_column(Integer, nullable=True, default=554)
    stream_path: Mapped[str | None] = mapped_column(String(255), nullable=True)

    username: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Fernet ciphertext. Never serialised to any API response.
    password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    selected_profile_token: Mapped[str | None] = mapped_column(String(255), nullable=True)
    selected_profile_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    profile_resolution: Mapped[str | None] = mapped_column(String(40), nullable=True)
    profile_encoding: Mapped[str | None] = mapped_column(String(40), nullable=True)
    snapshot_supported: Mapped[bool] = mapped_column(Boolean, default=False)

    manufacturer: Mapped[str | None] = mapped_column(String(120), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    area_id: Mapped[int | None] = mapped_column(
        ForeignKey("areas.id", ondelete="SET NULL"), nullable=True, index=True
    )
    installation_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    mounting_height_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    installation_photo_path: Mapped[str | None] = mapped_column(String(255), nullable=True)

    last_test_status: Mapped[TestStatus] = mapped_column(
        Enum(TestStatus, native_enum=False), default=TestStatus.untested
    )
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_error: Mapped[str | None] = mapped_column(Text, nullable=True)   # sanitized, no creds
    last_test_detail: Mapped[str | None] = mapped_column(Text, nullable=True)  # sanitized summary

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    # Which factory frame this camera is positioned in. Changing it invalidates
    # any calibration recorded against the previous frame.
    coordinate_system_id: Mapped[int | None] = mapped_column(
        ForeignKey("coordinate_systems.id", ondelete="SET NULL"), nullable=True, index=True
    )

    area: Mapped[Area | None] = relationship(back_populates="cameras")
    placement: Mapped["CameraPlacement | None"] = relationship(
        back_populates="camera", cascade="all, delete-orphan", uselist=False
    )
    coordinate_system: Mapped["CoordinateSystem | None"] = relationship()
    intrinsics_sets: Mapped[list["CameraIntrinsics"]] = relationship(
        back_populates="camera", cascade="all, delete-orphan", order_by="CameraIntrinsics.created_at"
    )
    observations: Mapped[list["PointObservation"]] = relationship(
        back_populates="camera", cascade="all, delete-orphan"
    )
    calibration_revisions: Mapped[list["CalibrationRevision"]] = relationship(
        back_populates="camera", cascade="all, delete-orphan",
        order_by="CalibrationRevision.revision_number",
    )
    zones: Mapped[list["MonitoredZone"]] = relationship(
        back_populates="camera", cascade="all, delete-orphan", order_by="MonitoredZone.name"
    )

    @property
    def active_intrinsics(self) -> "CameraIntrinsics | None":
        return next((i for i in self.intrinsics_sets if i.is_active), None)

    @property
    def active_calibration(self) -> "CalibrationRevision | None":
        return next((r for r in self.calibration_revisions if r.is_active), None)


class CameraPlacement(Base):
    __tablename__ = "camera_placements"
    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[int] = mapped_column(
        ForeignKey("cameras.id", ondelete="CASCADE"), unique=True, index=True
    )
    floor_plan_id: Mapped[int] = mapped_column(
        ForeignKey("floor_plans.id", ondelete="CASCADE"), index=True
    )
    # Normalized image coordinates in [0,1]; resizing the viewer cannot move a marker.
    norm_x: Mapped[float] = mapped_column(Float)
    norm_y: Mapped[float] = mapped_column(Float)
    # 0 deg points to the top of the plan, increasing clockwise.
    heading_deg: Mapped[float] = mapped_column(Float, default=0.0)
    mounting_height_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    fov_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    view_distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_status: Mapped[ReviewStatus] = mapped_column(
        Enum(ReviewStatus, native_enum=False), default=ReviewStatus.confirmed
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    camera: Mapped[Camera] = relationship(back_populates="placement")
    floor_plan: Mapped[FloorPlan] = relationship(back_populates="placements")


# =====================================================================
# Positioning and calibration
# =====================================================================
#
# Three notions of "verified" are kept apart and never conflated:
#
#   Camera.last_test_status            - the backend reached the device.
#   CameraPlacement.review_status      - a marker was confirmed on a floor plan.
#   CalibrationRevision.status         - the geometry was solved and validated.
#
# A camera can be online and uncalibrated, or calibrated and unreachable.


class CalibrationMethod(str, enum.Enum):
    manual = "manual"                 # typed or dragged; approximate by definition
    homography = "homography"         # image -> floor plane only, no camera pose
    pnp = "pnp"                       # full 6-DoF pose from solvePnP


class CalibrationStatus(str, enum.Enum):
    unconfigured = "unconfigured"
    approximate = "approximate"
    calibrated_unvalidated = "calibrated_unvalidated"
    validated = "validated"
    needs_recalibration = "needs_recalibration"


class PixelConvention(str, enum.Enum):
    raw = "raw"                       # distortion present in the pixels
    undistorted = "undistorted"       # pixels already undistorted with the stored K


class ObservationRole(str, enum.Enum):
    fit = "fit"                       # used to solve
    holdout = "holdout"               # reserved for independent validation


class PointRole(str, enum.Enum):
    """Default role of a surveyed point across every camera that sees it.

    A point reserved for validation is never fitted against, which is what makes
    it an independent check rather than a restatement of the fit.
    """
    calibration = "calibration"
    validation = "validation"


class ZoneKind(str, enum.Enum):
    monitored = "monitored"           # an area of interest
    entrance = "entrance"             # where people arrive in this view
    exit = "exit"                     # where people leave this view


class RelationshipKind(str, enum.Enum):
    overlap = "overlap"               # the two views cover common ground
    transition = "transition"         # directed: leaving A can lead to B
    excluded = "excluded"             # no *direct* association between the pair


class VerificationStatus(str, enum.Enum):
    unverified = "unverified"         # suggested or entered, nobody confirmed it
    verified = "verified"             # a human checked it on site
    needs_review = "needs_review"     # a zone or calibration it depends on changed


class CoordinateSystem(Base):
    """A factory world frame. Right-handed, X/Y on the floor, Z up, metres."""

    __tablename__ = "coordinate_systems"
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int | None] = mapped_column(
        ForeignKey("sites.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(120), unique=True)
    origin_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    origin_photo_path: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # One shared frame per floor. Areas on the same floor reference this rather
    # than quietly creating a second origin nobody can reconcile later.
    floor_id: Mapped[int | None] = mapped_column(
        ForeignKey("floors.id", ondelete="SET NULL"), nullable=True, index=True
    )
    x_axis_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    workspace_width_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    workspace_length_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Flat-plane mapping only holds on one continuous surface. A mezzanine or a
    # ramp needs its own frame, and saying so is better than a silent error.
    surface_description: Mapped[str | None] = mapped_column(Text, nullable=True)

    floor_plane_z: Mapped[float] = mapped_column(Float, default=0.0)
    grid_min_x: Mapped[float] = mapped_column(Float, default=-10.0)
    grid_max_x: Mapped[float] = mapped_column(Float, default=60.0)
    grid_min_y: Mapped[float] = mapped_column(Float, default=-10.0)
    grid_max_y: Mapped[float] = mapped_column(Float, default=60.0)
    grid_spacing_m: Mapped[float] = mapped_column(Float, default=1.0)

    # Installer-configurable acceptance thresholds.
    max_reprojection_error_px: Mapped[float] = mapped_column(Float, default=3.0)
    max_ground_error_m: Mapped[float] = mapped_column(Float, default=0.25)
    min_reference_points: Mapped[int] = mapped_column(Integer, default=6)

    # Bumped whenever the frame's physical meaning is redefined. Calibrations
    # recorded against an older definition are flagged rather than reinterpreted.
    definition_revision: Mapped[int] = mapped_column(Integer, default=1)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    site: Mapped["Site | None"] = relationship()
    floor: Mapped["Floor | None"] = relationship()
    reference_points: Mapped[list["WorldReferencePoint"]] = relationship(
        back_populates="coordinate_system", cascade="all, delete-orphan", order_by="WorldReferencePoint.name"
    )


class WorldReferencePoint(Base):
    """A surveyed point in a factory frame, reusable across cameras."""

    __tablename__ = "world_reference_points"
    __table_args__ = (UniqueConstraint("coordinate_system_id", "code", name="uq_refpoint_code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    coordinate_system_id: Mapped[int] = mapped_column(
        ForeignKey("coordinate_systems.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(160))
    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float] = mapped_column(Float)
    z: Mapped[float] = mapped_column(Float, default=0.0)
    role: Mapped[PointRole] = mapped_column(
        Enum(PointRole, native_enum=False), default=PointRole.calibration
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    measurement_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    uncertainty_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    photo_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Optional ArUco assistance. A marker ID alone fixes nothing in the world;
    # the surveyed XYZ above remains the authority.
    aruco_dictionary: Mapped[str | None] = mapped_column(String(40), nullable=True)
    aruco_marker_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    aruco_marker_size_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    aruco_corner_index: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    coordinate_system: Mapped[CoordinateSystem] = relationship(back_populates="reference_points")
    observations: Mapped[list["PointObservation"]] = relationship(
        back_populates="reference_point", cascade="all, delete-orphan"
    )


class CameraIntrinsics(Base):
    """A pinhole calibration bound to one exact image geometry."""

    __tablename__ = "camera_intrinsics"
    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(160), default="Imported calibration")
    model: Mapped[str] = mapped_column(String(40), default="pinhole")
    camera_matrix_json: Mapped[str] = mapped_column(Text)       # 3x3 row-major
    distortion_json: Mapped[str] = mapped_column(Text)          # list of coefficients

    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    image_rotation_deg: Mapped[int] = mapped_column(Integer, default=0)
    crop_json: Mapped[str | None] = mapped_column(Text, nullable=True)   # [x, y, w, h]

    lens_description: Mapped[str | None] = mapped_column(String(160), nullable=True)
    zoom_state: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source: Mapped[str] = mapped_column(String(40), default="import")    # import | checkerboard | approximate_fov
    rms_reprojection_error_px: Mapped[float | None] = mapped_column(Float, nullable=True)
    view_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quality_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    calibrated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    camera: Mapped["Camera"] = relationship(back_populates="intrinsics_sets")


class PointObservation(Base):
    """A reference point seen at a pixel location in one camera's image."""

    __tablename__ = "point_observations"
    __table_args__ = (
        UniqueConstraint("camera_id", "reference_point_id", name="uq_observation_per_camera_point"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), index=True)
    reference_point_id: Mapped[int] = mapped_column(
        ForeignKey("world_reference_points.id", ondelete="CASCADE"), index=True
    )
    pixel_u: Mapped[float] = mapped_column(Float)
    pixel_v: Mapped[float] = mapped_column(Float)
    # The image geometry the click was made on; a later stream change invalidates it.
    image_width: Mapped[int] = mapped_column(Integer)
    image_height: Mapped[int] = mapped_column(Integer)
    role: Mapped[ObservationRole] = mapped_column(
        Enum(ObservationRole, native_enum=False), default=ObservationRole.fit
    )
    source: Mapped[str] = mapped_column(String(30), default="manual")   # manual | aruco
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    camera: Mapped["Camera"] = relationship(back_populates="observations")
    reference_point: Mapped[WorldReferencePoint] = relationship(back_populates="observations")


class CalibrationRevision(Base):
    """One attempt at positioning a camera. Revisions are never overwritten.

    Only one revision per camera is active at a time, and activation is always an
    explicit step so a fresh solve cannot silently replace a working calibration.
    """

    __tablename__ = "calibration_revisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), index=True)
    coordinate_system_id: Mapped[int] = mapped_column(
        ForeignKey("coordinate_systems.id", ondelete="CASCADE"), index=True
    )
    coordinate_system_revision: Mapped[int] = mapped_column(Integer, default=1)
    revision_number: Mapped[int] = mapped_column(Integer, default=1)
    parent_revision_id: Mapped[int | None] = mapped_column(
        ForeignKey("calibration_revisions.id", ondelete="SET NULL"), nullable=True
    )

    method: Mapped[CalibrationMethod] = mapped_column(Enum(CalibrationMethod, native_enum=False))
    status: Mapped[CalibrationStatus] = mapped_column(
        Enum(CalibrationStatus, native_enum=False), default=CalibrationStatus.approximate
    )

    # Pose (manual or PnP). Quaternion is authoritative; RPY is a view of it.
    position_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    position_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    position_z: Mapped[float | None] = mapped_column(Float, nullable=True)
    quat_w: Mapped[float | None] = mapped_column(Float, nullable=True)
    quat_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    quat_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    quat_z: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Floor homography (homography method only).
    homography_json: Mapped[str | None] = mapped_column(Text, nullable=True)       # image -> floor
    homography_inverse_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    plane_z: Mapped[float] = mapped_column(Float, default=0.0)

    # Conventions and provenance, stored with every result.
    pixel_convention: Mapped[PixelConvention] = mapped_column(
        Enum(PixelConvention, native_enum=False), default=PixelConvention.raw
    )
    distortion_corrected: Mapped[bool] = mapped_column(Boolean, default=False)
    source_image_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_image_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    intrinsics_id: Mapped[int | None] = mapped_column(
        ForeignKey("camera_intrinsics.id", ondelete="SET NULL"), nullable=True
    )

    # Approximate-only hints, never presented as measured intrinsics.
    approx_hfov_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    approx_vfov_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    approx_range_m: Mapped[float | None] = mapped_column(Float, nullable=True)

    # The floor polygon the reference points actually cover. Projections outside
    # it are extrapolation and are flagged as such.
    coverage_polygon_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    stale_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    solver: Mapped[str | None] = mapped_column(String(80), nullable=True)
    metrics_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    warnings_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    camera: Mapped["Camera"] = relationship(back_populates="calibration_revisions")
    coordinate_system: Mapped[CoordinateSystem] = relationship()
    intrinsics: Mapped[CameraIntrinsics | None] = relationship()
    validations: Mapped[list["ValidationResult"]] = relationship(
        back_populates="revision", cascade="all, delete-orphan", order_by="ValidationResult.created_at"
    )


class ValidationResult(Base):
    """An independent check of a revision against held-out reference points."""

    __tablename__ = "validation_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    revision_id: Mapped[int] = mapped_column(
        ForeignKey("calibration_revisions.id", ondelete="CASCADE"), index=True
    )
    # Fitting error: how well the solve reproduced its own input. Not evidence
    # of real-world accuracy on its own.
    fit_reprojection_error_px: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Held-out error: measured against points the solver never saw.
    holdout_ground_error_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    holdout_max_error_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    holdout_point_count: Mapped[int] = mapped_column(Integer, default=0)
    fit_point_count: Mapped[int] = mapped_column(Integer, default=0)
    inlier_count: Mapped[int] = mapped_column(Integer, default=0)
    outlier_count: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    thresholds_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    details_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    revision: Mapped[CalibrationRevision] = relationship(back_populates="validations")


# =====================================================================
# Zones and the camera relationship graph
# =====================================================================
#
# This graph configures a future tracking service. It describes geometry and
# topology only: which views share ground, and which exits plausibly lead where.
# It identifies nobody.


class MonitoredZone(Base):
    """A polygon drawn in one camera's image, optionally mapped to the floor.

    Zones work without any metric calibration — an installer can mark "the door"
    in the picture before the camera is calibrated. The world polygon is filled
    in only when a mapping exists to produce it.
    """

    __tablename__ = "monitored_zones"
    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[ZoneKind] = mapped_column(Enum(ZoneKind, native_enum=False), default=ZoneKind.monitored)

    # Polygon in the source image's own pixels: [[u, v], ...]
    image_polygon_json: Mapped[str] = mapped_column(Text)
    # The image geometry those pixels belong to. A stream change invalidates them.
    image_width: Mapped[int] = mapped_column(Integer)
    image_height: Mapped[int] = mapped_column(Integer)

    # Floor polygon in world metres, derived from a calibration when one exists.
    world_polygon_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    world_from_revision_id: Mapped[int | None] = mapped_column(
        ForeignKey("calibration_revisions.id", ondelete="SET NULL"), nullable=True
    )
    coordinate_system_id: Mapped[int | None] = mapped_column(
        ForeignKey("coordinate_systems.id", ondelete="SET NULL"), nullable=True, index=True
    )

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    camera: Mapped["Camera"] = relationship(back_populates="zones")


class CameraRelationship(Base):
    """How two camera views relate on the ground.

    Absence of a record means *unknown*, not impossible. An explicit ``excluded``
    record is the only way to state that two views have no direct association,
    and even that only rules out a direct hop — travel via other cameras remains
    possible.
    """

    __tablename__ = "camera_relationships"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[RelationshipKind] = mapped_column(Enum(RelationshipKind, native_enum=False))

    camera_a_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), index=True)
    camera_b_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), index=True)
    coordinate_system_id: Mapped[int | None] = mapped_column(
        ForeignKey("coordinate_systems.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Overlap: the shared ground, in world metres when both are calibrated.
    overlap_polygon_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Or paired image polygons when world calibration is not available.
    zone_a_id: Mapped[int | None] = mapped_column(
        ForeignKey("monitored_zones.id", ondelete="SET NULL"), nullable=True
    )
    zone_b_id: Mapped[int | None] = mapped_column(
        ForeignKey("monitored_zones.id", ondelete="SET NULL"), nullable=True
    )

    # Transition: how long the walk plausibly takes, in seconds.
    min_travel_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_travel_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Suggested by geometry, or asserted by a person? Geometry cannot see walls.
    suggested_by_geometry: Mapped[bool] = mapped_column(Boolean, default=False)
    verification: Mapped[VerificationStatus] = mapped_column(
        Enum(VerificationStatus, native_enum=False), default=VerificationStatus.unverified
    )
    verified_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    camera_a: Mapped["Camera"] = relationship(foreign_keys=[camera_a_id])
    camera_b: Mapped["Camera"] = relationship(foreign_keys=[camera_b_id])
    zone_a: Mapped["MonitoredZone | None"] = relationship(foreign_keys=[zone_a_id])
    zone_b: Mapped["MonitoredZone | None"] = relationship(foreign_keys=[zone_b_id])
