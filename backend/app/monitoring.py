"""Monitoring lifecycle, plain-language rule summaries and the overview roll-up.

Three rules run through everything here.

*Derive what can be derived.* A camera's state up to "configured" is worked out
from evidence that already exists — a passed connection test, a saved analytic.
Only the two deliberate acts, asking for validation and going live, are stored,
so a stored flag can never contradict the thing it claims.

*An absent measurement is not zero.* A camera nobody can reach has an unknown
frame rate, not a frame rate of nothing. Counts that cannot be produced come
back as ``None`` with a reason, and the interface prints "Unavailable".

*Say what was observed, not what it meant.* A rule summary describes the
condition that fired. It never concludes why.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .models import (
    Camera, CameraFunction, CalibrationStatus, Event, EventKind, FunctionKind,
    MonitoringState, ReviewDecision, TestStatus,
)
from .scene import AVAILABLE_PROCESSING, FUNCTION_LABELS, NEEDS_FLOOR_MAPPING

# Which analytics a running detector would have to support to produce each kind
# of event. Empty until a processing service is connected, which is why no
# event in this deployment is ever produced by inference.
EVENT_KIND_FOR_FUNCTION: dict[FunctionKind, EventKind] = {
    FunctionKind.restricted_zone: EventKind.restricted_entry,
    FunctionKind.people_counting: EventKind.line_crossing,
    FunctionKind.product_counting: EventKind.line_crossing,
    FunctionKind.workstation_occupancy: EventKind.station_occupancy,
    FunctionKind.ppe_monitoring: EventKind.ppe_violation,
}

EVENT_LABELS: dict[EventKind, str] = {
    EventKind.restricted_entry: "Restricted-zone entry",
    EventKind.line_crossing: "Line crossing",
    EventKind.station_occupancy: "Station occupancy",
    EventKind.dwell_time: "Dwell time",
    EventKind.ppe_violation: "PPE check",
}


# =====================================================================
# Geometry validity
# =====================================================================

def geometry_is_valid(function: CameraFunction) -> tuple[bool, str | None]:
    """Whether a function's drawn region is usable.

    A half-drawn counting line or a two-corner polygon is not a configuration
    error to shout about while someone is still drawing; it is a reason the
    camera cannot be activated yet.
    """
    config = json.loads(function.config_json or "{}")

    if function.kind in (FunctionKind.people_counting, FunctionKind.product_counting):
        line = config.get("line")
        if not isinstance(line, list) or len(line) != 2:
            return False, "The counting line needs both ends."
        if any(not isinstance(p, list) or len(p) != 2 for p in line):
            return False, "The counting line is malformed."
        (ax, ay), (bx, by) = line[0], line[1]
        if abs(ax - bx) < 1e-6 and abs(ay - by) < 1e-6:
            return False, "The counting line has no length."
        if config.get("direction") not in ("a_to_b", "b_to_a", "both"):
            return False, "The counting direction has not been chosen."
        return True, None

    if function.kind in (FunctionKind.restricted_zone, FunctionKind.ppe_monitoring,
                         FunctionKind.workstation_occupancy):
        polygon = config.get("polygon")
        if not isinstance(polygon, list) or len(polygon) < 3:
            return False, "The zone needs at least three corners."
        if any(not isinstance(p, list) or len(p) != 2 for p in polygon):
            return False, "The zone outline is malformed."
        # Checked before area: a bow-tie outline has a shoelace area near zero,
        # and "crosses itself" tells the installer what to actually fix.
        if _self_intersects(polygon):
            return False, "The zone outline crosses itself."
        if _polygon_area(polygon) < 1.0:
            return False, "The zone encloses no area."
        return True, None

    # Functions with no drawn region of their own.
    return True, None


def _polygon_area(polygon: list[list[float]]) -> float:
    total = 0.0
    for i in range(len(polygon)):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % len(polygon)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2


def _segments_cross(a, b, c, d) -> bool:
    def side(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    d1, d2 = side(c, d, a), side(c, d, b)
    d3, d4 = side(a, b, c), side(a, b, d)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def _self_intersects(polygon: list[list[float]]) -> bool:
    n = len(polygon)
    if n < 4:
        return False
    for i in range(n):
        a, b = polygon[i], polygon[(i + 1) % n]
        for j in range(i + 1, n):
            # Skip neighbouring edges: they legitimately share a corner.
            if j == i or (j + 1) % n == i or j == (i + 1) % n:
                continue
            if _segments_cross(a, b, polygon[j], polygon[(j + 1) % n]):
                return True
    return False


# =====================================================================
# Plain-language rule summaries
# =====================================================================

_DIRECTION_WORDS = {
    "a_to_b": "from side A to side B",
    "b_to_a": "from side B to side A",
    "both": "in either direction",
}


def rule_summary(function: CameraFunction, camera: Camera | None = None) -> str:
    """One sentence an installer can check against what they meant.

    Reads as: during <when>, create an event when <condition>.
    """
    config = json.loads(function.config_json or "{}")
    schedule = config.get("schedule_label") or "any shift"
    when = "At any time" if schedule in ("any shift", "", None) else f"During {schedule}"
    where = function.name
    seconds = config.get("threshold_seconds")
    cooldown = config.get("cooldown_seconds")

    if function.kind == FunctionKind.restricted_zone:
        dwell = (f" for more than {_secs(seconds)}" if seconds
                 else "")
        condition = f"a person remains inside {where}{dwell}"
    elif function.kind in (FunctionKind.people_counting, FunctionKind.product_counting):
        subject = "a person" if function.kind == FunctionKind.people_counting else "a product"
        direction = _DIRECTION_WORDS.get(config.get("direction"), "across the line")
        condition = f"{subject} crosses {where} {direction}"
    elif function.kind == FunctionKind.workstation_occupancy:
        station = config.get("workstation_name") or where
        dwell = f" for more than {_secs(seconds)}" if seconds else ""
        condition = f"{station} is occupied{dwell}"
    elif function.kind == FunctionKind.ppe_monitoring:
        items = config.get("required_ppe") or []
        wearing = ", ".join(i.replace("_", " ") for i in items) or "the required PPE"
        condition = f"a person inside {where} is not wearing {wearing}"
    else:
        condition = f"{FUNCTION_LABELS.get(function.kind, where)} reports something"

    sentence = f"{when}, create an event when {condition}."
    if cooldown:
        sentence += f" Then wait {_secs(cooldown)} before reporting it again."
    return sentence


def _secs(value) -> str:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return "a moment"
    if seconds >= 60 and seconds % 60 == 0:
        minutes = int(seconds // 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    text = f"{seconds:g}"
    return f"{text} second{'s' if seconds != 1 else ''}"


# =====================================================================
# Lifecycle
# =====================================================================

def has_valid_analytics(camera: Camera) -> bool:
    return any(geometry_is_valid(f)[0] for f in camera.functions)


def monitoring_state(camera: Camera) -> MonitoringState:
    if camera.monitoring_active:
        return MonitoringState.active
    if camera.monitoring_requested:
        return MonitoringState.validation_pending
    if has_valid_analytics(camera):
        return MonitoringState.configured
    if camera.last_test_status == TestStatus.online:
        return MonitoringState.connected
    return MonitoringState.draft


STATE_LABELS: dict[MonitoringState, str] = {
    MonitoringState.draft: "Draft",
    MonitoringState.connected: "Connected",
    MonitoringState.configured: "Configured",
    MonitoringState.validation_pending: "Validation pending",
    MonitoringState.active: "Active",
}


def activation_blockers(camera: Camera) -> list[str]:
    """Everything standing between this camera and going live.

    Deliberately does *not* include floor-plan calibration: camera-only
    analytics are complete without it, and requiring a map would block the
    common case for no benefit.
    """
    blockers: list[str] = []
    if camera.last_test_status != TestStatus.online:
        blockers.append(
            "The connection has not been proven. Run a connection test that decodes video.")
    if not camera.functions:
        blockers.append("No analytics are configured, so there would be nothing to monitor.")
    else:
        for function in camera.functions:
            ok, why = geometry_is_valid(function)
            if not ok:
                blockers.append(f"{function.name}: {why}")
    if camera.area_id is None:
        blockers.append("No factory area is assigned, so events could not be placed anywhere.")
    return blockers


def calibration_needs_revalidation(camera: Camera) -> bool:
    active = next((r for r in camera.calibration_revisions if r.is_active), None)
    return bool(active and active.status == CalibrationStatus.needs_recalibration)


def camera_state_out(camera: Camera) -> dict:
    state = monitoring_state(camera)
    blockers = activation_blockers(camera)
    return {
        "state": state.value,
        "label": STATE_LABELS[state],
        "can_activate": not blockers,
        "blockers": blockers,
        "analytics_configured": len(camera.functions),
        "analytics_valid": sum(1 for f in camera.functions if geometry_is_valid(f)[0]),
        "processing_available": any(f.kind in AVAILABLE_PROCESSING for f in camera.functions),
        "calibration_needs_revalidation": calibration_needs_revalidation(camera),
    }


# =====================================================================
# Overview roll-up
# =====================================================================

def camera_health(cameras: list[Camera]) -> dict:
    """Online / offline / degraded, plus how many are actually monitoring.

    Connected and monitoring are counted separately and never added together:
    a camera can stream perfectly and be analysing nothing.
    """
    online = [c for c in cameras if c.last_test_status == TestStatus.online]
    degraded = [c for c in cameras if c.last_test_status == TestStatus.partial]
    untested = [c for c in cameras if c.last_test_status == TestStatus.untested]
    offline = [c for c in cameras
               if c.last_test_status in (TestStatus.unreachable, TestStatus.auth_failed,
                                         TestStatus.timeout, TestStatus.error)]
    return {
        "total": len(cameras),
        "online": len(online),
        "offline": len(offline),
        "degraded": len(degraded),
        "untested": len(untested),
        "monitoring_active": sum(1 for c in cameras if c.monitoring_active),
        "with_analytics": sum(1 for c in cameras if c.functions),
        "note": ("Connected and monitoring are different things: a camera can stream perfectly "
                 "and be analysing nothing."),
    }


def production_counts(cameras: list[Camera], events: list[Event]) -> dict:
    """Production numbers for the selected line and shift, when available.

    No processing service is connected, so there is nothing to count. This
    returns the reason rather than a zero, because a zero here would read as
    "the line produced nothing", which is a very different claim.
    """
    counting = [f for c in cameras for f in c.functions
                if f.kind in (FunctionKind.people_counting, FunctionKind.product_counting)]
    runnable = [f for f in counting if f.kind in AVAILABLE_PROCESSING]
    if not counting:
        reason = "No counting analytics are configured on these cameras."
    elif not runnable:
        reason = ("Counting lines are configured, but no detection service is connected to "
                  "produce counts from them.")
    else:
        reason = None

    return {
        "available": False if reason else True,
        "value": None,
        "unavailable_reason": reason,
        "counting_lines_configured": len(counting),
        "sample_crossings": sum(1 for e in events
                                if e.kind == EventKind.line_crossing and e.is_sample),
    }


def awaiting_review(events: list[Event]) -> int:
    return sum(1 for e in events if e.decision == ReviewDecision.unreviewed)


def recent_window(hours: int = 24) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)
