"""The guided installer flow.

Connect cameras -> Create area -> Add floor points -> Match points ->
Calculate mapping -> Check accuracy -> Connect cameras.

Everything here is phrased in what an installer measures and clicks. No XYZ, no
roll/pitch/yaw, no solver choice: the pipeline is fixed and documented, and the
full 3D route stays available under the existing calibration endpoints.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import floormapping as fm
from ..db import SessionLocal, get_db
from ..jobs import Job, runner
from ..models import (
    Building, CalibrationMethod, CalibrationRevision, CalibrationStatus, Camera, CoordinateSystem,
    Floor, MonitoredZone, ObservationRole, PixelConvention, PointObservation, PointRole,
    ValidationResult, WorldReferencePoint,
)
from ..positioning import intrinsics_from_record
from ..schemas_setup import (
    ActivateMappingIn, CalculateMappingIn, CheckAccuracyIn, ConsistencyCheckIn, FloorPointIn,
    FloorPointOut, FloorPointUpdate, MatchDraftIn, MatchOut, MatchStatusOut, ProjectPointIn,
    WorkspaceIn, WorkspaceOut, WorkspaceUpdate,
)
from ..security import current_operator

log = logging.getLogger("numenor.setup")

router = APIRouter(prefix="/api/setup", tags=["setup"], dependencies=[Depends(current_operator)])

# Editing these changes what the recorded coordinates physically mean.
REDEFINING_FIELDS = {"origin_description", "x_axis_description"}


# -- helpers -------------------------------------------------------------

def _workspace(db: Session, workspace_id: int) -> CoordinateSystem:
    cs = db.get(CoordinateSystem, workspace_id)
    if not cs:
        raise HTTPException(404, "Workspace not found.")
    return cs


def _camera(db: Session, camera_id: int) -> Camera:
    camera = db.get(Camera, camera_id)
    if not camera:
        raise HTTPException(404, "Camera not found.")
    return camera


def _floor_label(db: Session, floor_id: int | None) -> str | None:
    if not floor_id:
        return None
    floor = db.get(Floor, floor_id)
    if not floor:
        return None
    return f"{floor.building.site.name} · {floor.building.name} · {floor.name}"


def _workspace_out(db: Session, cs: CoordinateSystem) -> WorkspaceOut:
    points = db.scalars(select(WorldReferencePoint)
                        .where(WorldReferencePoint.coordinate_system_id == cs.id)).all()
    return WorkspaceOut(
        id=cs.id, name=cs.name, floor_id=cs.floor_id,
        floor_label=_floor_label(db, cs.floor_id),
        origin_description=cs.origin_description,
        x_axis_description=cs.x_axis_description,
        surface_description=cs.surface_description,
        width_m=cs.workspace_width_m, length_m=cs.workspace_length_m,
        grid_min_x=cs.grid_min_x, grid_max_x=cs.grid_max_x,
        grid_min_y=cs.grid_min_y, grid_max_y=cs.grid_max_y,
        grid_spacing_m=cs.grid_spacing_m, floor_plane_z=cs.floor_plane_z,
        max_ground_error_m=cs.max_ground_error_m,
        min_reference_points=cs.min_reference_points,
        definition_revision=cs.definition_revision,
        camera_count=db.scalar(select(func.count(Camera.id))
                               .where(Camera.coordinate_system_id == cs.id)) or 0,
        point_count=len(points),
        calibration_point_count=sum(1 for p in points if p.role == PointRole.calibration),
        validation_point_count=sum(1 for p in points if p.role == PointRole.validation),
        created_at=cs.created_at, updated_at=cs.updated_at,
    )


def _point_out(point: WorldReferencePoint, observed_by: list[int] | None = None) -> FloorPointOut:
    return FloorPointOut(
        id=point.id, workspace_id=point.coordinate_system_id,
        code=point.code, name=point.name, x=point.x, y=point.y, z=point.z,
        role=point.role, description=point.description,
        measurement_notes=point.measurement_notes, uncertainty_m=point.uncertainty_m,
        aruco_marker_id=point.aruco_marker_id,
        aruco_marker_size_m=point.aruco_marker_size_m,
        aruco_corner_index=point.aruco_corner_index,
        observed_by_camera_ids=observed_by or [],
    )


def mark_dependent_calibrations_stale(db: Session, reason: str,
                                      camera_ids: list[int] | None = None,
                                      workspace_id: int | None = None) -> int:
    """Flag active calibrations whose inputs moved. Nothing is silently reused."""
    stmt = select(CalibrationRevision).where(CalibrationRevision.is_active.is_(True))
    if camera_ids is not None:
        if not camera_ids:
            return 0
        stmt = stmt.where(CalibrationRevision.camera_id.in_(camera_ids))
    if workspace_id is not None:
        stmt = stmt.where(CalibrationRevision.coordinate_system_id == workspace_id)

    affected = db.scalars(stmt).all()
    for revision in affected:
        revision.status = CalibrationStatus.needs_recalibration
        revision.stale_reason = reason
        note = f"Marked stale: {reason}"
        revision.notes = f"{revision.notes}\n{note}" if revision.notes else note
    if affected:
        log.info("Marked %d calibration(s) stale: %s", len(affected), reason)
    return len(affected)


# =====================================================================
# Step B - create the monitored area
# =====================================================================

@router.get("/workspaces", response_model=list[WorkspaceOut])
def list_workspaces(db: Session = Depends(get_db),
                    floor_id: int | None = Query(default=None)) -> list[WorkspaceOut]:
    stmt = select(CoordinateSystem)
    if floor_id:
        stmt = stmt.where(CoordinateSystem.floor_id == floor_id)
    return [_workspace_out(db, cs)
            for cs in db.scalars(stmt.order_by(CoordinateSystem.name)).all()]


@router.post("/workspaces", response_model=WorkspaceOut, status_code=201)
def create_workspace(payload: WorkspaceIn, db: Session = Depends(get_db)) -> WorkspaceOut:
    """Create the area. One shared frame per floor: if the floor already has one,
    it is returned rather than a second origin being invented alongside it."""
    floor = db.get(Floor, payload.floor_id)
    if not floor:
        raise HTTPException(404, "Floor not found.")

    existing = db.scalars(select(CoordinateSystem)
                          .where(CoordinateSystem.floor_id == floor.id)).first()
    if existing:
        raise HTTPException(409, {
            "message": f"'{_floor_label(db, floor.id)}' already uses the workspace "
                       f"'{existing.name}'. Areas on one floor share a single origin so their "
                       "coordinates can be compared.",
            "workspace_id": existing.id,
            "required": "Add your area to that workspace, or pick a different floor. A separate "
                        "origin is only right for a surface that cannot share a flat-plane "
                        "mapping, such as a mezzanine.",
        })

    margin = max(2.0, 0.1 * max(payload.width_m, payload.length_m))
    cs = CoordinateSystem(
        name=payload.name,
        site_id=floor.building.site_id,
        floor_id=floor.id,
        origin_description=payload.origin_description,
        x_axis_description=payload.x_axis_description,
        surface_description=payload.surface_description,
        workspace_width_m=payload.width_m,
        workspace_length_m=payload.length_m,
        grid_min_x=-margin, grid_max_x=payload.width_m + margin,
        grid_min_y=-margin, grid_max_y=payload.length_m + margin,
        grid_spacing_m=payload.grid_spacing_m,
        max_ground_error_m=payload.max_ground_error_m,
        min_reference_points=payload.min_reference_points,
        floor_plane_z=0.0,
    )
    db.add(cs)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, f"A workspace named '{payload.name}' already exists.")
    db.refresh(cs)
    return _workspace_out(db, cs)


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceOut)
def read_workspace(workspace_id: int, db: Session = Depends(get_db)) -> WorkspaceOut:
    return _workspace_out(db, _workspace(db, workspace_id))


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceOut)
def update_workspace(workspace_id: int, payload: WorkspaceUpdate,
                     db: Session = Depends(get_db)) -> WorkspaceOut:
    cs = _workspace(db, workspace_id)
    data = payload.model_dump(exclude_unset=True,
                              exclude={"confirm_redefinition", "redefinition_reason"})

    if "floor_id" in data and data["floor_id"] is not None:
        floor = db.get(Floor, data["floor_id"])
        if not floor:
            raise HTTPException(404, "Floor not found.")
        clash = db.scalars(select(CoordinateSystem).where(
            CoordinateSystem.floor_id == floor.id,
            CoordinateSystem.id != cs.id)).first()
        if clash:
            raise HTTPException(409, {
                "message": f"'{_floor_label(db, floor.id)}' already uses the workspace "
                           f"'{clash.name}'.",
                "workspace_id": clash.id,
            })

    field_map = {"width_m": "workspace_width_m", "length_m": "workspace_length_m"}
    redefining = {f for f in REDEFINING_FIELDS if f in data and data[f] != getattr(cs, f)}

    active = db.scalars(select(CalibrationRevision).where(
        CalibrationRevision.coordinate_system_id == cs.id,
        CalibrationRevision.is_active.is_(True))).all()

    if redefining and active and not payload.confirm_redefinition:
        raise HTTPException(409, {
            "message": f"Changing the origin or axis description changes what every recorded "
                       f"coordinate means. {len(active)} camera mapping(s) were calculated "
                       "against the current description.",
            "affected_cameras": sorted({r.camera_id for r in active}),
            "required": "Re-send with confirm_redefinition=true. Every affected mapping will be "
                        "marked stale and must be re-checked.",
        })

    for field, value in data.items():
        setattr(cs, field_map.get(field, field), value)

    if "width_m" in data or "length_m" in data:
        width = cs.workspace_width_m or 20.0
        length = cs.workspace_length_m or 20.0
        margin = max(2.0, 0.1 * max(width, length))
        cs.grid_min_x, cs.grid_max_x = -margin, width + margin
        cs.grid_min_y, cs.grid_max_y = -margin, length + margin

    if redefining and active:
        cs.definition_revision += 1
        mark_dependent_calibrations_stale(
            db, f"The workspace origin or axis description changed: "
                f"{payload.redefinition_reason or 'no reason given'}.",
            workspace_id=cs.id)

    db.commit()
    db.refresh(cs)
    return _workspace_out(db, cs)


@router.post("/workspaces/{workspace_id}/cameras/{camera_id}", response_model=dict)
def assign_camera(workspace_id: int, camera_id: int, db: Session = Depends(get_db)) -> dict:
    """Put a camera in this workspace. Moving it invalidates any older mapping."""
    cs = _workspace(db, workspace_id)
    camera = _camera(db, camera_id)

    moved = camera.coordinate_system_id not in (None, cs.id)
    if moved:
        mark_dependent_calibrations_stale(
            db, f"The camera moved to the workspace '{cs.name}'; its mapping belongs to the "
                "previous one.", camera_ids=[camera.id])
    camera.coordinate_system_id = cs.id
    db.commit()
    return {"camera_id": camera.id, "workspace_id": cs.id, "previous_mapping_marked_stale": moved}


# =====================================================================
# Step C - add measured floor points
# =====================================================================

@router.get("/workspaces/{workspace_id}/points", response_model=list[FloorPointOut])
def list_points(workspace_id: int, db: Session = Depends(get_db)) -> list[FloorPointOut]:
    _workspace(db, workspace_id)
    points = db.scalars(select(WorldReferencePoint)
                        .where(WorldReferencePoint.coordinate_system_id == workspace_id)
                        .order_by(WorldReferencePoint.code)).all()
    return [_point_out(p, [o.camera_id for o in p.observations]) for p in points]


@router.post("/workspaces/{workspace_id}/points", response_model=FloorPointOut, status_code=201)
def create_point(workspace_id: int, payload: FloorPointIn,
                 db: Session = Depends(get_db)) -> FloorPointOut:
    cs = _workspace(db, workspace_id)
    if payload.workspace_id != workspace_id:
        raise HTTPException(422, "The workspace in the body does not match the URL.")

    point = WorldReferencePoint(
        coordinate_system_id=cs.id, code=payload.code, name=payload.name,
        x=payload.x, y=payload.y, z=cs.floor_plane_z, role=payload.role,
        description=payload.description, measurement_notes=payload.measurement_notes,
        uncertainty_m=payload.uncertainty_m,
        aruco_dictionary=payload.aruco_dictionary, aruco_marker_id=payload.aruco_marker_id,
        aruco_marker_size_m=payload.aruco_marker_size_m,
        aruco_corner_index=payload.aruco_corner_index,
    )
    db.add(point)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, f"Point code '{payload.code}' is already used in this workspace.")
    db.refresh(point)
    return _point_out(point)


@router.patch("/points/{point_id}", response_model=FloorPointOut)
def update_point(point_id: int, payload: FloorPointUpdate,
                 db: Session = Depends(get_db)) -> FloorPointOut:
    point = db.get(WorldReferencePoint, point_id)
    if not point:
        raise HTTPException(404, "Floor point not found.")

    data = payload.model_dump(exclude_unset=True)
    moved = any(f in data and data[f] != getattr(point, f) for f in ("x", "y"))
    role_changed = "role" in data and data["role"] != point.role

    for field, value in data.items():
        setattr(point, field, value)

    if moved or role_changed:
        reason = (f"Point {point.code} was re-measured." if moved
                  else f"Point {point.code} changed role, so the split between fitting and "
                       "validation points is different.")
        mark_dependent_calibrations_stale(
            db, reason, camera_ids=[o.camera_id for o in point.observations])

    db.commit()
    db.refresh(point)
    return _point_out(point, [o.camera_id for o in point.observations])


@router.delete("/points/{point_id}", status_code=204, response_model=None)
def delete_point(point_id: int, db: Session = Depends(get_db)) -> None:
    point = db.get(WorldReferencePoint, point_id)
    if not point:
        raise HTTPException(404, "Floor point not found.")
    camera_ids = [o.camera_id for o in point.observations]
    if camera_ids:
        mark_dependent_calibrations_stale(
            db, f"Point {point.code} was deleted.", camera_ids=camera_ids)
    db.delete(point)
    db.commit()


# =====================================================================
# Step D - match points in camera images
# =====================================================================

def _match_status(db: Session, camera: Camera) -> MatchStatusOut:
    workspace_id = camera.coordinate_system_id
    observations = sorted(camera.observations, key=lambda o: o.id)

    matches = [
        MatchOut(
            observation_id=o.id, reference_point_id=o.reference_point_id,
            code=o.reference_point.code, name=o.reference_point.name,
            role=o.reference_point.role,
            world_x=o.reference_point.x, world_y=o.reference_point.y,
            pixel_u=o.pixel_u, pixel_v=o.pixel_v,
            image_width=o.image_width, image_height=o.image_height,
        ) for o in observations
    ]

    all_points = db.scalars(select(WorldReferencePoint).where(
        WorldReferencePoint.coordinate_system_id == workspace_id)
        .order_by(WorldReferencePoint.code)).all() if workspace_id else []
    matched_ids = {o.reference_point_id for o in observations}
    unmatched = [_point_out(p) for p in all_points if p.id not in matched_ids]

    width = matches[0].image_width if matches else None
    height = matches[0].image_height if matches else None

    issues: list[dict] = []
    geometries = {(m.image_width, m.image_height) for m in matches}
    if len(geometries) > 1:
        issues.append({
            "level": "error", "code": "mixed_image_geometry",
            "message": f"Matches were made on different image sizes ({sorted(geometries)}). Pixel "
                       "positions are not comparable across them — clear the matches and redo "
                       "them on one stream.",
        })

    if matches:
        points = [fm.PointInput(
            reference_point_id=m.reference_point_id, code=m.code, name=m.name,
            world_x=m.world_x, world_y=m.world_y,
            pixel_u=m.pixel_u, pixel_v=m.pixel_v, role=m.role.value,
        ) for m in matches]
        issues.extend(fm.inspect_points(points, width or 1920, height or 1080))

    calibration_matched = sum(1 for m in matches if m.role == PointRole.calibration)
    validation_matched = sum(1 for m in matches if m.role == PointRole.validation)

    return MatchStatusOut(
        camera_id=camera.id, workspace_id=workspace_id,
        image_width=width, image_height=height,
        matches=matches, unmatched_points=unmatched,
        calibration_matched=calibration_matched,
        validation_matched=validation_matched,
        issues=issues,
        ready_to_calculate=(calibration_matched >= fm.MIN_POINTS
                            and not any(i["level"] == "error" for i in issues)),
    )


@router.get("/cameras/{camera_id}/matches", response_model=MatchStatusOut)
def get_matches(camera_id: int, db: Session = Depends(get_db)) -> MatchStatusOut:
    return _match_status(db, _camera(db, camera_id))


@router.put("/cameras/{camera_id}/matches", response_model=MatchStatusOut)
def save_matches(camera_id: int, payload: MatchDraftIn,
                 db: Session = Depends(get_db)) -> MatchStatusOut:
    """Save the installer's matches. Drafts are saved the same way — there is no
    separate draft store, so closing the browser never loses work."""
    camera = _camera(db, camera_id)
    if not camera.coordinate_system_id:
        raise HTTPException(422, {
            "message": "This camera is not part of a workspace yet.",
            "required": "Assign it to a workspace before matching points.",
        })

    ids = [m.reference_point_id for m in payload.matches]
    points = {p.id: p for p in db.scalars(
        select(WorldReferencePoint).where(WorldReferencePoint.id.in_(ids))).all()} if ids else {}
    missing = set(ids) - set(points)
    if missing:
        raise HTTPException(404, f"Floor point(s) not found: {sorted(missing)}.")

    wrong_workspace = [points[i].code for i in ids
                       if points[i].coordinate_system_id != camera.coordinate_system_id]
    if wrong_workspace:
        raise HTTPException(422, {
            "message": f"These points belong to a different workspace: {wrong_workspace}.",
            "required": "Match only points measured in this camera's workspace.",
        })

    if payload.replace_existing:
        for existing in list(camera.observations):
            db.delete(existing)
        db.flush()
        # The relationship still holds the deleted rows until it is reloaded.
        # Reusing one of those as an "existing" record would attach the update to
        # a doomed object, and the save would silently do nothing.
        db.refresh(camera)

    by_point = {o.reference_point_id: o for o in camera.observations}
    for m in payload.matches:
        point = points[m.reference_point_id]
        record = by_point.get(m.reference_point_id)
        if record is None:
            record = PointObservation(camera_id=camera.id, reference_point_id=point.id)
            db.add(record)
        record.pixel_u = m.pixel_u
        record.pixel_v = m.pixel_v
        record.image_width = payload.image_width
        record.image_height = payload.image_height
        # A point's registry role decides whether it is fitted or held out, so
        # the two cannot drift apart per camera.
        record.role = (ObservationRole.holdout if point.role == PointRole.validation
                       else ObservationRole.fit)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Each floor point can be matched once per camera.")
    db.refresh(camera)
    return _match_status(db, camera)


@router.delete("/cameras/{camera_id}/matches/{observation_id}", status_code=204, response_model=None)
def delete_match(camera_id: int, observation_id: int, db: Session = Depends(get_db)) -> None:
    camera = _camera(db, camera_id)
    record = db.get(PointObservation, observation_id)
    if not record or record.camera_id != camera.id:
        raise HTTPException(404, "Match not found for this camera.")
    db.delete(record)
    db.commit()


# =====================================================================
# Step E - calculate the mapping
# =====================================================================

def _collect_points(camera: Camera, image_width: int, image_height: int) -> list[fm.PointInput]:
    points: list[fm.PointInput] = []
    mismatched: list[str] = []
    for o in camera.observations:
        if (o.image_width, o.image_height) != (image_width, image_height):
            mismatched.append(o.reference_point.code)
            continue
        points.append(fm.PointInput(
            reference_point_id=o.reference_point_id,
            code=o.reference_point.code, name=o.reference_point.name,
            world_x=o.reference_point.x, world_y=o.reference_point.y,
            pixel_u=o.pixel_u, pixel_v=o.pixel_v,
            role=o.reference_point.role.value,
        ))
    if mismatched:
        raise HTTPException(422, {
            "message": f"{len(mismatched)} match(es) were made on a different image size than the "
                       f"{image_width}x{image_height} you are calculating for: {mismatched}.",
            "required": "Pixel positions cannot be compared across image sizes. Re-match those "
                        "points on the current stream.",
        })
    return points


async def _run_mapping(job: Job, camera_id: int, payload: CalculateMappingIn,
                       operator: str) -> dict:
    """The job body. Owns its own session: the request's is long gone."""
    with SessionLocal() as db:
        camera = db.get(Camera, camera_id)
        if not camera:
            raise fm.MappingError("The camera no longer exists.")
        cs = db.get(CoordinateSystem, camera.coordinate_system_id) if camera.coordinate_system_id else None
        if cs is None:
            raise fm.MappingError(
                "This camera is not part of a workspace.",
                hint="Assign it to a workspace, then match points again.")

        job.step = "Reading matched points"
        job.progress = 0.15
        points = _collect_points(camera, payload.image_width, payload.image_height)

        job.step = "Checking the lens calibration"
        job.progress = 0.3
        intrinsics = None
        record = next((i for i in camera.intrinsics_sets if i.is_active), None)
        if record is not None:
            candidate = intrinsics_from_record(record)
            try:
                candidate.require_matches(payload.image_width, payload.image_height)
                intrinsics = candidate
            except Exception:
                # A calibration for another stream size is not usable here, and
                # scaling it would be a guess. Fall back to raw pixels and say so.
                log.info("Camera %s: lens calibration is for %s, not %sx%s; using raw pixels.",
                         camera.id, candidate.geometry_key,
                         payload.image_width, payload.image_height)

        job.step = "Calculating the mapping"
        job.progress = 0.55
        result = fm.calculate_floor_mapping(
            points, payload.image_width, payload.image_height,
            intrinsics=intrinsics, plane_z=cs.floor_plane_z,
            ransac_threshold_px=payload.tolerance_px)

        job.step = "Saving"
        job.progress = 0.85
        highest = db.scalar(select(CalibrationRevision.revision_number)
                            .where(CalibrationRevision.camera_id == camera.id)
                            .order_by(CalibrationRevision.revision_number.desc()).limit(1))
        revision = CalibrationRevision(
            camera_id=camera.id,
            coordinate_system_id=cs.id,
            coordinate_system_revision=cs.definition_revision,
            revision_number=(highest or 0) + 1,
            method=CalibrationMethod.homography,
            status=CalibrationStatus.calibrated_unvalidated,
            homography_json=json.dumps(result.H_image_to_floor.tolist()),
            homography_inverse_json=json.dumps(result.H_floor_to_image.tolist()),
            plane_z=cs.floor_plane_z,
            pixel_convention=(PixelConvention.undistorted if result.distortion_corrected
                              else PixelConvention.raw),
            distortion_corrected=result.distortion_corrected,
            source_image_width=payload.image_width,
            source_image_height=payload.image_height,
            intrinsics_id=record.id if (record and intrinsics) else None,
            coverage_polygon_json=json.dumps(result.coverage_polygon),
            solver="guided floor mapping (RANSAC + refit on inliers)",
            metrics_json=json.dumps(result.to_dict()),
            warnings_json=json.dumps(result.warnings),
            notes=payload.notes,
            created_by=operator,
        )
        db.add(revision)
        db.commit()
        db.refresh(revision)

        summary = result.to_dict()
        summary["revision_id"] = revision.id
        summary["revision_number"] = revision.revision_number
        summary["camera_id"] = camera.id
        summary["workspace_id"] = cs.id
        summary["activated"] = False
        summary["next_step"] = (
            "Check the accuracy against your validation points, then activate the mapping."
        )
        return summary


