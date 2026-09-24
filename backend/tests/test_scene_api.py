"""The commissioning workspace: scene editing, visual placement, functions,
walk-through sessions, readiness and the extended export."""
from __future__ import annotations

import json

import numpy as np
import pytest

WIDTH, HEIGHT = 1920, 1080
K = [[1390.0, 0.0, 959.0], [0.0, 1388.0, 541.0], [0.0, 0.0, 1.0]]


@pytest.fixture(scope="module")
def site(client, auth) -> dict:
    """A workspace, a scene and two cameras — no floor plan anywhere."""
    client.post("/api/locations/resolve", headers=auth, json={
        "site": "Scene Site", "building": "Hall", "floor": "Ground", "area": "Line"})
    tree = client.get("/api/locations/tree", headers=auth).json()
    floor_id = next(f["id"] for s in tree for b in s["buildings"] for f in b["floors"]
                    if b["name"] == "Hall")

    ws = client.post("/api/setup/workspaces", headers=auth, json={
        "name": "Scene Bay", "floor_id": floor_id, "width_m": 30, "length_m": 20,
        "origin_description": "North-west corner of the bay.",
        "x_axis_description": "East along the north wall."}).json()

    scene = client.post("/api/scenes", headers=auth, json={
        "workspace_id": ws["id"], "name": "Assembly hall",
        "building_width_m": 30, "building_length_m": 20})
    assert scene.status_code == 201, scene.text
    scene = scene.json()

    area = client.post("/api/locations/resolve", headers=auth, json={
        "site": "Scene Site", "building": "Hall", "floor": "Ground", "area": "Line"}).json()
    cameras = {}
    for name, host in [("North", "192.0.2.41"), ("South", "192.0.2.42")]:
        cam = client.post("/api/cameras", headers=auth, json={
            "name": f"Scene {name}", "host": host, "connection_type": "rtsp",
            "area_id": area["id"]}).json()
        cameras[name] = cam
    return {"workspace": ws, "scene": scene, "cameras": cameras, "floor_id": floor_id}


# -- creating a scene ----------------------------------------------------

def test_a_scene_needs_no_floor_plan(client, auth, site):
    scene = client.get(f"/api/scenes/{site['scene']['id']}", headers=auth).json()
    assert scene["assets"] == []
    assert scene["objects"] == []
    assert scene["grid"]["max_x"] >= 30
    assert scene["conventions"]["units"] == "metres"
    assert scene["geometry_provenance"] == "estimated"


def test_one_scene_per_workspace(client, auth, site):
    res = client.post("/api/scenes", headers=auth, json={
        "workspace_id": site["workspace"]["id"], "name": "Duplicate"})
    assert res.status_code == 409
    assert res.json()["detail"]["scene_id"] == site["scene"]["id"]


def test_object_palette_offers_the_expected_kinds(client, auth):
    items = client.get("/api/scene-palette", headers=auth).json()["items"]
    kinds = {i["kind"] for i in items}
    assert {"wall", "door", "column", "machine", "rack", "workstation",
            "conveyor", "walkway", "restricted_area"} <= kinds
    assert all(i["default_width_m"] > 0 for i in items)


def test_objects_can_be_added_moved_and_measured(client, auth, site):
    scene_id = site["scene"]["id"]

    machine = client.post(f"/api/scenes/{scene_id}/objects", headers=auth, json={
        "kind": "machine", "name": "Press 1", "x": 6.0, "y": 5.0,
        "width_m": 2.5, "depth_m": 1.8, "height_m": 2.0})
    assert machine.status_code == 201, machine.text
    machine = machine.json()
    assert machine["provenance"] == "estimated"
    assert len(machine["footprint"]) == 4

    moved = client.patch(f"/api/scenes/{scene_id}/objects/{machine['id']}", headers=auth,
                         json={"x": 9.0, "y": 7.5, "rotation_deg": 30,
                               "provenance": "measured"}).json()
    assert moved["x"] == 9.0 and moved["rotation_deg"] == 30
    assert moved["provenance"] == "measured"
    # A rotated footprint is no longer axis-aligned.
    xs = [p[0] for p in moved["footprint"]]
    assert len({round(x, 3) for x in xs}) > 2

    site["machine"] = moved


