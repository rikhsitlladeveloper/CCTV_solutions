"""Factory coordinate systems and the shared reference-point registry."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..geometry import CONVENTIONS
from ..models import (
    CalibrationRevision, CalibrationStatus, Camera, CoordinateSystem, PointObservation,
    Site, WorldReferencePoint,
)
from ..schemas_calibration import (
    CoordinateSystemIn, CoordinateSystemOut, CoordinateSystemUpdate,
    ReferencePointIn, ReferencePointOut, ReferencePointUpdate,
)
from ..security import current_operator

log = logging.getLogger("numenor.coordinates")

router = APIRouter(prefix="/api/coordinate-systems", tags=["coordinate-systems"],
                   dependencies=[Depends(current_operator)])

# Editing any of these changes what the numbers in the frame physically mean, so
# existing calibrations can no longer be trusted without review.
REDEFINING_FIELDS = {"floor_plane_z", "origin_description", "site_id"}


def _out(db: Session, cs: CoordinateSystem) -> CoordinateSystemOut:
    data = CoordinateSystemOut.model_validate(cs)
    data.reference_point_count = db.scalar(
        select(func.count(WorldReferencePoint.id))
        .where(WorldReferencePoint.coordinate_system_id == cs.id)) or 0
    data.camera_count = db.scalar(
        select(func.count(Camera.id)).where(Camera.coordinate_system_id == cs.id)) or 0
    data.conventions = dict(CONVENTIONS)
    return data


def get_system(db: Session, system_id: int) -> CoordinateSystem:
    cs = db.get(CoordinateSystem, system_id)
    if not cs:
        raise HTTPException(404, "Coordinate system not found.")
    return cs


@router.get("", response_model=list[CoordinateSystemOut])
def list_systems(db: Session = Depends(get_db)) -> list[CoordinateSystemOut]:
    systems = db.scalars(select(CoordinateSystem).order_by(CoordinateSystem.name)).all()
    return [_out(db, cs) for cs in systems]


@router.get("/conventions")
def conventions() -> dict:
    """The single source of truth for frames, rotations and headings."""
    return {"conventions": CONVENTIONS}


@router.post("", response_model=CoordinateSystemOut, status_code=201)
def create_system(payload: CoordinateSystemIn, db: Session = Depends(get_db)) -> CoordinateSystemOut:
    if payload.site_id and not db.get(Site, payload.site_id):
        raise HTTPException(404, "Site not found.")
    cs = CoordinateSystem(**payload.model_dump())
    db.add(cs)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, f"A coordinate system named '{payload.name}' already exists.")
    db.refresh(cs)
    return _out(db, cs)


@router.get("/{system_id}", response_model=CoordinateSystemOut)
def read_system(system_id: int, db: Session = Depends(get_db)) -> CoordinateSystemOut:
    return _out(db, get_system(db, system_id))


@router.patch("/{system_id}", response_model=CoordinateSystemOut)
def update_system(system_id: int, payload: CoordinateSystemUpdate,
                  db: Session = Depends(get_db)) -> CoordinateSystemOut:
    """Edit a frame.

    Cosmetic edits (name, notes, grid extents, thresholds) apply freely. Anything
    that changes what the coordinates physically mean requires explicit consent
    and flags every calibration in the frame for recalibration, rather than
    silently reinterpreting existing numbers.
    """
    cs = get_system(db, system_id)
    data = payload.model_dump(exclude_unset=True,
                              exclude={"confirm_redefinition", "redefinition_reason"})

    redefining = {f for f in REDEFINING_FIELDS
                  if f in data and data[f] != getattr(cs, f)}

    affected = db.scalars(
        select(CalibrationRevision).where(
            CalibrationRevision.coordinate_system_id == cs.id,
            CalibrationRevision.is_active.is_(True),
        )).all()

    if redefining and affected and not payload.confirm_redefinition:
        raise HTTPException(409, {
            "message": (
                f"Changing {sorted(redefining)} redefines what coordinates in "
                f"'{cs.name}' mean. {len(affected)} active calibration(s) were solved against the "
                "current definition and would silently become wrong."
            ),
            "affected_calibrations": len(affected),
            "affected_cameras": sorted({r.camera_id for r in affected}),
            "required": "Re-send with confirm_redefinition=true to proceed. Every affected "
                        "calibration will be marked 'needs_recalibration'.",
        })

    for field, value in data.items():
        setattr(cs, field, value)

    if redefining and affected:
        cs.definition_revision += 1
        for revision in affected:
            revision.status = CalibrationStatus.needs_recalibration
            note = (f"Coordinate system '{cs.name}' was redefined "
                    f"(revision {cs.definition_revision}): {payload.redefinition_reason or 'no reason given'}.")
            revision.notes = f"{revision.notes}\n{note}" if revision.notes else note
        log.warning("Coordinate system %s redefined; flagged %d calibration(s) for recalibration.",
                    cs.id, len(affected))

    db.commit()
    db.refresh(cs)
    return _out(db, cs)


@router.delete("/{system_id}", status_code=204, response_model=None)
def delete_system(system_id: int, db: Session = Depends(get_db)) -> None:
    cs = get_system(db, system_id)
    cameras = db.scalar(select(func.count(Camera.id)).where(Camera.coordinate_system_id == cs.id)) or 0
    if cameras:
        raise HTTPException(409, f"{cameras} camera(s) still use this coordinate system.")
    db.delete(cs)
    db.commit()


# -- reference points ----------------------------------------------------

points_router = APIRouter(prefix="/api/reference-points", tags=["reference-points"],
                          dependencies=[Depends(current_operator)])


def _point_out(db: Session, point: WorldReferencePoint) -> ReferencePointOut:
    data = ReferencePointOut.model_validate(point)
    data.observation_count = db.scalar(
        select(func.count(PointObservation.id))
        .where(PointObservation.reference_point_id == point.id)) or 0
    return data


@points_router.get("", response_model=list[ReferencePointOut])
def list_points(db: Session = Depends(get_db),
                coordinate_system_id: int | None = Query(default=None)) -> list[ReferencePointOut]:
    stmt = select(WorldReferencePoint)
    if coordinate_system_id:
        stmt = stmt.where(WorldReferencePoint.coordinate_system_id == coordinate_system_id)
    points = db.scalars(stmt.order_by(WorldReferencePoint.code)).all()
    return [_point_out(db, p) for p in points]


@points_router.post("", response_model=ReferencePointOut, status_code=201)
def create_point(payload: ReferencePointIn, db: Session = Depends(get_db)) -> ReferencePointOut:
    get_system(db, payload.coordinate_system_id)
    point = WorldReferencePoint(**payload.model_dump())
    db.add(point)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            409, f"Reference point code '{payload.code}' already exists in this coordinate system.")
    db.refresh(point)
    return _point_out(db, point)


@points_router.get("/{point_id}", response_model=ReferencePointOut)
def read_point(point_id: int, db: Session = Depends(get_db)) -> ReferencePointOut:
    point = db.get(WorldReferencePoint, point_id)
    if not point:
        raise HTTPException(404, "Reference point not found.")
    return _point_out(db, point)


@points_router.patch("/{point_id}", response_model=ReferencePointOut)
def update_point(point_id: int, payload: ReferencePointUpdate,
                 db: Session = Depends(get_db)) -> ReferencePointOut:
    point = db.get(WorldReferencePoint, point_id)
    if not point:
        raise HTTPException(404, "Reference point not found.")

    data = payload.model_dump(exclude_unset=True)
    moved = any(f in data and data[f] != getattr(point, f) for f in ("x", "y", "z"))
    for field, value in data.items():
        setattr(point, field, value)

    if moved:
        # Its measured position changed, so anything solved from it is stale.
        stale = db.scalars(
            select(CalibrationRevision)
            .join(Camera, Camera.id == CalibrationRevision.camera_id)
            .join(PointObservation, PointObservation.camera_id == Camera.id)
            .where(PointObservation.reference_point_id == point.id,
                   CalibrationRevision.is_active.is_(True))).unique().all()
        for revision in stale:
            revision.status = CalibrationStatus.needs_recalibration
            note = f"Reference point {point.code} was re-measured after this calibration was solved."
            revision.notes = f"{revision.notes}\n{note}" if revision.notes else note

    db.commit()
    db.refresh(point)
    return _point_out(db, point)


@points_router.delete("/{point_id}", status_code=204, response_model=None)
def delete_point(point_id: int, db: Session = Depends(get_db)) -> None:
    point = db.get(WorldReferencePoint, point_id)
    if not point:
        raise HTTPException(404, "Reference point not found.")
    db.delete(point)
    db.commit()
