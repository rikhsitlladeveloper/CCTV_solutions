"""Camera positioning: intrinsics, observations, solving, validation, projection."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import numpy as np
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import calibration as calib
from ..db import get_db
from ..geometry import CONVENTIONS, GeometryError, Pose, orthonormalise, rpy_to_matrix
from ..intrinsics import Intrinsics, IntrinsicsError
from ..models import (
    CalibrationMethod, CalibrationRevision, CalibrationStatus, Camera, CameraIntrinsics,
    CoordinateSystem, ObservationRole, PixelConvention, PointObservation, ValidationResult,
    WorldReferencePoint,
)
from ..positioning import (
    NotProjectable, build_projection_model, export_calibration, intrinsics_from_record,
    validate_revision,
)
from ..schemas_calibration import (
    ActivateIn, CheckerboardConfig, HomographySolveIn, ImageToWorldIn, IntrinsicsImport,
    IntrinsicsOut, ManualPoseIn, ObservationBulkIn, ObservationIn, ObservationOut,
    PoseAdjustIn, PoseSolveIn, ReferencePointOut, RevisionOut, ValidationIn, WorldToImageIn,
)
from ..security import current_operator

log = logging.getLogger("numenor.calibration")

router = APIRouter(prefix="/api/cameras/{camera_id}", tags=["calibration"],
                   dependencies=[Depends(current_operator)])


# -- helpers -------------------------------------------------------------

def get_camera(db: Session, camera_id: int) -> Camera:
    camera = db.get(Camera, camera_id)
    if not camera:
        raise HTTPException(404, "Camera not found.")
    return camera


def get_system(db: Session, system_id: int) -> CoordinateSystem:
    cs = db.get(CoordinateSystem, system_id)
    if not cs:
        raise HTTPException(404, "Coordinate system not found.")
    return cs


def _intrinsics_out(record: CameraIntrinsics) -> IntrinsicsOut:
    data = IntrinsicsOut.model_validate(record)
    data.camera_matrix = json.loads(record.camera_matrix_json)
    data.distortion_coefficients = json.loads(record.distortion_json)
    data.crop = json.loads(record.crop_json) if record.crop_json else None
    try:
        intr = intrinsics_from_record(record)
        data.geometry_key = intr.geometry_key
        data.horizontal_fov_deg, data.vertical_fov_deg = intr.fov_degrees
    except (IntrinsicsError, GeometryError):
        data.geometry_key = f"{record.width}x{record.height}"
    return data


def revision_out(revision: CalibrationRevision) -> RevisionOut:
    data = RevisionOut.model_validate(revision)
    model = build_projection_model(revision)
    if model.pose is not None:
        roll, pitch, yaw = model.pose.rpy_degrees
        data.position = {"x": float(model.pose.position[0]),
                         "y": float(model.pose.position[1]),
                         "z": float(model.pose.position[2])}
        data.rpy_deg = {"roll": roll, "pitch": pitch, "yaw": yaw}
        data.quaternion_wxyz = model.pose.quaternion.tolist()
        data.T_world_camera = model.pose.T_world_camera.tolist()
        data.map_heading_deg = model.pose.map_heading_deg
    data.has_homography = revision.homography_json is not None
    data.metrics = json.loads(revision.metrics_json) if revision.metrics_json else None
    data.warnings = json.loads(revision.warnings_json) if revision.warnings_json else []
    data.caveats = model.caveats
    if revision.validations:
        latest = revision.validations[-1]
        data.latest_validation = {
            "passed": latest.passed,
            "fit_reprojection_error_px": latest.fit_reprojection_error_px,
            "holdout_ground_error_m": latest.holdout_ground_error_m,
            "holdout_max_error_m": latest.holdout_max_error_m,
            "holdout_point_count": latest.holdout_point_count,
            "fit_point_count": latest.fit_point_count,
            "inlier_count": latest.inlier_count,
            "outlier_count": latest.outlier_count,
            "reviewer": latest.reviewer,
            "created_at": latest.created_at.isoformat(),
        }
    return data


def _next_revision_number(db: Session, camera_id: int) -> int:
    highest = db.scalar(select(CalibrationRevision.revision_number)
                        .where(CalibrationRevision.camera_id == camera_id)
                        .order_by(CalibrationRevision.revision_number.desc()).limit(1))
    return (highest or 0) + 1


def _activate(db: Session, revision: CalibrationRevision) -> None:
    """Make one revision active. Previous ones are kept, never overwritten."""
    others = db.scalars(select(CalibrationRevision).where(
        CalibrationRevision.camera_id == revision.camera_id,
        CalibrationRevision.id != revision.id)).all()
    for other in others:
        other.is_active = False
    revision.is_active = True
    revision.activated_at = datetime.now(timezone.utc)


def _active_intrinsics_record(camera: Camera) -> CameraIntrinsics | None:
    return next((i for i in camera.intrinsics_sets if i.is_active), None)


def _bind_camera_to_system(camera: Camera, cs: CoordinateSystem) -> None:
    """Moving a camera between frames invalidates calibrations in the old one."""
    if camera.coordinate_system_id and camera.coordinate_system_id != cs.id:
        for revision in camera.calibration_revisions:
            if revision.is_active and revision.coordinate_system_id != cs.id:
                revision.status = CalibrationStatus.needs_recalibration
                note = ("The camera was moved to a different coordinate system; this calibration "
                        "belongs to the previous frame.")
                revision.notes = f"{revision.notes}\n{note}" if revision.notes else note
    camera.coordinate_system_id = cs.id


def _observations_for(camera: Camera, cs: CoordinateSystem,
                      holdout_ids: list[int]) -> tuple[list[PointObservation], list[int]]:
    obs = [o for o in camera.observations
           if o.reference_point.coordinate_system_id == cs.id]
    if not obs:
        raise HTTPException(422, {
            "message": "This camera has no reference-point observations in this coordinate system.",
            "required": "Mark at least four points in the camera image and link them to measured "
                        "reference points first.",
        })
    obs.sort(key=lambda o: o.id)
    holdout = set(holdout_ids)
    for o in obs:
        if o.role == ObservationRole.holdout:
            holdout.add(o.reference_point_id)
    holdout_positions = [i for i, o in enumerate(obs) if o.reference_point_id in holdout]
    return obs, holdout_positions


# =====================================================================
# Intrinsics
# =====================================================================

@router.get("/intrinsics", response_model=list[IntrinsicsOut])
def list_intrinsics(camera_id: int, db: Session = Depends(get_db)) -> list[IntrinsicsOut]:
    camera = get_camera(db, camera_id)
    return [_intrinsics_out(i) for i in camera.intrinsics_sets]


@router.post("/intrinsics", response_model=IntrinsicsOut, status_code=201)
def import_intrinsics(camera_id: int, payload: IntrinsicsImport,
                      db: Session = Depends(get_db)) -> IntrinsicsOut:
    """Import a validated JSON calibration. Rejected if it is not a usable pinhole model."""
    camera = get_camera(db, camera_id)
    flat = payload.camera_matrix
    K = np.array(flat, dtype=float).reshape(3, 3)

    try:
        intr = Intrinsics.from_payload(
            camera_matrix=K, distortion=payload.distortion_coefficients,
            width=payload.image_width, height=payload.image_height,
            model=payload.model, rotation_deg=payload.image_rotation_deg,
            crop=payload.crop, source="import", notes=payload.notes,
        )
    except IntrinsicsError as exc:
        raise HTTPException(422, str(exc)) from exc

    record = CameraIntrinsics(
        camera_id=camera.id,
        label=payload.label,
        model=intr.model,
        camera_matrix_json=json.dumps(intr.camera_matrix.tolist()),
        distortion_json=json.dumps(intr.distortion.tolist()),
        width=intr.width, height=intr.height,
        image_rotation_deg=intr.rotation_deg,
        crop_json=json.dumps(list(intr.crop)) if intr.crop else None,
        lens_description=payload.lens_description,
        zoom_state=payload.zoom_state,
        source="import",
        rms_reprojection_error_px=payload.rms_reprojection_error_px,
        calibrated_at=payload.calibrated_at,
        notes=payload.notes,
    )
    db.add(record)
    db.flush()
    if payload.activate:
        for other in camera.intrinsics_sets:
            other.is_active = False
        record.is_active = True
    db.commit()
    db.refresh(record)
    return _intrinsics_out(record)


@router.post("/intrinsics/checkerboard", response_model=dict)
async def checkerboard_calibration(
    camera_id: int,
    inner_cols: int = Query(ge=3, le=40),
    inner_rows: int = Query(ge=3, le=40),
    square_size_m: float = Query(gt=0.001, le=1.0),
    label: str = Query(default="Checkerboard calibration"),
    activate: bool = Query(default=False),
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
) -> dict:
    """Guided checkerboard calibration from uploaded images."""
    import cv2

    camera = get_camera(db, camera_id)
    config = CheckerboardConfig(inner_cols=inner_cols, inner_rows=inner_rows,
                                square_size_m=square_size_m, label=label, activate=activate)

    if len(files) > 60:
        raise HTTPException(413, "At most 60 calibration images can be processed at once.")

    captures = []
    per_image = []
    for upload in files:
        payload = await upload.read(20 * 1024 * 1024)
        buffer = np.frombuffer(payload, dtype=np.uint8)
        image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
        if image is None:
            per_image.append({"name": upload.filename, "detected": False,
                              "reason": "Not a readable image."})
            continue
        try:
            capture = calib.detect_checkerboard(image, config.inner_cols, config.inner_rows,
                                                name=upload.filename or "frame")
        except calib.CalibrationError as exc:
            per_image.append({"name": upload.filename, "detected": False, "reason": str(exc)})
            continue
        if capture is None:
            per_image.append({
                "name": upload.filename, "detected": False,
                "reason": f"No {config.inner_cols}x{config.inner_rows} checkerboard found. Check the "
                          "inner-corner counts and that the whole board is visible and in focus.",
            })
            continue
        captures.append(capture)
        per_image.append({"name": upload.filename, "detected": True,
                          "resolution": f"{capture.width}x{capture.height}"})

    try:
        result = calib.calibrate_intrinsics_from_checkerboard(
            captures, config.inner_cols, config.inner_rows, config.square_size_m)
    except calib.CalibrationError as exc:
        raise HTTPException(422, {"message": str(exc), "per_image": per_image}) from exc

    intr: Intrinsics = result["intrinsics"]
    record = CameraIntrinsics(
        camera_id=camera.id, label=config.label, model=intr.model,
        camera_matrix_json=json.dumps(intr.camera_matrix.tolist()),
        distortion_json=json.dumps(intr.distortion.tolist()),
        width=intr.width, height=intr.height,
        source="checkerboard",
        rms_reprojection_error_px=result["rms_reprojection_error_px"],
        view_count=len(captures),
        quality_json=json.dumps({"per_view": result["per_view"],
                                 "pose_diversity": result["pose_diversity"]}),
        calibrated_at=datetime.now(timezone.utc),
        lens_description=config.lens_description,
        zoom_state=config.zoom_state,
        notes=intr.notes,
    )
    db.add(record)
    db.flush()
    if config.activate:
        for other in camera.intrinsics_sets:
            other.is_active = False
        record.is_active = True
    db.commit()
    db.refresh(record)

    return {
        "intrinsics": _intrinsics_out(record).model_dump(),
        "rms_reprojection_error_px": result["rms_reprojection_error_px"],
        "per_view": result["per_view"],
        "per_image": per_image,
        "pose_diversity": result["pose_diversity"],
        "warnings": result["warnings"],
        "note": ("Reprojection error measures how well the model fits its own calibration images. "
                 "It is not a measure of positioning accuracy in the factory."),
    }


@router.post("/intrinsics/{intrinsics_id}/activate", response_model=IntrinsicsOut)
def activate_intrinsics(camera_id: int, intrinsics_id: int,
                        db: Session = Depends(get_db)) -> IntrinsicsOut:
    camera = get_camera(db, camera_id)
    record = db.get(CameraIntrinsics, intrinsics_id)
    if not record or record.camera_id != camera.id:
        raise HTTPException(404, "Intrinsics not found for this camera.")
    for other in camera.intrinsics_sets:
        other.is_active = False
    record.is_active = True
    db.commit()
    db.refresh(record)
    return _intrinsics_out(record)


@router.delete("/intrinsics/{intrinsics_id}", status_code=204, response_model=None)
def delete_intrinsics(camera_id: int, intrinsics_id: int, db: Session = Depends(get_db)) -> None:
    camera = get_camera(db, camera_id)
    record = db.get(CameraIntrinsics, intrinsics_id)
    if not record or record.camera_id != camera.id:
        raise HTTPException(404, "Intrinsics not found for this camera.")
    used_by = db.scalar(select(CalibrationRevision.id).where(
        CalibrationRevision.intrinsics_id == record.id,
        CalibrationRevision.is_active.is_(True)).limit(1))
    if used_by:
        raise HTTPException(409, "An active calibration uses these intrinsics.")
    db.delete(record)
    db.commit()


# =====================================================================
# Observations
# =====================================================================

@router.get("/observations", response_model=list[ObservationOut])
def list_observations(camera_id: int, db: Session = Depends(get_db)) -> list[ObservationOut]:
    camera = get_camera(db, camera_id)
    out = []
    for obs in sorted(camera.observations, key=lambda o: o.id):
        item = ObservationOut.model_validate(obs)
        item.reference_point = ReferencePointOut.model_validate(obs.reference_point)
        out.append(item)
    return out


@router.put("/observations", response_model=list[ObservationOut])
def save_observations(camera_id: int, payload: ObservationBulkIn,
                      db: Session = Depends(get_db)) -> list[ObservationOut]:
    camera = get_camera(db, camera_id)

    point_ids = {o.reference_point_id for o in payload.observations}
    points = {p.id: p for p in db.scalars(
        select(WorldReferencePoint).where(WorldReferencePoint.id.in_(point_ids))).all()}
    missing = point_ids - set(points)
    if missing:
        raise HTTPException(404, f"Reference point(s) not found: {sorted(missing)}.")

    systems = {points[i].coordinate_system_id for i in point_ids}
    if len(systems) > 1:
        raise HTTPException(422, {
            "message": "All observations for one camera must reference points in a single "
                       "coordinate system.",
            "coordinate_systems": sorted(systems),
        })

    if payload.replace_existing:
        for existing in list(camera.observations):
            db.delete(existing)
        db.flush()
        # The relationship still holds the deleted rows until it is reloaded.
        # Reusing one of those as an "existing" record would attach the update to
        # a doomed object, and the save would silently do nothing.
        db.refresh(camera)

    existing_by_point = {o.reference_point_id: o for o in camera.observations}
    for item in payload.observations:
        record = existing_by_point.get(item.reference_point_id)
        if record is None:
            record = PointObservation(camera_id=camera.id,
                                      reference_point_id=item.reference_point_id)
            db.add(record)
        record.pixel_u = item.pixel_u
        record.pixel_v = item.pixel_v
        record.image_width = item.image_width
        record.image_height = item.image_height
        record.role = item.role
        record.source = item.source
        record.notes = item.notes

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Each reference point can be observed once per camera.")
    db.refresh(camera)
    return list_observations(camera_id, db)


@router.delete("/observations/{observation_id}", status_code=204, response_model=None)
def delete_observation(camera_id: int, observation_id: int, db: Session = Depends(get_db)) -> None:
    camera = get_camera(db, camera_id)
    record = db.get(PointObservation, observation_id)
    if not record or record.camera_id != camera.id:
        raise HTTPException(404, "Observation not found for this camera.")
    db.delete(record)
    db.commit()


# =====================================================================
# Solving
# =====================================================================

@router.get("/calibration/revisions", response_model=list[RevisionOut])
def list_revisions(camera_id: int, db: Session = Depends(get_db)) -> list[RevisionOut]:
    camera = get_camera(db, camera_id)
    return [revision_out(r) for r in
            sorted(camera.calibration_revisions, key=lambda r: r.revision_number, reverse=True)]


@router.post("/calibration/manual", response_model=RevisionOut, status_code=201)
def save_manual_pose(camera_id: int, payload: ManualPoseIn,
                     db: Session = Depends(get_db),
                     operator: str = Depends(current_operator)) -> RevisionOut:
    """Save a hand-entered placement. Always recorded as approximate."""
    camera = get_camera(db, camera_id)
    cs = get_system(db, payload.coordinate_system_id)
    _bind_camera_to_system(camera, cs)

    try:
        R = rpy_to_matrix(payload.roll_deg, payload.pitch_deg, payload.yaw_deg)
        pose = Pose(orthonormalise(R), np.array([payload.x, payload.y, payload.z], dtype=float))
    except GeometryError as exc:
        raise HTTPException(422, str(exc)) from exc

    q = pose.quaternion
    revision = CalibrationRevision(
        camera_id=camera.id,
        coordinate_system_id=cs.id,
        coordinate_system_revision=cs.definition_revision,
        revision_number=_next_revision_number(db, camera.id),
        method=CalibrationMethod.manual,
        status=CalibrationStatus.approximate,
        position_x=payload.x, position_y=payload.y, position_z=payload.z,
        quat_w=float(q[0]), quat_x=float(q[1]), quat_y=float(q[2]), quat_z=float(q[3]),
        plane_z=cs.floor_plane_z,
        pixel_convention=PixelConvention.raw,
        source_image_width=payload.source_image_width,
        source_image_height=payload.source_image_height,
        approx_hfov_deg=payload.approx_hfov_deg,
        approx_vfov_deg=payload.approx_vfov_deg,
        approx_range_m=payload.approx_range_m,
        solver="manual entry",
        warnings_json=json.dumps([
            "Entered by hand and not solved against measured points. Any world coordinate derived "
            "from this placement is approximate."
        ]),
        notes=payload.notes,
        created_by=operator,
    )
    db.add(revision)
    db.flush()
    if payload.activate:
        _activate(db, revision)
    db.commit()
    db.refresh(revision)
    return revision_out(revision)


@router.post("/calibration/homography", response_model=RevisionOut, status_code=201)
def solve_homography(camera_id: int, payload: HomographySolveIn,
                     db: Session = Depends(get_db),
                     operator: str = Depends(current_operator)) -> RevisionOut:
    """Fit an image-to-floor homography. Produces no camera pose, by design."""
    camera = get_camera(db, camera_id)
    cs = get_system(db, payload.coordinate_system_id)
    _bind_camera_to_system(camera, cs)

    observations, holdout_positions = _observations_for(
        camera, cs, payload.holdout_reference_point_ids)

    mismatched = [o.id for o in observations
                  if (o.image_width, o.image_height) != (payload.image_width, payload.image_height)]
    if mismatched:
        raise HTTPException(422, {
            "message": f"{len(mismatched)} observation(s) were marked on a different image size "
                       f"than the {payload.image_width}x{payload.image_height} being solved for. "
                       "Pixel coordinates are not comparable across image geometries.",
            "observation_ids": mismatched,
        })

    image_points = np.array([[o.pixel_u, o.pixel_v] for o in observations], dtype=float)
    floor_points = np.array([[o.reference_point.x, o.reference_point.y] for o in observations],
                            dtype=float)

    intrinsics_record = _active_intrinsics_record(camera) if payload.use_intrinsics else None
    intr = None
    if intrinsics_record is not None:
        try:
            intr = intrinsics_from_record(intrinsics_record)
            intr.require_matches(payload.image_width, payload.image_height)
        except (IntrinsicsError, GeometryError) as exc:
            raise HTTPException(422, str(exc)) from exc

    try:
        result = calib.solve_floor_homography(
            image_points, floor_points, payload.image_width, payload.image_height,
            intrinsics=intr, plane_z=payload.plane_z,
            ransac_threshold_px=payload.ransac_threshold_px,
            holdout_indices=holdout_positions,
        )
    except calib.CalibrationError as exc:
        raise HTTPException(422, str(exc)) from exc

    revision = CalibrationRevision(
        camera_id=camera.id,
        coordinate_system_id=cs.id,
        coordinate_system_revision=cs.definition_revision,
        revision_number=_next_revision_number(db, camera.id),
        method=CalibrationMethod.homography,
        status=CalibrationStatus.calibrated_unvalidated,
        homography_json=json.dumps(result.H_image_to_floor.tolist()),
        homography_inverse_json=json.dumps(result.H_floor_to_image.tolist()),
        plane_z=payload.plane_z,
        pixel_convention=PixelConvention.undistorted if result.pixel_convention == calib.PIXELS_UNDISTORTED
        else PixelConvention.raw,
        distortion_corrected=result.distortion_corrected,
        source_image_width=payload.image_width,
        source_image_height=payload.image_height,
        intrinsics_id=intrinsics_record.id if intrinsics_record else None,
        solver=result.method,
        metrics_json=json.dumps(result.to_dict()),
        warnings_json=json.dumps(result.warnings),
        notes=payload.notes,
        created_by=operator,
    )
    db.add(revision)
    db.flush()
    if payload.activate:
        _activate(db, revision)
    db.commit()
    db.refresh(revision)
    return revision_out(revision)


@router.post("/calibration/pose", response_model=RevisionOut, status_code=201)
def solve_pose(camera_id: int, payload: PoseSolveIn,
               db: Session = Depends(get_db),
               operator: str = Depends(current_operator)) -> RevisionOut:
    """Recover a full 6-DoF camera pose with solvePnP. Requires active intrinsics."""
    camera = get_camera(db, camera_id)
    cs = get_system(db, payload.coordinate_system_id)
    _bind_camera_to_system(camera, cs)

    intrinsics_record = _active_intrinsics_record(camera)
    if intrinsics_record is None:
        raise HTTPException(422, {
            "message": "Full pose calibration needs camera intrinsics, which this camera does not "
                       "have. A pose cannot be recovered from reference points alone.",
            "required": "Import a calibration JSON or run the checkerboard workflow, then retry. "
                        "For floor-only tracking, a floor-plane homography needs no intrinsics.",
        })

    try:
        intr = intrinsics_from_record(intrinsics_record)
        intr.require_matches(payload.image_width, payload.image_height)
    except (IntrinsicsError, GeometryError) as exc:
        raise HTTPException(422, str(exc)) from exc

    observations, holdout_positions = _observations_for(
        camera, cs, payload.holdout_reference_point_ids)

    mismatched = [o.id for o in observations
                  if (o.image_width, o.image_height) != (payload.image_width, payload.image_height)]
    if mismatched:
        raise HTTPException(422, {
            "message": f"{len(mismatched)} observation(s) were marked on a different image size "
                       f"than the {payload.image_width}x{payload.image_height} being solved for.",
            "observation_ids": mismatched,
        })

    world = np.array([[o.reference_point.x, o.reference_point.y, o.reference_point.z]
                      for o in observations], dtype=float)
    pixels = np.array([[o.pixel_u, o.pixel_v] for o in observations], dtype=float)

    try:
        result = calib.solve_camera_pose(
            world, pixels, intr, payload.image_width, payload.image_height,
            use_ransac=payload.use_ransac,
            reprojection_threshold_px=payload.reprojection_threshold_px,
            holdout_indices=holdout_positions,
        )
    except calib.CalibrationError as exc:
        raise HTTPException(422, str(exc)) from exc

    q = result.pose.quaternion
    revision = CalibrationRevision(
        camera_id=camera.id,
        coordinate_system_id=cs.id,
        coordinate_system_revision=cs.definition_revision,
        revision_number=_next_revision_number(db, camera.id),
        method=CalibrationMethod.pnp,
        status=CalibrationStatus.calibrated_unvalidated,
        position_x=float(result.pose.position[0]),
        position_y=float(result.pose.position[1]),
        position_z=float(result.pose.position[2]),
        quat_w=float(q[0]), quat_x=float(q[1]), quat_y=float(q[2]), quat_z=float(q[3]),
        plane_z=cs.floor_plane_z,
        pixel_convention=PixelConvention.raw,
        distortion_corrected=True,        # solvePnP models distortion explicitly
        source_image_width=payload.image_width,
        source_image_height=payload.image_height,
        intrinsics_id=intrinsics_record.id,
        solver=result.solver,
        metrics_json=json.dumps(result.to_dict()),
        warnings_json=json.dumps(result.warnings),
        notes=payload.notes,
        created_by=operator,
    )
    db.add(revision)
    db.flush()
    if payload.activate:
        _activate(db, revision)
    db.commit()
    db.refresh(revision)
    return revision_out(revision)


@router.post("/calibration/revisions/{revision_id}/adjust", response_model=RevisionOut, status_code=201)
def adjust_pose(camera_id: int, revision_id: int, payload: PoseAdjustIn,
                db: Session = Depends(get_db),
                operator: str = Depends(current_operator)) -> RevisionOut:
    """Hand-correct a solved pose. Stored as a new, unvalidated revision."""
    camera = get_camera(db, camera_id)
    parent = db.get(CalibrationRevision, revision_id)
    if not parent or parent.camera_id != camera.id:
        raise HTTPException(404, "Calibration revision not found for this camera.")

    try:
        R = rpy_to_matrix(payload.roll_deg, payload.pitch_deg, payload.yaw_deg)
        pose = Pose(orthonormalise(R), np.array([payload.x, payload.y, payload.z], dtype=float))
    except GeometryError as exc:
        raise HTTPException(422, str(exc)) from exc

    q = pose.quaternion
    revision = CalibrationRevision(
        camera_id=camera.id,
        coordinate_system_id=parent.coordinate_system_id,
        coordinate_system_revision=parent.coordinate_system_revision,
        revision_number=_next_revision_number(db, camera.id),
        parent_revision_id=parent.id,
        method=CalibrationMethod.manual,
        status=CalibrationStatus.approximate,
        position_x=payload.x, position_y=payload.y, position_z=payload.z,
        quat_w=float(q[0]), quat_x=float(q[1]), quat_y=float(q[2]), quat_z=float(q[3]),
        plane_z=parent.plane_z,
        pixel_convention=parent.pixel_convention,
        source_image_width=parent.source_image_width,
        source_image_height=parent.source_image_height,
        intrinsics_id=parent.intrinsics_id,
        approx_hfov_deg=parent.approx_hfov_deg,
        approx_vfov_deg=parent.approx_vfov_deg,
        solver=f"manual adjustment of revision {parent.revision_number}",
        warnings_json=json.dumps([
            f"Hand-adjusted from solved revision {parent.revision_number}. The solver's validation "
            "no longer applies: this revision is unvalidated until it is checked again.",
            f"Reason given: {payload.reason}",
        ]),
        notes=payload.reason,
        created_by=operator,
    )
    db.add(revision)
    db.commit()          # deliberately NOT activated
    db.refresh(revision)
    return revision_out(revision)


@router.post("/calibration/revisions/{revision_id}/activate", response_model=RevisionOut)
def activate_revision(camera_id: int, revision_id: int, payload: ActivateIn,
                      db: Session = Depends(get_db)) -> RevisionOut:
    camera = get_camera(db, camera_id)
    revision = db.get(CalibrationRevision, revision_id)
    if not revision or revision.camera_id != camera.id:
        raise HTTPException(404, "Calibration revision not found for this camera.")
    if not payload.confirm:
        raise HTTPException(422, "Activation must be confirmed.")
    _activate(db, revision)
    if payload.notes:
        revision.notes = f"{revision.notes}\n{payload.notes}" if revision.notes else payload.notes
    db.commit()
    db.refresh(revision)
    log.info("Camera %s: activated calibration revision %s (%s).",
             camera.id, revision.revision_number, revision.method.value)
    return revision_out(revision)


@router.post("/calibration/revisions/{revision_id}/validate", response_model=dict)
def validate_calibration(camera_id: int, revision_id: int, payload: ValidationIn,
                         db: Session = Depends(get_db),
                         operator: str = Depends(current_operator)) -> dict:
    """Check a revision against held-out reference points and the site thresholds."""
    camera = get_camera(db, camera_id)
    revision = db.get(CalibrationRevision, revision_id)
    if not revision or revision.camera_id != camera.id:
        raise HTTPException(404, "Calibration revision not found for this camera.")
    cs = get_system(db, revision.coordinate_system_id)

    outcome = validate_revision(revision, camera, cs)

    record = ValidationResult(
        revision_id=revision.id,
        fit_reprojection_error_px=outcome.fit_reprojection_error_px,
        holdout_ground_error_m=outcome.holdout_ground_error_m,
        holdout_max_error_m=outcome.holdout_max_error_m,
        holdout_point_count=outcome.holdout_point_count,
        fit_point_count=outcome.fit_point_count,
        inlier_count=outcome.inlier_count,
        outlier_count=outcome.outlier_count,
        passed=outcome.passed,
        thresholds_json=json.dumps(outcome.thresholds),
        details_json=json.dumps(outcome.per_point),
        reviewer=payload.reviewer or operator,
        notes=payload.notes,
    )
    db.add(record)

    if outcome.passed:
        revision.status = CalibrationStatus.validated
    elif revision.method != CalibrationMethod.manual:
        revision.status = CalibrationStatus.calibrated_unvalidated

    db.commit()
    db.refresh(revision)
    return {"validation": outcome.to_dict(), "revision": revision_out(revision).model_dump()}


@router.get("/calibration/export")
def export(camera_id: int, revision_id: int | None = Query(default=None),
           db: Session = Depends(get_db)) -> dict:
    """Export a documented calibration bundle. Contains no credentials."""
    camera = get_camera(db, camera_id)
    if revision_id:
        revision = db.get(CalibrationRevision, revision_id)
        if not revision or revision.camera_id != camera.id:
            raise HTTPException(404, "Calibration revision not found for this camera.")
    else:
        revision = camera.active_calibration
        if revision is None:
            raise HTTPException(404, {
                "message": "This camera has no active calibration to export.",
                "hint": "Solve a calibration and activate it, or pass ?revision_id= explicitly.",
            })
    cs = get_system(db, revision.coordinate_system_id)
    return export_calibration(camera, revision, cs)


# =====================================================================
# Projection testing
# =====================================================================

def _active_model(db: Session, camera: Camera):
    revision = camera.active_calibration
    if revision is None:
        raise HTTPException(422, {
            "message": "This camera has no active calibration, so nothing can be projected.",
            "calibration_status": CalibrationStatus.unconfigured.value,
        })
    return revision, build_projection_model(revision)


@router.post("/projection/image-to-world", response_model=dict)
def image_to_world(camera_id: int, payload: ImageToWorldIn, db: Session = Depends(get_db)) -> dict:
    camera = get_camera(db, camera_id)
    revision, model = _active_model(db, camera)
    try:
        result = model.image_to_floor(payload.pixel_u, payload.pixel_v, payload.plane_z)
    except (NotProjectable, calib.CalibrationError, GeometryError, IntrinsicsError) as exc:
        raise HTTPException(422, str(exc)) from exc
    result["calibration_status"] = revision.status.value
    result["is_approximate"] = model.is_approximate
    result["revision_id"] = revision.id
    return result


@router.post("/projection/world-to-image", response_model=dict)
def world_to_image(camera_id: int, payload: WorldToImageIn, db: Session = Depends(get_db)) -> dict:
    camera = get_camera(db, camera_id)
    revision, model = _active_model(db, camera)
    try:
        result = model.world_to_image(payload.x, payload.y, payload.z)
    except (NotProjectable, calib.CalibrationError, GeometryError, IntrinsicsError) as exc:
        raise HTTPException(422, str(exc)) from exc
    result["calibration_status"] = revision.status.value
    result["is_approximate"] = model.is_approximate
    result["revision_id"] = revision.id
    return result


@router.get("/projection/overlay", response_model=dict)
def projection_overlay(camera_id: int, grid_spacing_m: float = Query(default=2.0, gt=0.05, le=50),
                       extent_m: float = Query(default=20.0, gt=1, le=200),
                       db: Session = Depends(get_db)) -> dict:
    """Reference points and a projected floor grid, drawn over the camera image."""
    camera = get_camera(db, camera_id)
    revision, model = _active_model(db, camera)
    cs = get_system(db, revision.coordinate_system_id)

    points = []
    for obs in camera.observations:
        rp = obs.reference_point
        entry = {"code": rp.code, "name": rp.name, "role": obs.role.value,
                 "marked_pixel": [obs.pixel_u, obs.pixel_v],
                 "world": [rp.x, rp.y, rp.z]}
        try:
            projected = model.world_to_image(rp.x, rp.y, rp.z)
            entry["projected_pixel"] = projected["pixel"]
            entry["reprojection_error_px"] = round(float(np.hypot(
                projected["pixel"][0] - obs.pixel_u, projected["pixel"][1] - obs.pixel_v)), 2)
        except NotProjectable as exc:
            entry["error"] = str(exc)
        points.append(entry)

    # Project a floor grid centred under the camera where a pose is available.
    segments = []
    if model.pose is not None:
        cx, cy = float(model.pose.position[0]), float(model.pose.position[1])
    else:
        xs = [rp.reference_point.x for rp in camera.observations] or [0.0]
        ys = [rp.reference_point.y for rp in camera.observations] or [0.0]
        cx, cy = float(np.mean(xs)), float(np.mean(ys))

    steps = int(extent_m / grid_spacing_m)
    for i in range(-steps, steps + 1):
        for axis in ("x", "y"):
            a = (cx + i * grid_spacing_m, cy - extent_m) if axis == "x" else (cx - extent_m, cy + i * grid_spacing_m)
            b = (cx + i * grid_spacing_m, cy + extent_m) if axis == "x" else (cx + extent_m, cy + i * grid_spacing_m)
            pixel_line = []
            for t in np.linspace(0, 1, 12):
                wx = a[0] + (b[0] - a[0]) * t
                wy = a[1] + (b[1] - a[1]) * t
                try:
                    projected = model.world_to_image(wx, wy, cs.floor_plane_z)
                except NotProjectable:
                    pixel_line.append(None)
                    continue
                pixel_line.append(projected["pixel"] if projected["in_image"] else None)
            if any(p is not None for p in pixel_line):
                segments.append({"axis": axis, "world_offset": i * grid_spacing_m,
                                 "pixels": pixel_line})

    return {
        "revision_id": revision.id,
        "calibration_status": revision.status.value,
        "is_approximate": model.is_approximate,
        "caveats": model.caveats,
        "reference_points": points,
        "grid": {"spacing_m": grid_spacing_m, "extent_m": extent_m, "segments": segments},
        "source_image": {"width": revision.source_image_width, "height": revision.source_image_height},
        "conventions": {"map_heading": CONVENTIONS["map_heading"]},
    }