def test_outline_objects_keep_their_points(client, auth, site):
    scene_id = site["scene"]["id"]
    walkway = client.post(f"/api/scenes/{scene_id}/objects", headers=auth, json={
        "kind": "walkway", "name": "Main aisle",
        "points": [[1, 1], [28, 1], [28, 3], [1, 3]],
        "provenance": "estimated"}).json()
    assert walkway["points"] == [[1, 1], [28, 1], [28, 3], [1, 3]]
    assert walkway["footprint"] == walkway["points"]


def test_scene_provenance_is_the_worst_of_its_objects(client, auth, site):
    scene = client.get(f"/api/scenes/{site['scene']['id']}", headers=auth).json()
    kinds = {o["provenance"] for o in scene["objects"]}
    assert "estimated" in kinds
    assert scene["geometry_provenance"] == "estimated", \
        "one estimated object must keep the whole scene estimated"


# -- draft and publish ---------------------------------------------------

def test_editing_a_draft_does_not_change_what_is_live(client, auth, site):
    scene_id = site["scene"]["id"]

    before = client.get(f"/api/scenes/{scene_id}/published", headers=auth).json()
    assert before["published"] is False

    published = client.post(f"/api/scenes/{scene_id}/publish", headers=auth,
                            json={"confirm": True})
    assert published.status_code == 200
    published = published.json()
    assert published["published_revision"] == published["draft_revision"]
    assert published["has_unpublished_changes"] is False

    live = client.get(f"/api/scenes/{scene_id}/published", headers=auth).json()
    live_count = len(live["objects"])

    # Move something in the draft: the live snapshot must not follow.
    client.post(f"/api/scenes/{scene_id}/objects", headers=auth, json={
        "kind": "rack", "name": "Rack A", "x": 20.0, "y": 15.0})

    still_live = client.get(f"/api/scenes/{scene_id}/published", headers=auth).json()
    assert len(still_live["objects"]) == live_count, "the draft leaked into the live scene"
    assert still_live["has_unpublished_changes"] is True

    draft = client.get(f"/api/scenes/{scene_id}", headers=auth).json()
    assert len(draft["objects"]) == live_count + 1
    assert draft["has_unpublished_changes"] is True


def test_bulk_replace_saves_the_whole_working_set(client, auth, site):
    scene_id = site["scene"]["id"]
    objects = [
        {"kind": "wall", "name": "North wall", "points": [[0, 0], [30, 0]]},
        {"kind": "wall", "name": "West wall", "points": [[0, 0], [0, 20]]},
        {"kind": "machine", "name": "Press 1", "x": 9.0, "y": 7.5,
         "width_m": 2.5, "depth_m": 1.8, "provenance": "measured"},
        {"kind": "column", "name": "Column B2", "x": 15.0, "y": 10.0,
         "provenance": "measured"},
        {"kind": "restricted_area", "name": "Robot cell",
         "points": [[18, 12], [24, 12], [24, 17], [18, 17]]},
    ]
    res = client.put(f"/api/scenes/{scene_id}/objects", headers=auth, json={"objects": objects})
    assert res.status_code == 200, res.text
    scene = res.json()
    assert len(scene["objects"]) == 5
    assert {o["name"] for o in scene["objects"]} == {
        "North wall", "West wall", "Press 1", "Column B2", "Robot cell"}


def test_scene_survives_a_reload(client, auth, site):
    """Everything is on the server, so a browser refresh loses nothing."""
    scene_id = site["scene"]["id"]
    first = client.get(f"/api/scenes/{scene_id}", headers=auth).json()
    again = client.get(f"/api/scenes/{scene_id}", headers=auth).json()
    assert [o["name"] for o in first["objects"]] == [o["name"] for o in again["objects"]]
    assert first["draft_revision"] == again["draft_revision"]


# -- assets --------------------------------------------------------------

def _png(width=1200, height=800) -> bytes:
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (width, height), "#eef1f6").save(buf, format="PNG")
    return buf.getvalue()


