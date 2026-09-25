"""A small, dependency-light ONVIF client.

Covers exactly the operations camera registration needs: reachability, device
information, media profile discovery, stream URIs and snapshot URIs.  Requests
are plain SOAP 1.2 over httpx with a WS-Security UsernameToken (password
digest), which is what ONVIF Profile S devices expect.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import socket
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import unquote

import httpx

from . import config
from .netguard import HostNotAllowed, resolve_camera_host

log = logging.getLogger("numenor.onvif")

NS = {
    "s": "http://www.w3.org/2003/05/soap-envelope",
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "trt": "http://www.onvif.org/ver10/media/wsdl",
    "tt": "http://www.onvif.org/ver10/schema",
}


class OnvifError(Exception):
    """An ONVIF exchange failed. ``kind`` classifies it for the operator."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind          # auth | unreachable | timeout | protocol | unsupported
        self.message = message


@dataclass
class OnvifProfile:
    token: str
    name: str
    encoding: str | None = None
    resolution: str | None = None
    fps: int | None = None


@dataclass
class DeviceInfo:
    manufacturer: str | None = None
    model: str | None = None
    firmware: str | None = None
    serial: str | None = None


@dataclass
class OnvifSession:
    profiles: list[OnvifProfile] = field(default_factory=list)
    device: DeviceInfo = field(default_factory=DeviceInfo)
    media_url: str | None = None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find_all(node: ET.Element, name: str) -> list[ET.Element]:
    return [el for el in node.iter() if _local(el.tag) == name]


def _find_text(node: ET.Element, name: str) -> str | None:
    for el in node.iter():
        if _local(el.tag) == name and el.text:
            return el.text.strip()
    return None


def _security_header(username: str | None, password: str | None) -> str:
    if not username:
        return ""
    nonce = secrets.token_bytes(16)
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    digest = base64.b64encode(
        hashlib.sha1(nonce + created.encode() + (password or "").encode()).digest()
    ).decode()
    return (
        '<s:Header><Security s:mustUnderstand="1" '
        'xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">'
        f"<UsernameToken><Username>{_xml_escape(username)}</Username>"
        '<Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">'
        f"{digest}</Password>"
        '<Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">'
        f"{base64.b64encode(nonce).decode()}</Nonce>"
        '<Created xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">'
        f"{created}</Created></UsernameToken></Security></s:Header>"
    )


def _xml_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;").replace("'", "&apos;"))


def _envelope(body: str, username: str | None, password: str | None) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
        'xmlns:tds="http://www.onvif.org/ver10/device/wsdl" '
        'xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
        'xmlns:tt="http://www.onvif.org/ver10/schema">'
        f"{_security_header(username, password)}<s:Body>{body}</s:Body></s:Envelope>"
    )


