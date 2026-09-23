"""The shared factory map and the multi-camera geometry check."""
from __future__ import annotations

import logging

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..geometry import CONVENTIONS, clip_polygon_to_rect, frustum_floor_polygon
from ..intrinsics import intrinsics_from_fov
import json

from ..models import (
    CalibrationMethod, CalibrationStatus, Camera, CoordinateSystem, FloorPlan, WorldReferencePoint,
)
from ..positioning import NotProjectable, build_projection_model, compare_ground_observations
from ..schemas_calibration import (
    CoordinateSystemOut, FactoryMapOut, FloorPlanAlignmentIn, MapCameraOut,
    MultiCameraCheckIn, ReferencePointOut,
)
from ..security import current_operator
from .coordinates import _out as coordinate_system_out

log = logging.getLogger("numenor.factorymap")

router = APIRouter(prefix="/api/factory-map", tags=["factory-map"],
                   dependencies=[Depends(current_operator)])


def calibration_status_for(camera: Camera) -> CalibrationStatus:
    revision = camera.active_calibration
    return revision.status if revision else CalibrationStatus.unconfigured


def _map_camera(camera: Camera, footprints: bool,
                cs: CoordinateSystem | None = None) -> MapCameraOut:
    revision = camera.active_calibration
    entry = MapCameraOut(
        camera_id=camera.id,
        name=camera.name,
        calibration_status=calibration_status_for(camera),
        connection_status=camera.last_test_status.value,
        area=camera.area.name if camera.area else None,
        method=revision.method if revision else None,
        revision_id=revision.id if revision else None,
        is_approximate=True,
    )
    if revision is None:
        return entry

    model = build_projection_model(revision)
    entry.is_approximate = model.is_approximate
    entry.approx_hfov_deg = revision.approx_hfov_deg
    entry.approx_range_m = revision.approx_range_m

    if model.pose is not None:
        roll, pitch, yaw = model.pose.rpy_degrees
        entry.position = {"x": float(model.pose.position[0]),
                          "y": float(model.pose.position[1]),
                          "z": float(model.pose.position[2])}
        entry.rpy_deg = {"roll": roll, "pitch": pitch, "yaw": yaw}
        entry.map_heading_deg = model.pose.map_heading_deg

    if model.intrinsics is not None:
        entry.horizontal_fov_deg, entry.vertical_fov_deg = model.intrinsics.fov_degrees

    # A camera mapped by floor homography has no pose, but it does know which
    # patch of floor its measured points cover. That area is what to draw: it is
    # where the mapping works, not where the camera hangs.
    if footprints and model.pose is None and revision.coverage_polygon_json:
        polygon = json.loads(revision.coverage_polygon_json)
        if cs is not None and polygon:
            polygon, trimmed = clip_polygon_to_rect(
                polygon, cs.grid_min_x, cs.grid_min_y, cs.grid_max_x, cs.grid_max_y)
            entry.floor_polygon_clipped = trimmed
        if len(polygon) >= 3:
            entry.floor_polygon = [[round(x, 3), round(y, 3)] for x, y in polygon]
            entry.area_is_mapped_coverage = True
        return entry

    # A projected view footprint needs both a pose and a lens model.
    if footprints and model.pose is not None:
        intr = model.intrinsics
        if intr is None and revision.approx_hfov_deg:
            intr = intrinsics_from_fov(revision.source_image_width or 1920,
                                       revision.source_image_height or 1080,
                                       revision.approx_hfov_deg)
        if intr is not None:
            max_range = revision.approx_range_m or 60.0
            polygon, clipped = frustum_floor_polygon(
                model.pose, intr, plane_z=revision.plane_z, max_distance_m=max_range)
            if cs is not None and polygon:
                # A shallow sight line can reach far outside the declared factory
                # area; keep the drawing inside the frame the grid describes.
                polygon, trimmed = clip_polygon_to_rect(
                    polygon, cs.grid_min_x, cs.grid_min_y, cs.grid_max_x, cs.grid_max_y)
                clipped = clipped or trimmed
            if len(polygon) >= 3:
                entry.floor_polygon = [[round(x, 3), round(y, 3)] for x, y in polygon]
                entry.floor_polygon_clipped = clipped
    return entry


@router.get("/{system_id}", response_model=FactoryMapOut)
def factory_map(system_id: int, db: Session = Depends(get_db),
                footprints: bool = Query(default=True)) -> FactoryMapOut:
    cs = db.get(CoordinateSystem, system_id)
    if not cs:
        raise HTTPException(404, "Coordinate system not found.")

    cameras = db.scalars(select(Camera)
                         .where(Camera.coordinate_system_id == cs.id)
                         .order_by(Camera.name)).all()
    points = db.scalars(select(WorldReferencePoint)
                        .where(WorldReferencePoint.coordinate_system_id == cs.id)
                        .order_by(WorldReferencePoint.code)).all()

    plan = db.scalars(select(FloorPlan)
                      .where(FloorPlan.world_coordinate_system_id == cs.id)
                      .limit(1)).first()
    plan_payload = None
    if plan and plan.world_metres_per_pixel:
        plan_payload = {
            "floor_plan_id": plan.id,
            "image_url": f"/api/floor-plans/{plan.id}/image?v={plan.version}",
            "width_px": plan.width_px,
            "height_px": plan.height_px,
            "metres_per_pixel": plan.world_metres_per_pixel,
            "origin_x": plan.world_origin_x,
            "origin_y": plan.world_origin_y,
            "rotation_deg": plan.world_rotation_deg or 0.0,
            "note": ("The plan is a backdrop aligned into world coordinates. Camera positions are "
                     "stored in metres and do not change when this image is replaced or realigned."),
        }

    return FactoryMapOut(
        coordinate_system=coordinate_system_out(db, cs),
        cameras=[_map_camera(c, footprints, cs) for c in cameras],
        reference_points=[ReferencePointOut.model_validate(p) for p in points],
        floor_plan=plan_payload,
        conventions=dict(CONVENTIONS),
    )


