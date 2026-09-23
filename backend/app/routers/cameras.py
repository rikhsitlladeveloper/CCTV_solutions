"""Camera CRUD, connection testing, ONVIF discovery and snapshots."""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import config, crypto, media, probe
from ..db import get_db
from ..models import Area, Building, Camera, ConnectionType, Floor, ReviewStatus, Site, TestStatus
from ..schemas import (
    CameraCreate, CameraOut, CameraSummary, CameraUpdate, PlacementIn, PlacementOut,
    ProfileOut, TestConnectionRequest, TestConnectionResult,
)
from ..security import current_operator
from ..serializers import camera_out

log = logging.getLogger("numenor.cameras")

router = APIRouter(prefix="/api/cameras", tags=["cameras"], dependencies=[Depends(current_operator)])

FAILED_STATUSES = (TestStatus.auth_failed, TestStatus.unreachable, TestStatus.timeout,
                   TestStatus.error, TestStatus.partial)

ALLOWED_PHOTO_TYPES = {"image/png": ".png", "image/jpeg": ".jpg"}


def _get_camera(db: Session, camera_id: int) -> Camera:
    camera = db.get(Camera, camera_id)
    if not camera:
        raise HTTPException(404, "Camera not found.")
    return camera


def _target(camera: Camera | None, override: TestConnectionRequest | None = None) -> probe.CameraTarget:
    """Build a probe target from a stored camera and/or wizard-supplied overrides."""
    o = override
    password: str | None = None
    if o and o.password:
        password = o.password
    elif camera and camera.password_encrypted and (o is None or o.use_stored_password or o.password is None):
        password = crypto.decrypt(camera.password_encrypted)

    def pick(attr: str, default=None):
        if o is not None and getattr(o, attr, None) is not None:
            return getattr(o, attr)
        if camera is not None:
            return getattr(camera, attr, default)
        return default

    return probe.CameraTarget(
        host=pick("host"),
        connection_type=pick("connection_type") or ConnectionType.onvif,
        onvif_port=pick("onvif_port") or 80,
        onvif_path=pick("onvif_path") or "/onvif/device_service",
        rtsp_port=pick("rtsp_port") or 554,
        stream_path=pick("stream_path"),
        username=pick("username"),
        password=password,
        profile_token=(o.profile_token if o and o.profile_token else
                       (camera.selected_profile_token if camera else None)),
    )


def _apply_result(camera: Camera, result: probe.ProbeResult) -> None:
    camera.last_test_status = result.status
    camera.last_test_at = datetime.now(timezone.utc)
    camera.last_test_error = media.sanitize(result.error)
    camera.last_test_detail = media.sanitize(result.summary)
    if result.snapshot_supported:
        camera.snapshot_supported = True
    if result.device:
        if result.device.manufacturer and not camera.manufacturer:
            camera.manufacturer = result.device.manufacturer[:120]
        if result.device.model and not camera.model:
            camera.model = result.device.model[:120]
    if result.profiles and result.selected_profile_token:
        chosen = next((p for p in result.profiles if p.token == result.selected_profile_token), None)
        if chosen and not camera.selected_profile_token:
            camera.selected_profile_token = chosen.token
            camera.selected_profile_name = chosen.name
            camera.profile_resolution = chosen.resolution
            camera.profile_encoding = chosen.encoding


def _result_out(result: probe.ProbeResult) -> TestConnectionResult:
    return TestConnectionResult(
        status=result.status,
        ok=result.ok,
        login_ok=result.login_ok,
        stream_ok=result.stream_ok,
        snapshot_supported=result.snapshot_supported,
        summary=result.summary,
        error=media.sanitize(result.error),
        tested_at=datetime.now(timezone.utc),
        profiles=[ProfileOut(token=p.token, name=p.name, encoding=p.encoding,
                             resolution=p.resolution, fps=p.fps) for p in result.profiles],
        selected_profile_token=result.selected_profile_token,
        manufacturer=result.device.manufacturer if result.device else None,
        model=result.device.model if result.device else None,
        firmware=result.device.firmware if result.device else None,
    )


# -- listing -------------------------------------------------------------

