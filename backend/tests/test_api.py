"""End-to-end API behaviour: auth, CRUD, credential exposure, placements."""
from __future__ import annotations

import io

from PIL import Image


def _png(width: int = 800, height: int = 600) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), "#eef1f6").save(buf, format="PNG")
    return buf.getvalue()


# -- authentication ------------------------------------------------------

def test_configuration_routes_require_authentication(client):
    for method, path in [
        ("get", "/api/cameras"),
        ("get", "/api/cameras/summary"),
        ("post", "/api/cameras"),
        ("get", "/api/locations/tree"),
        ("get", "/api/floor-plans"),
        ("get", "/api/preview/sessions"),
    ]:
        assert getattr(client, method)(path).status_code == 401, f"{method} {path} was not protected"


def test_health_is_public_and_says_nothing_sensitive(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert set(body) == {"status", "service", "mode"}


def test_bad_credentials_are_rejected(client):
    res = client.post("/api/auth/login", json={"username": "tester", "password": "nope"})
    assert res.status_code == 401


# -- locations -----------------------------------------------------------

def test_inline_location_creation_is_idempotent(client, auth):
    payload = {"site": "Plant 1", "building": "Hall A", "floor": "Level 1", "area": "Packaging"}
    first = client.post("/api/locations/resolve", json=payload, headers=auth).json()
    second = client.post("/api/locations/resolve", json=payload, headers=auth).json()
    assert first["id"] == second["id"], "the same names must not create duplicate locations"

    tree = client.get("/api/locations/tree", headers=auth).json()
    assert [s["name"] for s in tree].count("Plant 1") == 1


# -- cameras and credentials ---------------------------------------------

def test_camera_registration_never_returns_the_password(client, auth):
    area = client.post("/api/locations/resolve", headers=auth, json={
        "site": "Plant 1", "building": "Hall A", "floor": "Level 1", "area": "Packaging"}).json()

    res = client.post("/api/cameras", headers=auth, json={
        "name": "Line 1 North", "host": "192.168.50.11",
        "connection_type": "rtsp", "rtsp_port": 554, "stream_path": "/live",
        "username": "installer", "password": "S3cret-Value!",
        "area_id": area["id"],
    })
    assert res.status_code == 201, res.text
    body = res.json()

    assert "password" not in body
    assert body["has_password"] is True
    assert "S3cret-Value!" not in res.text
    # Unverified cameras are saved, but never shown as online.
    assert body["last_test_status"] == "untested"
    assert body["last_test_at"] is None

    listing = client.get("/api/cameras", headers=auth)
    assert "S3cret-Value!" not in listing.text
    detail = client.get(f"/api/cameras/{body['id']}", headers=auth)
    assert "S3cret-Value!" not in detail.text


def test_stored_password_is_encrypted_at_rest(client, auth):
    from sqlalchemy import select

    from app.crypto import decrypt
    from app.db import SessionLocal
    from app.models import Camera

    with SessionLocal() as db:
        camera = db.scalars(select(Camera).where(Camera.name == "Line 1 North")).one()
        assert camera.password_encrypted
        assert "S3cret-Value!" not in camera.password_encrypted
        assert decrypt(camera.password_encrypted) == "S3cret-Value!"


def test_empty_password_on_edit_keeps_the_stored_one(client, auth):
    camera = client.get("/api/cameras", headers=auth).json()[0]

    updated = client.patch(f"/api/cameras/{camera['id']}", headers=auth,
                           json={"notes": "Serviced"}).json()
    assert updated["has_password"] is True
    assert updated["notes"] == "Serviced"

    # An explicitly empty string is still "keep it".
    updated = client.patch(f"/api/cameras/{camera['id']}", headers=auth,
                           json={"password": ""}).json()
    assert updated["has_password"] is True


def test_clearing_the_password_needs_an_explicit_action(client, auth):
    area = client.post("/api/locations/resolve", headers=auth, json={
        "site": "Plant 1", "building": "Hall A", "floor": "Level 1", "area": "Packaging"}).json()
    cam = client.post("/api/cameras", headers=auth, json={
        "name": "Temp Cam", "host": "192.168.50.12", "username": "u",
        "password": "to-be-removed", "area_id": area["id"]}).json()

    cleared = client.patch(f"/api/cameras/{cam['id']}", headers=auth,
                           json={"clear_password": True}).json()
    assert cleared["has_password"] is False
    client.delete(f"/api/cameras/{cam['id']}", headers=auth)


def test_changing_connection_details_resets_verification(client, auth):
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Camera, TestStatus

    camera = client.get("/api/cameras", headers=auth).json()[0]
    with SessionLocal() as db:
        row = db.scalars(select(Camera).where(Camera.id == camera["id"])).one()
        row.last_test_status = TestStatus.online
        row.last_test_at = datetime.now(timezone.utc)
        db.commit()

    assert client.get(f"/api/cameras/{camera['id']}", headers=auth).json()["last_test_status"] == "online"

    moved = client.patch(f"/api/cameras/{camera['id']}", headers=auth,
                         json={"host": "192.168.50.99"}).json()
    assert moved["last_test_status"] == "untested", "a moved camera must not stay marked online"
    assert moved["last_test_at"] is None


def test_host_must_be_a_bare_address(client, auth):
    for host in ["http://192.168.1.5/onvif", "user@192.168.1.5", "192.168.1.5/live"]:
        res = client.post("/api/cameras", headers=auth, json={"name": "Bad", "host": host})
        assert res.status_code == 422, host


def test_connection_test_against_a_blocked_host_is_reported_not_crashed(client, auth):
    res = client.post("/api/cameras/test-connection", headers=auth,
                      json={"host": "169.254.169.254", "connection_type": "rtsp"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["status"] == "unreachable"
    assert "blocked by policy" in body["error"]


# -- floor plans and placement -------------------------------------------

def test_floor_plan_upload_placement_and_scale(client, auth):
    tree = client.get("/api/locations/tree", headers=auth).json()
    floor_id = tree[0]["buildings"][0]["floors"][0]["id"]

    res = client.post("/api/floor-plans", headers=auth,
                      files={"file": ("plan.png", _png(1600, 1000), "image/png")},
                      data={"floor_id": str(floor_id)})
    assert res.status_code == 201, res.text
    plan = res.json()
    assert (plan["width_px"], plan["height_px"]) == (1600, 1000)
    assert plan["scale_px_per_metre"] is None

    camera = client.get("/api/cameras", headers=auth).json()[0]
    placement = client.put(f"/api/cameras/{camera['id']}/placement", headers=auth, json={
        "floor_plan_id": plan["id"], "norm_x": 0.25, "norm_y": 0.4,
        "heading_deg": 135, "fov_deg": 90, "view_distance_m": 12,
    })
    assert placement.status_code == 200, placement.text
    assert placement.json()["review_status"] == "confirmed"

    # Scale: two points 800 px apart across a 1600 px image, declared as 40 m.
    scaled = client.put(f"/api/floor-plans/{plan['id']}/scale", headers=auth, json={
        "point_a_x": 0.1, "point_a_y": 0.5, "point_b_x": 0.6, "point_b_y": 0.5, "distance_m": 40,
    }).json()
    assert abs(scaled["scale_px_per_metre"] - 20.0) < 0.01


def test_replacing_a_plan_flags_placements_and_clears_the_scale(client, auth):
    plans = client.get("/api/floor-plans", headers=auth).json()
    plan = plans[0]
    assert plan["placement_count"] == 1
    assert plan["scale_px_per_metre"] is not None

    replaced = client.post("/api/floor-plans", headers=auth,
                           files={"file": ("plan2.png", _png(1200, 900), "image/png")},
                           data={"floor_id": str(plan["floor_id"])}).json()

    assert replaced["version"] == plan["version"] + 1
    assert replaced["needs_review_count"] == 1, "existing markers must not be treated as verified"
    assert replaced["scale_px_per_metre"] is None, "a scale measured on the old image must not carry over"

    camera = client.get("/api/cameras", headers=auth).json()[0]
    assert client.get(f"/api/cameras/{camera['id']}", headers=auth).json()["placement"]["review_status"] == "needs_review"


def test_only_png_and_jpeg_plans_are_accepted(client, auth):
    tree = client.get("/api/locations/tree", headers=auth).json()
    floor_id = tree[0]["buildings"][0]["floors"][0]["id"]

    res = client.post("/api/floor-plans", headers=auth,
                      files={"file": ("notes.txt", b"hello", "text/plain")},
                      data={"floor_id": str(floor_id)})
    assert res.status_code == 415

    res = client.post("/api/floor-plans", headers=auth,
                      files={"file": ("fake.png", b"not really a png", "image/png")},
                      data={"floor_id": str(floor_id)})
    assert res.status_code == 415


# -- summary -------------------------------------------------------------

def test_summary_separates_verification_from_placement(client, auth):
    summary = client.get("/api/cameras/summary", headers=auth).json()
    assert set(summary) == {
        "total", "reachable", "failed", "untested", "awaiting_placement", "placements_need_review",
    }
    assert summary["reachable"] == 0, "nothing is reachable until a test actually succeeds"
    assert summary["placements_need_review"] == 1


# -- ONVIF through the API ------------------------------------------------

def test_onvif_login_success_with_a_dead_stream_is_reported_as_partial(client, auth):
    """A working ONVIF control channel is not the same as working video.
    The camera must not be shown as online when no frame can be decoded."""
    from tests.onvif_simulator import OnvifSimulator

    # Point the advertised stream at a closed port so it fails immediately.
    with OnvifSimulator(rtsp_host="127.0.0.1", rtsp_port=9) as sim:
        res = client.post("/api/cameras/test-connection", headers=auth, json={
            "host": "127.0.0.1", "connection_type": "onvif",
            "onvif_port": sim.port, "onvif_path": "/onvif/device_service",
            "username": "admin", "password": "admin-pass",
        })
        assert res.status_code == 200, res.text
        body = res.json()

    assert body["login_ok"] is True
    assert body["stream_ok"] is False
    assert body["ok"] is False
    assert body["status"] == "partial"
    assert body["manufacturer"] == "Numenor Optics"
    assert [p["token"] for p in body["profiles"]] == ["profile_main", "profile_sub"]
    assert "video stream failed" in body["error"]


def test_onvif_profile_discovery_endpoint_lists_profiles(client, auth):
    from tests.onvif_simulator import OnvifSimulator

    with OnvifSimulator(rtsp_host="127.0.0.1", rtsp_port=9) as sim:
        body = client.post("/api/cameras/profiles", headers=auth, json={
            "host": "127.0.0.1", "onvif_port": sim.port,
            "username": "admin", "password": "admin-pass",
        }).json()

    assert body["profiles"][0]["resolution"] == "1920x1080"
    assert body["profiles"][0]["fps"] == 25
    assert body["snapshot_supported"] is True


def test_onvif_wrong_password_is_reported_as_auth_failure(client, auth):
    from tests.onvif_simulator import OnvifSimulator

    with OnvifSimulator() as sim:
        body = client.post("/api/cameras/test-connection", headers=auth, json={
            "host": "127.0.0.1", "connection_type": "onvif", "onvif_port": sim.port,
            "username": "admin", "password": "definitely-wrong",
        }).json()

    assert body["status"] == "auth_failed"
    assert body["login_ok"] is False
    assert "username or password" in body["error"]
    assert "definitely-wrong" not in str(body)
