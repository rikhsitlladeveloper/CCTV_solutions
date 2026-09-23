"""End-to-end calibration API: persistence, revisions, validation, projection, export."""
from __future__ import annotations

import json

import numpy as np
import pytest

from app.geometry import pose_from_look_at
from app.intrinsics import Intrinsics

WIDTH, HEIGHT = 1920, 1080

K = [[1380.0, 0.0, 958.0], [0.0, 1378.0, 543.0], [0.0, 0.0, 1.0]]
DIST = [-0.284, 0.083, 0.0006, -0.0004, -0.011]

POINTS = [
    ("RP-01", 2.0, 2.0, 0.0), ("RP-02", 12.0, 2.0, 0.0), ("RP-03", 22.0, 2.0, 0.0),
    ("RP-04", 2.0, 12.0, 0.0), ("RP-05", 12.0, 12.0, 0.0), ("RP-06", 22.0, 12.0, 0.0),
    ("RP-07", 7.0, 7.0, 0.0), ("RP-08", 17.0, 9.0, 0.0),
    ("RP-09", 12.0, 12.0, 2.4), ("RP-10", 12.0, 2.0, 2.1), ("RP-11", 7.0, 7.0, 1.6),
]

TRUTH_EYE = (2.0, 1.0, 5.2)
TRUTH_TARGET = (12.0, 10.0, 0.0)


@pytest.fixture(scope="module")
def intr() -> Intrinsics:
    return Intrinsics.from_payload(np.array(K), np.array(DIST), WIDTH, HEIGHT)


@pytest.fixture(scope="module")
def truth_pose():
    return pose_from_look_at(TRUTH_EYE, TRUTH_TARGET)


@pytest.fixture(scope="module")
def world(client, auth) -> dict:
    """A coordinate system, reference points and a camera with observations."""
    cs = client.post("/api/coordinate-systems", headers=auth, json={
        "name": "Calibration Test Frame",
        "origin_description": "Corner of columns A1/B1 at floor level.",
        "grid_min_x": -2, "grid_max_x": 28, "grid_min_y": -2, "grid_max_y": 26,
        "max_ground_error_m": 0.25, "min_reference_points": 6,
    })
    assert cs.status_code == 201, cs.text
    cs = cs.json()

    points = {}
    for code, x, y, z in POINTS:
        res = client.post("/api/reference-points", headers=auth, json={
            "coordinate_system_id": cs["id"], "code": code, "name": f"Point {code}",
            "x": x, "y": y, "z": z, "uncertainty_m": 0.005,
        })
        assert res.status_code == 201, res.text
        points[code] = res.json()

    area = client.post("/api/locations/resolve", headers=auth, json={
        "site": "Calib Site", "building": "Hall", "floor": "Ground", "area": "Bay"}).json()
    camera = client.post("/api/cameras", headers=auth, json={
        "name": "Calibration Test Camera", "host": "192.0.2.50",
        "connection_type": "rtsp", "area_id": area["id"],
    })
    assert camera.status_code == 201, camera.text

    return {"cs": cs, "points": points, "camera": camera.json()}


def _observations(world, intr, truth_pose, holdout_codes=()):
    payload = []
    for code, x, y, z in POINTS:
        cam = truth_pose.world_to_camera_points([[x, y, z]])
        pixels, in_front = intr.project_camera_points(cam)
        u, v = pixels[0]
        if not in_front[0] or not intr.contains_pixel(u, v):
            continue
        payload.append({
            "reference_point_id": world["points"][code]["id"],
            "pixel_u": float(u), "pixel_v": float(v),
            "image_width": WIDTH, "image_height": HEIGHT,
            "role": "holdout" if code in holdout_codes else "fit",
        })
    return payload


# -- coordinate systems --------------------------------------------------

