"""Runtime configuration for the Numenor camera platform.

Every secret is sourced from the environment or from a key file that lives
outside the repository and outside the database.  Nothing secret is ever
committed here.
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("NUMENOR_DATA_DIR", BASE_DIR / "data"))
FLOORPLAN_DIR = DATA_DIR / "floorplans"
PHOTO_DIR = DATA_DIR / "photos"

# Secrets live here by default: outside the source tree and outside the DB.
SECRET_DIR = Path(os.environ.get("NUMENOR_SECRET_DIR", Path.home() / ".numenor"))

DATABASE_URL = os.environ.get("NUMENOR_DATABASE_URL", f"sqlite:///{DATA_DIR / 'numenor.db'}")

# Networking guard rails -------------------------------------------------
# Cameras live on the factory LAN, so private address space is permitted.
# Public address space is refused unless an operator opts in explicitly.
ALLOW_PUBLIC_CAMERA_HOSTS = os.environ.get("NUMENOR_ALLOW_PUBLIC_HOSTS", "0") == "1"
# Development and test rigs only: lets a camera simulator run on this host.
# Leave unset in production, where loopback is never a real camera.
ALLOW_LOOPBACK_CAMERA_HOSTS = os.environ.get("NUMENOR_ALLOW_LOOPBACK", "0") == "1"
EXTRA_ALLOWED_CIDRS = [c.strip() for c in os.environ.get("NUMENOR_EXTRA_CIDRS", "").split(",") if c.strip()]

# Probe / preview limits -------------------------------------------------
CONNECT_TIMEOUT_S = float(os.environ.get("NUMENOR_CONNECT_TIMEOUT", "4"))
ONVIF_TIMEOUT_S = float(os.environ.get("NUMENOR_ONVIF_TIMEOUT", "8"))
SNAPSHOT_TIMEOUT_S = float(os.environ.get("NUMENOR_SNAPSHOT_TIMEOUT", "15"))
MAX_PREVIEW_SESSIONS = int(os.environ.get("NUMENOR_MAX_PREVIEW_SESSIONS", "4"))
PREVIEW_IDLE_TIMEOUT_S = float(os.environ.get("NUMENOR_PREVIEW_IDLE_TIMEOUT", "30"))
PREVIEW_MAX_LIFETIME_S = float(os.environ.get("NUMENOR_PREVIEW_MAX_LIFETIME", "600"))

MAX_UPLOAD_BYTES = int(os.environ.get("NUMENOR_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))

SESSION_TTL_S = int(os.environ.get("NUMENOR_SESSION_TTL", str(12 * 3600)))

CORS_ORIGINS = [o.strip() for o in os.environ.get(
    "NUMENOR_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
).split(",") if o.strip()]

FFMPEG_BIN = os.environ.get("NUMENOR_FFMPEG", "ffmpeg")


def ensure_dirs() -> None:
    for d in (DATA_DIR, FLOORPLAN_DIR, PHOTO_DIR):
        d.mkdir(parents=True, exist_ok=True)
    SECRET_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