@router.get("", response_model=list[CameraOut])
def list_cameras(
    db: Session = Depends(get_db),
    q: str | None = None,
    site_id: int | None = None,
    building_id: int | None = None,
    floor_id: int | None = None,
    area_id: int | None = None,
    status: str | None = Query(default=None, description="online|failed|untested|partial"),
    placement: str | None = Query(default=None, description="placed|unplaced|needs_review"),
) -> list[CameraOut]:
    stmt = select(Camera)
    if q:
        needle = f"%{q.strip()}%"
        stmt = stmt.where(or_(Camera.name.ilike(needle), Camera.host.ilike(needle),
                              Camera.manufacturer.ilike(needle), Camera.model.ilike(needle)))
    if area_id:
        stmt = stmt.where(Camera.area_id == area_id)
    elif floor_id:
        stmt = stmt.where(Camera.area_id.in_(select(Area.id).where(Area.floor_id == floor_id)))
    elif building_id:
        stmt = stmt.where(Camera.area_id.in_(
            select(Area.id).join(Floor).where(Floor.building_id == building_id)))
    elif site_id:
        stmt = stmt.where(Camera.area_id.in_(
            select(Area.id).join(Floor).join(Building).where(Building.site_id == site_id)))

    if status == "online":
        stmt = stmt.where(Camera.last_test_status == TestStatus.online)
    elif status == "untested":
        stmt = stmt.where(Camera.last_test_status == TestStatus.untested)
    elif status == "partial":
        stmt = stmt.where(Camera.last_test_status == TestStatus.partial)
    elif status == "failed":
        stmt = stmt.where(Camera.last_test_status.in_(FAILED_STATUSES))

    cameras = db.scalars(stmt.order_by(Camera.name)).all()
    if placement == "placed":
        cameras = [c for c in cameras if c.placement]
    elif placement == "unplaced":
        cameras = [c for c in cameras if not c.placement]
    elif placement == "needs_review":
        cameras = [c for c in cameras if c.placement and c.placement.review_status == ReviewStatus.needs_review]
    return [camera_out(c) for c in cameras]


@router.get("/summary", response_model=CameraSummary)
def summary(db: Session = Depends(get_db)) -> CameraSummary:
    rows = dict(db.execute(
        select(Camera.last_test_status, func.count(Camera.id)).group_by(Camera.last_test_status)
    ).all())
    cameras = db.scalars(select(Camera)).all()
    return CameraSummary(
        total=sum(rows.values()),
        reachable=rows.get(TestStatus.online, 0),
        failed=sum(rows.get(s, 0) for s in FAILED_STATUSES),
        untested=rows.get(TestStatus.untested, 0),
        awaiting_placement=sum(1 for c in cameras if not c.placement),
        placements_need_review=sum(
            1 for c in cameras if c.placement and c.placement.review_status == ReviewStatus.needs_review
        ),
    )


# -- unsaved-camera helpers (wizard step 1) -------------------------------

@router.post("/test-connection", response_model=TestConnectionResult)
async def test_unsaved(payload: TestConnectionRequest) -> TestConnectionResult:
    if not payload.host:
        raise HTTPException(422, "An IP address or hostname is required to test a connection.")
    result = await probe.test_camera(_target(None, payload))
    return _result_out(result)


@router.post("/profiles", response_model=TestConnectionResult)
async def discover_profiles(payload: TestConnectionRequest) -> TestConnectionResult:
    """ONVIF profile discovery for a camera that has not been saved yet."""
    if not payload.host:
        raise HTTPException(422, "An IP address or hostname is required.")
    payload.connection_type = ConnectionType.onvif
    result = await probe.test_camera(_target(None, payload), want_profiles=True)
    return _result_out(result)


@router.post("/snapshot-preview")
async def snapshot_unsaved(payload: TestConnectionRequest) -> Response:
    """A real snapshot for a camera the wizard has not saved yet.

    Same host vetting as every other outbound call; the credentials are used for
    this request only and are never persisted by this endpoint.
    """
    if not payload.host:
        raise HTTPException(422, "An IP address or hostname is required.")
    try:
        content, ctype = await _snapshot_for_target(_target(None, payload))
    except media.MediaError as exc:
        raise HTTPException(502, media.sanitize(exc.message) or "Snapshot failed.") from exc
    return Response(content=content, media_type=ctype, headers={"Cache-Control": "no-store"})