def test_coordinate_system_exposes_the_conventions(client, auth, world):
    conventions = client.get("/api/coordinate-systems/conventions", headers=auth).json()["conventions"]
    assert "Rz(yaw)" in conventions["rpy_convention"]
    assert "straight up" in conventions["zero_rotation_note"]
    assert "opposite directions" in conventions["yaw_vs_heading"]
    assert "X right, Y down, Z forward" in conventions["camera_frame"]


def test_grid_must_be_coherent(client, auth):
    res = client.post("/api/coordinate-systems", headers=auth, json={
        "name": "Bad grid", "grid_min_x": 10, "grid_max_x": 5})
    assert res.status_code == 422
    assert "greater than the minimums" in res.text


def test_duplicate_coordinate_system_names_are_rejected(client, auth, world):
    res = client.post("/api/coordinate-systems", headers=auth,
                      json={"name": world["cs"]["name"]})
    assert res.status_code == 409


def test_reference_point_codes_are_unique_within_a_frame(client, auth, world):
    res = client.post("/api/reference-points", headers=auth, json={
        "coordinate_system_id": world["cs"]["id"], "code": "RP-01", "name": "Duplicate",
        "x": 1, "y": 1, "z": 0})
    assert res.status_code == 409


def test_aruco_id_alone_is_not_enough(client, auth, world):
    res = client.post("/api/reference-points", headers=auth, json={
        "coordinate_system_id": world["cs"]["id"], "code": "RP-ARUCO", "name": "Marker",
        "x": 5, "y": 5, "z": 0, "aruco_marker_id": 17})
    assert res.status_code == 422
    assert "does not fix anything in the world" in res.text


# -- intrinsics ----------------------------------------------------------

def test_fisheye_import_is_rejected(client, auth, world):
    res = client.post(f"/api/cameras/{world['camera']['id']}/intrinsics", headers=auth, json={
        "model": "fisheye", "camera_matrix": K, "distortion_coefficients": [0, 0, 0, 0],
        "image_width": WIDTH, "image_height": HEIGHT})
    assert res.status_code == 422
    assert "pinhole model only" in res.text


def test_malformed_camera_matrix_is_rejected(client, auth, world):
    res = client.post(f"/api/cameras/{world['camera']['id']}/intrinsics", headers=auth, json={
        "camera_matrix": [[1, 2], [3, 4]], "image_width": WIDTH, "image_height": HEIGHT})
    assert res.status_code == 422
    assert "nine elements" in res.text


def test_non_finite_values_are_rejected(client, auth, world):
    """NaN must produce a clean 422, including when it is echoed back in the error."""
    for body in (
        '{"camera_matrix": [[1380,0,958],[0,1378,543],[0,0,1]], '
        '"distortion_coefficients": [NaN], "image_width": 1920, "image_height": 1080}',
        '{"camera_matrix": [[1380,0,958],[0,1378,Infinity],[0,0,1]], '
        '"image_width": 1920, "image_height": 1080}',
    ):
        res = client.post(
            f"/api/cameras/{world['camera']['id']}/intrinsics",
            headers={**auth, "Content-Type": "application/json"}, content=body)
        assert res.status_code == 422, res.text
        res.json()          # the error response itself must be valid JSON


def test_non_finite_pose_values_are_rejected(client, auth, world):
    res = client.post(f"/api/cameras/{world['camera']['id']}/calibration/manual",
                      headers={**auth, "Content-Type": "application/json"},
                      content='{"coordinate_system_id": %d, "x": NaN, "y": 0, "z": 4, '
                              '"roll_deg": 0, "pitch_deg": 0, "yaw_deg": 0}'
                              % world["cs"]["id"])
    assert res.status_code == 422, res.text
    res.json()


def test_import_intrinsics_and_activate(client, auth, world):
    res = client.post(f"/api/cameras/{world['camera']['id']}/intrinsics", headers=auth, json={
        "label": "Bench calibration", "camera_matrix": K, "distortion_coefficients": DIST,
        "image_width": WIDTH, "image_height": HEIGHT, "rms_reprojection_error_px": 0.21,
        "lens_description": "4 mm fixed", "activate": True})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["is_active"] is True
    assert body["geometry_key"] == "1920x1080@rot0/crop:full"
    assert 60 < body["horizontal_fov_deg"] < 90
    world["intrinsics"] = body


