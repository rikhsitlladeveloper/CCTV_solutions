"""Credential handling, encryption and the outbound network policy."""
from __future__ import annotations

import os
import stat

import pytest

from app import crypto
from app.media import sanitize
from app.netguard import HostNotAllowed, resolve_camera_host
from app.security import hash_password, verify_password


def test_password_roundtrip_and_key_stays_off_disk_readable():
    secret = "Camera!Pass#2026"
    token = crypto.encrypt(secret)
    assert secret not in token
    assert crypto.decrypt(token) == secret

    if crypto.KEY_FILE.exists():
        mode = stat.S_IMODE(os.stat(crypto.KEY_FILE).st_mode)
        assert mode == 0o600, "the credential key file must not be group/world readable"


def test_operator_password_hash_is_not_reversible():
    stored = hash_password("test-only-password")
    assert "test-only-password" not in stored
    assert stored.startswith("scrypt$")
    assert verify_password("test-only-password", stored)
    assert not verify_password("wrong", stored)


@pytest.mark.parametrize("text,expected", [
    ("rtsp://admin:hunter2@192.168.1.9:554/live", "rtsp://192.168.1.9:554/live"),
    ("http://user:pw@cam/snap.jpg failed", "http://cam/snap.jpg failed"),
    ("no credentials here", "no credentials here"),
])
def test_sanitize_strips_credentials_from_urls(text, expected):
    assert sanitize(text) == expected


@pytest.mark.parametrize("host", [
    "169.254.169.254",       # cloud metadata
    "8.8.8.8",               # public address space
    "example.com",           # public name
])
def test_public_and_metadata_hosts_are_refused(host):
    with pytest.raises(HostNotAllowed):
        resolve_camera_host(host, 80)


@pytest.mark.parametrize("host", ["192.168.10.5", "10.4.2.9", "172.16.3.1"])
def test_private_lan_hosts_are_allowed(host):
    assert resolve_camera_host(host, 554).ip == host


def test_ports_used_by_unrelated_services_are_refused():
    for port in (22, 3306, 5432, 6379):
        with pytest.raises(HostNotAllowed):
            resolve_camera_host("192.168.10.5", port)
    assert resolve_camera_host("192.168.10.5", 554)


@pytest.mark.parametrize("host", [
    "http://192.168.1.5/x",   # URL, not a host
    "admin@192.168.1.5",      # credentials smuggled into the host
    "192.168.1.5/onvif",      # path
    "",
])
def test_malformed_hosts_are_refused(host):
    with pytest.raises(HostNotAllowed):
        resolve_camera_host(host, 80)


def test_session_tokens_are_redacted_from_logs(caplog):
    """Image tags cannot send headers, so the token rides in the query string.
    It must not survive into the access log."""
    import logging

    from app.main import RedactTokens

    logger = logging.getLogger("numenor.test-redaction")
    logger.addFilter(RedactTokens())
    with caplog.at_level(logging.INFO, logger="numenor.test-redaction"):
        logger.info('GET /api/preview/abc/stream?token=eyJhbGciOi.secret HTTP/1.1 200')
        logger.info("%s", "/api/floor-plans/1/image?v=2&token=leaky-token-value")

    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "secret" not in text
    assert "leaky-token-value" not in text
    assert text.count("token=REDACTED") == 2
