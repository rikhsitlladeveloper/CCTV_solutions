"""Zones, marker assistance and the camera relationship graph.

The graph records how views relate on the ground, for a tracking service that
does not exist yet. It holds geometry and topology only and identifies nobody.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import floormapping as fm
from .. import markers as marker_tools
from .. import relationships as rel
from ..db import get_db
from ..models import (
    CalibrationStatus, Camera, CoordinateSystem, MonitoredZone, PointRole, RelationshipKind,
    VerificationStatus, WorldReferencePoint, ZoneKind,
)
from ..positioning import intrinsics_from_record
from ..schemas_setup import (
    AcceptMarkersIn, DetectMarkersIn, MatchDraftIn, RelationshipGraphOut, RelationshipIn,
    RelationshipOut, RelationshipUpdate, ZoneIn, ZoneOut, ZoneUpdate,
)
from ..security import current_operator
from .setup import _camera, _match_status, mark_dependent_calibrations_stale, save_matches

log = logging.getLogger("numenor.graph")

router = APIRouter(prefix="/api", tags=["zones-and-relationships"],
                   dependencies=[Depends(current_operator)])


# -- zones ---------------------------------------------------------------

def _zone_out(zone: MonitoredZone) -> ZoneOut:
    data = ZoneOut.model_validate(zone)
    data.camera_name = zone.camera.name if zone.camera else None
    data.image_polygon = json.loads(zone.image_polygon_json)
    data.world_polygon = json.loads(zone.world_polygon_json) if zone.world_polygon_json else None
    active = next((r for r in zone.camera.calibration_revisions if r.is_active), None) \
        if zone.camera else None
    data.world_is_stale = bool(
        zone.world_polygon_json and active and zone.world_from_revision_id != active.id)
    return data


def _derive_world_polygon(db: Session, zone: MonitoredZone) -> None:
    """Map a zone onto the floor using the camera's active mapping, if it has one."""
    camera = zone.camera
    active = next((r for r in camera.calibration_revisions if r.is_active), None)
    if active is None or not active.homography_json:
        zone.world_polygon_json = None
        zone.world_from_revision_id = None
        return
    if (active.source_image_width, active.source_image_height) != (zone.image_width, zone.image_height):
        zone.world_polygon_json = None
        zone.world_from_revision_id = None
        return

    H = np.array(json.loads(active.homography_json), dtype=float)
    intrinsics = (intrinsics_from_record(active.intrinsics)
                  if active.distortion_corrected and active.intrinsics else None)

    def project(u: float, v: float) -> tuple[float, float]:
        if intrinsics is not None:
            u, v = intrinsics.undistort_points([[u, v]])[0]
        return fm.project_image_to_floor(H, u, v)

    polygon = rel.zone_world_polygon(zone, project)
    zone.world_polygon_json = json.dumps(polygon) if polygon else None
    zone.world_from_revision_id = active.id if polygon else None
    zone.coordinate_system_id = camera.coordinate_system_id


@router.get("/cameras/{camera_id}/zones", response_model=list[ZoneOut])
def list_zones(camera_id: int, db: Session = Depends(get_db)) -> list[ZoneOut]:
    return [_zone_out(z) for z in _camera(db, camera_id).zones]


@router.post("/cameras/{camera_id}/zones", response_model=ZoneOut, status_code=201)
def create_zone(camera_id: int, payload: ZoneIn, db: Session = Depends(get_db)) -> ZoneOut:
    """Draw a zone in the camera image. No calibration needed — the world polygon
    is filled in later if and when a mapping exists."""
    camera = _camera(db, camera_id)
    zone = MonitoredZone(
        camera_id=camera.id, name=payload.name, kind=payload.kind,
        image_polygon_json=json.dumps([[float(u), float(v)] for u, v in payload.image_polygon]),
        image_width=payload.image_width, image_height=payload.image_height,
        coordinate_system_id=camera.coordinate_system_id,
        notes=payload.notes,
    )
    db.add(zone)
    db.flush()
    _derive_world_polygon(db, zone)
    db.commit()
    db.refresh(zone)
    return _zone_out(zone)


