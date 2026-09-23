"""Floor-plan upload, retrieval, scale and replacement.

Marker positions are stored as normalized coordinates, so a floor plan can be
re-rendered at any size without moving a camera.  Replacing the image is the one
operation that can invalidate placements, so it flags every existing placement
on that plan for human review instead of silently carrying it over.
"""
from __future__ import annotations

import io
import logging
import secrets

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..db import get_db
from ..models import Camera, Floor, FloorPlan, ReviewStatus
from ..schemas import CameraOut, FloorPlanOut, ScaleIn
from ..security import current_operator
from ..serializers import camera_out, floor_plan_out

log = logging.getLogger("numenor.floorplans")

router = APIRouter(prefix="/api/floor-plans", tags=["floor-plans"],
                   dependencies=[Depends(current_operator)])

ALLOWED_TYPES = {"image/png": ".png", "image/jpeg": ".jpg"}


def _get_plan(db: Session, plan_id: int) -> FloorPlan:
    plan = db.get(FloorPlan, plan_id)
    if not plan:
        raise HTTPException(404, "Floor plan not found.")
    return plan


async def _read_image(file: UploadFile) -> tuple[bytes, str, int, int]:
    suffix = ALLOWED_TYPES.get((file.content_type or "").lower())
    if not suffix:
        raise HTTPException(415, "Floor plans must be PNG or JPEG images.")
    payload = await file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(payload) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "That image is larger than the upload limit.")
    try:
        with Image.open(io.BytesIO(payload)) as img:
            img.verify()
        with Image.open(io.BytesIO(payload)) as img:
            width, height = img.size
            fmt = (img.format or "").lower()
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(415, "That file is not a readable PNG or JPEG image.") from exc
    if fmt not in ("png", "jpeg"):
        raise HTTPException(415, "Floor plans must be PNG or JPEG images.")
    if width < 50 or height < 50:
        raise HTTPException(422, "That image is too small to use as a floor plan.")
    return payload, suffix, width, height


@router.get("", response_model=list[FloorPlanOut])
def list_plans(db: Session = Depends(get_db),
               floor_id: int | None = Query(default=None)) -> list[FloorPlanOut]:
    stmt = select(FloorPlan)
    if floor_id:
        stmt = stmt.where(FloorPlan.floor_id == floor_id)
    return [floor_plan_out(p) for p in db.scalars(stmt).all()]


@router.post("", response_model=FloorPlanOut, status_code=201)
async def upload_plan(floor_id: int = Form(...), file: UploadFile = File(...),
                      db: Session = Depends(get_db)) -> FloorPlanOut:
    """Upload a plan for a floor, or replace the floor's existing plan."""
    floor = db.get(Floor, floor_id)
    if not floor:
        raise HTTPException(404, "Floor not found.")

    payload, suffix, width, height = await _read_image(file)
    filename = f"floor-{floor_id}-{secrets.token_hex(8)}{suffix}"
    (config.FLOORPLAN_DIR / filename).write_bytes(payload)
    content_type = "image/png" if suffix == ".png" else "image/jpeg"

    plan = floor.floor_plan
    if plan is None:
        plan = FloorPlan(floor_id=floor_id, image_path=filename,
                         original_filename=file.filename or filename,
                         content_type=content_type, width_px=width, height_px=height)
        db.add(plan)
    else:
        # Replacing the image: existing markers may no longer line up.
        old = plan.image_path
        plan.image_path = filename
        plan.original_filename = file.filename or filename
        plan.content_type = content_type
        plan.width_px = width
        plan.height_px = height
        plan.version += 1
        # Scale was measured against the previous image; it no longer applies.
        plan.scale_px_per_metre = None
        plan.scale_point_a_x = plan.scale_point_a_y = None
        plan.scale_point_b_x = plan.scale_point_b_y = None
        plan.scale_distance_m = None
        for placement in plan.placements:
            placement.review_status = ReviewStatus.needs_review
        if old and old != filename:
            (config.FLOORPLAN_DIR / old).unlink(missing_ok=True)
        log.info("Floor plan %s replaced; %d placement(s) flagged for review.",
                 plan.id, len(plan.placements))

    db.commit()
    db.refresh(plan)
    return floor_plan_out(plan)


@router.get("/{plan_id}", response_model=FloorPlanOut)
def get_plan(plan_id: int, db: Session = Depends(get_db)) -> FloorPlanOut:
    return floor_plan_out(_get_plan(db, plan_id))


@router.get("/{plan_id}/image")
def get_plan_image(plan_id: int, db: Session = Depends(get_db)) -> Response:
    plan = _get_plan(db, plan_id)
    path = config.FLOORPLAN_DIR / plan.image_path
    if not path.is_file():
        raise HTTPException(404, "The floor-plan image file is missing.")
    return Response(content=path.read_bytes(), media_type=plan.content_type,
                    headers={"Cache-Control": "private, max-age=300"})


@router.get("/{plan_id}/cameras", response_model=list[CameraOut])
def plan_cameras(plan_id: int, db: Session = Depends(get_db)) -> list[CameraOut]:
    """Cameras placed on this plan."""
    plan = _get_plan(db, plan_id)
    ids = [p.camera_id for p in plan.placements]
    if not ids:
        return []
    cameras = db.scalars(select(Camera).where(Camera.id.in_(ids)).order_by(Camera.name)).all()
    return [camera_out(c) for c in cameras]


@router.put("/{plan_id}/scale", response_model=FloorPlanOut)
def set_scale(plan_id: int, payload: ScaleIn, db: Session = Depends(get_db)) -> FloorPlanOut:
    """Set map scale from two points and the real distance between them."""
    plan = _get_plan(db, plan_id)
    dx = (payload.point_b_x - payload.point_a_x) * plan.width_px
    dy = (payload.point_b_y - payload.point_a_y) * plan.height_px
    pixel_distance = (dx * dx + dy * dy) ** 0.5
    if pixel_distance < 5:
        raise HTTPException(422, "Those two points are too close together to derive a scale.")
    plan.scale_px_per_metre = pixel_distance / payload.distance_m
    plan.scale_point_a_x = payload.point_a_x
    plan.scale_point_a_y = payload.point_a_y
    plan.scale_point_b_x = payload.point_b_x
    plan.scale_point_b_y = payload.point_b_y
    plan.scale_distance_m = payload.distance_m
    db.commit()
    db.refresh(plan)
    return floor_plan_out(plan)


@router.delete("/{plan_id}/scale", response_model=FloorPlanOut)
def clear_scale(plan_id: int, db: Session = Depends(get_db)) -> FloorPlanOut:
    plan = _get_plan(db, plan_id)
    plan.scale_px_per_metre = None
    plan.scale_point_a_x = plan.scale_point_a_y = None
    plan.scale_point_b_x = plan.scale_point_b_y = None
    plan.scale_distance_m = None
    db.commit()
    db.refresh(plan)
    return floor_plan_out(plan)


@router.delete("/{plan_id}", status_code=204, response_model=None)
def delete_plan(plan_id: int, db: Session = Depends(get_db)) -> None:
    plan = _get_plan(db, plan_id)
    (config.FLOORPLAN_DIR / plan.image_path).unlink(missing_ok=True)
    db.delete(plan)
    db.commit()
