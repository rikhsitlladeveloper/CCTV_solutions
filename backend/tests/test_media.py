"""Media adapter: URL construction, credential hygiene and preview session limits."""
from __future__ import annotations

import asyncio

import pytest

from app import config, media


def test_rtsp_url_percent_encodes_credentials():
    url = media.build_rtsp_url("192.168.1.9", 554, "/live", "oper@tor", "p@ss:word/1")
    assert url == "rtsp://oper%40tor:p%40ss%3Aword%2F1@192.168.1.9:554/live"
    # Whatever the credentials contain, they are stripped before anything is stored or shown.
    assert media.sanitize(url) == "rtsp://192.168.1.9:554/live"


def test_rtsp_url_normalises_a_missing_leading_slash():
    assert media.build_rtsp_url("10.0.0.5", 554, "stream1", None, None) \
        == "rtsp://10.0.0.5:554/stream1"


def test_onvif_stream_uri_is_repointed_at_the_vetted_address():
    """The camera advertises 10.99.99.99; we must keep talking to the address we vetted."""
    url = media.rtsp_url_from_onvif("rtsp://192.168.4.7:554/main", "admin", "pw")
    assert url.startswith("rtsp://admin:pw@192.168.4.7:554/main")


def test_non_rtsp_stream_uri_is_refused():
    with pytest.raises(media.MediaError) as exc:
        media.rtsp_url_from_onvif("https://192.168.4.7/stream", None, None)
    assert exc.value.kind == "unsupported"


def test_ffmpeg_errors_map_to_operator_readable_causes():
    cases = {
        "401 Unauthorized": "auth",
        "Server returned 404 Not Found": "invalid_stream",
        "Connection refused": "unreachable",
        "Connection timed out": "timeout",
        "Invalid data found when processing input": "unsupported",
    }
    for stderr, kind in cases.items():
        assert media._classify_ffmpeg_error(stderr).kind == kind, stderr


def test_preview_sessions_are_capped_and_released():
    async def scenario():
        manager = media.PreviewManager()
        created = [await manager.create(i) for i in range(config.MAX_PREVIEW_SESSIONS)]
        assert len(manager.list_sessions()) == config.MAX_PREVIEW_SESSIONS

        with pytest.raises(media.MediaError) as exc:
            await manager.create(999)
        assert "preview slots are in use" in exc.value.message

        assert await manager.stop(created[0].id)
        assert len(manager.list_sessions()) == config.MAX_PREVIEW_SESSIONS - 1
        # A freed slot can be reused.
        await manager.create(999)
        await manager.shutdown()
        assert manager.list_sessions() == []

    asyncio.run(scenario())


def test_a_second_preview_for_one_camera_replaces_the_first():
    async def scenario():
        manager = media.PreviewManager()
        first = await manager.create(7)
        second = await manager.create(7)
        assert manager.get(first.id) is None, "the stale session must be torn down"
        assert manager.get(second.id) is not None
        await manager.shutdown()

    asyncio.run(scenario())


def test_stopping_an_unknown_session_is_harmless():
    assert asyncio.run(media.PreviewManager().stop("no-such-session")) is False
