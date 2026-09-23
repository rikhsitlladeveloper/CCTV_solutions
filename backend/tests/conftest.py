"""Test fixtures.

Every test runs against a throwaway data directory and secret directory, so no
test ever touches a real deployment's database or encryption keys.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="numenor-tests-")
os.environ.setdefault("NUMENOR_DATA_DIR", f"{_TMP}/data")
os.environ.setdefault("NUMENOR_SECRET_DIR", f"{_TMP}/secrets")
os.environ.setdefault("NUMENOR_DATABASE_URL", f"sqlite:///{_TMP}/test.db")
os.environ.setdefault("NUMENOR_ADMIN_USER", "tester")
os.environ.setdefault("NUMENOR_ADMIN_PASSWORD", "test-only-password")
# The camera simulators in these tests run on this host.
os.environ.setdefault("NUMENOR_ALLOW_LOOPBACK", "1")


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def auth(client) -> dict[str, str]:
    res = client.post("/api/auth/login",
                      json={"username": "tester", "password": "test-only-password"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}
