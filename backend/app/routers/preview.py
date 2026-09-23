"""Browser-compatible preview sessions.

RTSP never reaches the browser.  A session starts an ffmpeg process that
transcodes the camera's stream to MJPEG; the browser consumes it from
``/api/preview/{session_id}/stream`` in an ``<img>``.  Sessions are capped,
idle-reaped and explicitly stoppable.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .. import config, crypto, media, onvif
from ..db import get_db
from ..models import Camera, ConnectionType
from ..netguard import HostNotAllowed, resolve_camera_host
from ..schemas import PreviewSessionOut
from ..security import current_operator

log = logging.getLogger("numenor.preview")

router = APIRouter(prefix="/api/preview", tags=["preview"],
                   dependencies=[Depends(current_operator)])

APPROX_NOTE = ("Live preview is transcoded on the server to MJPEG. "
               "Frame rate is reduced for bandwidth; this is a monitoring aid, not a recording.")


async def _rtsp_url_for(camera: Camera) -> str:
    password = crypto.decrypt(camera.password_encrypted) if camera.password_encrypted else None

    if camera.connection_type == ConnectionType.rtsp:
        try:
            resolved = resolve_camera_host(camera.host, camera.rtsp_port or 554)
        except HostNotAllowed as exc:
            raise HTTPException(400, str(exc)) from exc
        return media.build_rtsp_url(resolved.ip, camera.rtsp_port, camera.stream_path,
                                    camera.username, password)

    try:
        client = onvif.build_client(camera.host, camera.onvif_port, camera.onvif_path,
                                    camera.username, password)
        loop = asyncio.get_running_loop()

        def _uri() -> str:
            media_url = client.media_service_url()
            token = camera.selected_profile_token
            if not token:
                token = client.profiles(media_url)[0].token
            return client.stream_uri(media_url, token)

        stream_uri = await loop.run_in_executor(None, _uri)
        return media.rtsp_url_from_onvif(stream_uri, camera.username, password)
    except onvif.OnvifError as exc:
        raise HTTPException(502, media.sanitize(exc.message) or "Preview unavailable.") from exc
    except (media.MediaError, HostNotAllowed) as exc:
        detail = exc.message if isinstance(exc, media.MediaError) else str(exc)
        raise HTTPException(502, media.sanitize(detail) or "Preview unavailable.") from exc


@router.post("/{camera_id}/start", response_model=PreviewSessionOut)
async def start_preview(camera_id: int, db: Session = Depends(get_db)) -> PreviewSessionOut:
    camera = db.get(Camera, camera_id)
    if not camera:
        raise HTTPException(404, "Camera not found.")

    rtsp_url = await _rtsp_url_for(camera)   # validated before a slot is taken
    try:
        session = await media.preview_manager.create(camera.id)
    except media.MediaError as exc:
        raise HTTPException(429, exc.message) from exc

    _PENDING[session.id] = rtsp_url
    return PreviewSessionOut(
        session_id=session.id,
        camera_id=camera.id,
        stream_url=f"/api/preview/{session.id}/stream",
        expires_in_s=config.PREVIEW_MAX_LIFETIME_S,
        note=APPROX_NOTE,
    )


# Credential-bearing URLs stay in process memory, keyed by session, and are
# dropped as soon as the stream is attached.
_PENDING: dict[str, str] = {}


@router.get("/{session_id}/stream")
async def stream(session_id: str) -> StreamingResponse:
    session = media.preview_manager.get(session_id)
    if not session:
        raise HTTPException(404, "That preview session is no longer active. Start a new preview.")
    if session.attached:
        raise HTTPException(409, "That preview session is already streaming.")
    rtsp_url = _PENDING.pop(session_id, None)
    if not rtsp_url:
        raise HTTPException(409, "That preview session was already consumed. Start a new preview.")

    return StreamingResponse(
        media.preview_manager.stream(session, rtsp_url),
        media_type=f"multipart/x-mixed-replace; boundary={media.MJPEG_BOUNDARY}",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/{session_id}/stop", status_code=204, response_model=None)
async def stop_preview(session_id: str) -> None:
    _PENDING.pop(session_id, None)
    await media.preview_manager.stop(session_id)


@router.get("/sessions")
async def list_sessions() -> dict:
    sessions = media.preview_manager.list_sessions()
    return {
        "active": len(sessions),
        "limit": config.MAX_PREVIEW_SESSIONS,
        "sessions": [
            {"session_id": s.id, "camera_id": s.camera_id, "streaming": s.attached}
            for s in sessions
        ],
    }
