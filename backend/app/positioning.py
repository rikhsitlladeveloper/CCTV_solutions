"""Projection, validation and export built on a stored calibration.

One place decides how a given calibration turns pixels into world coordinates,
so the map, the 3D view, the test-projection tool and the multi-camera check can
never disagree about what a camera sees.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

from .calibration import (
    PIXELS_RAW, PIXELS_UNDISTORTED, CalibrationError, homography_image_to_floor,
)
from .geometry import CONVENTIONS, Pose, ProjectionError, intersect_ray_with_plane
from .intrinsics import Intrinsics
from .models import (
    CalibrationMethod, CalibrationRevision, CalibrationStatus, Camera, CameraIntrinsics,
    CoordinateSystem, ObservationRole, PixelConvention,
)

DEFAULT_MAX_RANGE_M = 200.0


class NotProjectable(ValueError):
    """The stored calibration cannot answer this projection question."""


# -- loading -------------------------------------------------------------

def intrinsics_from_record(record: CameraIntrinsics) -> Intrinsics:
    return Intrinsics.from_payload(
        camera_matrix=np.array(json.loads(record.camera_matrix_json), dtype=float),
        distortion=np.array(json.loads(record.distortion_json), dtype=float),
        width=record.width,
        height=record.height,
        model=record.model,
        rotation_deg=record.image_rotation_deg,
        crop=tuple(json.loads(record.crop_json)) if record.crop_json else None,
        source=record.source,
        notes=record.notes,
        metadata={"id": record.id, "label": record.label,
                  "calibrated_at": record.calibrated_at.isoformat() if record.calibrated_at else None},
    )


def pose_from_revision(revision: CalibrationRevision) -> Pose | None:
    if revision.position_x is None or revision.quat_w is None:
        return None
    return Pose.from_quaternion(
        revision.position_x, revision.position_y, revision.position_z,
        [revision.quat_w, revision.quat_x, revision.quat_y, revision.quat_z],
    )


@dataclass
class ProjectionModel:
    """Everything needed to project with one revision, and how far to trust it."""

    revision: CalibrationRevision
    method: CalibrationMethod
    pose: Pose | None = None
    intrinsics: Intrinsics | None = None
    H_image_to_floor: np.ndarray | None = None
    H_floor_to_image: np.ndarray | None = None
    plane_z: float = 0.0
    pixel_convention: str = PIXELS_RAW
    caveats: list[str] = field(default_factory=list)

    @property
    def is_approximate(self) -> bool:
        return self.method == CalibrationMethod.manual

    @property
    def supports_world_to_image(self) -> bool:
        return self.pose is not None and self.intrinsics is not None or self.H_floor_to_image is not None

    # -- image -> world ---------------------------------------------------

    def image_to_floor(self, u: float, v: float, plane_z: float | None = None) -> dict:
        """Project an image pixel onto a horizontal plane in world coordinates."""
        if not np.isfinite([u, v]).all():
            raise NotProjectable("The image point must be a pair of finite numbers.")
        z = self.plane_z if plane_z is None else float(plane_z)

        if self.intrinsics is not None and not self.intrinsics.contains_pixel(u, v, margin=2.0):
            raise NotProjectable(
                f"Pixel ({u:.0f}, {v:.0f}) lies outside the {self.intrinsics.width}x"
                f"{self.intrinsics.height} calibrated image."
            )

        if self.method == CalibrationMethod.homography:
            if self.H_image_to_floor is None:
                raise NotProjectable("This revision has no stored homography.")
            if z != self.plane_z:
                raise NotProjectable(
                    f"This homography was fitted for the plane Z = {self.plane_z} m and cannot be "
                    f"re-used for Z = {z} m. A homography maps one plane only; recover a full "
                    "camera pose to project onto other heights."
                )
            work_u, work_v = u, v
            if self.pixel_convention == PIXELS_UNDISTORTED and self.intrinsics is not None:
                work_u, work_v = self.intrinsics.undistort_points([[u, v]])[0]
            try:
                xy = homography_image_to_floor(self.H_image_to_floor, work_u, work_v)
            except CalibrationError as exc:
                raise NotProjectable(str(exc)) from exc
            return {
                "world": [float(xy[0]), float(xy[1]), z],
                "method": "homography",
                "plane_z": z,
                "distance_from_camera_m": None,
                "caveats": list(self.caveats),
            }

        if self.pose is None or self.intrinsics is None:
            raise NotProjectable(
                "This camera has no pose and intrinsics, so a pixel cannot be turned into a "
                "world position. Run a floor-plane or full-pose calibration first."
            )

        ray_camera = self.intrinsics.pixel_to_camera_ray(u, v)
        ray_world = self.pose.rotation @ ray_camera
        try:
            point = intersect_ray_with_plane(self.pose.position, ray_world, z, DEFAULT_MAX_RANGE_M)
        except ProjectionError as exc:
            raise NotProjectable(str(exc)) from exc

        return {
            "world": [float(point[0]), float(point[1]), float(point[2])],
            "method": "pose_ray_plane",
            "plane_z": z,
            "distance_from_camera_m": float(np.linalg.norm(point - self.pose.position)),
            "caveats": list(self.caveats),
        }

    # -- world -> image ---------------------------------------------------

    def world_to_image(self, x: float, y: float, z: float = 0.0) -> dict:
        if not np.isfinite([x, y, z]).all():
            raise NotProjectable("The world point must be three finite numbers.")

        if self.pose is not None and self.intrinsics is not None:
            cam = self.pose.world_to_camera_points([[x, y, z]])
            if cam[0, 2] <= 1e-6:
                raise NotProjectable(
                    "That world point is behind the camera, so it has no image position."
                )
            pixels, in_front = self.intrinsics.project_camera_points(cam)
            u, v = pixels[0]
            if not np.all(np.isfinite([u, v])):
                raise NotProjectable("The projection did not produce a finite pixel.")
            return {
                "pixel": [float(u), float(v)],
                "in_image": bool(self.intrinsics.contains_pixel(u, v)),
                "method": "pose_projection",
                "distance_from_camera_m": float(np.linalg.norm(
                    np.array([x, y, z]) - self.pose.position)),
                "caveats": list(self.caveats),
            }

        if self.H_floor_to_image is not None:
            if abs(z - self.plane_z) > 1e-6:
                raise NotProjectable(
                    f"This homography only describes the plane Z = {self.plane_z} m, so a point at "
                    f"Z = {z} m cannot be projected. Recover a full camera pose for off-plane points."
                )
            pt = np.array([x, y, 1.0])
            out = self.H_floor_to_image @ pt
            if abs(out[2]) < 1e-12:
                raise NotProjectable("That world point maps to the horizon and has no image position.")
            u, v = out[0] / out[2], out[1] / out[2]
            in_image = True
            if self.revision.source_image_width:
                in_image = (0 <= u <= self.revision.source_image_width - 1
                            and 0 <= v <= self.revision.source_image_height - 1)
            return {
                "pixel": [float(u), float(v)],
                "in_image": bool(in_image),
                "method": "homography_inverse",
                "distance_from_camera_m": None,
                "caveats": list(self.caveats),
            }

        raise NotProjectable(
            "This camera has no calibration that can project world points into the image."
        )


def build_projection_model(revision: CalibrationRevision) -> ProjectionModel:
    """Assemble the projection model for a stored revision, with its caveats."""
    caveats: list[str] = []
    intrinsics = None
    if revision.intrinsics is not None:
        intrinsics = intrinsics_from_record(revision.intrinsics)

    pose = pose_from_revision(revision)
    H = np.array(json.loads(revision.homography_json), dtype=float) \
        if revision.homography_json else None
    H_inv = np.array(json.loads(revision.homography_inverse_json), dtype=float) \
        if revision.homography_inverse_json else None

    if revision.method == CalibrationMethod.manual:
        caveats.append(
            "Position and orientation were entered by hand. They place the camera on the map but "
            "have not been solved against measured reference points, so any world coordinate "
            "derived from them is an approximation, not a measurement."
        )
        if intrinsics is None and revision.approx_hfov_deg:
            from .intrinsics import intrinsics_from_fov
            width = revision.source_image_width or 1920
            height = revision.source_image_height or 1080
            intrinsics = intrinsics_from_fov(width, height, revision.approx_hfov_deg)
            caveats.append(
                "The field of view is a nominal figure, not measured intrinsics: zero distortion "
                "and a centred principal point are assumed."
            )

    if revision.method == CalibrationMethod.homography and not revision.distortion_corrected:
        caveats.append(
            "Fitted on raw pixels without a distortion model. Lens distortion is not corrected, so "
            "error grows toward the image edges and outside the measured area."
        )

    if revision.status == CalibrationStatus.calibrated_unvalidated:
        caveats.append(
            "Solved but not independently validated: no held-out reference points have confirmed "
            "it against ground truth."
        )
    elif revision.status == CalibrationStatus.needs_recalibration:
        caveats.append(
            "Marked as needing recalibration. Results should not be relied on until it is redone."
        )

    return ProjectionModel(
        revision=revision,
        method=revision.method,
        pose=pose,
        intrinsics=intrinsics,
        H_image_to_floor=H,
        H_floor_to_image=H_inv,
        plane_z=revision.plane_z or 0.0,
        pixel_convention=revision.pixel_convention.value,
        caveats=caveats,
    )


# -- validation ----------------------------------------------------------

@dataclass
class ValidationOutcome:
    passed: bool
    fit_reprojection_error_px: float | None
    holdout_ground_error_m: float | None
    holdout_max_error_m: float | None
    holdout_point_count: int
    fit_point_count: int
    inlier_count: int
    outlier_count: int
    per_point: list[dict]
    thresholds: dict
    messages: list[str]

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "fit_reprojection_error_px": self.fit_reprojection_error_px,
            "holdout_ground_error_m": self.holdout_ground_error_m,
            "holdout_max_error_m": self.holdout_max_error_m,
            "holdout_point_count": self.holdout_point_count,
            "fit_point_count": self.fit_point_count,
            "inlier_count": self.inlier_count,
            "outlier_count": self.outlier_count,
            "per_point": self.per_point,
            "thresholds": self.thresholds,
            "messages": self.messages,
            "interpretation": {
                "fit_reprojection_error_px": (
                    "How closely the solve reproduced the points it was given. A low value means "
                    "the fit is self-consistent. On its own it is NOT evidence of real-world "
                    "accuracy: a solver can fit its own input well and still be wrong."
                ),
                "holdout_ground_error_m": (
                    "Distance on the floor between where held-out reference points actually are "
                    "and where this calibration puts them. These points were not used to solve, so "
                    "this is an independent check - but only over the area those points cover."
                ),
            },
        }


def validate_revision(revision: CalibrationRevision, camera: Camera,
                      coordinate_system: CoordinateSystem) -> ValidationOutcome:
    """Score a revision against its camera's observations and the site thresholds."""
    model = build_projection_model(revision)
    messages: list[str] = []

    fit_obs = [o for o in camera.observations if o.role == ObservationRole.fit]
    holdout_obs = [o for o in camera.observations if o.role == ObservationRole.holdout]

    metrics = json.loads(revision.metrics_json) if revision.metrics_json else {}
    fit_error = metrics.get("mean_reprojection_error_px")
    inliers = len(metrics.get("inlier_indices", []) or [])
    outliers = len(metrics.get("outlier_indices", []) or [])

    per_point: list[dict] = []
    errors: list[float] = []
    for obs in holdout_obs:
        point = obs.reference_point
        entry = {
            "reference_point_id": point.id,
            "code": point.code,
            "name": point.name,
            "expected_world": [point.x, point.y, point.z],
            "pixel": [obs.pixel_u, obs.pixel_v],
        }
        if (obs.image_width, obs.image_height) != (
                revision.source_image_width, revision.source_image_height):
            entry["error"] = (
                f"Observed at {obs.image_width}x{obs.image_height}, but the calibration was solved "
                f"at {revision.source_image_width}x{revision.source_image_height}. Pixels are not "
                "comparable across image geometries."
            )
            per_point.append(entry)
            continue
        try:
            projected = model.image_to_floor(obs.pixel_u, obs.pixel_v, plane_z=point.z)
            got = np.array(projected["world"])
            error = float(np.linalg.norm(got[:2] - np.array([point.x, point.y])))
            entry["projected_world"] = [round(float(c), 4) for c in got]
            entry["ground_error_m"] = round(error, 4)
            errors.append(error)
        except (NotProjectable, CalibrationError) as exc:
            entry["error"] = str(exc)
        per_point.append(entry)

    holdout_mean = float(np.mean(errors)) if errors else None
    holdout_max = float(np.max(errors)) if errors else None

    thresholds = {
        "max_reprojection_error_px": coordinate_system.max_reprojection_error_px,
        "max_ground_error_m": coordinate_system.max_ground_error_m,
        "min_reference_points": coordinate_system.min_reference_points,
    }

    passed = True
    if revision.method == CalibrationMethod.manual:
        passed = False
        messages.append(
            "A manual placement cannot be validated: there is nothing solved to check. Run a "
            "floor-plane or full-pose calibration against measured points."
        )
    if fit_error is not None and fit_error > thresholds["max_reprojection_error_px"]:
        passed = False
        messages.append(
            f"Fitting reprojection error {fit_error:.2f} px exceeds the configured limit of "
            f"{thresholds['max_reprojection_error_px']:.2f} px."
        )
    if not errors:
        passed = False
        messages.append(
            "No held-out reference points were available, so real-world accuracy has not been "
            "measured independently. Mark at least two observations as held-out and re-run."
        )
    else:
        if holdout_mean > thresholds["max_ground_error_m"]:
            passed = False
            messages.append(
                f"Mean held-out ground error {holdout_mean:.3f} m exceeds the configured limit of "
                f"{thresholds['max_ground_error_m']:.3f} m."
            )
        if len(errors) < 2:
            messages.append(
                "Only one held-out point was usable. Two or more, spread across the area, make the "
                "check meaningful."
            )

    total_points = len(fit_obs) + len(holdout_obs)
    if total_points < thresholds["min_reference_points"]:
        passed = False
        messages.append(
            f"{total_points} reference point(s) recorded, below the configured minimum of "
            f"{thresholds['min_reference_points']}."
        )

    if passed:
        messages.append(
            "Within the configured thresholds over the area the held-out points cover. That area "
            "is the extent of what has been checked."
        )

    return ValidationOutcome(
        passed=passed,
        fit_reprojection_error_px=fit_error,
        holdout_ground_error_m=holdout_mean,
        holdout_max_error_m=holdout_max,
        holdout_point_count=len(errors),
        fit_point_count=len(fit_obs),
        inlier_count=inliers,
        outlier_count=outliers,
        per_point=per_point,
        thresholds=thresholds,
        messages=messages,
    )