@router.post("/cameras/{camera_id}/calculate-mapping", response_model=dict, status_code=202)
async def calculate_mapping(camera_id: int, payload: CalculateMappingIn,
                            db: Session = Depends(get_db),
                            operator: str = Depends(current_operator)) -> dict:
    """Start the calculation. Returns a job to poll; nothing is activated by it."""
    camera = _camera(db, camera_id)
    job = runner.start("floor_mapping", lambda j: _run_mapping(j, camera.id, payload, operator))
    return {"job": job.to_dict(),
            "poll": f"/api/setup/jobs/{job.id}",
            "note": "The new mapping is saved as a draft revision. Your current one keeps working "
                    "until you activate the new one."}


@router.get("/jobs/{job_id}", response_model=dict)
def job_status(job_id: str) -> dict:
    job = runner.get(job_id)
    if not job:
        raise HTTPException(404, "That job has expired or never existed.")
    return job.to_dict()


# =====================================================================
# Step F - check accuracy, then activate
# =====================================================================

def _revision_for(db: Session, camera: Camera, revision_id: int | None) -> CalibrationRevision:
    if revision_id:
        revision = db.get(CalibrationRevision, revision_id)
        if not revision or revision.camera_id != camera.id:
            raise HTTPException(404, "That mapping revision does not belong to this camera.")
        return revision
    latest = sorted(camera.calibration_revisions, key=lambda r: r.revision_number)
    if not latest:
        raise HTTPException(404, {
            "message": "This camera has no mapping yet.",
            "required": "Match points and calculate a mapping first.",
        })
    return latest[-1]


