"""The supervisor's view: lifecycle, events, review, and honest absences."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture(scope="module")
def plant(client, auth) -> dict:
    """A small plant: one area, two cameras, nothing configured yet."""
    client.post("/api/locations/resolve", headers=auth, json={
        "site": "Mon Site", "building": "Plant", "floor": "Ground", "area": "Packing"})
    area = client.post("/api/locations/resolve", headers=auth, json={
        "site": "Mon Site", "building": "Plant", "floor": "Ground", "area": "Packing"}).json()

    cameras = {}
    for name, host in [("Packing A", "192.0.2.61"), ("Packing B", "192.0.2.62")]:
        cameras[name] = client.post("/api/cameras", headers=auth, json={
            "name": name, "host": host, "connection_type": "rtsp",
            "area_id": area["id"]}).json()
    return {"area": area, "cameras": cameras}


def _mark_online(camera_id: int) -> None:
    """Force a passed connection test without needing a real camera."""
    from app.db import SessionLocal
    from app.models import Camera, TestStatus

    with SessionLocal() as db:
        camera = db.get(Camera, camera_id)
        camera.last_test_status = TestStatus.online
        db.commit()


# -- lifecycle -----------------------------------------------------------

def test_a_new_camera_is_a_draft(client, auth, plant):
    cam = plant["cameras"]["Packing A"]
    body = client.get(f"/api/cameras/{cam['id']}/monitoring", headers=auth).json()
    assert body["state"] == "draft"
    assert body["can_activate"] is False
    assert any("connection has not been proven" in b for b in body["blockers"])


def test_a_proven_connection_makes_it_connected(client, auth, plant):
    cam = plant["cameras"]["Packing A"]
    _mark_online(cam["id"])
    body = client.get(f"/api/cameras/{cam['id']}/monitoring", headers=auth).json()
    assert body["state"] == "connected"
    # Connected is not configured: there is still nothing to monitor.
    assert body["can_activate"] is False
    assert any("No analytics are configured" in b for b in body["blockers"])


def test_an_analytic_with_a_valid_region_makes_it_configured(client, auth, plant):
    cam = plant["cameras"]["Packing A"]
    res = client.post(f"/api/cameras/{cam['id']}/functions", headers=auth, json={
        "kind": "restricted_zone", "name": "Conveyor Access", "space": "image",
        "image_width": 1280, "image_height": 720,
        "config": {"polygon": [[100, 100], [600, 100], [600, 500], [100, 500]],
                   "threshold_seconds": 2, "schedule_label": "Shift A"}})
    assert res.status_code == 201, res.text

    body = client.get(f"/api/cameras/{cam['id']}/monitoring", headers=auth).json()
    assert body["state"] == "configured"
    assert body["can_activate"] is True
    assert body["blockers"] == []
    # Configured is not running. Nothing analyses video in this deployment.
    assert body["processing_available"] is False


def test_incomplete_configuration_cannot_be_activated(client, auth, plant):
    """A camera that was never reached must not be allowed to go live."""
    cam = plant["cameras"]["Packing B"]
    res = client.post(f"/api/cameras/{cam['id']}/monitoring/activate", headers=auth,
                      json={"confirm": True})
    assert res.status_code == 422
    blockers = res.json()["detail"]["blockers"]
    assert any("connection has not been proven" in b for b in blockers)
    assert any("No analytics are configured" in b for b in blockers)

    state = client.get(f"/api/cameras/{cam['id']}/monitoring", headers=auth).json()
    assert state["state"] == "draft", "a refused activation must not move the state"


def test_activation_must_be_confirmed(client, auth, plant):
    cam = plant["cameras"]["Packing A"]
    res = client.post(f"/api/cameras/{cam['id']}/monitoring/activate", headers=auth,
                      json={"confirm": False})
    assert res.status_code == 422


def test_activate_and_deactivate(client, auth, plant):
    cam = plant["cameras"]["Packing A"]
    res = client.post(f"/api/cameras/{cam['id']}/monitoring/activate", headers=auth,
                      json={"confirm": True})
    assert res.status_code == 200, res.text
    assert res.json()["state"] == "active"

    res = client.post(f"/api/cameras/{cam['id']}/monitoring/deactivate", headers=auth)
    assert res.json()["state"] == "configured"


def test_camera_only_analytics_activate_without_any_floor_plan(client, auth, plant):
    """The common case must not be blocked by optional map calibration."""
    cam = plant["cameras"]["Packing A"]
    plans = client.get("/api/floor-plans", headers=auth).json()
    assert not any(p.get("floor_id") == plant["area"]["floor_id"] for p in plans), \
        "this test is only meaningful with no plan on that floor"

    res = client.post(f"/api/cameras/{cam['id']}/monitoring/activate", headers=auth,
                      json={"confirm": True})
    assert res.status_code == 200
    assert res.json()["state"] == "active"
    client.post(f"/api/cameras/{cam['id']}/monitoring/deactivate", headers=auth)


# -- region validity -----------------------------------------------------

@pytest.mark.parametrize("config,why", [
    ({"polygon": [[0, 0], [10, 0]]}, "three corners"),
    ({"polygon": [[0, 0], [10, 0], [10, 0.0001]]}, "no area"),
    ({"polygon": [[0, 0], [100, 100], [100, 0], [0, 100]]}, "crosses itself"),
])
def test_invalid_zones_block_activation(client, auth, plant, config, why):
    from app.models import CameraFunction, FunctionKind
    from app.monitoring import geometry_is_valid

    function = CameraFunction(camera_id=1, kind=FunctionKind.restricted_zone,
                              name="z", config_json=__import__("json").dumps(config))
    ok, problem = geometry_is_valid(function)
    assert ok is False
    assert why in problem


def test_an_incomplete_counting_line_is_rejected():
    import json

    from app.models import CameraFunction, FunctionKind
    from app.monitoring import geometry_is_valid

    half = CameraFunction(camera_id=1, kind=FunctionKind.people_counting, name="l",
                          config_json=json.dumps({"line": [[0, 0]], "direction": "a_to_b"}))
    assert geometry_is_valid(half) == (False, "The counting line needs both ends.")

    no_length = CameraFunction(camera_id=1, kind=FunctionKind.people_counting, name="l",
                               config_json=json.dumps({"line": [[5, 5], [5, 5]],
                                                       "direction": "a_to_b"}))
    assert geometry_is_valid(no_length)[0] is False

    fine = CameraFunction(camera_id=1, kind=FunctionKind.people_counting, name="l",
                          config_json=json.dumps({"line": [[0, 0], [100, 0]],
                                                  "direction": "both"}))
    assert geometry_is_valid(fine) == (True, None)


# -- plain-language rules ------------------------------------------------

def test_the_rule_reads_like_the_spec_example(client, auth, plant):
    cam = plant["cameras"]["Packing A"]
    body = client.get(f"/api/cameras/{cam['id']}/rule-summaries", headers=auth).json()
    rule = next(r for r in body["rules"] if r["name"] == "Conveyor Access")
    assert rule["summary"] == (
        "During Shift A, create an event when a person remains inside Conveyor Access "
        "for more than 2 seconds.")
    assert rule["geometry_valid"] is True


# -- events --------------------------------------------------------------

@pytest.fixture(scope="module")
def an_event(client, auth, plant) -> dict:
    cam = plant["cameras"]["Packing A"]
    function = client.get(f"/api/cameras/{cam['id']}/functions", headers=auth).json()[0]
    res = client.post("/api/events", headers=auth, json={
        "camera_id": cam["id"], "function_id": function["id"],
        "kind": "restricted_entry",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "duration_s": 4.5,
        "facts": ["A person was inside Conveyor Access for 4.5 s."],
        "image_width": 1280, "image_height": 720,
    })
    assert res.status_code == 201, res.text
    return res.json()


def test_an_ingested_event_snapshots_the_rule_wording(client, auth, an_event):
    assert "Conveyor Access" in an_event["rule_summary"]
    assert an_event["decision"] == "unreviewed"
    assert an_event["acknowledged"] is False
    assert an_event["is_sample"] is False, "a real service's output is never sample data"


def test_ingest_cannot_mark_its_own_output_as_sample(client, auth, plant):
    cam = plant["cameras"]["Packing A"]
    res = client.post("/api/events", headers=auth, json={
        "camera_id": cam["id"], "kind": "dwell_time",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "is_sample": True,          # not part of the contract; must be ignored
    })
    assert res.status_code == 201
    assert res.json()["is_sample"] is False


def test_review_and_acknowledgement_are_independent(client, auth, an_event):
    """Dismissing a detection is not the same as saying someone handled it."""
    eid = an_event["id"]

    body = client.post(f"/api/events/{eid}/review", headers=auth,
                       json={"decision": "dismissed", "notes": "Reflection on wet floor."}).json()
    assert body["decision"] == "dismissed"
    assert body["decided_by"] == "tester"
    assert body["acknowledged"] is False, "a review must not imply acknowledgement"

    body = client.post(f"/api/events/{eid}/acknowledge", headers=auth,
                       json={"acknowledged": True}).json()
    assert body["acknowledged"] is True
    assert body["acknowledged_by"] == "tester"
    assert body["decision"] == "dismissed", "acknowledging must not change the decision"
    assert body["notes"] == "Reflection on wet floor."


def test_decisions_persist_and_drive_the_counters(client, auth, plant, an_event):
    cam = plant["cameras"]["Packing A"]
    page = client.get("/api/events", headers=auth, params={"camera_id": cam["id"]}).json()
    assert page["counts"]["dismissed"] >= 1

    reread = client.get(f"/api/events/{an_event['id']}", headers=auth).json()
    assert reread["decision"] == "dismissed"
    assert reread["decided_at"] is not None

    only_open = client.get("/api/events", headers=auth,
                           params={"camera_id": cam["id"], "decision": "unreviewed"}).json()
    assert all(e["decision"] == "unreviewed" for e in only_open["items"])


def test_the_event_list_says_nothing_is_analysing_video(client, auth):
    page = client.get("/api/events", headers=auth).json()
    assert "no detection service" in page["note"].lower()


# -- overview ------------------------------------------------------------

def test_overview_counts_connected_and_monitoring_separately(client, auth, plant):
    body = client.get("/api/overview", headers=auth).json()
    health = body["health"]
    assert health["online"] >= 1
    assert health["monitoring_active"] == 0, "nothing was left activated"
    assert "analysing nothing" in health["note"]


def test_production_counts_are_unavailable_not_zero(client, auth, plant):
    """A zero would read as 'the line produced nothing'. It did not run at all."""
    body = client.get("/api/overview", headers=auth).json()
    production = body["production"]
    assert production["available"] is False
    assert production["value"] is None
    assert production["unavailable_reason"]


def test_overview_groups_cameras_when_there_is_no_floor_plan(client, auth, plant):
    body = client.get("/api/overview", headers=auth).json()
    assert isinstance(body["has_floor_plan"], bool)
    packing = next(g for g in body["groups"] if g["area"] == "Packing")
    assert packing["building"] == "Plant"
    assert len(packing["cameras"]) == 2


# -- wizard progress -----------------------------------------------------

def test_setup_progress_survives_a_reload(client, auth, plant):
    cam = plant["cameras"]["Packing B"]
    empty = client.get(f"/api/cameras/{cam['id']}/setup-progress", headers=auth).json()
    assert empty["step"] == "connect"
    assert empty["draft"] == {}

    saved = client.put(f"/api/cameras/{cam['id']}/setup-progress", headers=auth, json={
        "step": "analytics",
        "draft": {"assign": {"area_id": plant["area"]["id"]}, "note": "half done"},
        "completed": ["connect", "assign"]})
    assert saved.status_code == 200, saved.text

    # A completely fresh request, as a page reload would make.
    again = client.get(f"/api/cameras/{cam['id']}/setup-progress", headers=auth).json()
    assert again["step"] == "analytics"
    assert again["draft"]["note"] == "half done"
    assert again["completed"] == ["connect", "assign"]
    assert again["updated_by"] == "tester"

    client.delete(f"/api/cameras/{cam['id']}/setup-progress", headers=auth)
    assert client.get(f"/api/cameras/{cam['id']}/setup-progress",
                      headers=auth).json()["draft"] == {}


# -- discovery -----------------------------------------------------------

def test_discovery_reports_honestly_when_it_finds_nothing(client, auth, monkeypatch):
    """No cameras on a test host. That must not read as 'none exist'."""
    monkeypatch.setattr("app.routers.monitoring.discover", lambda timeout: [])
    body = client.post("/api/discovery/onvif", headers=auth, params={"seconds": 1}).json()
    assert body["devices"] == []
    assert "not found this way" in body["note"]


def test_discovery_flags_devices_already_registered(client, auth, plant, monkeypatch):
    from app.onvif import DiscoveredDevice

    found = [
        DiscoveredDevice(address="192.0.2.61", xaddrs=["http://192.0.2.61/onvif/device_service"],
                         scopes=[], types=None, name="Packing A", hardware="DS-2CD"),
        DiscoveredDevice(address="192.0.2.99", xaddrs=["http://192.0.2.99/onvif/device_service"],
                         scopes=[], types=None, name="Unknown cam", hardware=None),
    ]
    monkeypatch.setattr("app.routers.monitoring.discover", lambda timeout: found)
    devices = client.post("/api/discovery/onvif", headers=auth,
                          params={"seconds": 1}).json()["devices"]
    known = next(d for d in devices if d["address"] == "192.0.2.61")
    fresh = next(d for d in devices if d["address"] == "192.0.2.99")
    assert known["already_registered"] is True
    assert known["registered_camera_id"] == plant["cameras"]["Packing A"]["id"]
    assert fresh["already_registered"] is False