async def _snapshot_for_target(target: probe.CameraTarget) -> tuple[bytes, str]:
    """Snapshot via ONVIF when the camera offers one, otherwise an RTSP frame."""
    import asyncio

    from .. import onvif as onvif_mod
    from ..netguard import HostNotAllowed, resolve_camera_host

    if target.connection_type == ConnectionType.onvif:
        try:
            client = onvif_mod.build_client(target.host, target.onvif_port, target.onvif_path,
                                            target.username, target.password)
            loop = asyncio.get_running_loop()

            def _uris() -> tuple[str | None, str]:
                media_url = client.media_service_url()
                token = target.profile_token or client.profiles(media_url)[0].token
                return client.snapshot_uri(media_url, token), client.stream_uri(media_url, token)

            snapshot_uri, stream_uri = await loop.run_in_executor(None, _uris)
        except onvif_mod.OnvifError as exc:
            raise HTTPException(502, media.sanitize(exc.message) or "Snapshot failed.") from exc
        if snapshot_uri:
            return await media.fetch_http_snapshot(snapshot_uri, target.username, target.password)
        try:
            rtsp_url = media.rtsp_url_from_onvif(stream_uri, target.username, target.password)
        except HostNotAllowed as exc:
            raise HTTPException(400, str(exc)) from exc
    else:
        try:
            resolved = resolve_camera_host(target.host, target.rtsp_port or 554)
        except HostNotAllowed as exc:
            raise HTTPException(400, str(exc)) from exc
        rtsp_url = media.build_rtsp_url(resolved.ip, target.rtsp_port, target.stream_path,
                                        target.username, target.password)

    return await media.grab_rtsp_frame(rtsp_url), "image/jpeg"


# -- CRUD -----------------------------------------------------------------

@router.post("", response_model=CameraOut, status_code=201)
def create_camera(payload: CameraCreate, db: Session = Depends(get_db)) -> CameraOut:
    if payload.area_id and not db.get(Area, payload.area_id):
        raise HTTPException(404, "Location area not found.")
    data = payload.model_dump(exclude={"password"})
    camera = Camera(**data)
    if payload.password:
        camera.password_encrypted = crypto.encrypt(payload.password)
    db.add(camera)
    db.commit()
    db.refresh(camera)
    log.info("Camera %s registered (host=%s)", camera.id, camera.host)
    return camera_out(camera)


@router.get("/{camera_id}", response_model=CameraOut)
def get_camera(camera_id: int, db: Session = Depends(get_db)) -> CameraOut:
    return camera_out(_get_camera(db, camera_id))


@router.patch("/{camera_id}", response_model=CameraOut)
def update_camera(camera_id: int, payload: CameraUpdate, db: Session = Depends(get_db)) -> CameraOut:
    camera = _get_camera(db, camera_id)
    data = payload.model_dump(exclude_unset=True, exclude={"password", "clear_password"})
    if "area_id" in data and data["area_id"] is not None and not db.get(Area, data["area_id"]):
        raise HTTPException(404, "Location area not found.")

    connection_fields = {"host", "connection_type", "onvif_port", "onvif_path", "rtsp_port", "stream_path", "username"}
    connection_changed = any(
        field in data and data[field] != getattr(camera, field) for field in connection_fields
    )

    for field, value in data.items():
        setattr(camera, field, value)

    # An empty password field means "keep the existing password".
    if payload.clear_password:
        camera.password_encrypted = None
        connection_changed = True
    elif payload.password:
        camera.password_encrypted = crypto.encrypt(payload.password)
        connection_changed = True

    if connection_changed:
        # Connection details changed: the previous verification no longer applies.
        camera.last_test_status = TestStatus.untested
        camera.last_test_at = None
        camera.last_test_error = None
        camera.last_test_detail = "Connection details changed since the last test."

    db.commit()
    db.refresh(camera)
    return camera_out(camera)


@router.delete("/{camera_id}", status_code=204, response_model=None)
def delete_camera(camera_id: int, db: Session = Depends(get_db)) -> None:
    camera = _get_camera(db, camera_id)
    if camera.installation_photo_path:
        (config.PHOTO_DIR / camera.installation_photo_path).unlink(missing_ok=True)
    db.delete(camera)
    db.commit()


# -- saved-camera operations ----------------------------------------------

@router.post("/{camera_id}/test", response_model=TestConnectionResult)
async def test_saved(camera_id: int, payload: TestConnectionRequest | None = None,
                     db: Session = Depends(get_db)) -> TestConnectionResult:
    camera = _get_camera(db, camera_id)
    result = await probe.test_camera(_target(camera, payload))
    _apply_result(camera, result)
    db.commit()
    return _result_out(result)