def test_floor_plan_is_optional_but_can_be_scaled(client, auth, site):
    scene_id = site["scene"]["id"]
    res = client.post(f"/api/scenes/{scene_id}/assets", headers=auth,
                      files={"file": ("plan.png", _png(), "image/png")},
                      data={"kind": "floor_plan"})
    assert res.status_code == 201, res.text
    asset = res.json()
    assert asset["width_px"] == 1200
    assert asset["metres_per_pixel"] is None

    scaled = client.put(f"/api/scenes/{scene_id}/assets/{asset['id']}/scale", headers=auth,
                        json={"point_a": [100, 100], "point_b": [1100, 100],
                              "distance_m": 25.0, "origin_x": 0, "origin_y": 0}).json()
    assert abs(scaled["metres_per_pixel"] - 0.025) < 1e-6

    too_close = client.put(f"/api/scenes/{scene_id}/assets/{asset['id']}/scale", headers=auth,
                           json={"point_a": [100, 100], "point_b": [102, 100],
                                 "distance_m": 25.0})
    assert too_close.status_code == 422
    assert "too close together" in too_close.text


def test_only_glb_and_gltf_models_are_accepted(client, auth, site):
    scene_id = site["scene"]["id"]

    bad = client.post(f"/api/scenes/{scene_id}/assets", headers=auth,
                      files={"file": ("model.step", b"ISO-10303-21;", "application/octet-stream")},
                      data={"kind": "model_3d"})
    assert bad.status_code == 415
    assert "GLB or glTF" in bad.text

    fake = client.post(f"/api/scenes/{scene_id}/assets", headers=auth,
                       files={"file": ("model.glb", b"NOPE" + b"\x00" * 60,
                                       "model/gltf-binary")},
                       data={"kind": "model_3d"})
    assert fake.status_code == 415
    assert "header is wrong" in fake.text

    good = client.post(f"/api/scenes/{scene_id}/assets", headers=auth,
                       files={"file": ("model.glb", b"glTF" + b"\x00" * 100,
                                       "model/gltf-binary")},
                       data={"kind": "model_3d"})
    assert good.status_code == 201, good.text
    asset = good.json()
    assert asset["is_reference_only"] is True
    assert "reference only" in (asset["notes"] or "")

    aligned = client.put(f"/api/scenes/{scene_id}/assets/{asset['id']}/model-alignment",
                         headers=auth,
                         json={"scale": 0.001, "up_axis": "Y", "floor_offset_m": 0.0,
                               "rotation_deg": 90}).json()
    assert aligned["model_scale"] == 0.001
    assert aligned["model_up_axis"] == "Y"


# -- visual placement ----------------------------------------------------

def test_placing_and_aiming_a_camera_needs_no_angles(client, auth, site):
    camera = site["cameras"]["North"]
    res = client.post(f"/api/cameras/{camera['id']}/place", headers=auth, json={
        "workspace_id": site["workspace"]["id"],
        "x": 2.0, "y": 2.0, "height_m": 4.5,
        "target_x": 15.0, "target_y": 12.0,
        "mount_type": "wall",
        "illustrative_hfov_deg": 78, "illustrative_range_m": 20})
    assert res.status_code == 201, res.text
    body = res.json()

    assert body["is_approximate"] is True
    assert body["status"] == "approximate"
    assert body["fov_source"] == "illustrative"
    assert any("illustration only" in w for w in body["warnings"])
    assert body["position"]["z"] == 4.5
    assert body["aim"]["tilt_below_horizontal_deg"] > 0
    assert body["aim"]["map_heading_deg"] is not None
    assert body["frustum_floor_polygon"], "a cone with a lens model should meet the floor"

    site["north_revision"] = body["revision_id"]


def test_the_2d_heading_and_the_3d_pose_come_from_one_placement(client, auth, site):
    """The map arrow and the 3D frustum must not be able to disagree."""
    camera = site["cameras"]["North"]
    revisions = client.get(f"/api/cameras/{camera['id']}/calibration/revisions",
                           headers=auth).json()
    active = next(r for r in revisions if r["is_active"])

    # Heading derived from the stored pose...
    heading = active["map_heading_deg"]
    # ...and the direction implied by where it was aimed.
    import math
    dx = 15.0 - active["position"]["x"]
    dy = 12.0 - active["position"]["y"]
    expected = math.degrees(math.atan2(dx, dy)) % 360
    assert abs(heading - expected) < 0.5, (heading, expected)

    # The factory map reports the same thing.
    map_view = client.get(f"/api/factory-map/{site['workspace']['id']}", headers=auth).json()
    entry = next(c for c in map_view["cameras"] if c["camera_id"] == camera["id"])
    assert abs(entry["map_heading_deg"] - heading) < 1e-6
    assert entry["is_approximate"] is True


