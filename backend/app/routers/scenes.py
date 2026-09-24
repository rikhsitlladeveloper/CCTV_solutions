"""The commissioning workspace: scene editing, visual placement, functions,
walk-through sessions and capability readiness."""
from __future__ import annotations

import io
import json
import logging
import secrets
from datetime import datetime, timezone

import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import config, scene as scene_service
from ..db import get_db
from ..geometry import CONVENTIONS, GeometryError, frustum_floor_polygon, clip_polygon_to_rect
from ..intrinsics import IntrinsicsError, intrinsics_from_fov
from ..models import (
    CalibrationMethod, CalibrationRevision, CalibrationStatus, Camera, CameraFunction,
    CameraRelationship, CommissioningSession, CoordinateSystem, GeometryProvenance, MonitoredZone,
    MountType, PixelConvention, Scene, SceneAsset, SceneAssetKind, SceneObject, SessionCheckpoint,
    WorldReferencePoint,
)
from ..positioning import intrinsics_from_record
from ..schemas_scene import (
    CheckpointIn, CheckpointOut, FunctionIn, FunctionOut, FunctionUpdate, ModelAlignIn,
    PlacementOut, PlanScaleIn, PublishIn, SceneAssetOut, SceneIn, SceneObjectBulkIn,
    SceneObjectIn, SceneObjectOut, SceneObjectUpdate, SceneOut, SceneUpdate, SessionIn,
    SessionOut, VisualPlacementIn,
)
from ..security import current_operator
from .setup import _camera, _workspace, mark_dependent_calibrations_stale

log = logging.getLogger("numenor.scenes")

router = APIRouter(prefix="/api", tags=["scene"], dependencies=[Depends(current_operator)])

ALLOWED_PLAN_TYPES = {"image/png": ".png", "image/jpeg": ".jpg"}
ALLOWED_MODEL_TYPES = {
    "model/gltf-binary": ".glb",
    "application/octet-stream": ".glb",     # browsers often send this for .glb
    "model/gltf+json": ".gltf",
}
MAX_MODEL_BYTES = 80 * 1024 * 1024


# -- helpers -------------------------------------------------------------

def _get_scene(db: Session, scene_id: int) -> Scene:
    scene = db.get(Scene, scene_id)
    if not scene:
        raise HTTPException(404, "Scene not found.")
    return scene


def _asset_out(asset: SceneAsset) -> SceneAssetOut:
    data = SceneAssetOut.model_validate(asset)
    data.url = f"/api/scenes/{asset.scene_id}/assets/{asset.id}/file"
    return data


def _scene_out(db: Session, scene: Scene) -> SceneOut:
    cs = scene.coordinate_system
    return SceneOut(
        id=scene.id,
        workspace_id=scene.coordinate_system_id,
        workspace_name=cs.name,
        name=scene.name,
        building_width_m=scene.building_width_m,
        building_length_m=scene.building_length_m,
        geometry_provenance=scene_service.scene_provenance(scene.objects),
        notes=scene.notes,
        draft_revision=scene.draft_revision,
        published_revision=scene.published_revision,
        published_at=scene.published_at,
        published_by=scene.published_by,
        has_unpublished_changes=scene.has_unpublished_changes,
        objects=[SceneObjectOut(**scene_service.object_to_dict(o)) for o in scene.objects],
        assets=[_asset_out(a) for a in scene.assets],
        grid={
            "min_x": cs.grid_min_x, "max_x": cs.grid_max_x,
            "min_y": cs.grid_min_y, "max_y": cs.grid_max_y,
            "spacing_m": cs.grid_spacing_m, "floor_z": cs.floor_plane_z,
        },
        conventions={
            "world_frame": CONVENTIONS["world_frame"],
            "units": "metres",
            "origin": cs.origin_description or "not described",
            "x_axis": cs.x_axis_description or "not described",
        },
    )


def _touch_draft(scene: Scene) -> None:
    scene.draft_revision += 1


# =====================================================================
# Scene
# =====================================================================

@router.get("/scenes", response_model=list[SceneOut])
def list_scenes(db: Session = Depends(get_db),
                workspace_id: int | None = Query(default=None)) -> list[SceneOut]:
    stmt = select(Scene)
    if workspace_id:
        stmt = stmt.where(Scene.coordinate_system_id == workspace_id)
    return [_scene_out(db, s) for s in db.scalars(stmt.order_by(Scene.name)).all()]


