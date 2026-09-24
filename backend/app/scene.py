"""The factory scene: spatial model, publishing, visual camera aiming, readiness.

The scene says where things are. It is deliberately not a simulation: it carries
no cycle times, routing, capacities or machine behaviour, and placing cameras in
it does not make throughput simulation possible. Those properties would be a
separate model entirely.

Provenance is tracked per object. A scene drawn from memory organises cameras
perfectly well, but it is *estimated* geometry and never counts as surveyed
ground truth for metric validation.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass

import numpy as np

from .geometry import Pose, pose_from_look_at
from .models import (
    CalibrationMethod, CalibrationRevision, CalibrationStatus, Camera, CameraFunction,
    FunctionKind, GeometryProvenance, MountType, Scene, SceneObject, SceneObjectKind,
)

# Objects described by an outline rather than a box.
POLYGONAL_KINDS = {SceneObjectKind.walkway, SceneObjectKind.restricted_area}
LINEAR_KINDS = {SceneObjectKind.wall, SceneObjectKind.conveyor}

# Sensible starting sizes, in metres, so a dropped object looks like the thing
# it represents before anyone measures it.
DEFAULT_SIZES: dict[SceneObjectKind, tuple[float, float, float]] = {
    SceneObjectKind.wall: (4.0, 0.2, 3.0),
    SceneObjectKind.door: (1.0, 0.2, 2.1),
    SceneObjectKind.column: (0.4, 0.4, 4.0),
    SceneObjectKind.machine: (2.0, 1.5, 1.8),
    SceneObjectKind.rack: (2.7, 1.1, 2.5),
    SceneObjectKind.workstation: (1.6, 0.8, 1.0),
    SceneObjectKind.conveyor: (6.0, 0.8, 0.9),
    SceneObjectKind.walkway: (8.0, 1.5, 0.01),
    SceneObjectKind.restricted_area: (4.0, 4.0, 0.01),
}

PALETTE: dict[SceneObjectKind, str] = {
    SceneObjectKind.wall: "#8593a6",
    SceneObjectKind.door: "#0f7b4f",
    SceneObjectKind.column: "#5a6779",
    SceneObjectKind.machine: "#1d4ed8",
    SceneObjectKind.rack: "#6d5bd0",
    SceneObjectKind.workstation: "#0f7b4f",
    SceneObjectKind.conveyor: "#9a6207",
    SceneObjectKind.walkway: "#b9cdf7",
    SceneObjectKind.restricted_area: "#b0281f",
}


class SceneError(ValueError):
    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint


# -- object geometry -----------------------------------------------------

def object_footprint(obj: SceneObject) -> list[list[float]]:
    """The object's outline on the floor, in world metres."""
    if obj.points_json:
        try:
            points = json.loads(obj.points_json)
            if len(points) >= 2:
                return [[float(p[0]), float(p[1])] for p in points]
        except (json.JSONDecodeError, TypeError, IndexError):
            pass

    half_w, half_d = obj.width_m / 2.0, obj.depth_m / 2.0
    corners = [(-half_w, -half_d), (half_w, -half_d), (half_w, half_d), (-half_w, half_d)]
    rad = math.radians(obj.rotation_deg)
    cos_r, sin_r = math.cos(rad), math.sin(rad)
    return [[obj.x + cx * cos_r - cy * sin_r, obj.y + cx * sin_r + cy * cos_r]
            for cx, cy in corners]


def object_to_dict(obj: SceneObject) -> dict:
    return {
        "id": obj.id,
        "kind": obj.kind.value,
        "name": obj.name,
        "x": obj.x, "y": obj.y, "z": obj.z,
        "rotation_deg": obj.rotation_deg,
        "width_m": obj.width_m, "depth_m": obj.depth_m, "height_m": obj.height_m,
        "points": json.loads(obj.points_json) if obj.points_json else None,
        "footprint": object_footprint(obj),
        "provenance": obj.provenance.value,
        "colour": obj.colour or PALETTE.get(obj.kind, "#5a6779"),
        "notes": obj.notes,
    }