@router.patch("/zones/{zone_id}", response_model=ZoneOut)
def update_zone(zone_id: int, payload: ZoneUpdate, db: Session = Depends(get_db)) -> ZoneOut:
    zone = db.get(MonitoredZone, zone_id)
    if not zone:
        raise HTTPException(404, "Zone not found.")

    data = payload.model_dump(exclude_unset=True)
    geometry_changed = "image_polygon" in data

    if geometry_changed:
        width = data.get("image_width", zone.image_width)
        height = data.get("image_height", zone.image_height)
        for u, v in data["image_polygon"]:
            if not (0 <= u <= width - 1 and 0 <= v <= height - 1):
                raise HTTPException(422, f"Vertex ({u:.0f}, {v:.0f}) is outside the image.")
        zone.image_polygon_json = json.dumps([[float(u), float(v)] for u, v in data["image_polygon"]])
        zone.image_width = width
        zone.image_height = height

    for field in ("name", "kind", "notes"):
        if field in data:
            setattr(zone, field, data[field])

    if geometry_changed:
        _derive_world_polygon(db, zone)
        # A relationship built on this zone described the old shape.
        affected = db.scalars(select(rel.CameraRelationship).where(
            or_(rel.CameraRelationship.zone_a_id == zone.id,
                rel.CameraRelationship.zone_b_id == zone.id))).all()
        for relationship in affected:
            relationship.verification = VerificationStatus.needs_review
            relationship.review_reason = f"Zone '{zone.name}' was redrawn after this was confirmed."
        if affected:
            log.info("Zone %s changed; %d relationship(s) need review.", zone.id, len(affected))

    db.commit()
    db.refresh(zone)
    return _zone_out(zone)


@router.delete("/zones/{zone_id}", status_code=204, response_model=None)
def delete_zone(zone_id: int, db: Session = Depends(get_db)) -> None:
    zone = db.get(MonitoredZone, zone_id)
    if not zone:
        raise HTTPException(404, "Zone not found.")
    used_by = db.scalars(select(rel.CameraRelationship).where(
        or_(rel.CameraRelationship.zone_a_id == zone.id,
            rel.CameraRelationship.zone_b_id == zone.id))).all()
    if used_by:
        raise HTTPException(409, {
            "message": f"{len(used_by)} relationship(s) use this zone.",
            "required": "Remove or re-point those relationships first.",
        })
    db.delete(zone)
    db.commit()


# -- marker assistance ---------------------------------------------------

@router.get("/markers/dictionaries", response_model=dict)
def marker_dictionaries() -> dict:
    return {
        "dictionaries": sorted(marker_tools.SUPPORTED_DICTIONARIES),
        "note": ("Markers only help you find points in the picture. Where each marker is in the "
                 "building still comes from your measurements."),
    }


@router.post("/cameras/{camera_id}/detect-markers", response_model=dict)
async def detect_markers(camera_id: int, payload: DetectMarkersIn,
                         db: Session = Depends(get_db)) -> dict:
    """Detect markers in a fresh snapshot and propose matches for review."""
    import cv2

    from .cameras import _snapshot_for_target, _target

    camera = _camera(db, camera_id)
    if not camera.coordinate_system_id:
        raise HTTPException(422, {
            "message": "This camera is not part of a workspace yet.",
            "required": "Assign it to a workspace so its measured points can be matched.",
        })

    from ..media import MediaError
    try:
        content, _ = await _snapshot_for_target(_target(camera))
    except MediaError as exc:
        raise HTTPException(502, {"message": f"Could not get a picture: {exc.message}"}) from exc

    image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(502, {"message": "The snapshot could not be decoded."})

    known = [{
        "id": p.id, "code": p.code, "x": p.x, "y": p.y,
        "aruco_marker_id": p.aruco_marker_id, "aruco_corner_index": p.aruco_corner_index,
    } for p in db.scalars(select(WorldReferencePoint).where(
        WorldReferencePoint.coordinate_system_id == camera.coordinate_system_id)).all()]

    try:
        result = marker_tools.detect_markers(image, payload.dictionary, known)
    except marker_tools.MarkerError as exc:
        raise HTTPException(422, {"message": exc.message, "hint": exc.hint}) from exc

    result["camera_id"] = camera.id
    return result


@router.post("/cameras/{camera_id}/accept-markers", response_model=dict)
def accept_markers(camera_id: int, payload: AcceptMarkersIn,
                   db: Session = Depends(get_db)) -> dict:
    """Turn reviewed marker proposals into ordinary matches."""
    camera = _camera(db, camera_id)
    status = save_matches(camera.id, MatchDraftIn(
        image_width=payload.image_width, image_height=payload.image_height,
        matches=payload.accept, replace_existing=False), db)
    return {
        "accepted": len(payload.accept),
        "matches": status.calibration_matched + status.validation_matched,
        "note": "Accepted markers are now ordinary matches and can be edited or removed by hand.",
    }


# -- relationships -------------------------------------------------------

