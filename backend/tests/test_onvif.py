"""ONVIF client behaviour, exercised against a simulated device."""
from __future__ import annotations

import pytest

from app.onvif import OnvifClient, OnvifError
from tests.onvif_simulator import OnvifSimulator


def _client(port: int, username="admin", password="admin-pass") -> OnvifClient:
    return OnvifClient("127.0.0.1", port, "/onvif/device_service", username, password)


def test_discovery_reports_device_and_profiles():
    with OnvifSimulator() as sim:
        client = _client(sim.port)
        client.probe()

        device = client.device_information()
        assert device.manufacturer == "Numenor Optics"
        assert device.model == "NX-880"

        media_url = client.media_service_url()
        profiles = client.profiles(media_url)
        assert [p.token for p in profiles] == ["profile_main", "profile_sub"]
        assert profiles[0].resolution == "1920x1080"
        assert profiles[0].encoding == "H264"
        assert profiles[0].fps == 25


def test_advertised_xaddr_is_rewritten_to_the_vetted_address():
    """Cameras often advertise an address that is not routable from the server.
    The client must keep talking to the address the operator registered."""
    with OnvifSimulator() as sim:
        client = _client(sim.port)
        media_url = client.media_service_url()
        assert "10.99.99.99" not in media_url
        assert "127.0.0.1" in media_url


def test_stream_and_snapshot_uris_follow_the_selected_profile():
    with OnvifSimulator() as sim:
        client = _client(sim.port)
        media_url = client.media_service_url()
        assert client.stream_uri(media_url, "profile_main").endswith("/main")
        assert client.stream_uri(media_url, "profile_sub").endswith("/sub")
        assert client.snapshot_uri(media_url, "profile_main").endswith("/snap.jpg")


def test_bad_credentials_are_reported_as_an_auth_failure():
    with OnvifSimulator() as sim:
        client = _client(sim.port, username="admin", password="wrong")
        client.probe()  # unauthenticated liveness check still succeeds
        with pytest.raises(OnvifError) as exc:
            client.device_information()
        assert exc.value.kind == "auth"
        assert "username or password" in exc.value.message


def test_a_non_onvif_endpoint_is_reported_as_a_protocol_error():
    with OnvifSimulator() as sim:
        client = OnvifClient("127.0.0.1", sim.port, "/not-onvif", "admin", "admin-pass")
        with pytest.raises(OnvifError) as exc:
            client.device_information()
        assert exc.value.kind in ("protocol", "auth")


def test_unreachable_port_is_classified_as_unreachable():
    client = _client(9)  # nothing listens on discard
    with pytest.raises(OnvifError) as exc:
        client.probe()
    assert exc.value.kind in ("unreachable", "timeout")