def test_aiming_at_the_mount_point_is_refused(client, auth, site):
    camera = site["cameras"]["South"]
    res = client.post(f"/api/cameras/{camera['id']}/place", headers=auth, json={
        "workspace_id": site["workspace"]["id"],
        "x": 5.0, "y": 5.0, "height_m": 4.0,
        "target_x": 5.0, "target_y": 5.0, "target_z": 4.0})
    assert res.status_code == 422
    assert "no direction" in res.text


def test_a_drag_never_silently_replaces_solved_geometry(client, auth, site):
    """A hand placement must not overwrite a solved calibration."""
    from datetime import datetime, timezone

    from app.db import SessionLocal
    from app.models import (
        CalibrationMethod, CalibrationRevision, CalibrationStatus, PixelConvention,
    )

    camera = site["cameras"]["South"]
    with SessionLocal() as db:
        solved = CalibrationRevision(
            camera_id=camera["id"], coordinate_system_id=site["workspace"]["id"],
            revision_number=99, method=CalibrationMethod.homography,
            status=CalibrationStatus.validated,
            homography_json=json.dumps(np.eye(3).tolist()),
            homography_inverse_json=json.dumps(np.eye(3).tolist()),
            pixel_convention=PixelConvention.raw,
            source_image_width=WIDTH, source_image_height=HEIGHT,
            is_active=True, activated_at=datetime.now(timezone.utc))
        db.add(solved)
        db.commit()
        solved_id = solved.id

    res = client.post(f"/api/cameras/{camera['id']}/place", headers=auth, json={
        "workspace_id": site["workspace"]["id"],
        "x": 25.0, "y": 3.0, "height_m": 4.0,
        "target_x": 10.0, "target_y": 12.0})
    assert res.status_code == 201
    body = res.json()
    assert any("NOT activated" in w for w in body["warnings"])
    # The UI must not have to read the prose to know what happened.
    assert body["activated"] is False

    revisions = client.get(f"/api/cameras/{camera['id']}/calibration/revisions",
                           headers=auth).json()
    active = next(r for r in revisions if r["is_active"])
    assert active["id"] == solved_id, "the solved calibration must stay active"

    # ...but the installer can still choose the hand placement deliberately, and
    # doing so keeps the solved revision rather than deleting it.
    placed_id = body["revision_id"]
    switch = client.post(
        f"/api/cameras/{camera['id']}/calibration/revisions/{placed_id}/activate",
        headers=auth, json={"confirm": True})
    assert switch.status_code == 200, switch.text
    revisions = client.get(f"/api/cameras/{camera['id']}/calibration/revisions",
                           headers=auth).json()
    assert next(r for r in revisions if r["is_active"])["id"] == placed_id
    assert any(r["id"] == solved_id for r in revisions), "nothing is ever deleted"

    # Switch back, which proves the move is reversible and leaves this shared
    # fixture as the later readiness tests expect to find it.
    back = client.post(
        f"/api/cameras/{camera['id']}/calibration/revisions/{solved_id}/activate",
        headers=auth, json={"confirm": True})
    assert back.status_code == 200, back.text
    revisions = client.get(f"/api/cameras/{camera['id']}/calibration/revisions",
                           headers=auth).json()
    assert next(r for r in revisions if r["is_active"])["id"] == solved_id


# -- functions -----------------------------------------------------------

def test_catalogue_is_honest_about_processing(client, auth):
    body = client.get("/api/function-catalogue", headers=auth).json()
    kinds = {f["kind"] for f in body["functions"]}
    assert {"people_counting", "product_counting", "restricted_zone", "ppe_monitoring",
            "workstation_occupancy", "twin_positions", "cross_camera_tracking"} == kinds
    assert all(f["processing_available"] is False for f in body["functions"])
    assert "No detection service is connected" in body["processing_note"]