def scene_provenance(objects: list[SceneObject]) -> GeometryProvenance:
    """A scene is only as trustworthy as its least-measured object."""
    if objects and all(o.provenance == GeometryProvenance.measured for o in objects):
        return GeometryProvenance.measured
    return GeometryProvenance.estimated


def publish_snapshot(scene: Scene) -> str:
    """Freeze the draft objects so what is live stops moving when the draft does."""
    return json.dumps({
        "revision": scene.draft_revision,
        "building_width_m": scene.building_width_m,
        "building_length_m": scene.building_length_m,
        "geometry_provenance": scene_provenance(scene.objects).value,
        "objects": [object_to_dict(o) for o in scene.objects],
    })


# -- visual camera aiming ------------------------------------------------

@dataclass
class VisualPlacement:
    """What the installer did on screen, turned into a pose.

    The camera sits at ``(x, y, height)`` and looks at ``(target_x, target_y,
    target_z)``. Tilt and roll adjust that basic aim. The resulting pose uses the
    project's optical convention, so the 2D map, the 3D view and any projection
    all agree with each other.
    """
    x: float
    y: float
    height_m: float
    target_x: float
    target_y: float
    target_z: float = 0.0
    roll_deg: float = 0.0
    mount_type: MountType = MountType.wall


def pose_from_visual_placement(placement: VisualPlacement) -> Pose:
    """Aim a camera at a point, then apply any roll correction."""
    eye = np.array([placement.x, placement.y, placement.height_m], dtype=float)
    target = np.array([placement.target_x, placement.target_y, placement.target_z], dtype=float)

    if float(np.linalg.norm(target - eye)) < 1e-6:
        raise SceneError(
            "The camera is aimed at the exact spot it is mounted, so there is no direction.",
            hint="Click a target point away from the camera.",
        )

    pose = pose_from_look_at(eye, target)

    if abs(placement.roll_deg) > 1e-9:
        # Roll turns the picture about the optical axis without changing where
        # the camera points.
        rad = math.radians(placement.roll_deg)
        cos_r, sin_r = math.cos(rad), math.sin(rad)
        about_axis = np.array([[cos_r, -sin_r, 0.0],
                               [sin_r, cos_r, 0.0],
                               [0.0, 0.0, 1.0]])
        pose = Pose(pose.rotation @ about_axis, pose.position)

    return pose


def describe_aim(pose: Pose, placement: VisualPlacement) -> dict:
    """Plain-language summary of where a camera points."""
    heading = pose.map_heading_deg
    horizontal = math.hypot(placement.target_x - placement.x, placement.target_y - placement.y)
    drop = placement.height_m - placement.target_z
    tilt_down = math.degrees(math.atan2(drop, horizontal)) if horizontal > 1e-9 else 90.0
    return {
        "mount_type": placement.mount_type.value,
        "height_m": round(placement.height_m, 2),
        "aims_at": [round(placement.target_x, 2), round(placement.target_y, 2)],
        "distance_to_target_m": round(horizontal, 2),
        "tilt_below_horizontal_deg": round(tilt_down, 1),
        "map_heading_deg": round(heading, 1) if heading is not None else None,
        "note": ("Worked out from where you put the camera and what you aimed it at. "
                 "It is an approximate placement, not a measured one."),
    }


# -- readiness -----------------------------------------------------------

# Processing services this build can actually run. Everything else is
# configurable but explicitly not running.
AVAILABLE_PROCESSING: set[FunctionKind] = set()

FUNCTION_LABELS: dict[FunctionKind, str] = {
    FunctionKind.people_counting: "People counting",
    FunctionKind.product_counting: "Product counting",
    FunctionKind.restricted_zone: "Restricted-zone monitoring",
    FunctionKind.ppe_monitoring: "PPE monitoring",
    FunctionKind.workstation_occupancy: "Workstation occupancy",
    FunctionKind.twin_positions: "Show ground positions in the digital twin",
    FunctionKind.cross_camera_tracking: "Cross-camera tracking",
}

# Which functions need metric floor geometry rather than just picture regions.
NEEDS_FLOOR_MAPPING = {FunctionKind.twin_positions, FunctionKind.cross_camera_tracking}