class OnvifClient:
    """Talks to one camera. The host is vetted before any socket is opened."""

    def __init__(self, host: str, port: int = 80, path: str = "/onvif/device_service",
                 username: str | None = None, password: str | None = None,
                 timeout: float | None = None):
        self.host = host
        self.port = port or 80
        self.path = path or "/onvif/device_service"
        if not self.path.startswith("/"):
            self.path = "/" + self.path
        self.username = username
        self.password = password
        self.timeout = timeout or config.ONVIF_TIMEOUT_S
        self._resolved = resolve_camera_host(host, self.port)

    @property
    def device_url(self) -> str:
        return f"http://{self._bracket(self._resolved.ip)}:{self.port}{self.path}"

    @staticmethod
    def _bracket(ip: str) -> str:
        return f"[{ip}]" if ":" in ip else ip

    def _post(self, url: str, body: str, authenticated: bool = True) -> ET.Element:
        envelope = _envelope(
            body,
            self.username if authenticated else None,
            self.password if authenticated else None,
        )
        headers = {
            "Content-Type": "application/soap+xml; charset=utf-8",
            "Host": f"{self.host}:{self.port}",
        }
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=False) as client:
                resp = client.post(url, content=envelope.encode(), headers=headers)
        except httpx.ConnectTimeout as exc:
            raise OnvifError("timeout", f"Timed out connecting to {self.host}:{self.port}.") from exc
        except httpx.ReadTimeout as exc:
            raise OnvifError("timeout", f"{self.host} accepted the connection but did not reply in time.") from exc
        except httpx.ConnectError as exc:
            raise OnvifError("unreachable", f"Could not reach {self.host} on port {self.port}.") from exc
        except httpx.HTTPError as exc:
            raise OnvifError("protocol", f"HTTP error talking to {self.host}: {type(exc).__name__}.") from exc

        if resp.status_code in (401, 403):
            raise OnvifError("auth", "The camera rejected the supplied username or password.")
        if resp.status_code == 404:
            raise OnvifError(
                "protocol",
                f"No ONVIF service at {self.path}. Check the ONVIF endpoint path.",
            )
        if resp.status_code >= 500 and not resp.content:
            raise OnvifError("protocol", f"The camera returned HTTP {resp.status_code}.")

        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as exc:
            raise OnvifError(
                "protocol",
                f"{self.host}:{self.port} did not return an ONVIF response. "
                "This may not be an ONVIF endpoint.",
            ) from exc

        fault = next((el for el in root.iter() if _local(el.tag) == "Fault"), None)
        if fault is not None:
            reason = _find_text(fault, "Text") or "The camera reported a SOAP fault."
            subcode = ""
            for el in fault.iter():
                if _local(el.tag) == "Value" and el.text and "Onvif" not in (el.text or ""):
                    subcode += (el.text or "") + " "
            blob = (reason + " " + subcode).lower()
            if "notauthorized" in blob or "unauthorized" in blob or "auth" in blob:
                raise OnvifError("auth", "The camera rejected the supplied username or password.")
            if "actionnotsupported" in blob or "optionalfeature" in blob:
                raise OnvifError("unsupported", f"The camera does not support this ONVIF request: {reason}")
            raise OnvifError("protocol", f"The camera reported: {reason}")
        return root

    # -- operations ------------------------------------------------------

    def probe(self) -> None:
        """Unauthenticated liveness check; ONVIF requires this to be open."""
        self._post(self.device_url, "<tds:GetSystemDateAndTime/>", authenticated=False)

    def device_information(self) -> DeviceInfo:
        root = self._post(self.device_url, "<tds:GetDeviceInformation/>")
        return DeviceInfo(
            manufacturer=_find_text(root, "Manufacturer"),
            model=_find_text(root, "Model"),
            firmware=_find_text(root, "FirmwareVersion"),
            serial=_find_text(root, "SerialNumber"),
        )

    def media_service_url(self) -> str:
        """Locate the media service, falling back to the conventional path."""
        try:
            root = self._post(self.device_url, "<tds:GetCapabilities><tds:Category>Media</tds:Category></tds:GetCapabilities>")
            for el in root.iter():
                if _local(el.tag) == "Media":
                    xaddr = _find_text(el, "XAddr")
                    if xaddr:
                        return self._rewrite_host(xaddr)
        except OnvifError as exc:
            if exc.kind == "auth":
                raise
            log.debug("GetCapabilities failed (%s); falling back to default media path", exc)
        return f"http://{self._bracket(self._resolved.ip)}:{self.port}/onvif/media_service"

    def _rewrite_host(self, xaddr: str) -> str:
        """Cameras often advertise an unroutable XAddr; keep our vetted address."""
        try:
            parsed = httpx.URL(xaddr)
        except Exception:
            return xaddr
        return str(parsed.copy_with(host=self._resolved.ip, port=parsed.port or self.port))

    def profiles(self, media_url: str) -> list[OnvifProfile]:
        root = self._post(media_url, "<trt:GetProfiles/>")
        out: list[OnvifProfile] = []
        for node in _find_all(root, "Profiles"):
            token = node.attrib.get("token") or node.attrib.get("{http://www.onvif.org/ver10/schema}token")
            if not token:
                continue
            name = _find_text(node, "Name") or token
            encoding = resolution = None
            fps = None
            for enc in node.iter():
                if _local(enc.tag) == "VideoEncoderConfiguration":
                    encoding = _find_text(enc, "Encoding")
                    width = _find_text(enc, "Width")
                    height = _find_text(enc, "Height")
                    if width and height:
                        resolution = f"{width}x{height}"
                    frame_rate = _find_text(enc, "FrameRateLimit")
                    if frame_rate and frame_rate.isdigit():
                        fps = int(frame_rate)
                    break
            out.append(OnvifProfile(token=token, name=name, encoding=encoding,
                                    resolution=resolution, fps=fps))
        if not out:
            raise OnvifError("unsupported", "The camera did not report any media profiles.")
        return out

    def stream_uri(self, media_url: str, profile_token: str) -> str:
        body = (
            "<trt:GetStreamUri><trt:StreamSetup>"
            "<tt:Stream>RTP-Unicast</tt:Stream>"
            "<tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport>"
            f"</trt:StreamSetup><trt:ProfileToken>{_xml_escape(profile_token)}</trt:ProfileToken>"
            "</trt:GetStreamUri>"
        )
        root = self._post(media_url, body)
        uri = _find_text(root, "Uri")
        if not uri:
            raise OnvifError("unsupported", "The camera did not return a stream URI.")
        return uri

    def snapshot_uri(self, media_url: str, profile_token: str) -> str | None:
        body = f"<trt:GetSnapshotUri><trt:ProfileToken>{_xml_escape(profile_token)}</trt:ProfileToken></trt:GetSnapshotUri>"
        try:
            root = self._post(media_url, body)
        except OnvifError as exc:
            if exc.kind in ("unsupported", "protocol"):
                return None
            raise
        return _find_text(root, "Uri")