# -- observations --------------------------------------------------------

def test_observation_outside_the_image_is_rejected(client, auth, world):
    res = client.put(f"/api/cameras/{world['camera']['id']}/observations", headers=auth, json={
        "observations": [{"reference_point_id": world["points"]["RP-01"]["id"],
                          "pixel_u": 5000, "pixel_v": 10,
                          "image_width": WIDTH, "image_height": HEIGHT}]})
    assert res.status_code == 422
    assert "outside a 1920px-wide image" in res.text


def test_save_observations(client, auth, world, intr, truth_pose):
    payload = _observations(world, intr, truth_pose, holdout_codes={"RP-06", "RP-08", "RP-11"})
    assert len(payload) >= 8, "synthetic camera should see most of the points"

    res = client.put(f"/api/cameras/{world['camera']['id']}/observations", headers=auth,
                     json={"observations": payload, "replace_existing": True})
    assert res.status_code == 200, res.text
    saved = res.json()
    assert len(saved) == len(payload)
    assert sum(1 for o in saved if o["role"] == "holdout") == 3


# -- solving -------------------------------------------------------------

def test_pose_solve_recovers_ground_truth(client, auth, world, truth_pose):
    res = client.post(f"/api/cameras/{world['camera']['id']}/calibration/pose", headers=auth, json={
        "coordinate_system_id": world["cs"]["id"],
        "image_width": WIDTH, "image_height": HEIGHT, "activate": True})
    assert res.status_code == 201, res.text
    revision = res.json()

    assert revision["method"] == "pnp"
    assert revision["status"] == "calibrated_unvalidated"
    got = np.array([revision["position"]["x"], revision["position"]["y"], revision["position"]["z"]])
    assert np.allclose(got, TRUTH_EYE, atol=0.02), got
    assert revision["metrics"]["mean_reprojection_error_px"] < 1.0
    assert revision["is_active"] is True
    assert revision["distortion_corrected"] is True
    world["revision"] = revision


def test_pose_solve_at_the_wrong_resolution_is_refused(client, auth, world):
    res = client.post(f"/api/cameras/{world['camera']['id']}/calibration/pose", headers=auth, json={
        "coordinate_system_id": world["cs"]["id"], "image_width": 1280, "image_height": 720})
    assert res.status_code == 422
    assert "calibrated for 1920x1080" in res.text


def test_homography_solve_stores_no_pose(client, auth, world):
    """A homography maps one plane; it must not be dressed up as a camera pose."""
    res = client.post(f"/api/cameras/{world['camera']['id']}/calibration/homography",
                      headers=auth, json={
                          "coordinate_system_id": world["cs"]["id"],
                          "image_width": WIDTH, "image_height": HEIGHT, "plane_z": 0.0})
    assert res.status_code == 201, res.text
    revision = res.json()
    assert revision["method"] == "homography"
    assert revision["position"] is None
    assert revision["quaternion_wxyz"] is None
    assert revision["has_homography"] is True
    assert revision["is_active"] is False, "solving must not auto-activate over a working revision"


def test_revisions_accumulate_and_only_one_is_active(client, auth, world):
    revisions = client.get(f"/api/cameras/{world['camera']['id']}/calibration/revisions",
                           headers=auth).json()
    assert len(revisions) >= 2
    assert sum(1 for r in revisions if r["is_active"]) == 1
    numbers = [r["revision_number"] for r in revisions]
    assert len(set(numbers)) == len(numbers)