def function_status(function: CameraFunction, camera: Camera) -> dict:
    """What state a configured function is really in."""
    processing = function.kind in AVAILABLE_PROCESSING
    active = next((r for r in camera.calibration_revisions if r.is_active), None)
    has_mapping = bool(active and active.homography_json
                       and active.status != CalibrationStatus.needs_recalibration)

    blockers: list[str] = []
    if function.kind in NEEDS_FLOOR_MAPPING and not has_mapping:
        blockers.append(
            "Needs a validated floor mapping for this camera before positions mean anything "
            "on the factory floor."
        )

    if not processing:
        state = "configured_unavailable"
        summary = "Configured — processing unavailable"
        detail = ("The settings are saved and will be handed to a processing service when one is "
                  "connected. Nothing is analysing video today.")
    elif blockers:
        state = "blocked"
        summary = "Configured — blocked"
        detail = blockers[0]
    elif not function.enabled:
        state = "disabled"
        summary = "Configured — turned off"
        detail = "Saved but switched off."
    else:
        state = "running"
        summary = "Running"
        detail = "A processing service is handling this."

    return {
        "state": state,
        "summary": summary,
        "detail": detail,
        "processing_available": processing,
        "blockers": blockers,
        "space": function.space.value,
        "requires_floor_mapping": function.kind in NEEDS_FLOOR_MAPPING,
    }


def _ready(state: str, label: str, detail: str, next_step: str | None = None,
           counts: dict | None = None) -> dict:
    return {"state": state, "label": label, "detail": detail,
            "next_step": next_step, "counts": counts or {}}


