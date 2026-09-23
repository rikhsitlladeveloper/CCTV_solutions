"""Schema migrations.

The original release created its schema with ``Base.metadata.create_all``, which
happily adds *new tables* to an existing database but never alters existing ones.
The calibration work adds columns to ``cameras`` and ``floor_plans``, so those
need real migration steps.

Each step is small, ordered, idempotent and recorded in ``schema_migrations``, so
running an upgrade twice is harmless and a partially-applied upgrade resumes
where it stopped.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from collections.abc import Callable

from sqlalchemy import Connection, inspect, text

log = logging.getLogger("numenor.migrations")


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[Connection], None]


def _columns(conn: Connection, table: str) -> set[str]:
    inspector = inspect(conn)
    if table not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_columns(table)}


def _add_column(conn: Connection, table: str, column: str, ddl_type: str) -> None:
    """Add a column unless it is already there."""
    if table not in inspect(conn).get_table_names():
        return          # create_all will build it complete
    if column in _columns(conn, table):
        return
    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
    log.info("migration: added %s.%s", table, column)


def _m001_camera_coordinate_system(conn: Connection) -> None:
    _add_column(conn, "cameras", "coordinate_system_id", "INTEGER")


def _m002_floor_plan_world_alignment(conn: Connection) -> None:
    for column, ddl in [
        ("world_coordinate_system_id", "INTEGER"),
        ("world_metres_per_pixel", "FLOAT"),
        ("world_origin_x", "FLOAT"),
        ("world_origin_y", "FLOAT"),
        ("world_rotation_deg", "FLOAT"),
    ]:
        _add_column(conn, "floor_plans", column, ddl)


def _m003_indexes(conn: Connection) -> None:
    statements = [
        "CREATE INDEX IF NOT EXISTS ix_calibration_active "
        "ON calibration_revisions (camera_id, is_active)",
        "CREATE INDEX IF NOT EXISTS ix_observation_camera "
        "ON point_observations (camera_id, role)",
        "CREATE INDEX IF NOT EXISTS ix_intrinsics_active "
        "ON camera_intrinsics (camera_id, is_active)",
    ]
    tables = set(inspect(conn).get_table_names())
    for stmt in statements:
        target = stmt.split(" ON ")[1].split(" ")[0]
        if target in tables:
            conn.execute(text(stmt))


MIGRATIONS: list[Migration] = [
    Migration(1, "camera coordinate system reference", _m001_camera_coordinate_system),
    Migration(2, "floor plan world alignment", _m002_floor_plan_world_alignment),
    Migration(3, "calibration lookup indexes", _m003_indexes),
]

SCHEMA_VERSION = max(m.version for m in MIGRATIONS)


def _ensure_version_table(conn: Connection) -> None:
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY,"
        " name TEXT NOT NULL,"
        " applied_at TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    ))


def applied_versions(conn: Connection) -> set[int]:
    _ensure_version_table(conn)
    rows = conn.execute(text("SELECT version FROM schema_migrations")).fetchall()
    return {int(r[0]) for r in rows}


def run_migrations(engine) -> list[str]:
    """Apply any outstanding migrations. Returns the names that ran."""
    applied: list[str] = []
    with engine.begin() as conn:
        done = applied_versions(conn)
        for migration in sorted(MIGRATIONS, key=lambda m: m.version):
            if migration.version in done:
                continue
            migration.apply(conn)
            conn.execute(
                text("INSERT INTO schema_migrations (version, name) VALUES (:v, :n)"),
                {"v": migration.version, "n": migration.name},
            )
            applied.append(f"{migration.version:03d} {migration.name}")
    if applied:
        log.info("Applied %d migration(s): %s", len(applied), ", ".join(applied))
    return applied