def test_manual_adjustment_is_a_separate_unvalidated_revision(client, auth, world):
    parent = world["revision"]
    res = client.post(
        f"/api/cameras/{world['camera']['id']}/calibration/revisions/{parent['id']}/adjust",
        headers=auth, json={"x": 2.3, "y": 1.1, "z": 5.25,
                            "roll_deg": -92.0, "pitch_deg": 1.0, "yaw_deg": 40.0,
                            "reason": "Nudged to match the tape measure."})
    assert res.status_code == 201, res.text
    adjusted = res.json()
    assert adjusted["method"] == "manual"
    assert adjusted["status"] == "approximate"
    assert adjusted["parent_revision_id"] == parent["id"]
    assert adjusted["is_active"] is False, "a hand correction must not silently take over"
    assert any("no longer applies" in w for w in adjusted["warnings"])


def test_activation_is_explicit(client, auth, world):
    revisions = client.get(f"/api/cameras/{world['camera']['id']}/calibration/revisions",
                           headers=auth).json()
    pnp = next(r for r in revisions if r["method"] == "pnp")
    res = client.post(
        f"/api/cameras/{world['camera']['id']}/calibration/revisions/{pnp['id']}/activate",
        headers=auth, json={"confirm": True})
    assert res.status_code == 200
    assert res.json()["is_active"] is True

    after = client.get(f"/api/cameras/{world['camera']['id']}/calibration/revisions",
                       headers=auth).json()
    assert sum(1 for r in after if r["is_active"]) == 1


# -- validation ----------------------------------------------------------

def test_validation_uses_held_out_points(client, auth, world):
    revisions = client.get(f"/api/cameras/{world['camera']['id']}/calibration/revisions",
                           headers=auth).json()
    pnp = next(r for r in revisions if r["method"] == "pnp" and r["is_active"])

    res = client.post(
        f"/api/cameras/{world['camera']['id']}/calibration/revisions/{pnp['id']}/validate",
        headers=auth, json={"reviewer": "test-installer"})
    assert res.status_code == 200, res.text
    validation = res.json()["validation"]

    assert validation["holdout_point_count"] == 3
    assert validation["holdout_ground_error_m"] < 0.05
    assert validation["passed"] is True
    assert res.json()["revision"]["status"] == "validated"
    # Fitting error and ground error are reported separately and explained.
    assert "fit_reprojection_error_px" in validation["interpretation"]
    assert "NOT evidence of real-world accuracy" in validation["interpretation"]["fit_reprojection_error_px"]


def test_manual_placement_cannot_be_validated(client, auth, world):
    revisions = client.get(f"/api/cameras/{world['camera']['id']}/calibration/revisions",
                           headers=auth).json()
    manual = next(r for r in revisions if r["method"] == "manual")
    res = client.post(
        f"/api/cameras/{world['camera']['id']}/calibration/revisions/{manual['id']}/validate",
        headers=auth, json={})
    validation = res.json()["validation"]
    assert validation["passed"] is False
    assert any("cannot be validated" in m for m in validation["messages"])


# -- projection ----------------------------------------------------------

def test_image_to_world_and_back_round_trips(client, auth, world, intr, truth_pose):
    x, y = 9.0, 6.0
    cam = truth_pose.world_to_camera_points([[x, y, 0.0]])
    pixels, _ = intr.project_camera_points(cam)
    u, v = pixels[0]

    forward = client.post(f"/api/cameras/{world['camera']['id']}/projection/image-to-world",
                          headers=auth, json={"pixel_u": float(u), "pixel_v": float(v)})
    assert forward.status_code == 200, forward.text
    got = forward.json()
    assert abs(got["world"][0] - x) < 0.05 and abs(got["world"][1] - y) < 0.05
    assert got["method"] == "pose_ray_plane"
    assert got["is_approximate"] is False

    back = client.post(f"/api/cameras/{world['camera']['id']}/projection/world-to-image",
                       headers=auth, json={"x": x, "y": y, "z": 0.0})
    assert back.status_code == 200
    assert abs(back.json()["pixel"][0] - u) < 1.0
    assert back.json()["in_image"] is True


