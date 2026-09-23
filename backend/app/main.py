"""Numenor - camera registration and location setup.

On-premise by design: the API binds locally, all media processing happens on
this host, and nothing is sent to an external service.
"""
from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .db import init_db
from .media import preview_manager
from .netguard import HostNotAllowed
from .routers import auth, cameras, floorplans, locations, preview
from .security import ensure_operator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("numenor")

_TOKEN_IN_QUERY = re.compile(r"(token=)[^&\s\"']+")


class RedactTokens(logging.Filter):
    """Keep session tokens out of the logs.

    ``<img>`` and SVG ``<image>`` cannot send an Authorization header, so the
    snapshot, stream and floor-plan-image routes accept the session token as a
    query parameter. Access logs must not retain it.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            record.args = tuple(
                _TOKEN_IN_QUERY.sub(r"\1REDACTED", a) if isinstance(a, str) else a
                for a in record.args
            )
        if isinstance(record.msg, str):
            record.msg = _TOKEN_IN_QUERY.sub(r"\1REDACTED", record.msg)
        return True


for _name in ("uvicorn.access", "uvicorn.error", "numenor"):
    logging.getLogger(_name).addFilter(RedactTokens())

FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.ensure_dirs()
    init_db()
    username, generated = ensure_operator()
    if generated:
        log.warning(
            "\n%s\n  Numenor operator account created.\n  username: %s\n  password: %s\n"
            "  This password is shown once. Set NUMENOR_ADMIN_PASSWORD to choose your own.\n%s",
            "=" * 68, username, generated, "=" * 68,
        )
    else:
        log.info("Operator account ready (username: %s)", username)
    preview_manager.start_reaper()
    yield
    await preview_manager.shutdown()


app = FastAPI(
    title="Numenor Camera Registration",
    version="1.0.0",
    description="Camera registration, connection testing and floor-plan placement.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HostNotAllowed)
async def _host_not_allowed(_, exc: HostNotAllowed) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


app.include_router(auth.router)
app.include_router(locations.router)
app.include_router(cameras.router)
app.include_router(floorplans.router)
app.include_router(preview.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "numenor", "mode": "on-premise"}


# Serve the built frontend when it exists, so the platform runs as one process.
if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