def assess_readiness(cameras: list[Camera], scene: Scene | None,
                     relationships: list, reference_point_count: int) -> dict:
    """Per-capability readiness. No single ambiguous "setup complete" badge."""
    from .models import TestStatus

    total = len(cameras)
    reachable = [c for c in cameras if c.last_test_status == TestStatus.online]
    actives = {c.id: next((r for r in c.calibration_revisions if r.is_active), None)
               for c in cameras}

    placed = [c for c in cameras if actives[c.id] is not None]
    approximate = [c for c in cameras if actives[c.id]
                   and actives[c.id].method == CalibrationMethod.manual]
    mapped = [c for c in cameras if actives[c.id]
              and actives[c.id].homography_json
              and actives[c.id].status != CalibrationStatus.needs_recalibration]
    validated = [c for c in cameras if actives[c.id]
                 and actives[c.id].status == CalibrationStatus.validated]
    posed = [c for c in cameras if actives[c.id]
             and actives[c.id].method == CalibrationMethod.pnp
             and actives[c.id].status != CalibrationStatus.needs_recalibration]
    stale = [c for c in cameras if actives[c.id]
             and actives[c.id].status == CalibrationStatus.needs_recalibration]

    functions = [f for c in cameras for f in c.functions]
    runnable = [f for f in functions if f.kind in AVAILABLE_PROCESSING]

    capabilities = []

    # 1. Video connectivity
    capabilities.append(_ready(
        "ready" if total and len(reachable) == total else "partial" if reachable else "blocked",
        "Video connectivity",
        f"{len(reachable)} of {total} camera(s) answering."
        if total else "No cameras registered yet.",
        None if total and len(reachable) == total
        else "Check the cameras that are not answering." if total
        else "Register the cameras watching this area.",
        {"reachable": len(reachable), "total": total},
    ))

    # 2. Placement
    capabilities.append(_ready(
        "ready" if total and len(placed) == total else "partial" if placed else "blocked",
        "Camera placement",
        f"{len(placed)} of {total} placed in the scene"
        + (f", {len(approximate)} approximate." if approximate else "."),
        None if total and len(placed) == total else "Drag the remaining cameras onto the scene.",
        {"placed": len(placed), "approximate": len(approximate), "total": total},
    ))

    # 3. Detection configuration
    capabilities.append(_ready(
        "ready" if functions else "blocked",
        "Detection and counting configuration",
        f"{len(functions)} function(s) configured across {len({f.camera_id for f in functions})} camera(s)."
        if functions else "No camera functions configured.",
        None if functions else "Open a camera and choose what it should do.",
        {"configured": len(functions)},
    ))

    # 4. Whether anything can actually run them
    capabilities.append(_ready(
        "ready" if runnable and len(runnable) == len(functions) else
        "partial" if runnable else "unavailable",
        "AI processing availability",
        f"{len(runnable)} of {len(functions)} configured function(s) have a processing service."
        if functions else "Nothing configured yet.",
        "No detection service is connected to this deployment. Configuration is stored and will "
        "be handed over when one is." if functions and not runnable else None,
        {"runnable": len(runnable), "configured": len(functions)},
    ))

    # 5. Floor mapping
    capabilities.append(_ready(
        "ready" if total and len(validated) == total else "partial" if mapped else "blocked",
        "Metric floor mapping",
        f"{len(mapped)} camera(s) mapped, {len(validated)} checked against measured points."
        + (f" {len(stale)} need redoing." if stale else ""),
        "Check the mapped cameras against held-out measured points."
        if mapped and not validated else
        "Redo the stale mappings." if stale else
        "Measure floor points and match them in each camera." if not mapped else None,
        {"mapped": len(mapped), "validated": len(validated), "stale": len(stale), "total": total},
    ))

    # 6. Full 3D alignment
    capabilities.append(_ready(
        "ready" if posed and len(posed) == total else "partial" if posed else "unavailable",
        "Full 3D alignment",
        f"{len(posed)} camera(s) have a full 3D pose."
        if posed else "No camera has a full 3D pose. Floor mapping covers ground positions; "
                      "3D alignment is only needed for heights above the floor.",
        None if posed else "Optional. Import lens calibration and run full alignment if you need "
                           "positions off the floor plane.",
        {"posed": len(posed), "total": total},
    ))

    # 7. Relationships
    confirmed = [r for r in relationships if getattr(r.verification, "value", "") == "verified"]
    capabilities.append(_ready(
        "ready" if confirmed else "partial" if relationships else "blocked",
        "Camera relationships",
        f"{len(relationships)} recorded, {len(confirmed)} confirmed on site."
        if relationships else "No relationships recorded between cameras.",
        "Confirm the recorded relationships on site." if relationships and not confirmed
        else "Record which cameras share ground and where people walk between them."
        if not relationships else None,
        {"recorded": len(relationships), "confirmed": len(confirmed)},
    ))

    # 8. Cross-camera tracking
    capabilities.append(_ready(
        "unavailable",
        "Cross-camera tracking",
        "No tracking service is connected to this deployment.",
        "The geometry and relationships needed by a tracking service can be exported now; "
        "the service itself is not part of this system.",
        {},
    ))

    # 9. Scene quality
    if scene is None:
        scene_state, scene_detail, scene_next = "blocked", "No factory scene yet.", "Create the scene."
        counts = {}
    else:
        measured = sum(1 for o in scene.objects if o.provenance == GeometryProvenance.measured)
        counts = {"objects": len(scene.objects), "measured": measured,
                  "reference_points": reference_point_count}
        if not scene.objects:
            scene_state = "partial"
            scene_detail = "The scene is empty."
            scene_next = "Draw the walls and drop in the main machines."
        elif measured == len(scene.objects):
            scene_state = "ready"
            scene_detail = f"All {len(scene.objects)} object(s) have measured dimensions."
            scene_next = None
        else:
            scene_state = "partial"
            scene_detail = (f"{measured} of {len(scene.objects)} object(s) measured; the rest are "
                            "estimated. Estimated geometry organises cameras but is not survey "
                            "data.")
            scene_next = "Measure the objects you intend to use as calibration landmarks."
    capabilities.append(_ready(scene_state, "Scene geometry quality", scene_detail, scene_next, counts))

    order = {"ready": 0, "partial": 1, "unavailable": 1, "blocked": 2}
    worst = max((order.get(c["state"], 1) for c in capabilities), default=2)

    return {
        "capabilities": capabilities,
        "overall": ["ready", "partial", "blocked"][worst],
        "interpretation": (
            "Each capability stands on its own. A camera can be counting people in its picture "
            "while its floor geometry is still approximate, and that is a perfectly valid "
            "partial commissioning. There is deliberately no single 'setup complete' badge."
        ),
    }