def test_projecting_the_sky_is_refused(client, auth, world):
    """A pixel above the horizon has no floor position and must say so."""
    res = client.post(f"/api/cameras/{world['camera']['id']}/projection/image-to-world",
                      headers=auth, json={"pixel_u": 960.0, "pixel_v": 1.0})
    assert res.status_code == 422
    assert "horizon" in res.text or "behind the camera" in res.text


def test_point_behind_the_camera_has_no_pixel(client, auth, world):
    res = client.post(f"/api/cameras/{world['camera']['id']}/projection/world-to-image",
                      headers=auth, json={"x": -40.0, "y": -40.0, "z": 0.0})
    assert res.status_code == 422
    assert "behind the camera" in res.text


def test_projection_overlay_reports_reprojection_error(client, auth, world):
    res = client.get(f"/api/cameras/{world['camera']['id']}/projection/overlay", headers=auth)
    assert res.status_code == 200
    body = res.json()
    assert body["reference_points"]
    errors = [p["reprojection_error_px"] for p in body["reference_points"]
              if "reprojection_error_px" in p]
    assert errors and max(errors) < 5.0
    assert body["is_approximate"] is False


# -- coordinate-system protection ---------------------------------------

def test_redefining_a_frame_needs_confirmation_and_flags_calibrations(client, auth, world):
    res = client.patch(f"/api/coordinate-systems/{world['cs']['id']}", headers=auth,
                       json={"floor_plane_z": 0.5})
    assert res.status_code == 409
    assert "redefines what coordinates" in res.text

    # Cosmetic edits stay easy.
    ok = client.patch(f"/api/coordinate-systems/{world['cs']['id']}", headers=auth,
                      json={"notes": "Surveyed 2026-09"})
    assert ok.status_code == 200

    forced = client.patch(f"/api/coordinate-systems/{world['cs']['id']}", headers=auth,
                          json={"floor_plane_z": 0.5, "confirm_redefinition": True,
                                "redefinition_reason": "Floor screed added"})
    assert forced.status_code == 200
    assert forced.json()["definition_revision"] == 2

    revisions = client.get(f"/api/cameras/{world['camera']['id']}/calibration/revisions",
                           headers=auth).json()
    active = next(r for r in revisions if r["is_active"])
    assert active["status"] == "needs_recalibration"

    # Put it back so later tests work from a sane state.
    client.patch(f"/api/coordinate-systems/{world['cs']['id']}", headers=auth,
                 json={"floor_plane_z": 0.0, "confirm_redefinition": True,
                       "redefinition_reason": "revert"})


# -- export --------------------------------------------------------------

def test_export_is_documented_and_credential_free(client, auth, world):
    res = client.get(f"/api/cameras/{world['camera']['id']}/calibration/export", headers=auth)
    assert res.status_code == 200, res.text
    payload = res.json()

    assert payload["format"] == "numenor.camera-calibration"
    assert payload["conventions"]["rpy_convention"]
    assert payload["calibration"]["pose"]["T_world_camera"]
    assert payload["calibration"]["intrinsics"]["camera_matrix"]
    assert payload["calibration"]["source_image"] == {"width": WIDTH, "height": HEIGHT}
    assert payload["limitations"]

    text = json.dumps(payload).lower()
    for forbidden in ("password", "rtsp://", "192.0.2.50", "host"):
        assert forbidden not in text, f"export leaked {forbidden!r}"


# -- multi-camera --------------------------------------------------------