# -- export --------------------------------------------------------------

def export_calibration(camera: Camera, revision: CalibrationRevision,
                       coordinate_system: CoordinateSystem) -> dict:
    """A self-describing calibration bundle for downstream services.

    Contains no credentials and no stream URLs: geometry only.
    """
    model = build_projection_model(revision)
    latest_validation = revision.validations[-1] if revision.validations else None

    payload: dict = {
        "format": "numenor.camera-calibration",
        "format_version": "1.0",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "conventions": dict(CONVENTIONS),
        "coordinate_system": {
            "id": coordinate_system.id,
            "name": coordinate_system.name,
            "origin_description": coordinate_system.origin_description,
            "definition_revision": coordinate_system.definition_revision,
            "units": "metres",
            "floor_plane_z": coordinate_system.floor_plane_z,
        },
        "camera": {
            "id": camera.id,
            "name": camera.name,
            # Deliberately no host, username, password or stream URL.
            "manufacturer": camera.manufacturer,
            "model": camera.model,
            "location": {
                "site": camera.area.floor.building.site.name if camera.area else None,
                "building": camera.area.floor.building.name if camera.area else None,
                "floor": camera.area.floor.name if camera.area else None,
                "area": camera.area.name if camera.area else None,
            },
        },
        "calibration": {
            "revision_id": revision.id,
            "revision_number": revision.revision_number,
            "method": revision.method.value,
            "status": revision.status.value,
            "solver": revision.solver,
            "is_active": revision.is_active,
            "created_at": revision.created_at.isoformat(),
            "activated_at": revision.activated_at.isoformat() if revision.activated_at else None,
            "pixel_convention": revision.pixel_convention.value,
            "distortion_corrected": revision.distortion_corrected,
            "source_image": {
                "width": revision.source_image_width,
                "height": revision.source_image_height,
            },
            "plane_z": revision.plane_z,
            "caveats": model.caveats,
        },
        "limitations": [
            "Geometry only. This file describes where a camera is and how it projects; it makes no "
            "claim about detection, tracking or identity.",
            "A calibration is valid only for the exact image geometry recorded under source_image. "
            "Re-calibrate after any change of resolution, crop, rotation, lens or zoom.",
            "Accuracy figures apply to the area the reference points cover, not the whole view.",
        ],
    }

    if model.pose is not None:
        roll, pitch, yaw = model.pose.rpy_degrees
        payload["calibration"]["pose"] = {
            "T_world_camera": model.pose.T_world_camera.tolist(),
            "position_m": {"x": float(model.pose.position[0]),
                           "y": float(model.pose.position[1]),
                           "z": float(model.pose.position[2])},
            "quaternion_wxyz": model.pose.quaternion.tolist(),
            "rpy_deg": {"roll": roll, "pitch": pitch, "yaw": yaw},
            "map_heading_deg": model.pose.map_heading_deg,
        }
    else:
        payload["calibration"]["pose"] = None
        payload["calibration"]["pose_note"] = (
            "No camera pose is available. A floor homography maps image pixels to one floor plane "
            "but does not determine where the camera is; no pose has been invented from it."
        )

    if model.H_image_to_floor is not None:
        payload["calibration"]["floor_homography"] = {
            "H_image_to_floor": model.H_image_to_floor.tolist(),
            "H_floor_to_image": model.H_floor_to_image.tolist() if model.H_floor_to_image is not None else None,
            "applies_to_plane_z": revision.plane_z,
            "pixel_convention": revision.pixel_convention.value,
        }

    if revision.intrinsics is not None:
        payload["calibration"]["intrinsics"] = intrinsics_from_record(revision.intrinsics).to_dict()
        payload["calibration"]["intrinsics"]["rms_reprojection_error_px"] = \
            revision.intrinsics.rms_reprojection_error_px
        payload["calibration"]["intrinsics"]["lens"] = revision.intrinsics.lens_description
        payload["calibration"]["intrinsics"]["zoom_state"] = revision.intrinsics.zoom_state
    elif revision.approx_hfov_deg:
        payload["calibration"]["intrinsics"] = None
        payload["calibration"]["approximate_fov_deg"] = {
            "horizontal": revision.approx_hfov_deg,
            "vertical": revision.approx_vfov_deg,
            "note": "A nominal field of view for visualisation. Not measured intrinsics.",
        }

    payload["calibration"]["metrics"] = json.loads(revision.metrics_json) if revision.metrics_json else None
    payload["calibration"]["warnings"] = json.loads(revision.warnings_json) if revision.warnings_json else []

    if latest_validation:
        payload["calibration"]["validation"] = {
            "passed": latest_validation.passed,
            "fit_reprojection_error_px": latest_validation.fit_reprojection_error_px,
            "holdout_ground_error_m": latest_validation.holdout_ground_error_m,
            "holdout_max_error_m": latest_validation.holdout_max_error_m,
            "holdout_point_count": latest_validation.holdout_point_count,
            "fit_point_count": latest_validation.fit_point_count,
            "inlier_count": latest_validation.inlier_count,
            "outlier_count": latest_validation.outlier_count,
            "reviewer": latest_validation.reviewer,
            "validated_at": latest_validation.created_at.isoformat(),
            "thresholds": json.loads(latest_validation.thresholds_json)
            if latest_validation.thresholds_json else None,
            "note": ("Fitting error and held-out error mean different things; see "
                     "conventions and limitations."),
        }
    else:
        payload["calibration"]["validation"] = None

    payload["reference_points"] = [
        {
            "id": obs.reference_point.id,
            "code": obs.reference_point.code,
            "name": obs.reference_point.name,
            "world_m": [obs.reference_point.x, obs.reference_point.y, obs.reference_point.z],
            "pixel": [obs.pixel_u, obs.pixel_v],
            "role": obs.role.value,
            "observed_at_image": [obs.image_width, obs.image_height],
            "uncertainty_m": obs.reference_point.uncertainty_m,
        }
        for obs in camera.observations
    ]
    return payload