def test_counting_can_be_configured_without_any_calibration(client, auth, site):
    """Picture-space counting must not be blocked by missing 3D geometry."""
    camera = site["cameras"]["North"]
    res = client.post(f"/api/cameras/{camera['id']}/functions", headers=auth, json={
        "kind": "people_counting", "name": "Doorway count",
        "space": "image", "image_width": WIDTH, "image_height": HEIGHT,
        "config": {"line": [[300, 700], [1400, 720]], "direction": "a_to_b"}})
    assert res.status_code == 201, res.text
    body = res.json()

    assert body["space"] == "image"
    assert body["status"]["state"] == "configured_unavailable"
    assert body["status"]["summary"] == "Configured — processing unavailable"
    assert body["status"]["requires_floor_mapping"] is False
    assert "Nothing is analysing video" in body["status"]["detail"]


def test_counting_needs_a_line_and_a_direction(client, auth, site):
    camera = site["cameras"]["North"]
    for config in ({"direction": "a_to_b"}, {"line": [[1, 1], [2, 2]]},
                   {"line": [[1, 1], [2, 2]], "direction": "sideways"}):
        res = client.post(f"/api/cameras/{camera['id']}/functions", headers=auth, json={
            "kind": "people_counting", "name": "Bad", "space": "image",
            "image_width": WIDTH, "image_height": HEIGHT, "config": config})
        assert res.status_code == 422, config


def test_ppe_needs_an_area_and_a_ppe_list(client, auth, site):
    camera = site["cameras"]["North"]
    missing_ppe = client.post(f"/api/cameras/{camera['id']}/functions", headers=auth, json={
        "kind": "ppe_monitoring", "name": "Hi-vis", "space": "image",
        "image_width": WIDTH, "image_height": HEIGHT,
        "config": {"polygon": [[10, 10], [200, 10], [200, 200]]}})
    assert missing_ppe.status_code == 422
    assert "required_ppe" in missing_ppe.text

    good = client.post(f"/api/cameras/{camera['id']}/functions", headers=auth, json={
        "kind": "ppe_monitoring", "name": "Hi-vis in the cell", "space": "image",
        "image_width": WIDTH, "image_height": HEIGHT,
        "config": {"polygon": [[10, 10], [200, 10], [200, 200]],
                   "required_ppe": ["hi_vis", "helmet"]}})
    assert good.status_code == 201


def test_twin_positions_is_blocked_without_a_floor_mapping(client, auth, site):
    camera = site["cameras"]["North"]
    res = client.post(f"/api/cameras/{camera['id']}/functions", headers=auth, json={
        "kind": "twin_positions", "name": "Ground positions", "space": "world",
        "config": {}})
    assert res.status_code == 201
    status = res.json()["status"]
    assert status["requires_floor_mapping"] is True
    assert status["blockers"], "a hand placement is not a floor mapping"


def test_image_space_config_must_record_its_image_size(client, auth, site):
    camera = site["cameras"]["North"]
    res = client.post(f"/api/cameras/{camera['id']}/functions", headers=auth, json={
        "kind": "restricted_zone", "name": "No entry", "space": "image",
        "config": {"polygon": [[10, 10], [200, 10], [200, 200]]}})
    assert res.status_code == 422
    assert "image size" in res.text


# -- sessions ------------------------------------------------------------

def test_a_walkthrough_records_observations_not_accuracy(client, auth, site):
    session = client.post("/api/sessions", headers=auth, json={
        "workspace_id": site["workspace"]["id"], "name": "Commissioning walk",
        "camera_ids": [site["cameras"]["North"]["id"]]})
    assert session.status_code == 201, session.text
    session = session.json()

    added = client.post(f"/api/sessions/{session['id']}/checkpoints", headers=auth, json={
        "camera_id": site["cameras"]["North"]["id"],
        "label": "Stood on the aisle marker",
        "observation": "Camera sees the marker clearly."}).json()

    assert added["accuracy"]["checkpoints"] == 1
    assert added["accuracy"]["with_measured_position"] == 0
    assert "not evidence that it is correct" in added["accuracy"]["interpretation"]

    ended = client.post(f"/api/sessions/{session['id']}/end", headers=auth).json()
    assert ended["ended_at"] is not None