@router.post("/cameras/{camera_id}/check-accuracy", response_model=dict)
def check_accuracy(camera_id: int, payload: CheckAccuracyIn,
                   revision_id: int | None = Query(default=None),
                   db: Session = Depends(get_db),
                   operator: str = Depends(current_operator)) -> dict:
    """Score a mapping against points that took no part in calculating it."""
    camera = _camera(db, camera_id)
    revision = _revision_for(db, camera, revision_id)

    if revision.method != CalibrationMethod.homography or not revision.metrics_json:
        raise HTTPException(422, {
            "message": "Accuracy checking here covers floor mappings. This revision was produced "
                       "another way.",
            "required": "Use the advanced calibration validation for pose-based revisions.",
        })

    metrics = json.loads(revision.metrics_json)
    result = fm.MappingResult(
        H_image_to_floor=np.array(json.loads(revision.homography_json), dtype=float),
        H_floor_to_image=np.array(json.loads(revision.homography_inverse_json), dtype=float),
        pixel_convention=revision.pixel_convention.value,
        distortion_corrected=revision.distortion_corrected,
        image_width=revision.source_image_width or 0,
        image_height=revision.source_image_height or 0,
        plane_z=revision.plane_z,
        used_point_ids=metrics.get("used_point_ids", []),
        rejected_point_ids=metrics.get("rejected_point_ids", []),
        per_point=metrics.get("per_point", []),
        mean_error_m=metrics.get("mean_error_m", float("nan")),
        median_error_m=metrics.get("median_error_m", float("nan")),
        max_error_m=metrics.get("max_error_m", float("nan")),
        mean_reprojection_px=metrics.get("mean_reprojection_error_px", float("nan")),
        coverage_polygon=metrics.get("coverage_polygon", []),
        coverage_area_m2=metrics.get("coverage_area_m2", 0.0),
        condition_number=metrics.get("condition_number", 0.0),
        ransac_threshold_px=metrics.get("ransac_threshold_px", 3.0),
    )

    outcome = fm.evaluate_validation(result, payload.acceptance_threshold_m)

    record = ValidationResult(
        revision_id=revision.id,
        fit_reprojection_error_px=metrics.get("mean_reprojection_error_px"),
        holdout_ground_error_m=outcome["summary"]["mean_m"],
        holdout_max_error_m=outcome["summary"]["max_m"],
        holdout_point_count=outcome["held_out_count"],
        fit_point_count=len(result.used_point_ids),
        inlier_count=len(result.used_point_ids),
        outlier_count=len(result.rejected_point_ids),
        passed=outcome["passed"],
        thresholds_json=json.dumps({"acceptance_threshold_m": payload.acceptance_threshold_m}),
        details_json=json.dumps(outcome["held_out"]),
        reviewer=payload.reviewer or operator,
        notes=payload.notes,
    )
    db.add(record)

    # Passing the check does not activate anything. That stays a separate action.
    if outcome["passed"] and revision.status != CalibrationStatus.needs_recalibration:
        revision.status = CalibrationStatus.validated
    db.commit()

    return {
        "revision_id": revision.id,
        "revision_number": revision.revision_number,
        "is_active": revision.is_active,
        "calibration_status": revision.status.value,
        "validation": outcome,
        "next_step": ("Activate this mapping to start using it."
                      if not revision.is_active else "This mapping is already in use."),
    }


