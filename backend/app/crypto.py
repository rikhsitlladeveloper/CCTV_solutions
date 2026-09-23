"""Credential encryption at rest.

The Fernet key is never stored in the database and never committed to source.
It is read from ``NUMENOR_SECRET_KEY`` or from a 0600 key file under
``NUMENOR_SECRET_DIR`` (``~/.numenor`` by default), generated on first boot.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from . import config

log = logging.getLogger("numenor.crypto")

KEY_FILE = config.SECRET_DIR / "credential.key"


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    env_key = os.environ.get("NUMENOR_SECRET_KEY")
    if env_key:
        return Fernet(env_key.encode())

    config.SECRET_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    if KEY_FILE.exists():
        return Fernet(KEY_FILE.read_bytes().strip())

    key = Fernet.generate_key()
    # Create with restrictive permissions before any bytes are written.
    fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    log.warning("Generated a new camera-credential encryption key at %s. "
                "Back it up: without it, stored camera passwords cannot be decrypted.", KEY_FILE)
    return Fernet(key)


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:  # wrong key, or tampered ciphertext
        raise RuntimeError(
            "Stored camera credential could not be decrypted with the current key."
        ) from exc