def _relationship_out(record: rel.CameraRelationship, warnings: list[str] | None = None) -> RelationshipOut:
    data = RelationshipOut.model_validate(record)
    data.camera_a_name = record.camera_a.name if record.camera_a else None
    data.camera_b_name = record.camera_b.name if record.camera_b else None
    data.zone_a_name = record.zone_a.name if record.zone_a else None
    data.zone_b_name = record.zone_b.name if record.zone_b else None
    if record.overlap_polygon_json:
        polygon = json.loads(record.overlap_polygon_json)
        data.overlap_polygon = polygon
        data.overlap_area_m2 = round(fm.polygon_area(polygon), 2)
    data.warnings = warnings or []
    return data


@router.get("/relationships", response_model=RelationshipGraphOut)
def relationship_graph(db: Session = Depends(get_db),
                       workspace_id: int | None = Query(default=None),
                       suggest: bool = Query(default=True)) -> RelationshipGraphOut:
    stmt = select(Camera)
    if workspace_id:
        stmt = stmt.where(Camera.coordinate_system_id == workspace_id)
    cameras = db.scalars(stmt.order_by(Camera.name)).all()
    camera_ids = [c.id for c in cameras]

    records = db.scalars(select(rel.CameraRelationship).where(
        rel.CameraRelationship.camera_a_id.in_(camera_ids),
        rel.CameraRelationship.camera_b_id.in_(camera_ids))).all() if camera_ids else []

    nodes = []
    coverage_inputs = []
    for camera in cameras:
        active = next((r for r in camera.calibration_revisions if r.is_active), None)
        coverage = (json.loads(active.coverage_polygon_json)
                    if active and active.coverage_polygon_json else None)
        nodes.append({
            "camera_id": camera.id,
            "name": camera.name,
            "area": camera.area.name if camera.area else None,
            "workspace_id": camera.coordinate_system_id,
            "connection_status": camera.last_test_status.value,
            "calibration_status": active.status.value if active else "unconfigured",
            "has_mapping": bool(active and active.homography_json),
            "coverage_polygon": coverage,
            "zones": [{"id": z.id, "name": z.name, "kind": z.kind.value} for z in camera.zones],
        })
        if coverage:
            coverage_inputs.append({
                "camera_id": camera.id, "name": camera.name,
                "coordinate_system_id": camera.coordinate_system_id, "coverage": coverage,
            })

    suggestions = []
    if suggest:
        stated = {frozenset((r.camera_a_id, r.camera_b_id)) for r in records}
        suggestions = [s for s in rel.suggest_overlaps(coverage_inputs)
                       if frozenset((s["camera_a_id"], s["camera_b_id"])) not in stated]

    return RelationshipGraphOut(
        cameras=nodes,
        relationships=[_relationship_out(r) for r in records],
        summary=rel.describe_graph(records, camera_ids),
        suggestions=suggestions,
    )