@router.post("/{camera_id}/profiles", response_model=TestConnectionResult)
async def saved_profiles(camera_id: int, payload: TestConnectionRequest | None = None,
                         db: Session = Depends(get_db)) -> TestConnectionResult:
    camera = _get_camera(db, camera_id)
    target = _target(camera, payload)
    target.connection_type = ConnectionType.onvif
    result = await probe.test_camera(target, want_profiles=True)
    _apply_result(camera, result)
    db.commit()
    return _result_out(result)


@router.get("/{camera_id}/snapshot")
async def snapshot(camera_id: int, db: Session = Depends(get_db)) -> Response:
    """A real image from the device: ONVIF snapshot when offered, else an RTSP frame."""
    camera = _get_camera(db, camera_id)
    try:
        payload, ctype = await _snapshot_for_target(_target(camera))
    except media.MediaError as exc:
        raise HTTPException(502, media.sanitize(exc.message) or "Snapshot failed.") from exc
    return Response(content=payload, media_type=ctype, headers={"Cache-Control": "no-store"})


# -- placement -------------------------------------------------------------

@router.put("/{camera_id}/placement", response_model=PlacementOut)
def upsert_placement(camera_id: int, payload: PlacementIn, db: Session = Depends(get_db)) -> PlacementOut:
    from ..models import CameraPlacement, FloorPlan

    camera = _get_camera(db, camera_id)
    plan = db.get(FloorPlan, payload.floor_plan_id)
    if not plan:
        raise HTTPException(404, "Floor plan not found.")

    placement = camera.placement or CameraPlacement(camera_id=camera.id)
    placement.floor_plan_id = plan.id
    placement.norm_x = payload.norm_x
    placement.norm_y = payload.norm_y
    placement.heading_deg = payload.heading_deg % 360
    placement.mounting_height_m = (
        payload.mounting_height_m if payload.mounting_height_m is not None else camera.mounting_height_m
    )
    placement.fov_deg = payload.fov_deg
    placement.view_distance_m = payload.view_distance_m
    placement.review_status = payload.review_status
    db.add(placement)
    db.commit()
    db.refresh(placement)
    return PlacementOut.model_validate(placement)


@router.delete("/{camera_id}/placement", status_code=204, response_model=None)
def delete_placement(camera_id: int, db: Session = Depends(get_db)) -> None:
    camera = _get_camera(db, camera_id)
    if camera.placement:
        db.delete(camera.placement)
        db.commit()


# -- installation photo -----------------------------------------------------

@router.post("/{camera_id}/photo", response_model=CameraOut)
async def upload_photo(camera_id: int, file: UploadFile = File(...),
                       db: Session = Depends(get_db)) -> CameraOut:
    camera = _get_camera(db, camera_id)
    suffix = ALLOWED_PHOTO_TYPES.get((file.content_type or "").lower())
    if not suffix:
        raise HTTPException(415, "Installation photos must be PNG or JPEG.")
    payload = await file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(payload) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "The photo is larger than the upload limit.")

    from PIL import Image, UnidentifiedImageError
    import io
    try:
        Image.open(io.BytesIO(payload)).verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(415, "That file is not a readable PNG or JPEG image.") from exc

    filename = f"camera-{camera.id}-{secrets.token_hex(8)}{suffix}"
    (config.PHOTO_DIR / filename).write_bytes(payload)
    if camera.installation_photo_path:
        (config.PHOTO_DIR / camera.installation_photo_path).unlink(missing_ok=True)
    camera.installation_photo_path = filename
    db.commit()
    db.refresh(camera)
    return camera_out(camera)


@router.get("/{camera_id}/photo")
def get_photo(camera_id: int, db: Session = Depends(get_db)) -> Response:
    camera = _get_camera(db, camera_id)
    if not camera.installation_photo_path:
        raise HTTPException(404, "No installation photo for this camera.")
    path = config.PHOTO_DIR / camera.installation_photo_path
    if not path.is_file():
        raise HTTPException(404, "The installation photo file is missing.")
    ctype = "image/png" if path.suffix == ".png" else "image/jpeg"
    return Response(content=path.read_bytes(), media_type=ctype)
