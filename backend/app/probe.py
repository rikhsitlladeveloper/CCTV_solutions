"""Connection testing.

A test only ever contacts the one endpoint an operator registered or typed into
the wizard.  Nothing here enumerates or scans the network.

The result deliberately separates two things that are easy to conflate:

* ``login_ok``  - the ONVIF control channel accepted our credentials.
* ``stream_ok`` - a real video frame came back.

A camera that logs in but cannot deliver video is reported as *partial*, never
as online.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from . import media, onvif
from .models import ConnectionType, TestStatus
from .netguard import HostNotAllowed, resolve_camera_host

log = logging.getLogger("numenor.probe")

_ONVIF_STATUS = {
    "auth": TestStatus.auth_failed,
    "unreachable": TestStatus.unreachable,
    "timeout": TestStatus.timeout,
    "protocol": TestStatus.error,
    "unsupported": TestStatus.error,
}

_MEDIA_STATUS = {
    "auth": TestStatus.auth_failed,
    "unreachable": TestStatus.unreachable,
    "timeout": TestStatus.timeout,
    "invalid_stream": TestStatus.error,
    "unsupported": TestStatus.error,
    "error": TestStatus.error,
}


@dataclass
class ProbeResult:
    status: TestStatus
    summary: str
    error: str | None = None
    login_ok: bool = False
    stream_ok: bool = False
    snapshot_supported: bool = False
    profiles: list[onvif.OnvifProfile] = field(default_factory=list)
    device: onvif.DeviceInfo | None = None
    selected_profile_token: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == TestStatus.online


@dataclass
class CameraTarget:
    """Everything a probe needs, decrypted, and never serialised outward."""
    host: str
    connection_type: ConnectionType
    onvif_port: int | None = 80
    onvif_path: str | None = "/onvif/device_service"
    rtsp_port: int | None = 554
    stream_path: str | None = None
    username: str | None = None
    password: str | None = None
    profile_token: str | None = None


async def test_camera(target: CameraTarget, want_profiles: bool = True) -> ProbeResult:
    if target.connection_type == ConnectionType.onvif:
        return await _test_onvif(target, want_profiles)
    return await _test_rtsp(target)


async def _test_rtsp(target: CameraTarget) -> ProbeResult:
    try:
        resolved = resolve_camera_host(target.host, target.rtsp_port or 554)
    except HostNotAllowed as exc:
        return ProbeResult(TestStatus.unreachable, "Address rejected", media.sanitize(str(exc)))

    url = media.build_rtsp_url(
        resolved.ip, target.rtsp_port, target.stream_path, target.username, target.password
    )
    try:
        await media.grab_rtsp_frame(url)
    except media.MediaError as exc:
        return ProbeResult(
            status=_MEDIA_STATUS.get(exc.kind, TestStatus.error),
            summary="RTSP stream could not be opened",
            error=media.sanitize(exc.message),
        )
    return ProbeResult(
        status=TestStatus.online,
        summary="RTSP stream verified: a live video frame was decoded.",
        login_ok=bool(target.username),
        stream_ok=True,
        snapshot_supported=True,
    )


async def _test_onvif(target: CameraTarget, want_profiles: bool) -> ProbeResult:
    try:
        client = onvif.build_client(
            target.host, target.onvif_port, target.onvif_path, target.username, target.password
        )
    except onvif.OnvifError as exc:
        return ProbeResult(TestStatus.unreachable, "Address rejected", media.sanitize(exc.message))

    loop = asyncio.get_running_loop()

    def _discover() -> tuple[onvif.DeviceInfo, list[onvif.OnvifProfile], str]:
        client.probe()
        device = client.device_information()
        media_url = client.media_service_url()
        profiles = client.profiles(media_url) if want_profiles else []
        return device, profiles, media_url

    try:
        device, profiles, media_url = await loop.run_in_executor(None, _discover)
    except onvif.OnvifError as exc:
        return ProbeResult(
            status=_ONVIF_STATUS.get(exc.kind, TestStatus.error),
            summary="ONVIF connection failed",
            error=media.sanitize(exc.message),
        )

    result = ProbeResult(
        status=TestStatus.partial,
        summary="ONVIF login succeeded.",
        login_ok=True,
        profiles=profiles,
        device=device,
    )
    if not profiles:
        result.error = "The camera accepted the login but reported no media profiles."
        return result

    token = target.profile_token or profiles[0].token
    if token not in {p.token for p in profiles}:
        token = profiles[0].token
    result.selected_profile_token = token

    # ONVIF login working does not mean video works. Verify the stream separately.
    def _stream_uri() -> tuple[str, str | None]:
        return client.stream_uri(media_url, token), client.snapshot_uri(media_url, token)

    try:
        stream_uri, snapshot_uri = await loop.run_in_executor(None, _stream_uri)
    except onvif.OnvifError as exc:
        result.error = media.sanitize(
            f"ONVIF login succeeded, but the camera would not return a stream URI: {exc.message}"
        )
        result.summary = "ONVIF login succeeded; video stream unavailable."
        return result

    result.snapshot_supported = bool(snapshot_uri)

    try:
        rtsp_url = media.rtsp_url_from_onvif(stream_uri, target.username, target.password)
        await media.grab_rtsp_frame(rtsp_url)
    except (media.MediaError, HostNotAllowed) as exc:
        message = exc.message if isinstance(exc, media.MediaError) else str(exc)
        result.summary = "ONVIF login succeeded; video stream did not verify."
        result.error = media.sanitize(f"ONVIF control channel is working, but the video stream failed: {message}")
        return result

    result.status = TestStatus.online
    result.stream_ok = True
    profile = next((p for p in profiles if p.token == token), None)
    detail = f" ({profile.resolution} {profile.encoding})" if profile and profile.resolution else ""
    result.summary = f"ONVIF login and video stream verified{detail}."
    result.error = None
    return result