# -- readiness -----------------------------------------------------------

def test_readiness_reports_each_capability_separately(client, auth, site):
    res = client.get("/api/readiness", headers=auth,
                     params={"workspace_id": site["workspace"]["id"]})
    assert res.status_code == 200, res.text
    body = res.json()

    labels = [c["label"] for c in body["capabilities"]]
    assert labels == [
        "Video connectivity", "Camera placement", "Detection and counting configuration",
        "AI processing availability", "Metric floor mapping", "Full 3D alignment",
        "Camera relationships", "Cross-camera tracking", "Scene geometry quality",
    ]

    by_label = {c["label"]: c for c in body["capabilities"]}
    assert by_label["AI processing availability"]["state"] in ("unavailable", "partial")
    assert by_label["Cross-camera tracking"]["state"] == "unavailable"
    assert by_label["Scene geometry quality"]["detail"]

    # Partial commissioning is a valid, named state.
    assert body["overall"] in ("ready", "partial", "blocked")
    assert "no single 'setup complete' badge" in body["interpretation"]
    assert "not sufficient for throughput simulation" not in body["interpretation"]
    assert "cycle times" in body["simulation_note"]


def test_readiness_separates_estimated_scene_from_measured_validation(client, auth, site):
    body = client.get("/api/readiness", headers=auth,
                      params={"workspace_id": site["workspace"]["id"]}).json()
    scene_cap = next(c for c in body["capabilities"] if c["label"] == "Scene geometry quality")
    assert scene_cap["state"] == "partial"
    assert "not survey data" in scene_cap["detail"]

    # North was placed by hand; South carries a solved homography from an earlier
    # test. Both count as "placed", but only the solved one counts as mapped —
    # dragging a camera onto the scene is not floor mapping.
    mapping_cap = next(c for c in body["capabilities"] if c["label"] == "Metric floor mapping")
    placement_cap = next(c for c in body["capabilities"] if c["label"] == "Camera placement")

    assert placement_cap["counts"]["placed"] == 2
    assert placement_cap["counts"]["approximate"] == 1
    assert mapping_cap["counts"]["mapped"] == 1, \
        "an approximate placement must not be counted as metric floor mapping"


# -- export --------------------------------------------------------------

def test_export_carries_scene_functions_and_stays_credential_free(client, auth, site):
    res = client.get("/api/export/site-geometry", headers=auth,
                     params={"workspace_id": site["workspace"]["id"]})
    assert res.status_code == 200, res.text
    payload = res.json()

    assert payload["format_version"] == "1.1"
    assert payload["scene"]["name"] == "Assembly hall"
    assert payload["scene"]["objects"], "the published snapshot should carry objects"
    assert "throughput simulation" in payload["scene"]["note"]

    functions = [f for c in payload["cameras"] for f in c["functions"]]
    assert functions
    assert all(f["status"]["processing_available"] is False for f in functions)
    assert any("configured_unavailable" in f["status"]["state"] for f in functions)
    assert any("not analysing anything" in lim for lim in payload["limitations"])

    text = json.dumps(payload).lower()
    for forbidden in ("password", "rtsp://", "192.0.2.4", "\"host\""):
        assert forbidden not in text, f"export leaked {forbidden!r}"


def test_placement_is_used_straight_away_when_nothing_was_solved(client, auth, site):
    """With no solved geometry to protect, a placement takes effect immediately."""
    camera = site["cameras"]["North"]
    res = client.post(f"/api/cameras/{camera['id']}/place", headers=auth, json={
        "workspace_id": site["workspace"]["id"],
        "x": 3.0, "y": 3.0, "height_m": 4.0,
        "target_x": 12.0, "target_y": 9.0})
    assert res.status_code == 201
    body = res.json()
    assert body["activated"] is True
    assert not any("NOT activated" in w for w in body["warnings"])

    revisions = client.get(f"/api/cameras/{camera['id']}/calibration/revisions",
                           headers=auth).json()
    assert next(r for r in revisions if r["is_active"])["id"] == body["revision_id"]