def test_multi_camera_check_is_labelled_a_consistency_check(client, auth, world, intr, truth_pose):
    """Two cameras looking at one point should agree; the wording must not overclaim."""
    second_truth = pose_from_look_at((22.0, 2.0, 5.0), (10.0, 12.0, 0.0))
    area = client.post("/api/locations/resolve", headers=auth, json={
        "site": "Calib Site", "building": "Hall", "floor": "Ground", "area": "Bay"}).json()
    cam2 = client.post("/api/cameras", headers=auth, json={
        "name": "Second Test Camera", "host": "192.0.2.51",
        "connection_type": "rtsp", "area_id": area["id"]}).json()

    client.post(f"/api/cameras/{cam2['id']}/intrinsics", headers=auth, json={
        "camera_matrix": K, "distortion_coefficients": DIST,
        "image_width": WIDTH, "image_height": HEIGHT, "activate": True})

    payload = []
    for code, x, y, z in POINTS:
        cam = second_truth.world_to_camera_points([[x, y, z]])
        pixels, in_front = intr.project_camera_points(cam)
        u, v = pixels[0]
        if in_front[0] and intr.contains_pixel(u, v):
            payload.append({"reference_point_id": world["points"][code]["id"],
                            "pixel_u": float(u), "pixel_v": float(v),
                            "image_width": WIDTH, "image_height": HEIGHT, "role": "fit"})
    client.put(f"/api/cameras/{cam2['id']}/observations", headers=auth,
               json={"observations": payload, "replace_existing": True})
    solved = client.post(f"/api/cameras/{cam2['id']}/calibration/pose", headers=auth, json={
        "coordinate_system_id": world["cs"]["id"],
        "image_width": WIDTH, "image_height": HEIGHT, "activate": True})
    assert solved.status_code == 201, solved.text

    # Same physical floor point, seen by both.
    target = np.array([[10.0, 8.0, 0.0]])
    marks = []
    for camera_id, pose in ((world["camera"]["id"], truth_pose), (cam2["id"], second_truth)):
        pixels, _ = intr.project_camera_points(pose.world_to_camera_points(target))
        marks.append({"camera_id": camera_id,
                      "pixel_u": float(pixels[0][0]), "pixel_v": float(pixels[0][1])})

    res = client.post("/api/factory-map/check-ground-point", headers=auth, json={
        "coordinate_system_id": world["cs"]["id"], "label": "Aisle marker", "marks": marks})
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["usable_count"] == 2
    assert body["max_disagreement_m"] < 0.1
    assert "consistency check" in body["interpretation"]
    assert "not an accuracy measurement" in body["interpretation"]


def test_factory_map_reports_footprints_and_conventions(client, auth, world):
    res = client.get(f"/api/factory-map/{world['cs']['id']}", headers=auth)
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["coordinate_system"]["name"] == world["cs"]["name"]
    assert len(body["cameras"]) >= 2
    assert body["reference_points"]
    assert "Rz(yaw)" in body["conventions"]["rpy_convention"]

    calibrated = [c for c in body["cameras"] if c["position"]]
    assert calibrated
    assert any(c["floor_polygon"] for c in calibrated)
    assert all(c["is_approximate"] is False for c in calibrated)


def test_coverage_is_labelled_geometric_not_actual(client, auth, world):
    res = client.get(f"/api/factory-map/{world['cs']['id']}/coverage", headers=auth)
    assert res.status_code == 200
    body = res.json()
    assert "no account of machinery" in body["interpretation"]
    assert "not as a guarantee" in body["interpretation"]


# -- manual placement ----------------------------------------------------

def test_manual_placement_is_always_approximate(client, auth, world):
    area = client.post("/api/locations/resolve", headers=auth, json={
        "site": "Calib Site", "building": "Hall", "floor": "Ground", "area": "Bay"}).json()
    camera = client.post("/api/cameras", headers=auth, json={
        "name": "Manual Placement Camera", "host": "192.0.2.52",
        "connection_type": "rtsp", "area_id": area["id"]}).json()

    res = client.post(f"/api/cameras/{camera['id']}/calibration/manual", headers=auth, json={
        "coordinate_system_id": world["cs"]["id"],
        "x": 5.0, "y": 5.0, "z": 4.0,
        "roll_deg": -95.0, "pitch_deg": 0.0, "yaw_deg": 30.0,
        "approx_hfov_deg": 78.0, "approx_range_m": 15.0, "activate": True})
    assert res.status_code == 201, res.text
    revision = res.json()

    assert revision["method"] == "manual"
    assert revision["status"] == "approximate"
    assert any("approximate" in w for w in revision["warnings"])

    projected = client.post(f"/api/cameras/{camera['id']}/projection/image-to-world",
                            headers=auth, json={"pixel_u": 960.0, "pixel_v": 900.0})
    if projected.status_code == 200:
        body = projected.json()
        assert body["is_approximate"] is True
        assert any("approximation" in c for c in body["caveats"])