@router.post("/cameras/{camera_id}/activate-mapping", response_model=dict)
def activate_mapping(camera_id: int, payload: ActivateMappingIn,
                     revision_id: int | None = Query(default=None),
                     db: Session = Depends(get_db)) -> dict:
    """Put a mapping into use. Always explicit; earlier revisions are kept."""
    camera = _camera(db, camera_id)
    revision = _revision_for(db, camera, revision_id)
    if not payload.confirm:
        raise HTTPException(422, "Activation must be confirmed.")

    if revision.status == CalibrationStatus.needs_recalibration:
        raise HTTPException(409, {
            "message": "This mapping is marked stale because something it depends on changed.",
            "reason": revision.stale_reason,
            "required": "Recalculate it before putting it into use.",
        })

    for other in camera.calibration_revisions:
        other.is_active = False
    revision.is_active = True
    revision.activated_at = datetime.now(timezone.utc)
    if payload.notes:
        revision.notes = f"{revision.notes}\n{payload.notes}" if revision.notes else payload.notes

    # Zone world polygons were derived from whichever mapping was active before.
    for zone in camera.zones:
        if zone.world_polygon_json and zone.world_from_revision_id != revision.id:
            zone.world_polygon_json = None
            zone.world_from_revision_id = None

    db.commit()
    db.refresh(revision)
    latest = revision.validations[-1] if revision.validations else None
    log.info("Camera %s: activated floor mapping revision %s.", camera.id, revision.revision_number)
    return {
        "revision_id": revision.id,
        "revision_number": revision.revision_number,
        "is_active": True,
        "calibration_status": revision.status.value,
        "accuracy_checked": bool(latest),
        "accuracy_passed": latest.passed if latest else None,
        "note": ("Active and checked against held-out points." if latest and latest.passed
                 else "Active, but its accuracy has not been confirmed against separately "
                      "measured points."),
    }


