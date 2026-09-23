"""Operator authentication for the configuration and preview APIs.

Uses scrypt password hashing and HMAC-signed bearer tokens from the standard
library, so there is no additional dependency surface for auth.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import config

log = logging.getLogger("numenor.auth")

_SCRYPT = dict(n=2 ** 14, r=8, p=1, dklen=32)
TOKEN_KEY_FILE = config.SECRET_DIR / "session.key"
ADMIN_FILE = config.SECRET_DIR / "operator.json"

_bearer = HTTPBearer(auto_error=False)


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _token_key() -> bytes:
    env = os.environ.get("NUMENOR_SESSION_KEY")
    if env:
        return env.encode()
    config.SECRET_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    if TOKEN_KEY_FILE.exists():
        return TOKEN_KEY_FILE.read_bytes().strip()
    key = secrets.token_bytes(32)
    fd = os.open(TOKEN_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    return key


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${_b64e(salt)}${_b64e(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode(), salt=_b64d(salt_b64), **_SCRYPT)
        return hmac.compare_digest(digest, _b64d(digest_b64))
    except Exception:
        return False


# -- operator account ----------------------------------------------------
# Credentials live in a 0600 file outside the repo and outside the database.
# Nothing is seeded with a known password: if the operator does not supply one,
# a random password is generated once and printed to the server console.

def ensure_operator() -> tuple[str, str | None]:
    """Return (username, generated_password_or_None)."""
    username = os.environ.get("NUMENOR_ADMIN_USER", "operator")
    config.SECRET_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)

    env_password = os.environ.get("NUMENOR_ADMIN_PASSWORD")
    if env_password:
        _write_operator(username, hash_password(env_password))
        return username, None

    if ADMIN_FILE.exists():
        try:
            data = json.loads(ADMIN_FILE.read_text())
            if data.get("username") and data.get("password_hash"):
                return data["username"], None
        except (json.JSONDecodeError, OSError):
            log.warning("Operator file was unreadable; regenerating credentials.")

    generated = secrets.token_urlsafe(12)
    _write_operator(username, hash_password(generated))
    return username, generated


def _write_operator(username: str, password_hash: str) -> None:
    payload = json.dumps({"username": username, "password_hash": password_hash})
    fd = os.open(ADMIN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(payload)


def _load_operator() -> dict | None:
    if not ADMIN_FILE.exists():
        return None
    try:
        return json.loads(ADMIN_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def authenticate(username: str, password: str) -> bool:
    account = _load_operator()
    if not account:
        return False
    if not hmac.compare_digest(username, account.get("username", "")):
        # Still run the KDF so a wrong username is not faster than a wrong password.
        verify_password(password, account.get("password_hash", "scrypt$$"))
        return False
    return verify_password(password, account.get("password_hash", ""))


def issue_token(username: str) -> tuple[str, int]:
    expires = int(time.time()) + config.SESSION_TTL_S
    body = _b64e(json.dumps({"sub": username, "exp": expires}).encode())
    sig = _b64e(hmac.new(_token_key(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}", expires


def _decode_token(token: str) -> dict | None:
    try:
        body, sig = token.split(".")
    except ValueError:
        return None
    expected = _b64e(hmac.new(_token_key(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        claims = json.loads(_b64d(body))
    except (json.JSONDecodeError, ValueError):
        return None
    if claims.get("exp", 0) < time.time():
        return None
    return claims


async def current_operator(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    """Authenticated-route dependency.

    Accepts a bearer token, or a ``token`` query parameter for the endpoints the
    browser reaches through ``<img>``/``<video>`` tags, which cannot carry
    headers. Those tokens are the same short-lived session tokens.
    """
    token = creds.credentials if creds else request.query_params.get("token")
    claims = _decode_token(token) if token else None
    if not claims:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return claims["sub"]