def test_pose_solve_without_intrinsics_explains_the_alternative(client, auth, world):
    area = client.post("/api/locations/resolve", headers=auth, json={
        "site": "Calib Site", "building": "Hall", "floor": "Ground", "area": "Bay"}).json()
    camera = client.post("/api/cameras", headers=auth, json={
        "name": "No Intrinsics Camera", "host": "192.0.2.53",
        "connection_type": "rtsp", "area_id": area["id"]}).json()

    res = client.post(f"/api/cameras/{camera['id']}/calibration/pose", headers=auth, json={
        "coordinate_system_id": world["cs"]["id"],
        "image_width": WIDTH, "image_height": HEIGHT})
    assert res.status_code == 422
    assert "needs camera intrinsics" in res.text
    assert "homography needs no intrinsics" in res.text


# -- schema migration ----------------------------------------------------

def test_migrations_upgrade_a_pre_calibration_database(tmp_path):
    """A database from the first release must gain the new columns, not break."""
    import sqlite3

    from sqlalchemy import create_engine, inspect

    from app.migrations import SCHEMA_VERSION, applied_versions, run_migrations

    legacy = tmp_path / "legacy.db"
    conn = sqlite3.connect(legacy)
    conn.executescript("""
        CREATE TABLE cameras (id INTEGER PRIMARY KEY, name TEXT, host TEXT);
        CREATE TABLE floor_plans (id INTEGER PRIMARY KEY, floor_id INTEGER, image_path TEXT);
        INSERT INTO cameras (name, host) VALUES ('Legacy cam', '10.0.0.5');
        INSERT INTO floor_plans (floor_id, image_path) VALUES (1, 'plan.png');
    """)
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite:///{legacy}")
    applied = run_migrations(engine)
    assert applied, "migrations should run on a legacy database"

    inspector = inspect(engine)
    camera_cols = {c["name"] for c in inspector.get_columns("cameras")}
    plan_cols = {c["name"] for c in inspector.get_columns("floor_plans")}
    assert "coordinate_system_id" in camera_cols
    assert {"world_metres_per_pixel", "world_origin_x", "world_rotation_deg"} <= plan_cols

    # Existing rows survive.
    with engine.connect() as c:
        from sqlalchemy import text
        assert c.execute(text("SELECT COUNT(*) FROM cameras")).scalar() == 1

    # Re-running is a no-op.
    assert run_migrations(engine) == []
    with engine.connect() as c:
        assert applied_versions(c) == {m for m in range(1, SCHEMA_VERSION + 1)}


# -- demo data -----------------------------------------------------------

def test_demo_data_is_labelled_synthetic_and_never_claims_a_live_camera(client, auth):
    from app.db import SessionLocal
    from app.demo_data import remove_demo_data, seed_demo_data

    with SessionLocal() as db:
        result = seed_demo_data(db)
    assert result["created"] is True
    assert "SYNTHETIC" in result["warning"]

    cameras = client.get("/api/cameras", headers=auth, params={"q": "Demo"}).json()
    assert len(cameras) >= 3
    for camera in cameras:
        assert camera["last_test_status"] == "untested", "demo cameras must never look online"
        assert "SYNTHETIC" in (camera["notes"] or "")

    # Recovered poses should land on the ground truth used to generate them.
    for entry in result["cameras"]:
        if entry["recovered_position_m"] is None:
            assert entry["method"] == "homography"      # no pose invented from a homography
            continue
        assert np.allclose(entry["recovered_position_m"], entry["ground_truth_position_m"], atol=0.05)

    with SessionLocal() as db:
        removed = remove_demo_data(db)
    assert removed["coordinate_systems"] == 1
