"""Validation and suggestion logic for the camera relationship graph.

The graph says how camera views relate on the ground: which share a patch of
floor, and which exits plausibly lead where. It configures a future tracking
service. It identifies nobody, and holds no information about people.

Three states, deliberately distinguishable:

* **unknown** — no record. Nothing has been established either way.
* **allowed** — an ``overlap`` or ``transition`` record someone entered.
* **excluded** — an explicit record saying these two views have no *direct*
  association. Travel between them via other cameras remains possible.

A missing record is never treated as a confirmed impossibility.
"""
from __future__ import annotations

import json
import math

import numpy as np

from .floormapping import convex_hull, polygon_area
from .models import CameraRelationship, MonitoredZone, RelationshipKind, VerificationStatus


class RelationshipError(ValueError):
    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint


def validate_relationship(kind: RelationshipKind, camera_a_id: int, camera_b_id: int,
                          *, min_travel_seconds: float | None,
                          max_travel_seconds: float | None,
                          zone_a: MonitoredZone | None, zone_b: MonitoredZone | None,
                          camera_a_system: int | None, camera_b_system: int | None,
                          existing: list[CameraRelationship]) -> list[str]:
    """Reject nonsense, return warnings for the merely questionable."""
    warnings: list[str] = []

    if camera_a_id == camera_b_id:
        raise RelationshipError(
            "A camera cannot be related to itself.",
            hint="Pick two different cameras.",
        )

    if kind == RelationshipKind.transition:
        if min_travel_seconds is None or max_travel_seconds is None:
            raise RelationshipError(
                "A transition needs both a minimum and a maximum plausible travel time.",
                hint="Time the walk between the two areas and allow a margin either side.",
            )
        if min_travel_seconds < 0 or max_travel_seconds < 0:
            raise RelationshipError("Travel times cannot be negative.")
        if min_travel_seconds > max_travel_seconds:
            raise RelationshipError(
                f"The minimum travel time ({min_travel_seconds} s) is greater than the maximum "
                f"({max_travel_seconds} s).",
            )
        if max_travel_seconds > 3600:
            warnings.append(
                f"A maximum of {max_travel_seconds:.0f} s is over an hour, which will make this "
                "transition match almost anything."
            )
        if math.isclose(min_travel_seconds, max_travel_seconds):
            warnings.append(
                "The minimum and maximum are the same, leaving no tolerance for walking speed."
            )

    # Zones must belong to the cameras they are claimed for.
    if zone_a is not None and zone_a.camera_id != camera_a_id:
        raise RelationshipError(
            f"Zone '{zone_a.name}' belongs to another camera, not the source camera.",
        )
    if zone_b is not None and zone_b.camera_id != camera_b_id:
        raise RelationshipError(
            f"Zone '{zone_b.name}' belongs to another camera, not the destination camera.",
        )

    if kind == RelationshipKind.transition:
        if zone_a is not None and zone_a.kind.value not in ("exit", "monitored"):
            warnings.append(
                f"'{zone_a.name}' is marked as an {zone_a.kind.value} zone but is being used as "
                "the exit from the source camera."
            )
        if zone_b is not None and zone_b.kind.value not in ("entrance", "monitored"):
            warnings.append(
                f"'{zone_b.name}' is marked as an {zone_b.kind.value} zone but is being used as "
                "the entrance to the destination camera."
            )

    if kind == RelationshipKind.overlap:
        if camera_a_system and camera_b_system and camera_a_system != camera_b_system:
            raise RelationshipError(
                "These cameras are positioned in different coordinate systems, so a shared "
                "overlap area in world coordinates is not meaningful.",
                hint="Put both cameras in the same workspace frame, or record the overlap as "
                     "paired image zones instead.",
            )

    # Contradictions and duplicates.
    for other in existing:
        same_pair = ({other.camera_a_id, other.camera_b_id} == {camera_a_id, camera_b_id})
        if not same_pair:
            continue
        if other.kind == RelationshipKind.excluded and kind != RelationshipKind.excluded:
            raise RelationshipError(
                "These two cameras are explicitly recorded as having no direct association. "
                "Remove that exclusion before adding an overlap or transition.",
            )
        if kind == RelationshipKind.excluded and other.kind != RelationshipKind.excluded:
            raise RelationshipError(
                f"An existing {other.kind.value} record connects these cameras. Remove it before "
                "excluding the pair.",
            )
        if other.kind == kind == RelationshipKind.overlap:
            raise RelationshipError("These cameras already have an overlap record.")
        if (other.kind == kind == RelationshipKind.transition
                and other.camera_a_id == camera_a_id and other.camera_b_id == camera_b_id):
            raise RelationshipError(
                "This directed transition already exists.",
                hint="Edit the existing record, or add the reverse direction instead.",
            )

    return warnings


