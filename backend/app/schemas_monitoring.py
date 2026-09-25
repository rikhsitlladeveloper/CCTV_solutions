"""Contracts for the monitoring lifecycle, the event log and setup progress."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from .models import EventKind, ReviewDecision


# -- events --------------------------------------------------------------

class EventIn(BaseModel):
    """What an external processing service posts when an analytic fires.

    ``is_sample`` cannot be set here. Sample data is seeded by a separate,
    explicit route, so a real service can never accidentally mark its output as
    a demonstration, nor a demonstration pass itself off as real.
    """

    camera_id: int
    function_id: int | None = None
    kind: EventKind
    started_at: datetime
    duration_s: float | None = Field(default=None, ge=0, le=86400)
    facts: list[str] = Field(default_factory=list, max_length=20)
    overlay: list[dict[str, Any]] | None = None
    image_width: int | None = Field(default=None, ge=2, le=20000)
    image_height: int | None = Field(default=None, ge=2, le=20000)
    snapshot_path: str | None = None
    clip_path: str | None = None

    @model_validator(mode="after")
    def _facts_are_observations(self):
        for fact in self.facts:
            if len(fact) > 300:
                raise ValueError("Each observed fact must be under 300 characters.")
        return self


class EventReviewIn(BaseModel):
    """Was the detection correct? Nothing else."""

    decision: ReviewDecision
    notes: str | None = Field(default=None, max_length=4000)


class EventAcknowledgeIn(BaseModel):
    """Someone has seen or handled it. Says nothing about correctness."""

    acknowledged: bool = True
    notes: str | None = Field(default=None, max_length=4000)


class EventOut(BaseModel):
    id: int
    camera_id: int
    camera_name: str
    area: str | None
    building: str | None
    function_id: int | None
    kind: EventKind
    kind_label: str
    started_at: datetime
    duration_s: float | None
    rule_summary: str
    facts: list[str]
    overlay: list[dict[str, Any]] | None
    image_width: int | None
    image_height: int | None
    has_snapshot: bool
    has_clip: bool

    decision: ReviewDecision
    decided_by: str | None
    decided_at: datetime | None
    acknowledged: bool
    acknowledged_by: str | None
    acknowledged_at: datetime | None
    notes: str | None

    is_sample: bool
    created_at: datetime


class EventPage(BaseModel):
    items: list[EventOut]
    total: int
    counts: dict[str, int]
    note: str


# -- lifecycle -----------------------------------------------------------

class ActivateIn(BaseModel):
    confirm: bool = False
    notes: str | None = None


class CameraStateOut(BaseModel):
    camera_id: int
    state: str
    label: str
    can_activate: bool
    blockers: list[str]
    analytics_configured: int
    analytics_valid: int
    processing_available: bool
    calibration_needs_revalidation: bool


# -- setup wizard --------------------------------------------------------

class ProgressIn(BaseModel):
    step: str = Field(min_length=1, max_length=40)
    draft: dict[str, Any] = Field(default_factory=dict)
    completed: list[str] = Field(default_factory=list, max_length=20)


class ProgressOut(BaseModel):
    camera_id: int
    step: str
    draft: dict[str, Any]
    completed: list[str]
    updated_at: datetime | None
    updated_by: str | None


# -- discovery -----------------------------------------------------------

class DiscoveredOut(BaseModel):
    address: str
    xaddrs: list[str]
    name: str | None
    hardware: str | None
    already_registered: bool
    registered_camera_id: int | None


class DiscoveryOut(BaseModel):
    devices: list[DiscoveredOut]
    searched_seconds: float
    note: str
