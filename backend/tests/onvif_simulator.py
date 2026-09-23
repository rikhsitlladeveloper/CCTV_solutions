"""A minimal fake ONVIF device, good enough to exercise the client end to end.

It speaks just the operations Numenor uses and can be told to reject
credentials, so the auth-failure path is covered too.
"""
from __future__ import annotations

import base64
import hashlib
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VALID_USER = "admin"
VALID_PASSWORD = "admin-pass"
SERVICE_PATHS = ("/onvif/device_service", "/onvif/media_service")


def _digest_matches(payload: str, password: str) -> bool:
    """Recompute the WS-Security password digest exactly as ONVIF specifies."""
    nonce = re.search(r"<Nonce[^>]*>([^<]*)</Nonce>", payload)
    created = re.search(r"<Created[^>]*>([^<]*)</Created>", payload)
    digest = re.search(r"<Password[^>]*>([^<]*)</Password>", payload)
    if not (nonce and created and digest):
        return False
    expected = base64.b64encode(
        hashlib.sha1(base64.b64decode(nonce.group(1)) + created.group(1).encode()
                     + password.encode()).digest()
    ).decode()
    return expected == digest.group(1)

DEVICE_INFO = """<tds:GetDeviceInformationResponse>
 <tds:Manufacturer>Numenor Optics</tds:Manufacturer>
 <tds:Model>NX-880</tds:Model>
 <tds:FirmwareVersion>4.2.1</tds:FirmwareVersion>
 <tds:SerialNumber>SN-00417</tds:SerialNumber>
</tds:GetDeviceInformationResponse>"""

CAPABILITIES = """<tds:GetCapabilitiesResponse><tds:Capabilities>
 <tt:Media><tt:XAddr>http://10.99.99.99:80/onvif/media_service</tt:XAddr></tt:Media>
</tds:Capabilities></tds:GetCapabilitiesResponse>"""

PROFILES = """<trt:GetProfilesResponse>
 <trt:Profiles token="profile_main" fixed="true">
  <tt:Name>MainStream</tt:Name>
  <tt:VideoEncoderConfiguration>
   <tt:Encoding>H264</tt:Encoding>
   <tt:Resolution><tt:Width>1920</tt:Width><tt:Height>1080</tt:Height></tt:Resolution>
   <tt:RateControl><tt:FrameRateLimit>25</tt:FrameRateLimit></tt:RateControl>
  </tt:VideoEncoderConfiguration>
 </trt:Profiles>
 <trt:Profiles token="profile_sub">
  <tt:Name>SubStream</tt:Name>
  <tt:VideoEncoderConfiguration>
   <tt:Encoding>H264</tt:Encoding>
   <tt:Resolution><tt:Width>640</tt:Width><tt:Height>360</tt:Height></tt:Resolution>
   <tt:RateControl><tt:FrameRateLimit>15</tt:FrameRateLimit></tt:RateControl>
  </tt:VideoEncoderConfiguration>
 </trt:Profiles>
</trt:GetProfilesResponse>"""

AUTH_FAULT = """<s:Fault>
 <s:Code><s:Value>s:Sender</s:Value>
  <s:Subcode><s:Value>ter:NotAuthorized</s:Value></s:Subcode></s:Code>
 <s:Reason><s:Text xml:lang="en">Sender not authorized</s:Text></s:Reason>
</s:Fault>"""

ENVELOPE = """<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
 xmlns:tds="http://www.onvif.org/ver10/device/wsdl"
 xmlns:trt="http://www.onvif.org/ver10/media/wsdl"
 xmlns:tt="http://www.onvif.org/ver10/schema"
 xmlns:ter="http://www.onvif.org/ver10/error">
<s:Body>{body}</s:Body></s:Envelope>"""


class _Handler(BaseHTTPRequestHandler):
    require_auth = True
    rtsp_host = "10.99.99.99"
    rtsp_port = 8554

    def log_message(self, *args):  # silence the test run
        pass

    def do_POST(self):  # noqa: N802 - http.server API
        path = self.path.split("?")[0]
        if path not in SERVICE_PATHS:
            return self.send_error(404, "No ONVIF service here")

        length = int(self.headers.get("Content-Length", 0))
        payload = self.rfile.read(length).decode("utf-8", "replace")
        action = self._action(payload)

        # GetSystemDateAndTime must answer without credentials, per ONVIF.
        if action != "GetSystemDateAndTime" and self.require_auth:
            user = re.search(r"<Username>([^<]*)</Username>", payload)
            if not user or user.group(1) != VALID_USER or not _digest_matches(payload, VALID_PASSWORD):
                return self._reply(AUTH_FAULT)

        if action == "GetSystemDateAndTime":
            return self._reply("<tds:GetSystemDateAndTimeResponse/>")
        if action == "GetDeviceInformation":
            return self._reply(DEVICE_INFO)
        if action == "GetCapabilities":
            return self._reply(CAPABILITIES)
        if action == "GetProfiles":
            return self._reply(PROFILES)
        if action == "GetStreamUri":
            token = re.search(r"<trt:ProfileToken>([^<]*)</trt:ProfileToken>", payload)
            path = "main" if (token and token.group(1) == "profile_main") else "sub"
            uri = f"rtsp://{self.rtsp_host}:{self.rtsp_port}/{path}"
            return self._reply(
                f"<trt:GetStreamUriResponse><tt:MediaUri><tt:Uri>{uri}</tt:Uri>"
                "</tt:MediaUri></trt:GetStreamUriResponse>")
        if action == "GetSnapshotUri":
            return self._reply(
                "<trt:GetSnapshotUriResponse><tt:MediaUri>"
                f"<tt:Uri>http://10.99.99.99:{self.server.server_address[1]}/snap.jpg</tt:Uri>"
                "</tt:MediaUri></trt:GetSnapshotUriResponse>")
        return self._reply("<s:Fault><s:Reason><s:Text>Unsupported</s:Text></s:Reason></s:Fault>")

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/snap.jpg"):
            # A 1x1 JPEG is enough to prove the fetch path works.
            body = bytes.fromhex(
                "ffd8ffdb004300ff ffffffffffffffffff ffffffffffffffffff"
                "ffffffffffffffffff ffffffffffffffffff ffffffffffffffffff"
                "ffffffffffffffffff ffffffffffffffffff".replace(" ", "")
            ) + b"\xff\xd9"
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    @staticmethod
    def _action(payload: str) -> str:
        match = re.search(r"<(?:\w+:)?(Get\w+)", payload)
        return match.group(1) if match else ""

    def _reply(self, body: str) -> None:
        data = ENVELOPE.format(body=body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/soap+xml; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class OnvifSimulator:
    def __init__(self, require_auth: bool = True,
                 rtsp_host: str = "10.99.99.99", rtsp_port: int = 8554):
        handler = type("Handler", (_Handler,), {
            "require_auth": require_auth, "rtsp_host": rtsp_host, "rtsp_port": rtsp_port,
        })
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def __enter__(self) -> "OnvifSimulator":
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()