@router.post("/relationships", response_model=RelationshipOut, status_code=201)
def create_relationship(payload: RelationshipIn, db: Session = Depends(get_db),
                        operator: str = Depends(current_operator)) -> RelationshipOut:
    camera_a = _camera(db, payload.camera_a_id)
    camera_b = _camera(db, payload.camera_b_id)
    zone_a = db.get(MonitoredZone, payload.zone_a_id) if payload.zone_a_id else None
    zone_b = db.get(MonitoredZone, payload.zone_b_id) if payload.zone_b_id else None

    if payload.zone_a_id and zone_a is None:
        raise HTTPException(404, "The source zone does not exist.")
    if payload.zone_b_id and zone_b is None:
        raise HTTPException(404, "The destination zone does not exist.")

    existing = db.scalars(select(rel.CameraRelationship).where(
        or_(rel.CameraRelationship.camera_a_id.in_([camera_a.id, camera_b.id]),
            rel.CameraRelationship.camera_b_id.in_([camera_a.id, camera_b.id])))).all()

    try:
        warnings = rel.validate_relationship(
            payload.kind, camera_a.id, camera_b.id,
            min_travel_seconds=payload.min_travel_seconds,
            max_travel_seconds=payload.max_travel_seconds,
            zone_a=zone_a, zone_b=zone_b,
            camera_a_system=camera_a.coordinate_system_id,
            camera_b_system=camera_b.coordinate_system_id,
            existing=existing)
    except rel.RelationshipError as exc:
        raise HTTPException(422, {"message": exc.message, "hint": exc.hint}) from exc

    record = rel.CameraRelationship(
        kind=payload.kind,
        camera_a_id=camera_a.id, camera_b_id=camera_b.id,
        coordinate_system_id=camera_a.coordinate_system_id,
        zone_a_id=payload.zone_a_id, zone_b_id=payload.zone_b_id,
        overlap_polygon_json=(json.dumps(payload.overlap_polygon)
                              if payload.overlap_polygon else None),
        min_travel_seconds=payload.min_travel_seconds,
        max_travel_seconds=payload.max_travel_seconds,
        verification=payload.verification,
        verified_by=operator if payload.verification == VerificationStatus.verified else None,
        verified_at=(datetime.now(timezone.utc)
                     if payload.verification == VerificationStatus.verified else None),
        notes=payload.notes,
        created_by=operator,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return _relationship_out(record, warnings)


@router.patch("/relationships/{relationship_id}", response_model=RelationshipOut)
def update_relationship(relationship_id: int, payload: RelationshipUpdate,
                        db: Session = Depends(get_db),
                        operator: str = Depends(current_operator)) -> RelationshipOut:
    record = db.get(rel.CameraRelationship, relationship_id)
    if not record:
        raise HTTPException(404, "Relationship not found.")

    data = payload.model_dump(exclude_unset=True)
    min_t = data.get("min_travel_seconds", record.min_travel_seconds)
    max_t = data.get("max_travel_seconds", record.max_travel_seconds)
    if min_t is not None and max_t is not None and min_t > max_t:
        raise HTTPException(422, {
            "message": f"Minimum travel time ({min_t} s) exceeds the maximum ({max_t} s)."})

    for field in ("zone_a_id", "zone_b_id", "min_travel_seconds", "max_travel_seconds", "notes"):
        if field in data:
            setattr(record, field, data[field])
    if "overlap_polygon" in data:
        record.overlap_polygon_json = (json.dumps(data["overlap_polygon"])
                                       if data["overlap_polygon"] else None)
    if "verification" in data and data["verification"] is not None:
        record.verification = data["verification"]
        if data["verification"] == VerificationStatus.verified:
            record.verified_by = operator
            record.verified_at = datetime.now(timezone.utc)
            record.review_reason = None

    db.commit()
    db.refresh(record)
    return _relationship_out(record)


@router.delete("/relationships/{relationship_id}", status_code=204, response_model=None)
def delete_relationship(relationship_id: int, db: Session = Depends(get_db)) -> None:
    record = db.get(rel.CameraRelationship, relationship_id)
    if not record:
        raise HTTPException(404, "Relationship not found.")
    db.delete(record)
    db.commit()


@router.post("/relationships/accept-suggestion", response_model=RelationshipOut, status_code=201)
def accept_suggestion(payload: RelationshipIn, db: Session = Depends(get_db),
                      operator: str = Depends(current_operator)) -> RelationshipOut:
    """Turn a geometric suggestion into a record, still marked unverified.

    Geometry cannot see walls or racking, so accepting a suggestion records the
    shared area but does not claim anyone has confirmed the views really overlap.
    """
    created = create_relationship(payload, db, operator)
    record = db.get(rel.CameraRelationship, created.id)
    record.suggested_by_geometry = True
    record.verification = VerificationStatus.unverified
    db.commit()
    db.refresh(record)
    out = _relationship_out(record)
    out.warnings = ["Suggested from mapped floor areas. Confirm on site: geometry cannot see "
                    "walls, machinery or racking."]
    return out


# -- export --------------------------------------------------------------

@router.get("/export/site-geometry", response_model=dict)
def export_site_geometry(workspace_id: int = Query(...), db: Session = Depends(get_db)) -> dict:
    """Everything a tracking service needs: floor mappings plus camera topology.

    Geometry and topology only. No credentials, no host addresses, no stream
    URLs, and nothing about people.
    """
    from ..geometry import CONVENTIONS

    cs = db.get(CoordinateSystem, workspace_id)
    if not cs:
        raise HTTPException(404, "Workspace not found.")

    cameras = db.scalars(select(Camera).where(
        Camera.coordinate_system_id == cs.id).order_by(Camera.name)).all()
    camera_ids = [c.id for c in cameras]
    records = db.scalars(select(rel.CameraRelationship).where(
        rel.CameraRelationship.camera_a_id.in_(camera_ids),
        rel.CameraRelationship.camera_b_id.in_(camera_ids))).all() if camera_ids else []

    points = db.scalars(select(WorldReferencePoint).where(
        WorldReferencePoint.coordinate_system_id == cs.id)
        .order_by(WorldReferencePoint.code)).all()

    camera_payload = []
    for camera in cameras:
        active = next((r for r in camera.calibration_revisions if r.is_active), None)
        latest_validation = (active.validations[-1] if active and active.validations else None)
        entry: dict = {
            "camera_id": camera.id,
            "name": camera.name,
            "area": camera.area.name if camera.area else None,
            "calibration": None,
            "zones": [{
                "zone_id": z.id, "name": z.name, "kind": z.kind.value,
                "image_polygon": json.loads(z.image_polygon_json),
                "image_size": [z.image_width, z.image_height],
                "world_polygon": json.loads(z.world_polygon_json) if z.world_polygon_json else None,
            } for z in camera.zones],
        }
        if active and active.homography_json:
            entry["calibration"] = {
                "revision_id": active.id,
                "revision_number": active.revision_number,
                "method": active.method.value,
                "status": active.status.value,
                "stale_reason": active.stale_reason,
                "H_image_to_floor": json.loads(active.homography_json),
                "H_floor_to_image": json.loads(active.homography_inverse_json),
                "maps_to": "floor XY in metres on the plane Z = %.3f" % active.plane_z,
                "pixel_convention": active.pixel_convention.value,
                "distortion_corrected": active.distortion_corrected,
                "source_image": {"width": active.source_image_width,
                                 "height": active.source_image_height},
                "covered_area_polygon": (json.loads(active.coverage_polygon_json)
                                         if active.coverage_polygon_json else None),
                "activated_at": active.activated_at.isoformat() if active.activated_at else None,
                "accuracy": ({
                    "checked": True,
                    "passed": latest_validation.passed,
                    "held_out_mean_error_m": latest_validation.holdout_ground_error_m,
                    "held_out_max_error_m": latest_validation.holdout_max_error_m,
                    "held_out_points": latest_validation.holdout_point_count,
                    "reviewer": latest_validation.reviewer,
                    "checked_at": latest_validation.created_at.isoformat(),
                } if latest_validation else {
                    "checked": False,
                    "note": "No independent accuracy check has been recorded for this mapping.",
                }),
            }
        camera_payload.append(entry)

    return {
        "format": "numenor.site-geometry",
        "format_version": "1.0",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "conventions": {
            "world_frame": CONVENTIONS["world_frame"],
            "floor_plane": CONVENTIONS["floor_plane"],
            "homography_direction": (
                "H_image_to_floor maps homogeneous image coordinates [u, v, 1] to floor "
                "[wx, wy, w]; divide by w for metres."),
            "pixel_convention": (
                "Each mapping states whether it expects raw pixels or pixels undistorted with "
                "that camera's stored matrix. Apply the stated one; they are not interchangeable."),
        },
        "workspace": {
            "id": cs.id, "name": cs.name,
            "floor": (f"{cs.floor.building.site.name} · {cs.floor.building.name} · {cs.floor.name}"
                      if cs.floor else None),
            "origin_description": cs.origin_description,
            "x_axis_description": cs.x_axis_description,
            "surface_description": cs.surface_description,
            "definition_revision": cs.definition_revision,
            "width_m": cs.workspace_width_m, "length_m": cs.workspace_length_m,
            "floor_plane_z": cs.floor_plane_z,
            "units": "metres",
        },
        "reference_points": [{
            "id": p.id, "code": p.code, "name": p.name,
            "x": p.x, "y": p.y, "role": p.role.value,
            "uncertainty_m": p.uncertainty_m,
        } for p in points],
        "cameras": camera_payload,
        "relationships": [{
            "id": r.id, "kind": r.kind.value,
            "camera_a_id": r.camera_a_id, "camera_a_name": r.camera_a.name,
            "camera_b_id": r.camera_b_id, "camera_b_name": r.camera_b.name,
            "directed": r.kind == RelationshipKind.transition,
            "zone_a_id": r.zone_a_id, "zone_b_id": r.zone_b_id,
            "overlap_polygon": (json.loads(r.overlap_polygon_json)
                                if r.overlap_polygon_json else None),
            "min_travel_seconds": r.min_travel_seconds,
            "max_travel_seconds": r.max_travel_seconds,
            "verification": r.verification.value,
            "suggested_by_geometry": r.suggested_by_geometry,
            "notes": r.notes,
        } for r in records],
        "graph_summary": rel.describe_graph(records, camera_ids),
        "limitations": [
            "Geometry and topology only. Nothing here identifies or describes any person.",
            "A floor mapping is a flat-plane mapping. It is valid on the stated floor surface, "
            "for the stated image size, and most trustworthy inside covered_area_polygon.",
            "A mapping is not a camera pose: it does not say where a camera physically is.",
            "Covered area and overlap polygons are geometric. They take no account of walls, "
            "machinery, racking or any other occlusion.",
            "A camera pair with no relationship record is unknown, not impossible. Only an "
            "'excluded' record states that two views have no direct association, and even then "
            "travel via other cameras remains possible.",
            "Accuracy figures apply to the area the validation points cover, not the whole view.",
        ],
    }