# =====================================================================
# Projection
# =====================================================================

def _active_mapping(camera: Camera) -> tuple[CalibrationRevision, np.ndarray, np.ndarray]:
    revision = next((r for r in camera.calibration_revisions if r.is_active), None)
    if revision is None or not revision.homography_json:
        raise HTTPException(422, {
            "message": "This camera has no active floor mapping.",
            "required": "Calculate a mapping and activate it.",
        })
    return (revision,
            np.array(json.loads(revision.homography_json), dtype=float),
            np.array(json.loads(revision.homography_inverse_json), dtype=float))


@router.post("/cameras/{camera_id}/project", response_model=dict)
def project_point(camera_id: int, payload: ProjectPointIn, db: Session = Depends(get_db)) -> dict:
    """Turn a pixel into a floor position, or a floor position into a pixel."""
    camera = _camera(db, camera_id)
    revision, H, H_inv = _active_mapping(camera)

    coverage = json.loads(revision.coverage_polygon_json) if revision.coverage_polygon_json else []
    caveats: list[str] = []
    if not revision.distortion_corrected:
        caveats.append(
            "This mapping was fitted on raw pixels with no lens calibration, so distortion is not "
            "corrected. Accuracy falls off toward the edges of the frame.")
    if revision.status == CalibrationStatus.needs_recalibration:
        caveats.append(f"The mapping is stale: {revision.stale_reason}")
    elif revision.status != CalibrationStatus.validated:
        caveats.append("This mapping has not been checked against separately measured points.")

    if payload.pixel_u is not None:
        if revision.source_image_width and not (
                0 <= payload.pixel_u <= revision.source_image_width - 1
                and 0 <= payload.pixel_v <= revision.source_image_height - 1):
            raise HTTPException(422, {
                "message": f"That pixel is outside the {revision.source_image_width}x"
                           f"{revision.source_image_height} image this mapping was made for.",
            })
        work_u, work_v = payload.pixel_u, payload.pixel_v
        if revision.distortion_corrected and revision.intrinsics is not None:
            work_u, work_v = intrinsics_from_record(revision.intrinsics).undistort_points(
                [[payload.pixel_u, payload.pixel_v]])[0]
        try:
            x, y = fm.project_image_to_floor(H, work_u, work_v)
        except fm.MappingError as exc:
            raise HTTPException(422, {"message": exc.message, "hint": exc.hint}) from exc

        outside = bool(coverage) and not fm.point_in_polygon(x, y, coverage)
        if outside:
            caveats.append(
                "This position is outside the area the measured points cover, so it is an "
                "extrapolation rather than something the mapping was checked over.")
        return {
            "floor": {"x": round(x, 4), "y": round(y, 4)},
            "within_checked_area": not outside,
            "caveats": caveats,
            "revision_id": revision.id,
        }

    try:
        u, v = fm.project_floor_to_image(H_inv, payload.x, payload.y)
    except fm.MappingError as exc:
        raise HTTPException(422, {"message": exc.message, "hint": exc.hint}) from exc

    in_image = True
    if revision.source_image_width:
        in_image = (0 <= u <= revision.source_image_width - 1
                    and 0 <= v <= revision.source_image_height - 1)
    outside = bool(coverage) and not fm.point_in_polygon(payload.x, payload.y, coverage)
    if outside:
        caveats.append("That floor position is outside the area the measured points cover.")
    return {
        "pixel": {"u": round(u, 2), "v": round(v, 2)},
        "in_image": in_image,
        "within_checked_area": not outside,
        "caveats": caveats,
        "revision_id": revision.id,
    }