def polygon_intersection(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    """Convex intersection of two floor polygons, via Sutherland-Hodgman."""
    if len(a) < 3 or len(b) < 3:
        return []

    def inside(p, edge_start, edge_end):
        return ((edge_end[0] - edge_start[0]) * (p[1] - edge_start[1])
                - (edge_end[1] - edge_start[1]) * (p[0] - edge_start[0])) >= -1e-12

    def intersect(p1, p2, q1, q2):
        x1, y1, x2, y2 = p1[0], p1[1], p2[0], p2[1]
        x3, y3, x4, y4 = q1[0], q1[1], q2[0], q2[1]
        denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        if abs(denom) < 1e-12:
            return p2
        t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
        return [x1 + t * (x2 - x1), y1 + t * (y2 - y1)]

    # Orient the clip polygon counter-clockwise so the inside test is consistent.
    clip = convex_hull(np.array(b, dtype=float))
    output = list(a)
    for i in range(len(clip)):
        if not output:
            break
        edge_start, edge_end = clip[i], clip[(i + 1) % len(clip)]
        current, output = output, []
        for j, point in enumerate(current):
            prev = current[j - 1]
            if inside(point, edge_start, edge_end):
                if not inside(prev, edge_start, edge_end):
                    output.append(intersect(prev, point, edge_start, edge_end))
                output.append(list(point))
            elif inside(prev, edge_start, edge_end):
                output.append(intersect(prev, point, edge_start, edge_end))
    return output


def suggest_overlaps(cameras_with_coverage: list[dict],
                     min_area_m2: float = 1.0) -> list[dict]:
    """Propose overlaps from mapped coverage polygons.

    A suggestion only. Two polygons sharing floor area says the *geometry* allows
    a common view; it cannot see the racking between them. Every suggestion has
    to be confirmed on site before it counts.
    """
    suggestions = []
    for i, a in enumerate(cameras_with_coverage):
        for b in cameras_with_coverage[i + 1:]:
            if a.get("coordinate_system_id") != b.get("coordinate_system_id"):
                continue
            poly_a, poly_b = a.get("coverage"), b.get("coverage")
            if not poly_a or not poly_b:
                continue
            shared = polygon_intersection(poly_a, poly_b)
            area = polygon_area(shared)
            if area < min_area_m2:
                continue
            suggestions.append({
                "camera_a_id": a["camera_id"], "camera_a_name": a["name"],
                "camera_b_id": b["camera_id"], "camera_b_name": b["name"],
                "coordinate_system_id": a.get("coordinate_system_id"),
                "overlap_polygon": [[round(p[0], 3), round(p[1], 3)] for p in shared],
                "overlap_area_m2": round(area, 2),
                "fraction_of_a": round(area / max(1e-9, polygon_area(poly_a)), 3),
                "fraction_of_b": round(area / max(1e-9, polygon_area(poly_b)), 3),
                "note": (
                    "Suggested from mapped floor areas. Geometry cannot see walls, machinery or "
                    "racking, so confirm on site before relying on it."
                ),
            })
    return sorted(suggestions, key=lambda s: -s["overlap_area_m2"])


def describe_graph(relationships: list[CameraRelationship], camera_ids: list[int]) -> dict:
    """Summarise the graph, including how much of it is simply unknown."""
    pairs = len(camera_ids) * (len(camera_ids) - 1) // 2
    overlaps = [r for r in relationships if r.kind == RelationshipKind.overlap]
    transitions = [r for r in relationships if r.kind == RelationshipKind.transition]
    excluded = [r for r in relationships if r.kind == RelationshipKind.excluded]

    stated_pairs = {frozenset((r.camera_a_id, r.camera_b_id)) for r in relationships}
    verified = [r for r in relationships if r.verification == VerificationStatus.verified]
    needs_review = [r for r in relationships if r.verification == VerificationStatus.needs_review]

    return {
        "cameras": len(camera_ids),
        "possible_pairs": pairs,
        "overlaps": len(overlaps),
        "transitions": len(transitions),
        "excluded": len(excluded),
        "pairs_with_a_record": len(stated_pairs),
        "pairs_unknown": max(0, pairs - len(stated_pairs)),
        "verified": len(verified),
        "unverified": len(relationships) - len(verified) - len(needs_review),
        "needs_review": len(needs_review),
        "interpretation": (
            "A pair with no record is unknown, not impossible. Only an explicit exclusion states "
            "that two views have no direct association, and even then people can still travel "
            "between them by way of other cameras."
        ),
    }


def zone_world_polygon(zone: MonitoredZone, project) -> list[list[float]] | None:
    """Map an image-space zone onto the floor, if the projection allows it."""
    try:
        image_polygon = json.loads(zone.image_polygon_json)
    except (json.JSONDecodeError, TypeError):
        return None
    world = []
    for u, v in image_polygon:
        try:
            x, y = project(float(u), float(v))
        except Exception:
            return None
        if not (math.isfinite(x) and math.isfinite(y)):
            return None
        world.append([round(x, 3), round(y, 3)])
    return world if len(world) >= 3 else None