@router.post("/scenes", response_model=SceneOut, status_code=201)
def create_scene(payload: SceneIn, db: Session = Depends(get_db)) -> SceneOut:
    """One scene per workspace. A floor plan is never required to make one."""
    cs = _workspace(db, payload.workspace_id)
    existing = db.scalars(select(Scene)
                          .where(Scene.coordinate_system_id == cs.id)).first()
    if existing:
        raise HTTPException(409, {
            "message": f"'{cs.name}' already has the scene '{existing.name}'.",
            "scene_id": existing.id,
        })

    scene = Scene(
        coordinate_system_id=cs.id, name=payload.name,
        building_width_m=payload.building_width_m or cs.workspace_width_m,
        building_length_m=payload.building_length_m or cs.workspace_length_m,
        notes=payload.notes,
    )
    db.add(scene)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "That workspace already has a scene.")
    db.refresh(scene)
    return _scene_out(db, scene)


@router.get("/scenes/{scene_id}", response_model=SceneOut)
def read_scene(scene_id: int, db: Session = Depends(get_db)) -> SceneOut:
    return _scene_out(db, _get_scene(db, scene_id))


@router.patch("/scenes/{scene_id}", response_model=SceneOut)
def update_scene(scene_id: int, payload: SceneUpdate, db: Session = Depends(get_db)) -> SceneOut:
    scene = _get_scene(db, scene_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(scene, field, value)
    _touch_draft(scene)
    db.commit()
    db.refresh(scene)
    return _scene_out(db, scene)


@router.get("/scenes/{scene_id}/published", response_model=dict)
def read_published(scene_id: int, db: Session = Depends(get_db)) -> dict:
    """What Monitor mode sees: the last published snapshot, not the live draft."""
    scene = _get_scene(db, scene_id)
    if not scene.published_snapshot_json:
        return {
            "published": False,
            "message": "This scene has never been published, so there is nothing live yet.",
            "draft_objects": len(scene.objects),
        }
    snapshot = json.loads(scene.published_snapshot_json)
    return {
        "published": True,
        "published_at": scene.published_at.isoformat() if scene.published_at else None,
        "published_by": scene.published_by,
        "published_revision": scene.published_revision,
        "has_unpublished_changes": scene.has_unpublished_changes,
        **snapshot,
    }


@router.post("/scenes/{scene_id}/publish", response_model=SceneOut)
def publish_scene(scene_id: int, payload: PublishIn, db: Session = Depends(get_db),
                  operator: str = Depends(current_operator)) -> SceneOut:
    """Make the draft live. Until this, edits change nothing that is running."""
    scene = _get_scene(db, scene_id)
    if not payload.confirm:
        raise HTTPException(422, "Publishing must be confirmed.")
    scene.published_snapshot_json = scene_service.publish_snapshot(scene)
    scene.published_revision = scene.draft_revision
    scene.published_at = datetime.now(timezone.utc)
    scene.published_by = operator
    if payload.notes:
        scene.notes = f"{scene.notes}\n{payload.notes}" if scene.notes else payload.notes
    db.commit()
    db.refresh(scene)
    log.info("Scene %s published at revision %s by %s.", scene.id, scene.published_revision, operator)
    return _scene_out(db, scene)


# -- objects -------------------------------------------------------------

@router.post("/scenes/{scene_id}/objects", response_model=SceneObjectOut, status_code=201)
def add_object(scene_id: int, payload: SceneObjectIn,
               db: Session = Depends(get_db)) -> SceneObjectOut:
    scene = _get_scene(db, scene_id)
    obj = SceneObject(
        scene_id=scene.id, kind=payload.kind, name=payload.name,
        x=payload.x, y=payload.y, z=payload.z, rotation_deg=payload.rotation_deg,
        width_m=payload.width_m, depth_m=payload.depth_m, height_m=payload.height_m,
        points_json=json.dumps(payload.points) if payload.points else None,
        provenance=payload.provenance,
        colour=payload.colour or scene_service.PALETTE.get(payload.kind),
        notes=payload.notes,
    )
    db.add(obj)
    _touch_draft(scene)
    db.commit()
    db.refresh(obj)
    return SceneObjectOut(**scene_service.object_to_dict(obj))


@router.put("/scenes/{scene_id}/objects", response_model=SceneOut)
def replace_objects(scene_id: int, payload: SceneObjectBulkIn,
                    db: Session = Depends(get_db)) -> SceneOut:
    """Save the whole working set. The editor keeps undo/redo on the client and
    writes the resulting state here, so a draft is never half-applied."""
    scene = _get_scene(db, scene_id)
    for existing in list(scene.objects):
        db.delete(existing)
    db.flush()
    db.refresh(scene)

    for item in payload.objects:
        db.add(SceneObject(
            scene_id=scene.id, kind=item.kind, name=item.name,
            x=item.x, y=item.y, z=item.z, rotation_deg=item.rotation_deg,
            width_m=item.width_m, depth_m=item.depth_m, height_m=item.height_m,
            points_json=json.dumps(item.points) if item.points else None,
            provenance=item.provenance,
            colour=item.colour or scene_service.PALETTE.get(item.kind),
            notes=item.notes,
        ))
    _touch_draft(scene)
    db.commit()
    db.refresh(scene)
    return _scene_out(db, scene)


@router.patch("/scenes/{scene_id}/objects/{object_id}", response_model=SceneObjectOut)
def update_object(scene_id: int, object_id: int, payload: SceneObjectUpdate,
                  db: Session = Depends(get_db)) -> SceneObjectOut:
    scene = _get_scene(db, scene_id)
    obj = db.get(SceneObject, object_id)
    if not obj or obj.scene_id != scene.id:
        raise HTTPException(404, "Scene object not found.")

    data = payload.model_dump(exclude_unset=True)
    if "points" in data:
        obj.points_json = json.dumps(data.pop("points")) if data["points"] else None
    for field, value in data.items():
        setattr(obj, field, value)
    _touch_draft(scene)
    db.commit()
    db.refresh(obj)
    return SceneObjectOut(**scene_service.object_to_dict(obj))


@router.delete("/scenes/{scene_id}/objects/{object_id}", status_code=204, response_model=None)
def delete_object(scene_id: int, object_id: int, db: Session = Depends(get_db)) -> None:
    scene = _get_scene(db, scene_id)
    obj = db.get(SceneObject, object_id)
    if not obj or obj.scene_id != scene.id:
        raise HTTPException(404, "Scene object not found.")
    db.delete(obj)
    _touch_draft(scene)
    db.commit()


@router.get("/scene-palette", response_model=dict)
def palette() -> dict:
    """The object types an installer can drop, with sensible starting sizes."""
    return {
        "items": [
            {
                "kind": kind.value,
                "label": kind.value.replace("_", " ").capitalize(),
                "default_width_m": size[0],
                "default_depth_m": size[1],
                "default_height_m": size[2],
                "colour": scene_service.PALETTE[kind],
                "shape": ("outline" if kind in scene_service.POLYGONAL_KINDS
                          else "run" if kind in scene_service.LINEAR_KINDS else "box"),
            }
            for kind, size in scene_service.DEFAULT_SIZES.items()
        ],
        "note": ("Dimensions start as estimates. Mark an object as measured once you have "
                 "actually measured it — only measured objects count as survey geometry."),
    }


# -- assets --------------------------------------------------------------

@router.post("/scenes/{scene_id}/assets", response_model=SceneAssetOut, status_code=201)
async def upload_asset(scene_id: int, kind: SceneAssetKind = Form(...),
                       file: UploadFile = File(...),
                       db: Session = Depends(get_db)) -> SceneAssetOut:
    """A floor-plan image or a GLB/glTF model. Neither is required."""
    scene = _get_scene(db, scene_id)
    content_type = (file.content_type or "").lower()
    filename = file.filename or "asset"

    if kind == SceneAssetKind.floor_plan:
        suffix = ALLOWED_PLAN_TYPES.get(content_type)
        if not suffix:
            raise HTTPException(415, "Floor plans must be PNG or JPEG.")
        payload = await file.read(config.MAX_UPLOAD_BYTES + 1)
        if len(payload) > config.MAX_UPLOAD_BYTES:
            raise HTTPException(413, "That image is larger than the upload limit.")
        from PIL import Image, UnidentifiedImageError
        try:
            with Image.open(io.BytesIO(payload)) as img:
                img.verify()
            with Image.open(io.BytesIO(payload)) as img:
                width, height = img.size
                fmt = (img.format or "").lower()
        except (UnidentifiedImageError, OSError) as exc:
            raise HTTPException(415, "That file is not a readable PNG or JPEG.") from exc
        if fmt not in ("png", "jpeg"):
            raise HTTPException(415, "Floor plans must be PNG or JPEG.")
        stored_type = "image/png" if suffix == ".png" else "image/jpeg"
    else:
        lowered = filename.lower()
        if not (lowered.endswith(".glb") or lowered.endswith(".gltf")):
            raise HTTPException(415, {
                "message": "3D models must be GLB or glTF.",
                "hint": "Other CAD formats are not supported. Export to GLB from your CAD tool.",
            })
        if content_type not in ALLOWED_MODEL_TYPES and content_type not in ("", "application/json"):
            raise HTTPException(415, "3D models must be GLB or glTF.")
        payload = await file.read(MAX_MODEL_BYTES + 1)
        if len(payload) > MAX_MODEL_BYTES:
            raise HTTPException(413, "That model is larger than the 80 MB limit.")
        if lowered.endswith(".glb") and payload[:4] != b"glTF":
            raise HTTPException(415, "That file does not look like a GLB — its header is wrong.")
        suffix = ".glb" if lowered.endswith(".glb") else ".gltf"
        stored_type = "model/gltf-binary" if suffix == ".glb" else "model/gltf+json"
        width = height = None

    stored = f"scene-{scene.id}-{secrets.token_hex(8)}{suffix}"
    (config.FLOORPLAN_DIR / stored).write_bytes(payload)

    # One asset of each kind per scene; a new one replaces the old.
    for old in [a for a in scene.assets if a.kind == kind]:
        (config.FLOORPLAN_DIR / old.file_path).unlink(missing_ok=True)
        db.delete(old)
    db.flush()

    asset = SceneAsset(
        scene_id=scene.id, kind=kind, file_path=stored, original_filename=filename,
        content_type=stored_type, size_bytes=len(payload),
        width_px=width, height_px=height,
        is_reference_only=(kind == SceneAssetKind.model_3d),
        notes=("Imported geometry is reference only: nothing extracts editable objects from a "
               "mesh, so it is never treated as measured."
               if kind == SceneAssetKind.model_3d else None),
    )
    db.add(asset)
    _touch_draft(scene)
    db.commit()
    db.refresh(asset)
    return _asset_out(asset)


@router.get("/scenes/{scene_id}/assets/{asset_id}/file")
def asset_file(scene_id: int, asset_id: int, db: Session = Depends(get_db)) -> Response:
    asset = db.get(SceneAsset, asset_id)
    if not asset or asset.scene_id != scene_id:
        raise HTTPException(404, "Asset not found.")
    path = config.FLOORPLAN_DIR / asset.file_path
    if not path.is_file():
        raise HTTPException(404, "The asset file is missing.")
    return Response(content=path.read_bytes(), media_type=asset.content_type,
                    headers={"Cache-Control": "private, max-age=300"})


@router.put("/scenes/{scene_id}/assets/{asset_id}/scale", response_model=SceneAssetOut)
def set_plan_scale(scene_id: int, asset_id: int, payload: PlanScaleIn,
                   db: Session = Depends(get_db)) -> SceneAssetOut:
    """Two points on the plan plus their measured separation gives the scale."""
    scene = _get_scene(db, scene_id)
    asset = db.get(SceneAsset, asset_id)
    if not asset or asset.scene_id != scene.id:
        raise HTTPException(404, "Asset not found.")
    if asset.kind != SceneAssetKind.floor_plan:
        raise HTTPException(422, "Only a floor-plan image is scaled this way.")

    pixel_distance = float(np.hypot(payload.point_b[0] - payload.point_a[0],
                                    payload.point_b[1] - payload.point_a[1]))
    if pixel_distance < 5:
        raise HTTPException(422, {
            "message": "Those two points are too close together to give a reliable scale.",
            "hint": "Pick two points far apart on the plan — opposite walls, for instance.",
        })

    asset.metres_per_pixel = payload.distance_m / pixel_distance
    asset.origin_x = payload.origin_x
    asset.origin_y = payload.origin_y
    asset.rotation_deg = payload.rotation_deg
    _touch_draft(scene)
    db.commit()
    db.refresh(asset)
    return _asset_out(asset)


@router.put("/scenes/{scene_id}/assets/{asset_id}/model-alignment", response_model=SceneAssetOut)
def align_model(scene_id: int, asset_id: int, payload: ModelAlignIn,
                db: Session = Depends(get_db)) -> SceneAssetOut:
    scene = _get_scene(db, scene_id)
    asset = db.get(SceneAsset, asset_id)
    if not asset or asset.scene_id != scene.id:
        raise HTTPException(404, "Asset not found.")
    if asset.kind != SceneAssetKind.model_3d:
        raise HTTPException(422, "Only an imported model is aligned this way.")

    asset.model_scale = payload.scale
    asset.model_up_axis = payload.up_axis
    asset.model_floor_offset_m = payload.floor_offset_m
    asset.model_rotation_deg = payload.rotation_deg
    _touch_draft(scene)
    db.commit()
    db.refresh(asset)
    return _asset_out(asset)


@router.delete("/scenes/{scene_id}/assets/{asset_id}", status_code=204, response_model=None)
def delete_asset(scene_id: int, asset_id: int, db: Session = Depends(get_db)) -> None:
    scene = _get_scene(db, scene_id)
    asset = db.get(SceneAsset, asset_id)
    if not asset or asset.scene_id != scene.id:
        raise HTTPException(404, "Asset not found.")
    (config.FLOORPLAN_DIR / asset.file_path).unlink(missing_ok=True)
    db.delete(asset)
    _touch_draft(scene)
    db.commit()


# =====================================================================
# Visual placement and aiming
# =====================================================================

@router.post("/cameras/{camera_id}/place", response_model=PlacementOut, status_code=201)
def place_camera(camera_id: int, payload: VisualPlacementIn,
                 db: Session = Depends(get_db),
                 operator: str = Depends(current_operator)) -> PlacementOut:
    """Drop a camera into the scene and aim it at a point.

    Orientation is derived from the drop position, the mounting height and the
    aim target, so the installer never types an angle. The result is stored as an
    **approximate** pose: it organises the scene, it is not a measurement.
    """
    camera = _camera(db, camera_id)
    cs = _workspace(db, payload.workspace_id)

    if camera.coordinate_system_id and camera.coordinate_system_id != cs.id:
        mark_dependent_calibrations_stale(
            db, f"The camera moved into the scene '{cs.name}'; its earlier geometry belongs to "
                "another area.", camera_ids=[camera.id])
    camera.coordinate_system_id = cs.id

    placement = scene_service.VisualPlacement(
        x=payload.x, y=payload.y, height_m=payload.height_m,
        target_x=payload.target_x, target_y=payload.target_y, target_z=payload.target_z,
        roll_deg=payload.roll_deg, mount_type=payload.mount_type,
    )
    try:
        pose = scene_service.pose_from_visual_placement(placement)
    except (scene_service.SceneError, GeometryError) as exc:
        raise HTTPException(422, {"message": getattr(exc, "message", str(exc)),
                                  "hint": getattr(exc, "hint", None)}) from exc

    warnings: list[str] = []

    # A real calibration must never be silently replaced by a drag.
    active = next((r for r in camera.calibration_revisions if r.is_active), None)
    if active is not None and active.method != CalibrationMethod.manual:
        warnings.append(
            f"This camera already has a solved {active.method.value} calibration. The new "
            "placement is saved as a separate revision and is NOT activated, so the solved "
            "geometry keeps working.")

    activate = payload.activate and not (active and active.method != CalibrationMethod.manual)

    # Which field of view is being drawn, and where it came from.
    intrinsics_record = next((i for i in camera.intrinsics_sets if i.is_active), None)
    fov_source = "none"
    hfov = payload.illustrative_hfov_deg
    vfov = None
    if intrinsics_record is not None:
        try:
            intr = intrinsics_from_record(intrinsics_record)
            hfov, vfov = intr.fov_degrees
            fov_source = "measured_intrinsics"
        except (IntrinsicsError, GeometryError):
            pass
    if fov_source == "none" and hfov:
        fov_source = "illustrative"
        warnings.append(
            "The cone drawn on the scene uses the field of view you typed, which is an "
            "illustration only. It is not measured intrinsics, and it does not touch the "
            "camera's own zoom.")

    highest = db.scalar(select(CalibrationRevision.revision_number)
                        .where(CalibrationRevision.camera_id == camera.id)
                        .order_by(CalibrationRevision.revision_number.desc()).limit(1))
    q = pose.quaternion
    revision = CalibrationRevision(
        camera_id=camera.id, coordinate_system_id=cs.id,
        coordinate_system_revision=cs.definition_revision,
        revision_number=(highest or 0) + 1,
        method=CalibrationMethod.manual,
        status=CalibrationStatus.approximate,
        position_x=float(pose.position[0]), position_y=float(pose.position[1]),
        position_z=float(pose.position[2]),
        quat_w=float(q[0]), quat_x=float(q[1]), quat_y=float(q[2]), quat_z=float(q[3]),
        plane_z=cs.floor_plane_z,
        pixel_convention=PixelConvention.raw,
        source_image_width=intrinsics_record.width if intrinsics_record else None,
        source_image_height=intrinsics_record.height if intrinsics_record else None,
        intrinsics_id=intrinsics_record.id if intrinsics_record else None,
        approx_hfov_deg=hfov, approx_vfov_deg=vfov,
        approx_range_m=payload.illustrative_range_m,
        mount_type=payload.mount_type.value,
        aim_target_x=payload.target_x, aim_target_y=payload.target_y,
        aim_target_z=payload.target_z,
        solver="visual placement",
        warnings_json=json.dumps([
            "Placed and aimed by hand in the scene editor. The position and direction are "
            "approximate; nothing here is a measurement.", *warnings,
        ]),
        notes=payload.notes, created_by=operator,
    )
    db.add(revision)
    db.flush()

    if activate:
        for other in camera.calibration_revisions:
            if other.id != revision.id:
                other.is_active = False
        revision.is_active = True
        revision.activated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(revision)

    # Draw where the view meets the floor, when a lens model allows it.
    polygon = None
    clipped = False
    intr = None
    if intrinsics_record is not None:
        try:
            intr = intrinsics_from_record(intrinsics_record)
        except (IntrinsicsError, GeometryError):
            intr = None
    if intr is None and hfov:
        intr = intrinsics_from_fov(1920, 1080, hfov)
    if intr is not None:
        poly, clipped = frustum_floor_polygon(
            pose, intr, plane_z=cs.floor_plane_z,
            max_distance_m=payload.illustrative_range_m or 40.0)
        if poly:
            poly, trimmed = clip_polygon_to_rect(poly, cs.grid_min_x, cs.grid_min_y,
                                                 cs.grid_max_x, cs.grid_max_y)
            clipped = clipped or trimmed
        if len(poly) >= 3:
            polygon = [[round(x, 3), round(y, 3)] for x, y in poly]

    return PlacementOut(
        camera_id=camera.id,
        revision_id=revision.id,
        revision_number=revision.revision_number,
        status=revision.status.value,
        position={"x": float(pose.position[0]), "y": float(pose.position[1]),
                  "z": float(pose.position[2])},
        aim=scene_service.describe_aim(pose, placement),
        frustum_floor_polygon=polygon,
        frustum_clipped=clipped,
        is_approximate=True,
        fov_source=fov_source,
        activated=bool(activate),
        warnings=warnings,
    )


# =====================================================================
# Camera functions
# =====================================================================

def _function_out(function: CameraFunction, camera: Camera) -> FunctionOut:
    return FunctionOut(
        id=function.id, camera_id=function.camera_id, kind=function.kind,
        label=scene_service.FUNCTION_LABELS[function.kind],
        name=function.name, enabled=function.enabled, space=function.space,
        config=json.loads(function.config_json or "{}"),
        image_width=function.image_width, image_height=function.image_height,
        zone_id=function.zone_id, scene_object_id=function.scene_object_id,
        notes=function.notes,
        status=scene_service.function_status(function, camera),
        updated_at=function.updated_at,
    )


@router.get("/function-catalogue", response_model=dict)
def function_catalogue() -> dict:
    """What a camera can be asked to do, and whether anything can do it here."""
    from ..models import FunctionKind

    return {
        "functions": [
            {
                "kind": kind.value,
                "label": scene_service.FUNCTION_LABELS[kind],
                "processing_available": kind in scene_service.AVAILABLE_PROCESSING,
                "requires_floor_mapping": kind in scene_service.NEEDS_FLOOR_MAPPING,
                "configuration": {
                    FunctionKind.people_counting: "Draw a line and choose which way to count.",
                    FunctionKind.product_counting: "Draw a line and choose which way to count.",
                    FunctionKind.restricted_zone: "Draw the area to watch.",
                    FunctionKind.ppe_monitoring: "Draw the area and choose the required PPE.",
                    FunctionKind.workstation_occupancy: "Draw the area and link it to a workstation.",
                    FunctionKind.twin_positions: "Needs a validated floor mapping for this camera.",
                    FunctionKind.cross_camera_tracking: "Needs camera relationships and a tracking service.",
                }[kind],
                "space": ("world" if kind in scene_service.NEEDS_FLOOR_MAPPING else "image"),
            }
            for kind in FunctionKind
        ],
        "note": ("Counting and zone monitoring work on the picture and need no 3D calibration. "
                 "Only functions that report positions on the factory floor need a mapping."),
        "processing_note": ("No detection service is connected to this deployment. Settings are "
                            "stored and exported; nothing is analysing video."),
    }


@router.get("/cameras/{camera_id}/functions", response_model=list[FunctionOut])
def list_functions(camera_id: int, db: Session = Depends(get_db)) -> list[FunctionOut]:
    camera = _camera(db, camera_id)
    return [_function_out(f, camera) for f in camera.functions]


@router.post("/cameras/{camera_id}/functions", response_model=FunctionOut, status_code=201)
def add_function(camera_id: int, payload: FunctionIn,
                 db: Session = Depends(get_db)) -> FunctionOut:
    """Configure what a camera should do. Never blocked by missing calibration:
    picture-space counting and zone watching work with no geometry at all."""
    camera = _camera(db, camera_id)

    if payload.zone_id:
        zone = db.get(MonitoredZone, payload.zone_id)
        if not zone or zone.camera_id != camera.id:
            raise HTTPException(404, "That zone does not belong to this camera.")
    if payload.scene_object_id and not db.get(SceneObject, payload.scene_object_id):
        raise HTTPException(404, "Scene object not found.")

    function = CameraFunction(
        camera_id=camera.id, kind=payload.kind, name=payload.name,
        enabled=payload.enabled, space=payload.space,
        config_json=json.dumps(payload.config),
        image_width=payload.image_width, image_height=payload.image_height,
        zone_id=payload.zone_id, scene_object_id=payload.scene_object_id,
        notes=payload.notes,
    )
    db.add(function)
    db.commit()
    db.refresh(function)
    return _function_out(function, camera)


@router.patch("/functions/{function_id}", response_model=FunctionOut)
def update_function(function_id: int, payload: FunctionUpdate,
                    db: Session = Depends(get_db)) -> FunctionOut:
    function = db.get(CameraFunction, function_id)
    if not function:
        raise HTTPException(404, "Function not found.")
    data = payload.model_dump(exclude_unset=True)
    if "config" in data and data["config"] is not None:
        function.config_json = json.dumps(data.pop("config"))
    for field, value in data.items():
        setattr(function, field, value)
    db.commit()
    db.refresh(function)
    return _function_out(function, function.camera)


@router.delete("/functions/{function_id}", status_code=204, response_model=None)
def delete_function(function_id: int, db: Session = Depends(get_db)) -> None:
    function = db.get(CameraFunction, function_id)
    if not function:
        raise HTTPException(404, "Function not found.")
    db.delete(function)
    db.commit()


# =====================================================================
# Walk-through sessions
# =====================================================================

def _session_out(db: Session, session: CommissioningSession) -> SessionOut:
    checkpoints = []
    for cp in session.checkpoints:
        item = CheckpointOut.model_validate(cp)
        if cp.camera_id:
            camera = db.get(Camera, cp.camera_id)
            item.camera_name = camera.name if camera else None
        checkpoints.append(item)

    measured = [c for c in session.checkpoints if c.error_m is not None]
    accuracy: dict = {
        "checkpoints": len(session.checkpoints),
        "with_measured_position": len(measured),
    }
    if measured:
        errors = [c.error_m for c in measured]
        accuracy.update({
            "mean_error_m": round(float(np.mean(errors)), 4),
            "max_error_m": round(float(np.max(errors)), 4),
            "interpretation": (
                "Measured against positions you surveyed, so these are real errors over the "
                "points you checked."),
        })
    else:
        accuracy["interpretation"] = (
            "No checkpoint has an independently measured position, so this session records "
            "observations rather than accuracy. A trajectory that looks smooth is not evidence "
            "that it is correct.")
    return SessionOut(
        id=session.id, workspace_id=session.coordinate_system_id, name=session.name,
        camera_ids=json.loads(session.camera_ids_json or "[]"),
        started_at=session.started_at, ended_at=session.ended_at,
        operator=session.operator, notes=session.notes,
        checkpoints=checkpoints, accuracy=accuracy,
    )


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(db: Session = Depends(get_db),
                  workspace_id: int | None = Query(default=None)) -> list[SessionOut]:
    stmt = select(CommissioningSession)
    if workspace_id:
        stmt = stmt.where(CommissioningSession.coordinate_system_id == workspace_id)
    return [_session_out(db, s) for s in
            db.scalars(stmt.order_by(CommissioningSession.started_at.desc())).all()]


@router.post("/sessions", response_model=SessionOut, status_code=201)
def start_session(payload: SessionIn, db: Session = Depends(get_db),
                  operator: str = Depends(current_operator)) -> SessionOut:
    cs = _workspace(db, payload.workspace_id)
    for camera_id in payload.camera_ids:
        if not db.get(Camera, camera_id):
            raise HTTPException(404, f"Camera {camera_id} not found.")
    session = CommissioningSession(
        coordinate_system_id=cs.id, name=payload.name,
        camera_ids_json=json.dumps(payload.camera_ids),
        operator=operator, notes=payload.notes,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return _session_out(db, session)


@router.post("/sessions/{session_id}/checkpoints", response_model=SessionOut, status_code=201)
def add_checkpoint(session_id: int, payload: CheckpointIn,
                   db: Session = Depends(get_db)) -> SessionOut:
    """Record what was seen, and where. A picture point is projected onto the
    floor when the camera has a mapping; an error figure appears only when the
    installer supplies an independently measured position."""
    session = db.get(CommissioningSession, session_id)
    if not session:
        raise HTTPException(404, "Session not found.")

    projected_x = projected_y = error = None
    if payload.camera_id and payload.pixel_u is not None:
        camera = _camera(db, payload.camera_id)
        from .setup import ProjectPointIn, project_point
        try:
            result = project_point(camera.id, ProjectPointIn(
                pixel_u=payload.pixel_u, pixel_v=payload.pixel_v), db)
            projected_x = result["floor"]["x"]
            projected_y = result["floor"]["y"]
        except HTTPException:
            projected_x = projected_y = None      # no mapping; the note still stands

    if (projected_x is not None and payload.measured_x is not None):
        error = float(np.hypot(projected_x - payload.measured_x,
                               projected_y - payload.measured_y))

    db.add(SessionCheckpoint(
        session_id=session.id, camera_id=payload.camera_id, label=payload.label,
        pixel_u=payload.pixel_u, pixel_v=payload.pixel_v,
        image_width=payload.image_width, image_height=payload.image_height,
        projected_x=projected_x, projected_y=projected_y,
        measured_x=payload.measured_x, measured_y=payload.measured_y,
        error_m=error, observation=payload.observation,
    ))
    db.commit()
    db.refresh(session)
    return _session_out(db, session)


@router.post("/sessions/{session_id}/end", response_model=SessionOut)
def end_session(session_id: int, db: Session = Depends(get_db)) -> SessionOut:
    session = db.get(CommissioningSession, session_id)
    if not session:
        raise HTTPException(404, "Session not found.")
    session.ended_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(session)
    return _session_out(db, session)


@router.delete("/sessions/{session_id}", status_code=204, response_model=None)
def delete_session(session_id: int, db: Session = Depends(get_db)) -> None:
    session = db.get(CommissioningSession, session_id)
    if not session:
        raise HTTPException(404, "Session not found.")
    db.delete(session)
    db.commit()


# =====================================================================
# Readiness
# =====================================================================

@router.get("/readiness", response_model=dict)
def readiness(workspace_id: int = Query(...), db: Session = Depends(get_db)) -> dict:
    """Per-capability status, with the next useful action for each."""
    cs = _workspace(db, workspace_id)
    cameras = db.scalars(select(Camera)
                         .where(Camera.coordinate_system_id == cs.id)
                         .order_by(Camera.name)).all()
    camera_ids = [c.id for c in cameras]
    relationships = db.scalars(select(CameraRelationship).where(
        CameraRelationship.camera_a_id.in_(camera_ids),
        CameraRelationship.camera_b_id.in_(camera_ids))).all() if camera_ids else []
    scene = db.scalars(select(Scene).where(Scene.coordinate_system_id == cs.id)).first()
    points = db.scalar(select(WorldReferencePoint)
                       .where(WorldReferencePoint.coordinate_system_id == cs.id)
                       .with_only_columns(WorldReferencePoint.id).limit(1))
    point_count = len(db.scalars(select(WorldReferencePoint.id).where(
        WorldReferencePoint.coordinate_system_id == cs.id)).all())
    void = points

    result = scene_service.assess_readiness(cameras, scene, relationships, point_count)
    result["workspace"] = {"id": cs.id, "name": cs.name}
    result["scene"] = {"id": scene.id, "name": scene.name,
                       "published": scene.published_revision is not None,
                       "has_unpublished_changes": scene.has_unpublished_changes} if scene else None
    result["simulation_note"] = (
        "This is a spatial model of the factory. Simulating throughput would additionally need "
        "cycle times, routing, capacities and machine-state behaviour, none of which this system "
        "holds. Placing cameras does not make simulation possible."
    )
    return result
