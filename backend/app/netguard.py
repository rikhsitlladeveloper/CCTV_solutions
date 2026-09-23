"""Guard rails for outbound requests to camera endpoints.

The platform only ever talks to hosts an operator explicitly registered or
typed into the wizard.  It never sweeps the factory network, and it refuses to
be pointed at arbitrary backend URLs, cloud metadata services or unrelated
infrastructure.
"""
from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass

from . import config

# Cloud metadata and other endpoints that must never be reachable through us.
_DENY_ADDRS = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("fd00:ec2::254"),
}

# Ports a camera may realistically answer on. Keeps a registered "camera" from
# being used as a probe against databases, admin panels or message brokers.
ALLOWED_PORTS = set(range(1, 65536)) - {
    22, 23, 25, 135, 137, 138, 139, 445, 465, 587, 1433, 1521,
    2375, 2376, 3306, 3389, 5432, 5672, 6379, 9200, 11211, 27017,
}


class HostNotAllowed(ValueError):
    """Raised when a host or port fails the outbound policy."""


@dataclass(frozen=True)
class ResolvedHost:
    hostname: str
    ip: str


def _extra_networks() -> list[ipaddress._BaseNetwork]:
    nets = []
    for cidr in config.EXTRA_ALLOWED_CIDRS:
        try:
            nets.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            continue
    return nets


def _check_ip(ip: ipaddress._BaseAddress, hostname: str) -> None:
    if ip in _DENY_ADDRS:
        raise HostNotAllowed(f"Address {ip} is blocked by policy.")
    if ip.is_loopback:
        if config.ALLOW_LOOPBACK_CAMERA_HOSTS:
            return
        raise HostNotAllowed(
            f"'{hostname}' resolves to the loopback address. Point the camera at its LAN address."
        )
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        raise HostNotAllowed(f"Address {ip} is not a valid camera address.")
    if any(ip in net for net in _extra_networks()):
        return
    if ip.is_private or ip.is_link_local:
        return
    if config.ALLOW_PUBLIC_CAMERA_HOSTS:
        return
    raise HostNotAllowed(
        f"'{hostname}' resolves to the public address {ip}. Numenor only contacts cameras on the "
        "factory LAN. Add the range to NUMENOR_EXTRA_CIDRS if this is intended."
    )


def resolve_camera_host(hostname: str, port: int | None = None) -> ResolvedHost:
    """Resolve and vet a camera host. Returns the pinned IP to connect to.

    Connecting to the returned IP (rather than re-resolving the name) closes the
    DNS-rebinding window between the check and the connection.
    """
    hostname = (hostname or "").strip()
    if not hostname:
        raise HostNotAllowed("No camera address was supplied.")
    if "/" in hostname or "@" in hostname or " " in hostname:
        raise HostNotAllowed("The camera address must be a bare IP address or hostname.")

    if port is not None and port not in ALLOWED_PORTS:
        raise HostNotAllowed(f"Port {port} is not permitted for camera traffic.")

    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None

    if literal is not None:
        _check_ip(literal, hostname)
        return ResolvedHost(hostname=hostname, ip=str(literal))

    try:
        infos = socket.getaddrinfo(hostname, port or 80, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise HostNotAllowed(f"'{hostname}' could not be resolved.") from exc

    if not infos:
        raise HostNotAllowed(f"'{hostname}' could not be resolved.")

    addr = infos[0][4][0]
    ip = ipaddress.ip_address(addr)
    _check_ip(ip, hostname)
    return ResolvedHost(hostname=hostname, ip=str(ip))
