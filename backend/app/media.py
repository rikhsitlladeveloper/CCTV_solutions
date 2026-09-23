"""Server-side media adapter.

Browsers cannot play RTSP.  Everything the UI displays is produced here:

* snapshots  - an ONVIF HTTP snapshot, or a single JPEG frame decoded from RTSP.
* live view  - ffmpeg transcodes RTSP into MJPEG, which the browser renders in
  an ``<img>`` as ``multipart/x-mixed-replace``.

No RTSP URL is ever handed to the browser.  ffmpeg is always launched with an
argument list, never through a shell, so nothing in a camera record can be
interpolated into a command line.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import secrets
import subprocess
import time
from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import quote, urlsplit

import httpx

from . import config
from .netguard import HostNotAllowed, resolve_camera_host

log = logging.getLogger("numenor.media")

_CRED_IN_URL = re.compile(r"(?P<scheme>[a-zA-Z][\w+.-]*://)(?:[^/@\s]*@)")


def sanitize(text: str | None) -> str | None:
    """Strip any embedded credentials before a string is stored, logged or returned."""
    if not text:
        return text
    cleaned = _CRED_IN_URL.sub(lambda m: m.group("scheme"), text)
    return cleaned[:600]


@lru_cache(maxsize=1)
def _rtsp_timeout_flag() -> str | None:
    """Pick the socket-timeout flag this ffmpeg build understands.

    ffmpeg 4.x spells it ``-stimeout``; 5.0 renamed it to ``-timeout`` (and kept
    ``-timeout`` on the demuxer meaning listen-timeout in older builds, so the
    two cannot be used interchangeably).  Probing once keeps us correct on both.
    """
    try:
        out = subprocess.run(
            [config.FFMPEG_BIN, "-hide_banner", "-h", "demuxer=rtsp"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("-stimeout"):
            return "-stimeout"
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("-timeout") and "microseconds" in stripped:
            return "-timeout"
    return None


def _rtsp_input_args(timeout_s: float) -> list[str]:
    args = ["-rtsp_transport", "tcp"]
    flag = _rtsp_timeout_flag()
    if flag:
        args += [flag, str(int(timeout_s * 1_000_000))]
    return args


class MediaError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind      # auth | unreachable | timeout | invalid_stream | unsupported | error
        self.message = message


def build_rtsp_url(ip: str, port: int | None, path: str | None,
                   username: str | None, password: str | None) -> str:
    """Build the RTSP URL used internally. Never returned to a client."""
    host = f"[{ip}]" if ":" in ip else ip
    auth = ""
    if username:
        auth = f"{quote(username, safe='')}:{quote(password or '', safe='')}@"
    stream = path or ""
    if stream and not stream.startswith("/"):
        stream = "/" + stream
    return f"rtsp://{auth}{host}:{port or 554}{stream}"


def rtsp_url_from_onvif(stream_uri: str, username: str | None, password: str | None) -> str:
    """Re-point an ONVIF-advertised stream URI at the vetted address and add credentials."""
    parts = urlsplit(stream_uri)
    if parts.scheme != "rtsp":
        raise MediaError("unsupported", f"The camera returned a non-RTSP stream URI ({parts.scheme or 'unknown'}).")
    host = parts.hostname or ""
    resolved = resolve_camera_host(host, parts.port or 554)
    tail = parts.path + (f"?{parts.query}" if parts.query else "")
    return build_rtsp_url(resolved.ip, parts.port or 554, tail, username, password)


def _classify_ffmpeg_error(stderr: str) -> MediaError:
    blob = stderr.lower()
    if "401" in blob or "unauthorized" in blob:
        return MediaError("auth", "The camera rejected the supplied username or password for the video stream.")
    if "404" in blob or "not found" in blob:
        return MediaError("invalid_stream", "The stream path was not found on the camera. Check the RTSP stream path.")
    if "connection refused" in blob:
        return MediaError("unreachable", "The camera refused the RTSP connection. Check the RTSP port.")
    if "no route to host" in blob or "network is unreachable" in blob or "host is unreachable" in blob:
        return MediaError("unreachable", "The camera could not be reached on the network.")
    if "timed out" in blob or "timeout" in blob:
        return MediaError("timeout", "The camera did not deliver video before the timeout expired.")
    if "invalid data found" in blob or "could not find codec" in blob:
        return MediaError("unsupported", "The stream was reached but its format could not be decoded for preview.")
    if "method describe failed" in blob or "protocol not found" in blob:
        return MediaError("invalid_stream", "The camera did not accept the RTSP request for this stream path.")
    return MediaError("error", "The video stream could not be opened.")


def _tail(stderr: bytes, lines: int = 6) -> str:
    text = stderr.decode("utf-8", "replace").strip().splitlines()
    return "\n".join(text[-lines:])


async def grab_rtsp_frame(rtsp_url: str, timeout: float | None = None) -> bytes:
    """Decode one JPEG frame from an RTSP stream. Raises MediaError on failure."""
    timeout = timeout or config.SNAPSHOT_TIMEOUT_S
    args = [
        config.FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-nostdin",
        *_rtsp_input_args(timeout),
        "-i", rtsp_url,
        "-frames:v", "1", "-q:v", "4", "-f", "image2", "-vcodec", "mjpeg",
        "pipe:1",
    ]
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout + 3)
    except asyncio.TimeoutError:
        await _terminate(proc)
        raise MediaError("timeout", "Timed out waiting for a video frame from the camera.")

    if proc.returncode != 0 or not stdout:
        err = _classify_ffmpeg_error(_tail(stderr))
        log.info("Snapshot failed: %s", sanitize(err.message))
        raise err
    return stdout


async def fetch_http_snapshot(uri: str, username: str | None, password: str | None,
                              timeout: float | None = None) -> tuple[bytes, str]:
    """Fetch an ONVIF HTTP snapshot. Redirects are refused, the host is re-vetted."""
    parts = urlsplit(uri)
    if parts.scheme not in ("http", "https"):
        raise MediaError("unsupported", "The camera advertised an unsupported snapshot URL scheme.")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        resolved = resolve_camera_host(parts.hostname or "", port)
    except HostNotAllowed as exc:
        raise MediaError("unreachable", str(exc)) from exc

    host = f"[{resolved.ip}]" if ":" in resolved.ip else resolved.ip
    target = f"{parts.scheme}://{host}:{port}{parts.path or '/'}"
    if parts.query:
        target += f"?{parts.query}"
    headers = {"Host": parts.netloc.split("@")[-1]}

    auths = []
    if username:
        auths.append(httpx.DigestAuth(username, password or ""))
        auths.append(httpx.BasicAuth(username, password or ""))
    else:
        auths.append(None)

    last: httpx.Response | None = None
    for auth in auths:
        try:
            async with httpx.AsyncClient(
                timeout=timeout or config.SNAPSHOT_TIMEOUT_S, follow_redirects=False, verify=False
            ) as client:
                resp = await client.get(target, auth=auth, headers=headers)
        except httpx.TimeoutException as exc:
            raise MediaError("timeout", "The camera did not return a snapshot before the timeout expired.") from exc
        except httpx.HTTPError as exc:
            raise MediaError("unreachable", "The camera's snapshot endpoint could not be reached.") from exc
        last = resp
        if resp.status_code == 200:
            ctype = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
            if not ctype.startswith("image/"):
                raise MediaError("unsupported", "The snapshot endpoint did not return an image.")
            return resp.content, ctype
        if resp.status_code not in (401, 403):
            break

    if last is not None and last.status_code in (401, 403):
        raise MediaError("auth", "The camera rejected the supplied credentials for snapshots.")
    raise MediaError("error", f"The camera returned HTTP {last.status_code if last else '???'} for the snapshot.")


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=3)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()


# -- preview sessions ----------------------------------------------------

MJPEG_BOUNDARY = "numenorframe"


@dataclass
class PreviewSession:
    id: str
    camera_id: int
    started_at: float = field(default_factory=time.monotonic)
    last_touch: float = field(default_factory=time.monotonic)
    process: asyncio.subprocess.Process | None = None
    attached: bool = False
    stop_reason: str | None = None

    def touch(self) -> None:
        self.last_touch = time.monotonic()


class PreviewManager:
    """Tracks ffmpeg preview processes: caps concurrency and reaps abandoned ones."""

    def __init__(self) -> None:
        self._sessions: dict[str, PreviewSession] = {}
        self._lock = asyncio.Lock()
        self._reaper: asyncio.Task | None = None

    def start_reaper(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.create_task(self._reap_loop())

    async def shutdown(self) -> None:
        if self._reaper:
            self._reaper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reaper
        for session in list(self._sessions.values()):
            await self._stop(session, "server shutdown")

    async def _reap_loop(self) -> None:
        while True:
            await asyncio.sleep(5)
            now = time.monotonic()
            for session in list(self._sessions.values()):
                idle = now - session.last_touch
                age = now - session.started_at
                if age > config.PREVIEW_MAX_LIFETIME_S:
                    await self._stop(session, "maximum preview lifetime reached")
                elif idle > config.PREVIEW_IDLE_TIMEOUT_S:
                    await self._stop(session, "preview abandoned by the client")

    async def create(self, camera_id: int) -> PreviewSession:
        async with self._lock:
            # A camera only ever needs one live preview.
            for existing in list(self._sessions.values()):
                if existing.camera_id == camera_id:
                    await self._stop(existing, "replaced by a newer preview request")
            if len(self._sessions) >= config.MAX_PREVIEW_SESSIONS:
                raise MediaError(
                    "error",
                    f"All {config.MAX_PREVIEW_SESSIONS} preview slots are in use. "
                    "Stop another preview and try again.",
                )
            session = PreviewSession(id=secrets.token_urlsafe(12), camera_id=camera_id)
            self._sessions[session.id] = session
            return session

    def get(self, session_id: str) -> PreviewSession | None:
        return self._sessions.get(session_id)

    def list_sessions(self) -> list[PreviewSession]:
        return list(self._sessions.values())

    async def stop(self, session_id: str, reason: str = "stopped by operator") -> bool:
        session = self._sessions.get(session_id)
        if not session:
            return False
        await self._stop(session, reason)
        return True

    async def _stop(self, session: PreviewSession, reason: str) -> None:
        session.stop_reason = reason
        self._sessions.pop(session.id, None)
        if session.process:
            await _terminate(session.process)
            session.process = None
        log.info("Preview %s for camera %s stopped: %s", session.id, session.camera_id, reason)

    async def stream(self, session: PreviewSession, rtsp_url: str):
        """Yield an MJPEG multipart body produced by ffmpeg."""
        args = [
            config.FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-nostdin",
            *_rtsp_input_args(config.SNAPSHOT_TIMEOUT_S),
            "-i", rtsp_url,
            "-an", "-r", "8", "-q:v", "7",
            "-vf", "scale='min(960,iw)':-2",
            "-f", "mpjpeg", "-boundary_tag", MJPEG_BOUNDARY,
            "pipe:1",
        ]
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )
        session.process = proc
        session.attached = True
        session.touch()

        stderr_buf = bytearray()

        async def drain_stderr() -> None:
            assert proc.stderr is not None
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                if len(stderr_buf) < 4096:
                    stderr_buf.extend(line)

        stderr_task = asyncio.create_task(drain_stderr())
        try:
            assert proc.stdout is not None
            first = True
            while True:
                chunk = await proc.stdout.read(32768)
                if not chunk:
                    break
                if first:
                    first = False
                    log.info("Preview %s streaming for camera %s", session.id, session.camera_id)
                session.touch()
                yield chunk
        except (asyncio.CancelledError, GeneratorExit):
            raise
        finally:
            stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stderr_task
            if proc.returncode not in (0, None) and stderr_buf:
                log.info("Preview %s ended: %s", session.id,
                         sanitize(_classify_ffmpeg_error(stderr_buf.decode('utf-8', 'replace')).message))
            await self._stop(session, session.stop_reason or "client disconnected")


preview_manager = PreviewManager()
