"""The supervisor's view: factory overview, the event log and going live.

Two things this module refuses to do.

It never invents a measurement. Where a number cannot be produced — because no
detection service is connected, or because the camera is offline — the response
carries ``null`` and a reason, never a zero. A zero is a claim.

It never lets a half-configured camera be monitored. Activation checks the
evidence and returns the specific reasons it cannot proceed.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .. import monitoring as mon
from ..db import get_db
from ..models import (
    Area, Building, Camera, CameraFunction, Event, EventKind, Floor, FloorPlan, MonitoredZone,
    ReviewDecision, SetupProgress, TestStatus,
)
from ..onvif import discover
from ..schemas_monitoring import (
    ActivateIn, CameraStateOut, DiscoveredOut, DiscoveryOut, EventAcknowledgeIn, EventIn,
    EventOut, EventPage, EventReviewIn, ProgressIn, ProgressOut,
)
from ..security import current_operator

log = logging.getLogger("numenor.monitoring")

router = APIRouter(prefix="/api", tags=["monitoring"],
                   dependencies=[Depends(current_operator)])


def _camera(db: Session, camera_id: int) -> Camera:
    camera = db.get(Camera, camera_id)
    if not camera:
        raise HTTPException(404, "Camera not found.")
    return camera


def _location(camera: Camera) -> tuple[str | None, str | None]:
    area = camera.area
    if area is None:
        return None, None
    floor = area.floor
    building = floor.building if floor else None
    return area.name, (building.name if building else None)


def _event_out(event: Event) -> EventOut:
    area, building = _location(event.camera)
    return EventOut(
        id=event.id, camera_id=event.camera_id, camera_name=event.camera.name,
        area=area, building=building,
        function_id=event.function_id,
        kind=event.kind, kind_label=mon.EVENT_LABELS.get(event.kind, event.kind.value),
        started_at=event.started_at, duration_s=event.duration_s,
        rule_summary=event.rule_summary,
        facts=json.loads(event.facts_json or "[]"),
        overlay=json.loads(event.overlay_json) if event.overlay_json else None,
        image_width=event.image_width, image_height=event.image_height,
        has_snapshot=bool(event.snapshot_path), has_clip=bool(event.clip_path),
        decision=event.decision, decided_by=event.decided_by, decided_at=event.decided_at,
        acknowledged=event.acknowledged_at is not None,
        acknowledged_by=event.acknowledged_by, acknowledged_at=event.acknowledged_at,
        notes=event.notes,
        is_sample=event.is_sample, created_at=event.created_at,
    )


# =====================================================================
# Factory overview
# =====================================================================

@router.get("/overview")
def factory_overview(building_id: int | None = Query(default=None),
                     floor_id: int | None = Query(default=None),
                     hours: int = Query(default=24, ge=1, le=720),
                     db: Session = Depends(get_db)) -> dict:
    """Everything the supervisor's main screen needs, in one request."""
    stmt = select(Camera).options(
        selectinload(Camera.functions),
        selectinload(Camera.calibration_revisions),
        selectinload(Camera.area).selectinload(Area.floor).selectinload(Floor.building),
    )
    cameras = list(db.scalars(stmt).unique())

    if floor_id is not None:
        cameras = [c for c in cameras if c.area and c.area.floor_id == floor_id]
    elif building_id is not None:
        cameras = [c for c in cameras
                   if c.area and c.area.floor and c.area.floor.building_id == building_id]

    camera_ids = [c.id for c in cameras]
    events: list[Event] = []
    if camera_ids:
        events = list(db.scalars(
            select(Event)
            .options(selectinload(Event.camera).selectinload(Camera.area)
                     .selectinload(Area.floor).selectinload(Floor.building))
            .where(Event.camera_id.in_(camera_ids),
                   Event.started_at >= mon.recent_window(hours))
            .order_by(Event.started_at.desc())
        ).unique())

    # Grouped by building and area, so a site with no floor plan still has a
    # usable structure to browse rather than a flat list of thirty cameras.
    groups: dict[str, dict] = {}
    for camera in cameras:
        area = camera.area
        floor = area.floor if area else None
        building = floor.building if floor else None
        key = f"{building.id if building else 0}:{area.id if area else 0}"
        group = groups.setdefault(key, {
            "building": building.name if building else "Unassigned",
            "floor": floor.name if floor else None,
            "area": area.name if area else "No area yet",
            "floor_id": floor.id if floor else None,
            "cameras": [],
        })
        group["cameras"].append({
            "id": camera.id,
            "name": camera.name,
            "connection": camera.last_test_status.value,
            "monitoring": mon.monitoring_state(camera).value,
            "analytics": len(camera.functions),
        })

    plan_stmt = select(FloorPlan).options(selectinload(FloorPlan.floor))
    plans = list(db.scalars(plan_stmt).unique())
    if floor_id is not None:
        plans = [p for p in plans if p.floor_id == floor_id]
    elif building_id is not None:
        plans = [p for p in plans if p.floor and p.floor.building_id == building_id]

    return {
        "health": mon.camera_health(cameras),
        "events": {
            "awaiting_review": mon.awaiting_review(events),
            "total_in_window": len(events),
            "window_hours": hours,
            "sample_rows": sum(1 for e in events if e.is_sample),
        },
        "production": mon.production_counts(cameras, events),
        "groups": sorted(groups.values(), key=lambda g: (g["building"], g["area"])),
        "floor_plans": [
            {"id": p.id, "floor_id": p.floor_id,
             # A plan is identified by the floor it belongs to; the record has
             # no name of its own, only the uploaded file's.
             "floor": p.floor.name if p.floor else None,
             "original_filename": p.original_filename,
             "width_px": p.width_px, "height_px": p.height_px,
             "has_scale": p.scale_px_per_metre is not None}
            for p in plans
        ],
        "has_floor_plan": bool(plans),
        "recent_events": [_event_out(e) for e in events[:12]],
    }


