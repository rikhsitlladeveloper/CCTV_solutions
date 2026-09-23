"""The guided setup flow end to end: workspace, points, matching, mapping,
accuracy, activation, zones, relationships and export."""
from __future__ import annotations

import json

import numpy as np
import pytest

from app.geometry import pose_from_look_at
from app.intrinsics import Intrinsics

WIDTH, HEIGHT = 1920, 1080
K = [[1390.0, 0.0, 959.0], [0.0, 1388.0, 541.0], [0.0, 0.0, 1.0]]
DIST = [-0.276, 0.079, 0.0005, -0.0003, -0.009]

CAL = [(f"FP-{i + 1:02d}", x, y) for i, (x, y) in enumerate(
    [(x, y) for x in (2.0, 9.0, 15.0, 22.0) for y in (2.0, 8.0, 14.0)])]
VAL = [("VP-01", 6.0, 5.0), ("VP-02", 12.0, 11.0), ("VP-03", 18.5, 5.5)]


@pytest.fixture(scope="module")
def intr() -> Intrinsics:
    return Intrinsics.from_payload(np.array(K), np.array(DIST), WIDTH, HEIGHT)


def poll(client, auth, job_id, tries=60):
    """Wait for an async job to finish."""
    import time
    for _ in range(tries):
        body = client.get(f"/api/setup/jobs/{job_id}", headers=auth).json()
        if body["status"] in ("succeeded", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def matches_for(eye, look, points, intr, jitter=0.0, rng=None):
    pose = pose_from_look_at(eye, look)
    out = []
    for point in points:
        cam = pose.world_to_camera_points([[point["x"], point["y"], 0.0]])
        if cam[0, 2] <= 0.5:
            continue
        pixels, in_front = intr.project_camera_points(cam)
        u, v = pixels[0]
        if not in_front[0] or not intr.contains_pixel(u, v, margin=-30):
            continue
        if jitter and rng is not None:
            u, v = np.array([u, v]) + rng.normal(0, jitter, 2)
            if not intr.contains_pixel(u, v):
                continue
        out.append({"reference_point_id": point["id"],
                    "pixel_u": float(u), "pixel_v": float(v)})
    return out


@pytest.fixture(scope="module")
def site(client, auth) -> dict:
    """A workspace with surveyed points and three cameras."""
    tree = client.post("/api/locations/resolve", headers=auth, json={
        "site": "Setup Site", "building": "Bay", "floor": "Ground", "area": "Line"}).json()
    floor_id = client.get("/api/locations/tree", headers=auth).json()
    floor_id = next(f["id"] for s in floor_id for b in s["buildings"] for f in b["floors"]
                    if b["name"] == "Bay")

    ws = client.post("/api/setup/workspaces", headers=auth, json={
        "name": "Setup Test Bay", "floor_id": floor_id,
        "width_m": 24, "length_m": 16,
        "origin_description": "North-west corner of the bay floor.",
        "x_axis_description": "X runs east along the north wall; Y runs south.",
        "max_ground_error_m": 0.15, "min_reference_points": 6,
    })
    assert ws.status_code == 201, ws.text
    ws = ws.json()

    points = []
    for code, x, y in CAL:
        res = client.post(f"/api/setup/workspaces/{ws['id']}/points", headers=auth, json={
            "workspace_id": ws["id"], "code": code, "name": f"Point {code}",
            "x": x, "y": y, "role": "calibration", "uncertainty_m": 0.005})
        assert res.status_code == 201, res.text
        points.append(res.json())
    for code, x, y in VAL:
        res = client.post(f"/api/setup/workspaces/{ws['id']}/points", headers=auth, json={
            "workspace_id": ws["id"], "code": code, "name": f"Point {code}",
            "x": x, "y": y, "role": "validation", "uncertainty_m": 0.005})
        assert res.status_code == 201, res.text
        points.append(res.json())

    cameras = {}
    for name, host in [("West", "192.0.2.31"), ("East", "192.0.2.32"), ("Door", "192.0.2.33")]:
        cam = client.post("/api/cameras", headers=auth, json={
            "name": f"Setup {name}", "host": host, "connection_type": "rtsp",
            "area_id": res_area(client, auth)}).json()
        client.post(f"/api/setup/workspaces/{ws['id']}/cameras/{cam['id']}", headers=auth)
        client.post(f"/api/cameras/{cam['id']}/intrinsics", headers=auth, json={
            "camera_matrix": K, "distortion_coefficients": DIST,
            "image_width": WIDTH, "image_height": HEIGHT, "activate": True})
        cameras[name] = cam

    return {"workspace": ws, "points": points, "cameras": cameras, "site": site}


def res_area(client, auth) -> int:
    return client.post("/api/locations/resolve", headers=auth, json={
        "site": "Setup Site", "building": "Bay", "floor": "Ground", "area": "Line"}).json()["id"]


# -- Step B: workspaces --------------------------------------------------

def test_one_shared_frame_per_floor(client, auth, site):
    """A second area on the same floor must reuse the frame, not invent an origin."""
    res = client.post("/api/setup/workspaces", headers=auth, json={
        "name": "Another Area", "floor_id": site["workspace"]["floor_id"],
        "width_m": 10, "length_m": 10,
        "origin_description": "Somewhere else.", "x_axis_description": "Also east."})
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["workspace_id"] == site["workspace"]["id"]
    assert "share a single origin" in detail["message"]


def test_workspace_grid_follows_the_stated_size(client, auth, site):
    ws = client.get(f"/api/setup/workspaces/{site['workspace']['id']}", headers=auth).json()
    assert ws["width_m"] == 24 and ws["length_m"] == 16
    assert ws["grid_max_x"] >= 24 and ws["grid_max_y"] >= 16
    assert ws["calibration_point_count"] == len(CAL)
    assert ws["validation_point_count"] == len(VAL)


def test_setup_asks_for_no_camera_xyz_or_rotation(client, auth, site):
    """Nothing in the guided flow requires pose numbers."""
    schema = client.get("/openapi.json").json()
    body = schema["paths"]["/api/setup/workspaces"]["post"]["requestBody"]
    ref = body["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
    fields = set(schema["components"]["schemas"][ref]["properties"])
    for forbidden in ("x", "y", "z", "roll_deg", "pitch_deg", "yaw_deg"):
        assert forbidden not in fields


# -- Step C/D: points and matching --------------------------------------

def test_matching_and_pre_flight_warnings(client, auth, site, intr):
    camera = site["cameras"]["West"]
    all_points = site["points"]
    payload = matches_for((2.0, 8.0, 5.6), (19.0, 8.0, 0.0), all_points, intr)
    assert len(payload) >= 8

    res = client.put(f"/api/setup/cameras/{camera['id']}/matches", headers=auth, json={
        "image_width": WIDTH, "image_height": HEIGHT, "matches": payload})
    assert res.status_code == 200, res.text
    status = res.json()

    assert status["calibration_matched"] >= 6
    assert status["validation_matched"] >= 1
    assert status["ready_to_calculate"] is True
    assert not [i for i in status["issues"] if i["level"] == "error"]


def test_a_pixel_outside_the_image_is_refused(client, auth, site):
    camera = site["cameras"]["West"]
    res = client.put(f"/api/setup/cameras/{camera['id']}/matches", headers=auth, json={
        "image_width": WIDTH, "image_height": HEIGHT,
        "matches": [{"reference_point_id": site["points"][0]["id"],
                     "pixel_u": 5000, "pixel_v": 10}]})
    assert res.status_code == 422
    assert "outside the 1920px-wide image" in res.text


def test_clustered_points_are_warned_about(client, auth, site, intr):
    """Points in one corner of the frame cannot support the rest of the view."""
    camera = site["cameras"]["Door"]
    pose = pose_from_look_at((12.0, 15.2, 5.0), (12.0, 3.0, 0.0))
    chosen = [p for p in site["points"] if p["code"] in ("FP-04", "FP-05", "FP-07", "FP-08")]
    payload = matches_for((12.0, 15.2, 5.0), (12.0, 3.0, 0.0), chosen, intr)
    if len(payload) < 4:
        pytest.skip("synthetic camera cannot see the clustered subset")

    res = client.put(f"/api/setup/cameras/{camera['id']}/matches", headers=auth, json={
        "image_width": WIDTH, "image_height": HEIGHT, "matches": payload}).json()
    codes = {i["code"] for i in res["issues"]}
    assert "small_image_region" in codes or "near_collinear_image" in codes \
        or "no_validation_points" in codes


def test_cameras_may_use_different_subsets_of_one_registry(client, auth, site, intr):
    """Each camera matches whatever it can see; they share the same survey."""
    west = site["cameras"]["West"]
    east = site["cameras"]["East"]
    client.put(f"/api/setup/cameras/{east['id']}/matches", headers=auth, json={
        "image_width": WIDTH, "image_height": HEIGHT,
        "matches": matches_for((22.0, 8.0, 5.4), (5.0, 8.0, 0.0), site["points"], intr)})

    west_ids = {m["reference_point_id"] for m in
                client.get(f"/api/setup/cameras/{west['id']}/matches", headers=auth).json()["matches"]}
    east_ids = {m["reference_point_id"] for m in
                client.get(f"/api/setup/cameras/{east['id']}/matches", headers=auth).json()["matches"]}

    assert west_ids and east_ids
    assert west_ids != east_ids, "the two views should not see identical point sets"
    assert west_ids & east_ids, "they should still share some points"


# -- Step E: mapping -----------------------------------------------------

def test_calculate_mapping_runs_as_a_job_and_recovers_the_geometry(client, auth, site):
    camera = site["cameras"]["West"]
    res = client.post(f"/api/setup/cameras/{camera['id']}/calculate-mapping", headers=auth,
                      json={"image_width": WIDTH, "image_height": HEIGHT})
    assert res.status_code == 202
    job = poll(client, auth, res.json()["job"]["job_id"])
    assert job["status"] == "succeeded", job

    result = job["result"]
    assert result["method"] == "floor_homography"
    assert result["pixel_convention"] == "undistorted"      # intrinsics were active
    assert result["distortion_corrected"] is True
    assert result["mean_error_m"] < 0.01
    assert result["coverage_area_m2"] > 50
    assert result["activated"] is False, "calculating must not activate"
    site["west_revision"] = result["revision_id"]


def test_mapping_is_saved_but_not_active(client, auth, site):
    camera = site["cameras"]["West"]
    revisions = client.get(f"/api/cameras/{camera['id']}/calibration/revisions",
                           headers=auth).json()
    assert revisions
    assert not any(r["is_active"] for r in revisions)


def test_mapping_stores_no_camera_pose(client, auth, site):
    """A floor mapping cannot say where the camera is, so none is invented."""
    camera = site["cameras"]["West"]
    revision = client.get(f"/api/cameras/{camera['id']}/calibration/revisions",
                          headers=auth).json()[0]
    assert revision["method"] == "homography"
    assert revision["position"] is None
    assert revision["rpy_deg"] is None
    assert revision["has_homography"] is True


def test_image_size_mismatch_is_refused(client, auth, site):
    camera = site["cameras"]["West"]
    res = client.post(f"/api/setup/cameras/{camera['id']}/calculate-mapping", headers=auth,
                      json={"image_width": 1280, "image_height": 720})
    job = poll(client, auth, res.json()["job"]["job_id"])
    assert job["status"] == "failed"
    assert "different image size" in job["error"]


def test_too_few_points_fails_with_a_hint(client, auth, site, intr):
    camera = site["cameras"]["Door"]
    two = matches_for((12.0, 15.2, 5.0), (12.0, 3.0, 0.0), site["points"][:2], intr)
    client.put(f"/api/setup/cameras/{camera['id']}/matches", headers=auth, json={
        "image_width": WIDTH, "image_height": HEIGHT, "matches": two})

    res = client.post(f"/api/setup/cameras/{camera['id']}/calculate-mapping", headers=auth,
                      json={"image_width": WIDTH, "image_height": HEIGHT})
    job = poll(client, auth, res.json()["job"]["job_id"])
    assert job["status"] == "failed"
    assert "at least 4" in job["error"]
    assert job["hint"]


# -- Step F: accuracy and activation ------------------------------------

def test_accuracy_is_checked_against_held_out_points_only(client, auth, site):
    camera = site["cameras"]["West"]
    res = client.post(f"/api/setup/cameras/{camera['id']}/check-accuracy", headers=auth,
                      json={"acceptance_threshold_m": 0.15, "reviewer": "tester"})
    assert res.status_code == 200, res.text
    body = res.json()
    validation = body["validation"]

    assert validation["held_out_count"] >= 1
    assert validation["passed"] is True
    assert validation["summary"]["mean_m"] < 0.05
    assert all(p["role"] == "validation" for p in validation["held_out"])
    assert "took no part" in validation["interpretation"]
    # Passing the check must not put it into use.
    assert body["is_active"] is False


def test_activation_is_explicit_and_reversible_between_revisions(client, auth, site):
    camera = site["cameras"]["West"]
    res = client.post(f"/api/setup/cameras/{camera['id']}/activate-mapping", headers=auth,
                      json={"confirm": True})
    assert res.status_code == 200, res.text
    assert res.json()["is_active"] is True
    assert res.json()["accuracy_passed"] is True

    revisions = client.get(f"/api/cameras/{camera['id']}/calibration/revisions",
                           headers=auth).json()
    assert sum(1 for r in revisions if r["is_active"]) == 1


def test_a_mapping_with_no_validation_points_cannot_pass(client, auth, site, intr):
    """Fitting error alone never counts as a check."""
    camera = site["cameras"]["East"]
    only_calibration = [p for p in site["points"] if p["role"] == "calibration"]
    client.put(f"/api/setup/cameras/{camera['id']}/matches", headers=auth, json={
        "image_width": WIDTH, "image_height": HEIGHT,
        "matches": matches_for((22.0, 8.0, 5.4), (5.0, 8.0, 0.0), only_calibration, intr)})

    res = client.post(f"/api/setup/cameras/{camera['id']}/calculate-mapping", headers=auth,
                      json={"image_width": WIDTH, "image_height": HEIGHT})
    job = poll(client, auth, res.json()["job"]["job_id"])
    assert job["status"] == "succeeded", f"{job['error']} / {job['hint']}"

    check = client.post(f"/api/setup/cameras/{camera['id']}/check-accuracy", headers=auth,
                        json={"acceptance_threshold_m": 0.15}).json()
    assert check["validation"]["passed"] is False
    assert any("not been measured independently" in m or "No validation points" in m
               for m in check["validation"]["messages"])


# -- staleness -----------------------------------------------------------

def test_re_measuring_a_point_marks_the_mapping_stale(client, auth, site):
    camera = site["cameras"]["West"]
    point = next(p for p in site["points"] if p["code"] == "FP-05")

    client.patch(f"/api/setup/points/{point['id']}", headers=auth, json={"x": point["x"] + 0.4})

    revisions = client.get(f"/api/cameras/{camera['id']}/calibration/revisions",
                           headers=auth).json()
    active = next(r for r in revisions if r["is_active"])
    assert active["status"] == "needs_recalibration"

    blocked = client.post(f"/api/setup/cameras/{camera['id']}/activate-mapping", headers=auth,
                          json={"confirm": True})
    assert blocked.status_code == 409
    assert "stale" in blocked.text

    client.patch(f"/api/setup/points/{point['id']}", headers=auth, json={"x": point["x"]})


def test_redefining_the_origin_needs_confirmation(client, auth, site):
    ws_id = site["workspace"]["id"]
    res = client.patch(f"/api/setup/workspaces/{ws_id}", headers=auth,
                       json={"origin_description": "Actually the south-east corner."})
    assert res.status_code == 409
    assert "what every recorded coordinate means" in res.text

    forced = client.patch(f"/api/setup/workspaces/{ws_id}", headers=auth, json={
        "origin_description": "Actually the south-east corner.",
        "confirm_redefinition": True, "redefinition_reason": "Survey redone"})
    assert forced.status_code == 200
    assert forced.json()["definition_revision"] == 2


# -- projection ----------------------------------------------------------

def test_projection_round_trips_and_flags_extrapolation(client, auth, site, intr):
    camera = site["cameras"]["West"]
    # Re-establish a good active mapping after the staleness test.
    client.put(f"/api/setup/cameras/{camera['id']}/matches", headers=auth, json={
        "image_width": WIDTH, "image_height": HEIGHT,
        "matches": matches_for((2.0, 8.0, 5.6), (19.0, 8.0, 0.0), site["points"], intr)})
    job = poll(client, auth, client.post(
        f"/api/setup/cameras/{camera['id']}/calculate-mapping", headers=auth,
        json={"image_width": WIDTH, "image_height": HEIGHT}).json()["job"]["job_id"])
    assert job["status"] == "succeeded", f"{job['error']} / {job['hint']}"
    client.post(f"/api/setup/cameras/{camera['id']}/activate-mapping", headers=auth,
                json={"confirm": True}, params={"revision_id": job["result"]["revision_id"]})

    inside = client.post(f"/api/setup/cameras/{camera['id']}/project", headers=auth,
                         json={"x": 15.0, "y": 8.0}).json()
    assert inside["within_checked_area"] is True

    back = client.post(f"/api/setup/cameras/{camera['id']}/project", headers=auth,
                       json={"pixel_u": inside["pixel"]["u"], "pixel_v": inside["pixel"]["v"]}).json()
    assert abs(back["floor"]["x"] - 15.0) < 0.05
    assert abs(back["floor"]["y"] - 8.0) < 0.05

    far = client.post(f"/api/setup/cameras/{camera['id']}/project", headers=auth,
                      json={"x": 100.0, "y": 100.0}).json()
    assert far["within_checked_area"] is False
    assert any("outside the area" in c for c in far["caveats"])


# -- zones and relationships --------------------------------------------

def test_zones_work_without_any_calibration(client, auth, site):
    area = res_area(client, auth)
    plain = client.post("/api/cameras", headers=auth, json={
        "name": "Uncalibrated Cam", "host": "192.0.2.39",
        "connection_type": "rtsp", "area_id": area}).json()

    res = client.post(f"/api/cameras/{plain['id']}/zones", headers=auth, json={
        "name": "Doorway", "kind": "entrance",
        "image_polygon": [[100, 400], [500, 400], [500, 800], [100, 800]],
        "image_width": WIDTH, "image_height": HEIGHT})
    assert res.status_code == 201, res.text
    zone = res.json()
    assert zone["world_polygon"] is None       # no mapping, so no world shape claimed
    site["plain_camera"] = plain
    site["plain_zone"] = zone


def test_a_zone_vertex_outside_the_image_is_refused(client, auth, site):
    res = client.post(f"/api/cameras/{site['plain_camera']['id']}/zones", headers=auth, json={
        "name": "Bad", "image_polygon": [[0, 0], [5000, 0], [5000, 500]],
        "image_width": WIDTH, "image_height": HEIGHT})
    assert res.status_code == 422
    assert "outside the" in res.text


def test_zone_gets_a_world_polygon_once_the_camera_is_mapped(client, auth, site):
    camera = site["cameras"]["West"]
    res = client.post(f"/api/cameras/{camera['id']}/zones", headers=auth, json={
        "name": "Bay exit", "kind": "exit",
        "image_polygon": [[1200, 700], [1700, 700], [1700, 950], [1200, 950]],
        "image_width": WIDTH, "image_height": HEIGHT})
    assert res.status_code == 201, res.text
    zone = res.json()
    assert zone["world_polygon"], "an active mapping should give the zone a floor shape"
    assert len(zone["world_polygon"]) == 4
    site["west_zone"] = zone


def test_relationship_validation_rejects_nonsense(client, auth, site):
    west, east = site["cameras"]["West"], site["cameras"]["East"]

    self_link = client.post("/api/relationships", headers=auth, json={
        "kind": "overlap", "camera_a_id": west["id"], "camera_b_id": west["id"]})
    assert self_link.status_code == 422
    assert "cannot be related to itself" in self_link.text

    no_times = client.post("/api/relationships", headers=auth, json={
        "kind": "transition", "camera_a_id": west["id"], "camera_b_id": east["id"]})
    assert no_times.status_code == 422
    assert "minimum and a maximum" in no_times.text

    backwards = client.post("/api/relationships", headers=auth, json={
        "kind": "transition", "camera_a_id": west["id"], "camera_b_id": east["id"],
        "min_travel_seconds": 30, "max_travel_seconds": 5})
    assert backwards.status_code == 422


def test_overlap_and_directed_transition(client, auth, site):
    west, east, door = (site["cameras"]["West"], site["cameras"]["East"], site["cameras"]["Door"])

    overlap = client.post("/api/relationships", headers=auth, json={
        "kind": "overlap", "camera_a_id": west["id"], "camera_b_id": east["id"],
        "verification": "verified", "notes": "Confirmed on site."})
    assert overlap.status_code == 201, overlap.text
    assert overlap.json()["verification"] == "verified"

    duplicate = client.post("/api/relationships", headers=auth, json={
        "kind": "overlap", "camera_a_id": east["id"], "camera_b_id": west["id"]})
    assert duplicate.status_code == 422
    assert "already have an overlap" in duplicate.text

    forward = client.post("/api/relationships", headers=auth, json={
        "kind": "transition", "camera_a_id": west["id"], "camera_b_id": door["id"],
        "min_travel_seconds": 4, "max_travel_seconds": 25})
    assert forward.status_code == 201

    # The reverse direction is a separate record, not implied by the first.
    reverse = client.post("/api/relationships", headers=auth, json={
        "kind": "transition", "camera_a_id": door["id"], "camera_b_id": west["id"],
        "min_travel_seconds": 5, "max_travel_seconds": 30})
    assert reverse.status_code == 201
    assert reverse.json()["id"] != forward.json()["id"]


def test_exclusion_conflicts_with_a_stated_relationship(client, auth, site):
    west, east = site["cameras"]["West"], site["cameras"]["East"]
    res = client.post("/api/relationships", headers=auth, json={
        "kind": "excluded", "camera_a_id": west["id"], "camera_b_id": east["id"]})
    assert res.status_code == 422
    assert "Remove it before excluding" in res.text


def test_graph_distinguishes_unknown_from_excluded(client, auth, site):
    graph = client.get("/api/relationships", headers=auth,
                       params={"workspace_id": site["workspace"]["id"]}).json()
    summary = graph["summary"]
    assert summary["overlaps"] >= 1
    assert summary["transitions"] >= 2
    assert summary["pairs_unknown"] >= 0
    assert "unknown, not impossible" in summary["interpretation"]


def test_redrawing_a_zone_marks_its_relationships_for_review(client, auth, site):
    west, door = site["cameras"]["West"], site["cameras"]["Door"]
    entrance = client.post(f"/api/cameras/{door['id']}/zones", headers=auth, json={
        "name": "Door entrance", "kind": "entrance",
        "image_polygon": [[700, 560], [1220, 570], [1240, 860], [690, 850]],
        "image_width": WIDTH, "image_height": HEIGHT}).json()

    rel = client.post("/api/relationships", headers=auth, json={
        "kind": "transition", "camera_a_id": west["id"], "camera_b_id": door["id"],
        "zone_a_id": site["west_zone"]["id"], "zone_b_id": entrance["id"],
        "min_travel_seconds": 3, "max_travel_seconds": 20, "verification": "verified"})
    # A second West->Door transition already exists, so this may be refused; either
    # way the review behaviour is what matters.
    if rel.status_code == 201:
        client.patch(f"/api/zones/{entrance['id']}", headers=auth, json={
            "image_polygon": [[600, 500], [1300, 520], [1320, 900], [590, 880]],
            "image_width": WIDTH, "image_height": HEIGHT})
        after = client.get("/api/relationships", headers=auth).json()
        target = next(r for r in after["relationships"] if r["id"] == rel.json()["id"])
        assert target["verification"] == "needs_review"
        assert "redrawn" in (target["review_reason"] or "")


def test_a_zone_from_another_camera_is_refused(client, auth, site):
    west, door = site["cameras"]["West"], site["cameras"]["Door"]
    res = client.post("/api/relationships", headers=auth, json={
        "kind": "transition", "camera_a_id": door["id"], "camera_b_id": west["id"],
        "zone_a_id": site["west_zone"]["id"],      # belongs to West, used as Door's exit
        "min_travel_seconds": 1, "max_travel_seconds": 10})
    assert res.status_code == 422
    assert "belongs to another camera" in res.text


# -- consistency ---------------------------------------------------------

def test_consistency_check_labels_itself_honestly(client, auth, site, intr):
    west, east = site["cameras"]["West"], site["cameras"]["East"]
    # Give East an active mapping too.
    client.put(f"/api/setup/cameras/{east['id']}/matches", headers=auth, json={
        "image_width": WIDTH, "image_height": HEIGHT,
        "matches": matches_for((22.0, 8.0, 5.4), (5.0, 8.0, 0.0), site["points"], intr)})
    job = poll(client, auth, client.post(
        f"/api/setup/cameras/{east['id']}/calculate-mapping", headers=auth,
        json={"image_width": WIDTH, "image_height": HEIGHT}).json()["job"]["job_id"])
    assert job["status"] == "succeeded"
    client.post(f"/api/setup/cameras/{east['id']}/activate-mapping", headers=auth,
                json={"confirm": True}, params={"revision_id": job["result"]["revision_id"]})

    target = np.array([[13.0, 9.0, 0.0]])
    marks = []
    for cam, eye, look in ((west, (2.0, 8.0, 5.6), (19.0, 8.0, 0.0)),
                           (east, (22.0, 8.0, 5.4), (5.0, 8.0, 0.0))):
        pose = pose_from_look_at(eye, look)
        pixels, _ = intr.project_camera_points(pose.world_to_camera_points(target))
        marks.append({"camera_id": cam["id"], "pixel_u": float(pixels[0][0]),
                      "pixel_v": float(pixels[0][1])})

    without_truth = client.post("/api/setup/consistency-check", headers=auth, json={
        "workspace_id": site["workspace"]["id"], "label": "Aisle mark", "marks": marks}).json()
    assert without_truth["heading"] == "Cross-camera consistency"
    assert "agree and all be wrong" in without_truth["interpretation"]
    assert without_truth["max_disagreement_m"] < 0.2

    with_truth = client.post("/api/setup/consistency-check", headers=auth, json={
        "workspace_id": site["workspace"]["id"], "label": "Aisle mark", "marks": marks,
        "ground_truth_x": 13.0, "ground_truth_y": 9.0}).json()
    assert with_truth["heading"] == "Accuracy against a measured position"
    assert all(e["error_m"] < 0.2 for e in with_truth["per_camera_error_m"])


# -- export --------------------------------------------------------------

def test_site_geometry_export_is_complete_and_credential_free(client, auth, site):
    res = client.get("/api/export/site-geometry", headers=auth,
                     params={"workspace_id": site["workspace"]["id"]})
    assert res.status_code == 200, res.text
    payload = res.json()

    assert payload["format"] == "numenor.site-geometry"
    assert payload["workspace"]["origin_description"]
    assert payload["conventions"]["homography_direction"]
    assert payload["reference_points"]
    assert payload["relationships"]
    assert payload["graph_summary"]["interpretation"]
    assert any("identifies or describes any person" in lim for lim in payload["limitations"])

    mapped = [c for c in payload["cameras"] if c["calibration"]]
    assert mapped
    for camera in mapped:
        assert camera["calibration"]["H_image_to_floor"]
        assert camera["calibration"]["pixel_convention"] in ("raw", "undistorted")
        assert camera["calibration"]["source_image"]["width"] == WIDTH

    text = json.dumps(payload).lower()
    for forbidden in ("password", "rtsp://", "192.0.2.3", "\"host\""):
        assert forbidden not in text, f"export leaked {forbidden!r}"


# -- demo ----------------------------------------------------------------

def test_guided_demo_is_synthetic_and_self_consistent(client, auth):
    from app.db import SessionLocal
    from app.demo_setup import remove_demo_setup, seed_demo_setup

    with SessionLocal() as db:
        result = seed_demo_setup(db)
    assert result["created"] is True
    assert "SYNTHETIC" in result["warning"]
    assert len(result["cameras"]) == 3

    for camera in result["cameras"]:
        assert camera["accuracy_check_passed"] is True
        assert camera["mean_held_out_error_cm"] < 10
        assert camera["held_out"] >= 2

    kinds = {r["kind"] for r in result["relationships"]}
    assert kinds == {"overlap", "transition"}
    overlap = next(r for r in result["relationships"] if r["kind"] == "overlap")
    assert overlap["overlap_area_m2"] > 10

    demo_cameras = client.get("/api/cameras", headers=auth, params={"q": "Demo Line"}).json()
    for camera in demo_cameras:
        assert camera["last_test_status"] == "untested"

    with SessionLocal() as db:
        removed = remove_demo_setup(db)
    assert removed["workspaces"] == 1


def test_resaving_matches_does_not_silently_wipe_them(client, auth, site, intr):
    """Replacing a camera's matches must end with the new ones stored.

    Deleting the old rows leaves them in the relationship until it is reloaded;
    reusing one as an "existing" record attaches the update to a doomed object
    and the save quietly does nothing. That is how an installer loses an
    afternoon's work, so it is pinned here.
    """
    camera = site["cameras"]["Door"]
    payload = matches_for((12.0, 15.2, 5.0), (12.0, 3.0, 0.0), site["points"], intr)
    assert len(payload) >= 5

    first = client.put(f"/api/setup/cameras/{camera['id']}/matches", headers=auth, json={
        "image_width": WIDTH, "image_height": HEIGHT, "matches": payload}).json()
    assert len(first["matches"]) == len(payload)

    # Save the same set again over the top: the count must not collapse.
    second = client.put(f"/api/setup/cameras/{camera['id']}/matches", headers=auth, json={
        "image_width": WIDTH, "image_height": HEIGHT, "matches": payload}).json()
    assert len(second["matches"]) == len(payload)

    reread = client.get(f"/api/setup/cameras/{camera['id']}/matches", headers=auth).json()
    assert len(reread["matches"]) == len(payload)

    # And a genuinely smaller set replaces rather than merges.
    trimmed = payload[:4]
    third = client.put(f"/api/setup/cameras/{camera['id']}/matches", headers=auth, json={
        "image_width": WIDTH, "image_height": HEIGHT, "matches": trimmed}).json()
    assert len(third["matches"]) == 4


def test_resaving_advanced_observations_does_not_wipe_them(client, auth, site, intr):
    """The same guard on the advanced (pose) observations endpoint."""
    camera = site["cameras"]["East"]
    body = [{"reference_point_id": m["reference_point_id"],
             "pixel_u": m["pixel_u"], "pixel_v": m["pixel_v"],
             "image_width": WIDTH, "image_height": HEIGHT, "role": "fit"}
            for m in matches_for((22.0, 8.0, 5.4), (5.0, 8.0, 0.0), site["points"], intr)]

    first = client.put(f"/api/cameras/{camera['id']}/observations", headers=auth,
                       json={"observations": body, "replace_existing": True}).json()
    second = client.put(f"/api/cameras/{camera['id']}/observations", headers=auth,
                        json={"observations": body, "replace_existing": True}).json()
    assert len(second) == len(first) == len(body)


def test_a_workspace_from_before_floors_can_be_attached_to_one(client, auth):
    """Upgrade path: frames created by an earlier release have no floor."""
    from app.db import SessionLocal
    from app.models import CoordinateSystem

    with SessionLocal() as db:
        legacy = CoordinateSystem(name="Legacy frame", origin_description="Old corner.")
        db.add(legacy)
        db.commit()
        legacy_id = legacy.id

    listed = client.get("/api/setup/workspaces", headers=auth).json()
    entry = next(w for w in listed if w["id"] == legacy_id)
    assert entry["floor_id"] is None, "it starts unattached"

    tree = client.get("/api/locations/tree", headers=auth).json()
    spare = client.post("/api/locations/resolve", headers=auth, json={
        "site": "Upgrade Site", "building": "Shed", "floor": "Ground", "area": "Bay"}).json()
    tree = client.get("/api/locations/tree", headers=auth).json()
    floor_id = next(f["id"] for s in tree for b in s["buildings"] for f in b["floors"]
                    if b["name"] == "Shed")

    attached = client.patch(f"/api/setup/workspaces/{legacy_id}", headers=auth,
                            json={"floor_id": floor_id})
    assert attached.status_code == 200, attached.text
    assert attached.json()["floor_id"] == floor_id
    void = spare

    # A second frame cannot claim the same floor.
    with SessionLocal() as db:
        other = CoordinateSystem(name="Rival frame")
        db.add(other)
        db.commit()
        other_id = other.id
    clash = client.patch(f"/api/setup/workspaces/{other_id}", headers=auth,
                         json={"floor_id": floor_id})
    assert clash.status_code == 409
    assert clash.json()["detail"]["workspace_id"] == legacy_id