def build_client(host: str, port: int | None, path: str | None,
                 username: str | None, password: str | None) -> OnvifClient:
    try:
        return OnvifClient(host, port or 80, path or "/onvif/device_service", username, password)
    except HostNotAllowed as exc:
        raise OnvifError("unreachable", str(exc)) from exc


# =====================================================================
# WS-Discovery
# =====================================================================

WS_DISCOVERY_GROUP = "239.255.255.250"
WS_DISCOVERY_PORT = 3702

_PROBE = """<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
            xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"
            xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
            xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
  <e:Header>
    <w:MessageID>uuid:{message_id}</w:MessageID>
    <w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
    <w:Action e:mustUnderstand="true">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>
  </e:Header>
  <e:Body>
    <d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe>
  </e:Body>
</e:Envelope>"""


@dataclass
class DiscoveredDevice:
    """A device that answered a discovery probe. Nothing here is authenticated."""

    address: str                  # the IP the reply came from
    xaddrs: list[str]             # device service URLs the device advertised
    scopes: list[str]
    types: str | None
    name: str | None              # parsed from an onvif scope, when present
    hardware: str | None


def _scope_value(scopes: list[str], key: str) -> str | None:
    prefix = f"onvif://www.onvif.org/{key}/"
    for scope in scopes:
        if scope.startswith(prefix):
            return unquote(scope[len(prefix):]) or None
    return None


def discover(timeout: float = 4.0, interface_ip: str | None = None) -> list[DiscoveredDevice]:
    """Ask the local network which ONVIF devices are present.

    This is WS-Discovery: one multicast question that devices choose to answer.
    It is not a port scan — no address is contacted that did not reply first,
    and no credentials are sent or needed.

    Timeouts are normal. A network that blocks multicast, or a camera on a
    different VLAN, simply produces nothing; that means "not found this way",
    never "not present", and the caller is expected to say so.
    """
    message_id = str(uuid.uuid4())
    payload = _PROBE.format(message_id=message_id).encode("utf-8")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        if interface_ip:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                            socket.inet_aton(interface_ip))
            sock.bind((interface_ip, 0))
        else:
            sock.bind(("", 0))
        sock.settimeout(0.5)
        # Sent more than once: UDP is lossy and a dropped probe looks exactly
        # like an absent camera.
        for _ in range(2):
            sock.sendto(payload, (WS_DISCOVERY_GROUP, WS_DISCOVERY_PORT))

        deadline = time.monotonic() + timeout
        found: dict[str, DiscoveredDevice] = {}
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            device = _parse_probe_match(data, addr[0])
            if device and device.address not in found:
                found[device.address] = device
        return sorted(found.values(), key=lambda d: d.address)
    finally:
        sock.close()


def _parse_probe_match(data: bytes, source_ip: str) -> DiscoveredDevice | None:
    try:
        root = ET.fromstring(data.decode("utf-8", "replace"))
    except ET.ParseError:
        return None
    match = next(iter(_find_all(root, "ProbeMatch")), None)
    if match is None:
        return None
    xaddrs = (_find_text(match, "XAddrs") or "").split()
    scopes = (_find_text(match, "Scopes") or "").split()
    return DiscoveredDevice(
        address=source_ip,
        xaddrs=xaddrs,
        scopes=scopes,
        types=_find_text(match, "Types"),
        name=_scope_value(scopes, "name"),
        hardware=_scope_value(scopes, "hardware"),
    )
