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


def _create_index(conn: Connection, name: str, table: str, columns: list[str]) -> None:
    """Create an index only if the table and every column it needs are present.

    Normally ``create_all`` has already built the tables, but skipping instead of
    raising means a partially-built database degrades to a missing index rather
    than a server that will not start.
    """
    if table not in inspect(conn).get_table_names():
        return
    present = _columns(conn, table)
    missing = [c for c in columns if c not in present]
    if missing:
        log.warning("migration: skipping index %s; %s lacks %s", name, table, missing)
        return
    conn.execute(text(
        f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({', '.join(columns)})"))


def _m003_indexes(conn: Connection) -> None:
    _create_index(conn, "ix_calibration_active", "calibration_revisions", ["camera_id", "is_active"])
    _create_index(conn, "ix_observation_camera", "point_observations", ["camera_id", "role"])
    _create_index(conn, "ix_intrinsics_active", "camera_intrinsics", ["camera_id", "is_active"])


def _m004_workspace_fields(conn: Connection) -> None:
    """Shared floor frames gain workspace descriptions."""
    for column, ddl in [
        ("floor_id", "INTEGER"),
        ("x_axis_description", "TEXT"),
        ("workspace_width_m", "FLOAT"),
        ("workspace_length_m", "FLOAT"),
        ("surface_description", "TEXT"),
    ]:
        _add_column(conn, "coordinate_systems", column, ddl)


def _m005_reference_point_role(conn: Connection) -> None:
    """Points gain a default role. Existing points keep being fitted against,
    which is what they were already used for - nothing is reclassified."""
    _add_column(conn, "world_reference_points", "role", "VARCHAR(20)")
    if "world_reference_points" in inspect(conn).get_table_names():
        conn.execute(text(
            "UPDATE world_reference_points SET role = 'calibration' WHERE role IS NULL"))


def _m006_revision_coverage(conn: Connection) -> None:
    for column, ddl in [
        ("coverage_polygon_json", "TEXT"),
        ("stale_reason", "TEXT"),
    ]:
        _add_column(conn, "calibration_revisions", column, ddl)


def _m007_relationship_indexes(conn: Connection) -> None:
    _create_index(conn, "ix_zone_camera", "monitored_zones", ["camera_id", "kind"])
    _create_index(conn, "ix_relationship_pair", "camera_relationships",
                  ["camera_a_id", "camera_b_id", "kind"])


def _m008_visual_placement(conn: Connection) -> None:
    """Mount type and aim target, so a visual placement can be replayed."""
    for column, ddl in [
        ("mount_type", "VARCHAR(20)"),
        ("aim_target_x", "FLOAT"),
        ("aim_target_y", "FLOAT"),
        ("aim_target_z", "FLOAT"),
    ]:
        _add_column(conn, "calibration_revisions", column, ddl)


def _m009_scene_indexes(conn: Connection) -> None:
    _create_index(conn, "ix_scene_objects_scene", "scene_objects", ["scene_id", "kind"])
    _create_index(conn, "ix_camera_functions_camera", "camera_functions", ["camera_id", "kind"])
    _create_index(conn, "ix_checkpoints_session", "session_checkpoints", ["session_id"])


MIGRATIONS: list[Migration] = [
    Migration(1, "camera coordinate system reference", _m001_camera_coordinate_system),
    Migration(2, "floor plan world alignment", _m002_floor_plan_world_alignment),
    Migration(3, "calibration lookup indexes", _m003_indexes),
    Migration(4, "workspace fields on coordinate systems", _m004_workspace_fields),
    Migration(5, "reference point role", _m005_reference_point_role),
    Migration(6, "calibration coverage polygon", _m006_revision_coverage),
    Migration(7, "zone and relationship indexes", _m007_relationship_indexes),
    Migration(8, "visual placement mount and aim", _m008_visual_placement),
    Migration(9, "scene and function indexes", _m009_scene_indexes),
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