@router.post("/consistency-check", response_model=dict)
def consistency_check(payload: ConsistencyCheckIn, db: Session = Depends(get_db)) -> dict:
    """Mark one stationary floor point in several cameras and compare."""
    cs = _workspace(db, payload.workspace_id)

    truth = None
    if payload.reference_point_id:
        point = db.get(WorldReferencePoint, payload.reference_point_id)
        if not point:
            raise HTTPException(404, "Reference point not found.")
        truth = (point.x, point.y)
    elif payload.ground_truth_x is not None and payload.ground_truth_y is not None:
        truth = (payload.ground_truth_x, payload.ground_truth_y)

    entries: list[dict] = []
    warnings: list[str] = []
    for mark in payload.marks:
        camera = db.get(Camera, mark.camera_id)
        if not camera:
            raise HTTPException(404, f"Camera {mark.camera_id} not found.")
        entry: dict = {"camera_id": camera.id, "camera_name": camera.name,
                       "pixel": [mark.pixel_u, mark.pixel_v], "floor": None}

        if camera.coordinate_system_id != cs.id:
            entry["error"] = (f"'{camera.name}' is in a different workspace, so its positions "
                              "cannot be compared with these.")
            warnings.append(entry["error"])
            entries.append(entry)
            continue
        try:
            projected = project_point(camera.id, ProjectPointIn(
                pixel_u=mark.pixel_u, pixel_v=mark.pixel_v), db)
        except HTTPException as exc:
            detail = exc.detail
            entry["error"] = detail.get("message") if isinstance(detail, dict) else str(detail)
            entries.append(entry)
            continue

        entry["floor"] = [projected["floor"]["x"], projected["floor"]["y"]]
        entry["within_checked_area"] = projected["within_checked_area"]
        entry["caveats"] = projected["caveats"]
        if not projected["within_checked_area"]:
            warnings.append(f"'{camera.name}' places the point outside its checked area.")
        entries.append(entry)

    usable = [e for e in entries if e["floor"]]
    pairwise = []
    for i in range(len(usable)):
        for j in range(i + 1, len(usable)):
            a, b = usable[i], usable[j]
            pairwise.append({
                "camera_a": a["camera_name"], "camera_b": b["camera_name"],
                "disagreement_m": round(float(np.hypot(a["floor"][0] - b["floor"][0],
                                                       a["floor"][1] - b["floor"][1])), 4),
            })

    result: dict = {
        "label": payload.label,
        "workspace": {"id": cs.id, "name": cs.name},
        "cameras": entries,
        "usable_count": len(usable),
        "pairwise": pairwise,
        "max_disagreement_m": round(max((p["disagreement_m"] for p in pairwise), default=0.0), 4),
        "mean_disagreement_m": round(
            float(np.mean([p["disagreement_m"] for p in pairwise])) if pairwise else 0.0, 4),
        "warnings": warnings,
    }

    if truth and usable:
        result["ground_truth"] = {"x": truth[0], "y": truth[1]}
        result["per_camera_error_m"] = [{
            "camera_name": e["camera_name"],
            "error_m": round(float(np.hypot(e["floor"][0] - truth[0], e["floor"][1] - truth[1])), 4),
        } for e in usable]
        result["heading"] = "Accuracy against a measured position"
        result["interpretation"] = (
            "This point has an independently measured position, so these are real errors, not "
            "just disagreement between cameras.")
    else:
        result["heading"] = "Cross-camera consistency"
        result["interpretation"] = (
            "No measured position was given for this point, so this shows only how closely the "
            "cameras agree with each other. They can agree and all be wrong together. For "
            "accuracy, mark a surveyed point instead.")
    return result