# =====================================================================
# Events
# =====================================================================

@router.get("/events", response_model=EventPage)
def list_events(camera_id: int | None = Query(default=None),
                area_id: int | None = Query(default=None),
                kind: EventKind | None = Query(default=None),
                decision: ReviewDecision | None = Query(default=None),
                acknowledged: bool | None = Query(default=None),
                since_hours: int | None = Query(default=None, ge=1, le=8760),
                include_samples: bool = Query(default=True),
                limit: int = Query(default=100, ge=1, le=500),
                offset: int = Query(default=0, ge=0),
                db: Session = Depends(get_db)) -> EventPage:
    stmt = select(Event).options(
        selectinload(Event.camera).selectinload(Camera.area).selectinload(Area.floor)
        .selectinload(Floor.building)
    )
    if camera_id is not None:
        stmt = stmt.where(Event.camera_id == camera_id)
    if kind is not None:
        stmt = stmt.where(Event.kind == kind)
    if decision is not None:
        stmt = stmt.where(Event.decision == decision)
    if acknowledged is True:
        stmt = stmt.where(Event.acknowledged_at.is_not(None))
    elif acknowledged is False:
        stmt = stmt.where(Event.acknowledged_at.is_(None))
    if since_hours is not None:
        stmt = stmt.where(Event.started_at >= mon.recent_window(since_hours))
    if not include_samples:
        stmt = stmt.where(Event.is_sample.is_(False))

    rows = list(db.scalars(stmt.order_by(Event.started_at.desc())).unique())
    if area_id is not None:
        rows = [e for e in rows if e.camera.area_id == area_id]

    total = len(rows)
    counts = {
        "awaiting_review": sum(1 for e in rows if e.decision == ReviewDecision.unreviewed),
        "confirmed": sum(1 for e in rows if e.decision == ReviewDecision.confirmed),
        "dismissed": sum(1 for e in rows if e.decision == ReviewDecision.dismissed),
        "unacknowledged": sum(1 for e in rows if e.acknowledged_at is None),
        "sample": sum(1 for e in rows if e.is_sample),
    }
    page = rows[offset:offset + limit]

    note = ("No detection service is connected to this deployment, so nothing here was produced "
            "by live inference. Rows marked as sample data are for demonstration.")
    return EventPage(items=[_event_out(e) for e in page], total=total, counts=counts, note=note)


@router.get("/events/{event_id}", response_model=EventOut)
def get_event(event_id: int, db: Session = Depends(get_db)) -> EventOut:
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(404, "Event not found.")
    return _event_out(event)