@router.post("/check-ground-point", response_model=dict)
def check_ground_point(payload: MultiCameraCheckIn, db: Session = Depends(get_db)) -> dict:
    """Project the same physical ground point from several cameras and compare.

    This measures agreement between calibrations, not accuracy.
    """
    cs = db.get(CoordinateSystem, payload.coordinate_system_id)
    if not cs:
        raise HTTPException(404, "Coordinate system not found.")

    entries = []
    for mark in payload.marks:
        camera = db.get(Camera, mark.camera_id)
        if not camera:
            raise HTTPException(404, f"Camera {mark.camera_id} not found.")
        if camera.coordinate_system_id != cs.id:
            raise HTTPException(422, {
                "message": f"Camera '{camera.name}' is not positioned in this coordinate system, so "
                           "its projections cannot be compared with the others.",
                "camera_id": camera.id,
            })

        entry: dict = {"camera_id": camera.id, "camera_name": camera.name, "world": None,
                       "pixel": [mark.pixel_u, mark.pixel_v]}
        revision = camera.active_calibration
        if revision is None:
            entry["error"] = "No active calibration."
            entries.append(entry)
            continue

        model = build_projection_model(revision)
        entry["calibration_status"] = revision.status.value
        entry["method"] = revision.method.value
        entry["is_approximate"] = model.is_approximate
        try:
            projected = model.image_to_floor(mark.pixel_u, mark.pixel_v, payload.plane_z)
            entry["world"] = projected["world"]
            entry["distance_from_camera_m"] = projected["distance_from_camera_m"]
        except NotProjectable as exc:
            entry["error"] = str(exc)
        entries.append(entry)

    result = compare_ground_observations(entries)
    result["label"] = payload.label
    result["plane_z"] = payload.plane_z
    result["coordinate_system"] = {"id": cs.id, "name": cs.name}
    if any(e.get("is_approximate") for e in entries if e.get("world")):
        result["warning"] = (
            "At least one camera in this comparison uses a hand-entered placement, so its world "
            "position is an approximation. Disagreement figures mix approximate and solved "
            "geometry."
        )
    return result


@router.get("/{system_id}/coverage", response_model=dict)
def coverage(system_id: int, db: Session = Depends(get_db)) -> dict:
    """Geometric floor coverage for every camera with usable geometry."""
    cs = db.get(CoordinateSystem, system_id)
    if not cs:
        raise HTTPException(404, "Coordinate system not found.")

    cameras = db.scalars(select(Camera)
                         .where(Camera.coordinate_system_id == cs.id)
                         .order_by(Camera.name)).all()
    entries = [_map_camera(c, footprints=True, cs=cs) for c in cameras]
    with_footprint = [e for e in entries if e.floor_polygon]

    total_area = 0.0
    for entry in with_footprint:
        polygon = np.array(entry.floor_polygon)
        if len(polygon) >= 3:      # shoelace
            x, y = polygon[:, 0], polygon[:, 1]
            total_area += float(abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1))) / 2.0)

    return {
        "coordinate_system": {"id": cs.id, "name": cs.name},
        "cameras": [e.model_dump() for e in entries],
        "cameras_with_footprint": len(with_footprint),
        "cameras_without_footprint": len(entries) - len(with_footprint),
        "sum_of_footprint_areas_m2": round(total_area, 2),
        "interpretation": (
            "These footprints are pure geometry: where each camera's image border meets the floor "
            "plane. They take no account of machinery, racking, walls, people or any other "
            "occlusion, and a clipped footprint means part of the view points above the horizon. "
            "Treat this as geometric coverage, not as a guarantee that anything in the area is "
            "actually visible. Overlapping areas are counted once per camera, so the sum is not "
            "the covered floor area."
        ),
    }


@router.put("/floor-plans/{plan_id}/alignment", response_model=dict)
def align_floor_plan(plan_id: int, payload: FloorPlanAlignmentIn,
                     db: Session = Depends(get_db)) -> dict:
    """Place a floor-plan image inside a world frame, as a backdrop only."""
    plan = db.get(FloorPlan, plan_id)
    if not plan:
        raise HTTPException(404, "Floor plan not found.")
    cs = db.get(CoordinateSystem, payload.coordinate_system_id)
    if not cs:
        raise HTTPException(404, "Coordinate system not found.")

    plan.world_coordinate_system_id = cs.id
    plan.world_metres_per_pixel = payload.metres_per_pixel
    plan.world_origin_x = payload.origin_x
    plan.world_origin_y = payload.origin_y
    plan.world_rotation_deg = payload.rotation_deg
    db.commit()

    return {
        "floor_plan_id": plan.id,
        "coordinate_system_id": cs.id,
        "metres_per_pixel": plan.world_metres_per_pixel,
        "origin_x": plan.world_origin_x,
        "origin_y": plan.world_origin_y,
        "rotation_deg": plan.world_rotation_deg,
        "note": ("Alignment positions the picture only. No camera position, reference point or "
                 "calibration was changed: world coordinates are independent of this image."),
    }
