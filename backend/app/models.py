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

    area: Mapped[Area | None] = relationship(back_populates="cameras")
    placement: Mapped["CameraPlacement | None"] = relationship(
        back_populates="camera", cascade="all, delete-orphan", uselist=False
    )


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