@router.post("/events", response_model=EventOut, status_code=201)
def ingest_event(payload: EventIn, db: Session = Depends(get_db)) -> EventOut:
    """Where an external processing service reports what it saw.

    The rule's wording is snapshotted onto the event, so editing the rule
    afterwards cannot rewrite the reason a past event fired.
    """
    camera = _camera(db, payload.camera_id)

    function = None
    if payload.function_id is not None:
        function = db.get(CameraFunction, payload.function_id)
        if not function or function.camera_id != camera.id:
            raise HTTPException(404, "That analytic does not belong to this camera.")

    event = Event(
        camera_id=camera.id,
        function_id=function.id if function else None,
        kind=payload.kind,
        started_at=payload.started_at,
        duration_s=payload.duration_s,
        rule_summary=(mon.rule_summary(function, camera) if function
                      else "Reported without a configured rule."),
        facts_json=json.dumps(payload.facts),
        overlay_json=json.dumps(payload.overlay) if payload.overlay else None,
        image_width=payload.image_width, image_height=payload.image_height,
        snapshot_path=payload.snapshot_path, clip_path=payload.clip_path,
        is_sample=False,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return _event_out(event)


@router.post("/events/{event_id}/review", response_model=EventOut)
def review_event(event_id: int, payload: EventReviewIn,
                 operator: str = Depends(current_operator),
                 db: Session = Depends(get_db)) -> EventOut:
    """Record whether the detection was correct.

    Reviewing does not acknowledge. Someone deciding a detection was wrong has
    still dealt with it, but the two facts are recorded separately because they
    answer different questions and different people ask them.
    """
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(404, "Event not found.")
    event.decision = payload.decision
    event.decided_by = operator
    event.decided_at = datetime.now(timezone.utc)
    if payload.notes is not None:
        event.notes = payload.notes
    db.commit()
    db.refresh(event)
    return _event_out(event)


@router.post("/events/{event_id}/acknowledge", response_model=EventOut)
def acknowledge_event(event_id: int, payload: EventAcknowledgeIn,
                      operator: str = Depends(current_operator),
                      db: Session = Depends(get_db)) -> EventOut:
    """Record that a person has seen or handled it. Says nothing about correctness."""
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(404, "Event not found.")
    if payload.acknowledged:
        event.acknowledged_by = operator
        event.acknowledged_at = datetime.now(timezone.utc)
    else:
        event.acknowledged_by = None
        event.acknowledged_at = None
    if payload.notes is not None:
        event.notes = payload.notes
    db.commit()
    db.refresh(event)
    return _event_out(event)


@router.get("/events/{event_id}/snapshot")
def event_snapshot(event_id: int, db: Session = Depends(get_db)) -> Response:
    from pathlib import Path
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(404, "Event not found.")
    if not event.snapshot_path:
        raise HTTPException(404, "This event has no stored snapshot.")
    path = Path(event.snapshot_path)
    if not path.is_file():
        raise HTTPException(410, "The stored evidence file is no longer on disk.")
    return Response(content=path.read_bytes(), media_type="image/jpeg")


# =====================================================================
# Monitoring lifecycle
# =====================================================================

@router.get("/cameras/{camera_id}/monitoring", response_model=CameraStateOut)
def camera_state(camera_id: int, db: Session = Depends(get_db)) -> CameraStateOut:
    camera = _camera(db, camera_id)
    return CameraStateOut(camera_id=camera.id, **mon.camera_state_out(camera))


@router.post("/cameras/{camera_id}/monitoring/request-validation",
             response_model=CameraStateOut)
def request_validation(camera_id: int, db: Session = Depends(get_db)) -> CameraStateOut:
    camera = _camera(db, camera_id)
    blockers = mon.activation_blockers(camera)
    if blockers:
        raise HTTPException(422, {
            "message": "This camera is not configured enough to validate yet.",
            "blockers": blockers,
        })
    camera.monitoring_requested = True
    db.commit()
    db.refresh(camera)
    return CameraStateOut(camera_id=camera.id, **mon.camera_state_out(camera))


@router.post("/cameras/{camera_id}/monitoring/activate", response_model=CameraStateOut)
def activate_monitoring(camera_id: int, payload: ActivateIn,
                        db: Session = Depends(get_db)) -> CameraStateOut:
    """Switch monitoring on. Deliberate, and refused when anything is missing.

    Floor-plan calibration is *not* required: camera-only analytics are
    complete without one, and demanding a map here would block the common case.
    """
    camera = _camera(db, camera_id)
    if not payload.confirm:
        raise HTTPException(422, "Activation must be confirmed.")
    blockers = mon.activation_blockers(camera)
    if blockers:
        raise HTTPException(422, {
            "message": "This camera cannot be activated yet.",
            "blockers": blockers,
        })
    camera.monitoring_requested = True
    camera.monitoring_active = True
    camera.monitoring_activated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(camera)
    return CameraStateOut(camera_id=camera.id, **mon.camera_state_out(camera))


@router.post("/cameras/{camera_id}/monitoring/deactivate", response_model=CameraStateOut)
def deactivate_monitoring(camera_id: int, db: Session = Depends(get_db)) -> CameraStateOut:
    camera = _camera(db, camera_id)
    camera.monitoring_active = False
    camera.monitoring_requested = False
    db.commit()
    db.refresh(camera)
    return CameraStateOut(camera_id=camera.id, **mon.camera_state_out(camera))


# =====================================================================
# Setup wizard progress
# =====================================================================

@router.get("/cameras/{camera_id}/setup-progress", response_model=ProgressOut)
def get_progress(camera_id: int, db: Session = Depends(get_db)) -> ProgressOut:
    camera = _camera(db, camera_id)
    record = camera.setup_progress
    if record is None:
        return ProgressOut(camera_id=camera.id, step="connect", draft={}, completed=[],
                           updated_at=None, updated_by=None)
    return ProgressOut(
        camera_id=camera.id, step=record.step,
        draft=json.loads(record.draft_json or "{}"),
        completed=json.loads(record.completed_json or "[]"),
        updated_at=record.updated_at, updated_by=record.updated_by,
    )


@router.put("/cameras/{camera_id}/setup-progress", response_model=ProgressOut)
def save_progress(camera_id: int, payload: ProgressIn,
                  operator: str = Depends(current_operator),
                  db: Session = Depends(get_db)) -> ProgressOut:
    """Save where the installer has got to, so a reload does not lose the job."""
    camera = _camera(db, camera_id)
    record = camera.setup_progress
    if record is None:
        record = SetupProgress(camera_id=camera.id)
        db.add(record)
    record.step = payload.step
    record.draft_json = json.dumps(payload.draft)
    record.completed_json = json.dumps(payload.completed)
    record.updated_by = operator
    db.commit()
    db.refresh(record)
    return ProgressOut(
        camera_id=camera.id, step=record.step,
        draft=json.loads(record.draft_json or "{}"),
        completed=json.loads(record.completed_json or "[]"),
        updated_at=record.updated_at, updated_by=record.updated_by,
    )


@router.delete("/cameras/{camera_id}/setup-progress", status_code=204, response_model=None)
def clear_progress(camera_id: int, db: Session = Depends(get_db)) -> None:
    camera = _camera(db, camera_id)
    if camera.setup_progress is not None:
        db.delete(camera.setup_progress)
        db.commit()


# =====================================================================
# Rule summaries
# =====================================================================

@router.get("/cameras/{camera_id}/rule-summaries")
def rule_summaries(camera_id: int, db: Session = Depends(get_db)) -> dict:
    """Each configured analytic as one plain sentence, plus whether it is usable."""
    camera = _camera(db, camera_id)
    out = []
    for function in camera.functions:
        ok, why = mon.geometry_is_valid(function)
        out.append({
            "function_id": function.id,
            "name": function.name,
            "kind": function.kind.value,
            "summary": mon.rule_summary(function, camera),
            "geometry_valid": ok,
            "geometry_problem": why,
        })
    return {"camera_id": camera.id, "rules": out}


# =====================================================================
# ONVIF discovery
# =====================================================================

@router.post("/discovery/onvif", response_model=DiscoveryOut)
def discover_onvif(seconds: float = Query(default=4.0, ge=1.0, le=15.0),
                   db: Session = Depends(get_db)) -> DiscoveryOut:
    """Ask the local network which ONVIF devices are there.

    Multicast WS-Discovery, run on this host. It is a question devices choose to
    answer, not a scan: nothing is contacted that did not reply first, and no
    credentials are involved.
    """
    try:
        devices = discover(timeout=seconds)
    except OSError as exc:
        raise HTTPException(503, {
            "message": "Discovery could not run on this host.",
            "detail": str(exc),
            "hint": "Add the camera by IP or RTSP URL instead.",
        }) from exc

    known = {c.host: c.id for c in db.scalars(select(Camera))}
    out = [
        DiscoveredOut(
            address=d.address, xaddrs=d.xaddrs, name=d.name, hardware=d.hardware,
            already_registered=d.address in known,
            registered_camera_id=known.get(d.address),
        )
        for d in devices
    ]
    note = ("Discovery finds only devices that answer multicast on this network segment. "
            "Cameras on another VLAN, or a network that blocks multicast, will not appear "
            "here — that means 'not found this way', not 'not present'. Add those by address.")
    return DiscoveryOut(devices=out, searched_seconds=seconds, note=note)