# -- multi-camera consistency -------------------------------------------

def compare_ground_observations(entries: list[dict]) -> dict:
    """Compare where several cameras place the same physical ground point.

    ``entries`` are ``{"camera_id", "camera_name", "world"|"error"}`` dictionaries.
    This is a *consistency* check between cameras, not an accuracy measurement:
    every camera could agree and all be wrong together.
    """
    usable = [e for e in entries if e.get("world") is not None]
    pairs = []
    for i in range(len(usable)):
        for j in range(i + 1, len(usable)):
            a, b = usable[i], usable[j]
            distance = math.dist(a["world"][:2], b["world"][:2])
            pairs.append({
                "camera_a_id": a["camera_id"], "camera_a_name": a["camera_name"],
                "camera_b_id": b["camera_id"], "camera_b_name": b["camera_name"],
                "disagreement_m": round(distance, 4),
            })

    centroid = None
    spread = None
    if usable:
        points = np.array([e["world"][:2] for e in usable], dtype=float)
        centroid = points.mean(axis=0)
        spread = float(np.max(np.linalg.norm(points - centroid, axis=1))) if len(points) > 1 else 0.0

    return {
        "cameras": entries,
        "usable_count": len(usable),
        "pairwise": pairs,
        "max_disagreement_m": round(max((p["disagreement_m"] for p in pairs), default=0.0), 4),
        "mean_disagreement_m": round(
            float(np.mean([p["disagreement_m"] for p in pairs])) if pairs else 0.0, 4),
        "centroid_world": [round(float(c), 4) for c in centroid] if centroid is not None else None,
        "max_distance_from_centroid_m": round(spread, 4) if spread is not None else None,
        "interpretation": (
            "This is a consistency check between calibrations, not an accuracy measurement. It "
            "shows how far apart the cameras place the same physical point. Agreement does not "
            "prove either camera is correct - they can share a common error. Only a surveyed "
            "ground-truth coordinate measures accuracy."
        ),
    }
