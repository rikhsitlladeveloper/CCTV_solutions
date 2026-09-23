"""The guided floor-mapping pipeline.

One documented route from "installer clicked some points" to "we can turn a
pixel into a floor position", with no solver choice to make.

The pipeline
------------
1. Read the image geometry the observations were marked on.
2. If valid intrinsics exist for that geometry, undistort the observation
   pixels into a stated output camera matrix. Otherwise work on raw pixels and
   say so — never claim distortion was corrected.
3. Estimate ``H_image_to_floor`` with RANSAC.
4. Refit on the accepted inliers alone.
5. Check conditioning, invertibility, residuals and degenerate geometry.
6. Evaluate held-out validation points that took no part in the fit.
7. Store the homography, its inverse, the pixel convention, the coverage
   polygon and the quality figures.

Direction
---------
``H_image_to_floor`` maps homogeneous *image* coordinates to floor XY::

    [wx, wy, w]ᵀ = H · [u, v, 1]ᵀ      floor_x = wx / w,  floor_y = wy / w

Pixel space
-----------
Exactly one of two spaces is used, recorded on the result and applied
identically during calibration, preview, validation and export:

* ``raw`` — pixels as they come out of the stream, distortion included.
* ``undistorted`` — pixels after ``cv2.undistortPoints`` with ``P`` set to the
  stored camera matrix, so the output is still pixels in that same matrix.

Normalised camera coordinates are never mixed with pixel coordinates.

A floor homography is not a camera pose. It cannot say where the camera is, and
this module does not pretend otherwise.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from .calibration import PIXELS_RAW, PIXELS_UNDISTORTED, CalibrationError, DegenerateConfiguration
from .intrinsics import Intrinsics

log = logging.getLogger("numenor.floormapping")

MIN_POINTS = 4
RECOMMENDED_POINTS = 6
GOOD_POINTS = 10

# Above this, the homography is numerically fragile and small pixel errors
# become large floor errors.
MAX_CONDITION_NUMBER = 1e7


class MappingError(CalibrationError):
    """A mapping could not be produced, with an explanation an installer can act on."""

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint


@dataclass
class PointInput:
    """One correspondence: a surveyed floor point seen at an image pixel."""
    reference_point_id: int
    code: str
    name: str
    world_x: float
    world_y: float
    pixel_u: float
    pixel_v: float
    role: str                 # "calibration" or "validation"


@dataclass
class MappingResult:
    H_image_to_floor: np.ndarray
    H_floor_to_image: np.ndarray
    pixel_convention: str
    distortion_corrected: bool
    image_width: int
    image_height: int
    plane_z: float
    used_point_ids: list[int]
    rejected_point_ids: list[int]
    per_point: list[dict]
    mean_error_m: float
    median_error_m: float
    max_error_m: float
    mean_reprojection_px: float
    coverage_polygon: list[list[float]]
    coverage_area_m2: float
    condition_number: float
    ransac_threshold_px: float
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "method": "floor_homography",
            "H_image_to_floor": self.H_image_to_floor.tolist(),
            "H_floor_to_image": self.H_floor_to_image.tolist(),
            "pixel_convention": self.pixel_convention,
            "distortion_corrected": self.distortion_corrected,
            "image_width": self.image_width,
            "image_height": self.image_height,
            "plane_z": self.plane_z,
            "used_point_ids": self.used_point_ids,
            "rejected_point_ids": self.rejected_point_ids,
            "inlier_indices": self.used_point_ids,
            "outlier_indices": self.rejected_point_ids,
            "per_point": self.per_point,
            "mean_error_m": round(self.mean_error_m, 4),
            "median_error_m": round(self.median_error_m, 4),
            "max_error_m": round(self.max_error_m, 4),
            "mean_reprojection_error_px": round(self.mean_reprojection_px, 4),
            "coverage_polygon": self.coverage_polygon,
            "coverage_area_m2": round(self.coverage_area_m2, 2),
            "condition_number": round(self.condition_number, 1),
            "ransac_threshold_px": self.ransac_threshold_px,
            "ransac_threshold_space": self.pixel_convention,
            "warnings": self.warnings,
            "notes": self.notes,
        }


# -- geometry helpers ----------------------------------------------------

def _collinearity(points: np.ndarray) -> float:
    """0 = a straight line, 1 = evenly spread. Scale free."""
    if points.shape[0] < 3:
        return 0.0
    centred = points - points.mean(axis=0)
    sv = np.linalg.svd(centred, compute_uv=False)
    return 0.0 if sv[0] < 1e-12 else float(sv[1] / sv[0])


def convex_hull(points: np.ndarray) -> list[list[float]]:
    if points.shape[0] < 3:
        return [[float(x), float(y)] for x, y in points]
    hull = cv2.convexHull(points.astype(np.float32).reshape(-1, 1, 2))
    return [[float(p[0][0]), float(p[0][1])] for p in hull]


def polygon_area(polygon: list[list[float]]) -> float:
    if len(polygon) < 3:
        return 0.0
    pts = np.array(polygon, dtype=float)
    x, y = pts[:, 0], pts[:, 1]
    return float(abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1))) / 2.0)


def point_in_polygon(x: float, y: float, polygon: list[list[float]]) -> bool:
    if len(polygon) < 3:
        return False
    contour = np.array(polygon, dtype=np.float32).reshape(-1, 1, 2)
    return cv2.pointPolygonTest(contour, (float(x), float(y)), False) >= 0


def apply_homography(H: np.ndarray, points) -> np.ndarray:
    """Project points through a homography, with non-finite results as NaN."""
    pts = np.asarray(points, dtype=float).reshape(-1, 2)
    homogeneous = np.column_stack([pts, np.ones(len(pts))])
    out = (np.asarray(H, dtype=float) @ homogeneous.T).T
    w = out[:, 2:3]
    with np.errstate(divide="ignore", invalid="ignore"):
        result = out[:, :2] / w
    # A vanishing denominator is the horizon: undefined, not "very far away".
    result[np.abs(w.ravel()) < 1e-9] = np.nan
    result[~np.isfinite(result).all(axis=1)] = np.nan
    return result


def project_image_to_floor(H: np.ndarray, u: float, v: float) -> tuple[float, float]:
    out = apply_homography(H, [[u, v]])[0]
    if not np.all(np.isfinite(out)):
        raise MappingError(
            "That point sits on the horizon line of this mapping, where a floor "
            "position does not exist.",
            hint="Pick a point lower in the image, on the floor itself.",
        )
    return float(out[0]), float(out[1])


def project_floor_to_image(H_inv: np.ndarray, x: float, y: float) -> tuple[float, float]:
    out = apply_homography(H_inv, [[x, y]])[0]
    if not np.all(np.isfinite(out)):
        raise MappingError("That floor position does not project into this image.")
    return float(out[0]), float(out[1])


# -- pre-flight checks ---------------------------------------------------

def inspect_points(points: list[PointInput], image_width: int, image_height: int) -> list[dict]:
    """Problems worth warning about before anyone presses Calculate."""
    issues: list[dict] = []
    fitting = [p for p in points if p.role == "calibration"]
    validation = [p for p in points if p.role == "validation"]

    if len(fitting) < MIN_POINTS:
        issues.append({
            "level": "error", "code": "too_few_points",
            "message": f"{len(fitting)} calibration point(s) matched. At least {MIN_POINTS} are "
                       "needed to calculate a mapping.",
        })
    elif len(fitting) < RECOMMENDED_POINTS:
        issues.append({
            "level": "warning", "code": "few_points",
            "message": f"Only {len(fitting)} calibration points. Six to ten, spread across the "
                       "area, give a noticeably more reliable mapping.",
        })

    if not validation:
        issues.append({
            "level": "warning", "code": "no_validation_points",
            "message": "No validation points are matched, so accuracy cannot be checked "
                       "independently. Measure two or three extra points and mark them as "
                       "validation points.",
        })

    if len(fitting) >= 3:
        image_pts = np.array([[p.pixel_u, p.pixel_v] for p in fitting])
        world_pts = np.array([[p.world_x, p.world_y] for p in fitting])

        for label, pts in (("image", image_pts), ("floor", world_pts)):
            ratio = _collinearity(pts)
            if ratio < 0.02:
                issues.append({
                    "level": "error", "code": f"collinear_{label}",
                    "message": f"The {label} points lie almost in a straight line, which cannot "
                               "define a mapping.",
                })
            elif ratio < 0.15:
                issues.append({
                    "level": "warning", "code": f"near_collinear_{label}",
                    "message": f"The {label} points are close to a straight line. The mapping will "
                               "be unreliable away from that line.",
                })

        # How much of the frame the points actually span.
        span_x = (image_pts[:, 0].max() - image_pts[:, 0].min()) / max(1, image_width)
        span_y = (image_pts[:, 1].max() - image_pts[:, 1].min()) / max(1, image_height)
        if span_x < 0.3 or span_y < 0.3:
            issues.append({
                "level": "warning", "code": "small_image_region",
                "message": f"The matched points cover only {span_x * 100:.0f}% of the frame width "
                           f"and {span_y * 100:.0f}% of its height. The mapping will be poor "
                           "outside that region.",
            })

    # Duplicates in either space.
    seen_world: dict[tuple, str] = {}
    for p in points:
        key = (round(p.world_x, 3), round(p.world_y, 3))
        if key in seen_world:
            issues.append({
                "level": "warning", "code": "duplicate_world",
                "message": f"{p.code} and {seen_world[key]} have the same measured position "
                           f"({p.world_x}, {p.world_y}).",
            })
        seen_world[key] = p.code

    for i, a in enumerate(points):
        for b in points[i + 1:]:
            if math.hypot(a.pixel_u - b.pixel_u, a.pixel_v - b.pixel_v) < 5:
                issues.append({
                    "level": "warning", "code": "duplicate_image",
                    "message": f"{a.code} and {b.code} were clicked within 5 px of each other.",
                })
    return issues


# -- the pipeline --------------------------------------------------------

def calculate_floor_mapping(points: list[PointInput],
                            image_width: int, image_height: int,
                            intrinsics: Intrinsics | None = None,
                            plane_z: float = 0.0,
                            ransac_threshold_px: float = 3.0) -> MappingResult:
    """Run the full pipeline. Raises :class:`MappingError` with an actionable hint."""
    fitting = [p for p in points if p.role == "calibration"]
    validation = [p for p in points if p.role == "validation"]

    if len(fitting) < MIN_POINTS:
        raise MappingError(
            f"A mapping needs at least {MIN_POINTS} calibration points; {len(fitting)} are matched.",
            hint="Match more measured floor points, or change a validation point to a "
                 "calibration point.",
        )

    warnings: list[str] = []
    notes: list[str] = []

    image_pts = np.array([[p.pixel_u, p.pixel_v] for p in fitting], dtype=np.float64)
    world_pts = np.array([[p.world_x, p.world_y] for p in fitting], dtype=np.float64)

    if not np.all(np.isfinite(image_pts)) or not np.all(np.isfinite(world_pts)):
        raise MappingError("Some matched points contain values that are not finite numbers.")

    # 1-2. Pixel space.
    work_pts = image_pts
    pixel_convention = PIXELS_RAW
    distortion_corrected = False
    if intrinsics is not None:
        intrinsics.require_matches(image_width, image_height)
        if intrinsics.has_distortion:
            work_pts = intrinsics.undistort_points(image_pts)
            distortion_corrected = True
            pixel_convention = PIXELS_UNDISTORTED
            notes.append(
                "Lens distortion was removed using this camera's stored calibration. Pixel "
                f"positions are expressed in the same camera matrix ({intrinsics.geometry_key})."
            )
        else:
            notes.append("The stored calibration has no distortion, so raw pixels are used directly.")
    else:
        warnings.append(
            "No lens calibration is available for this camera, so the mapping was fitted on raw "
            "pixels. Lens distortion has NOT been corrected. On a wide-angle lens this limits "
            "accuracy, especially toward the edges of the frame."
        )

    # 3. Robust estimate. Fitted floor -> image so the RANSAC threshold really is
    # in pixels, matching what the installer sets.
    H_floor_to_image, mask = cv2.findHomography(
        world_pts, work_pts, method=cv2.RANSAC,
        ransacReprojThreshold=ransac_threshold_px, maxIters=5000, confidence=0.999,
    )
    if H_floor_to_image is None or not np.all(np.isfinite(H_floor_to_image)):
        raise MappingError(
            "No mapping fits these points.",
            hint="Check that each point was clicked on the right feature, and that the measured "
                 "coordinates match the physical positions.",
        )

    inlier_mask = mask.ravel().astype(bool) if mask is not None else np.ones(len(fitting), bool)
    if int(inlier_mask.sum()) < MIN_POINTS:
        raise MappingError(
            f"Only {int(inlier_mask.sum())} of {len(fitting)} points agree with each other, below "
            f"the {MIN_POINTS} needed.",
            hint="Usually one or two points are mis-clicked or mis-measured. Re-check the points "
                 "and try again.",
        )

    # 4. Refit on the inliers alone, so rejected points pull nothing.
    refit, _ = cv2.findHomography(world_pts[inlier_mask], work_pts[inlier_mask], method=0)
    if refit is not None and np.all(np.isfinite(refit)):
        H_floor_to_image = refit
    else:
        warnings.append("Refitting on the accepted points failed; the robust estimate is used as-is.")

    # 5. Conditioning and invertibility.
    determinant = float(np.linalg.det(H_floor_to_image))
    if abs(determinant) < 1e-12:
        raise DegenerateConfiguration(
            "The calculated mapping cannot be inverted, which means the points do not describe a "
            "flat surface seen from one viewpoint."
        )
    condition_number = float(np.linalg.cond(H_floor_to_image))
    if not np.isfinite(condition_number) or condition_number > MAX_CONDITION_NUMBER:
        raise DegenerateConfiguration(
            f"The mapping is numerically unstable (condition number {condition_number:.1e}). Small "
            "clicking errors would turn into large position errors.",
        )
    if condition_number > MAX_CONDITION_NUMBER / 100:
        warnings.append(
            f"The mapping is poorly conditioned (condition number {condition_number:.1e}). Spread "
            "the points more widely across the floor."
        )

    H_image_to_floor = np.linalg.inv(H_floor_to_image)
    H_image_to_floor = H_image_to_floor / H_image_to_floor[2, 2]

    # 6. Residuals for every point, fitted and held out alike.
    per_point: list[dict] = []
    used_ids: list[int] = []
    rejected_ids: list[int] = []
    fit_errors: list[float] = []
    pixel_errors: list[float] = []

    for i, p in enumerate(fitting):
        predicted = apply_homography(H_image_to_floor, [work_pts[i]])[0]
        entry = _point_entry(p, predicted, "calibration")
        if bool(inlier_mask[i]):
            used_ids.append(p.reference_point_id)
            entry["used_in_fit"] = True
            if np.isfinite(entry["error_m"]):
                fit_errors.append(entry["error_m"])
            back = apply_homography(H_floor_to_image, [[p.world_x, p.world_y]])[0]
            if np.all(np.isfinite(back)):
                pixel_errors.append(float(np.linalg.norm(back - work_pts[i])))
        else:
            rejected_ids.append(p.reference_point_id)
            entry["used_in_fit"] = False
            entry["rejected"] = True
        per_point.append(entry)

    if rejected_ids:
        warnings.append(
            f"{len(rejected_ids)} point(s) disagreed with the rest and were left out of the "
            f"mapping: {', '.join(p['code'] for p in per_point if p.get('rejected'))}. Re-check "
            "those measurements and image clicks."
        )

    # Held-out points: projected through a mapping that never saw them.
    for p in validation:
        pixel = np.array([[p.pixel_u, p.pixel_v]], dtype=np.float64)
        if distortion_corrected and intrinsics is not None:
            pixel = intrinsics.undistort_points(pixel)
        predicted = apply_homography(H_image_to_floor, pixel)[0]
        entry = _point_entry(p, predicted, "validation")
        entry["used_in_fit"] = False
        per_point.append(entry)

    coverage_polygon = convex_hull(world_pts[inlier_mask])
    coverage_area = polygon_area(coverage_polygon)

    if len(used_ids) >= GOOD_POINTS:
        notes.append(f"{len(used_ids)} points were used, which is a good basis for this area.")

    return MappingResult(
        H_image_to_floor=H_image_to_floor,
        H_floor_to_image=H_floor_to_image,
        pixel_convention=pixel_convention,
        distortion_corrected=distortion_corrected,
        image_width=int(image_width),
        image_height=int(image_height),
        plane_z=float(plane_z),
        used_point_ids=used_ids,
        rejected_point_ids=rejected_ids,
        per_point=per_point,
        mean_error_m=float(np.mean(fit_errors)) if fit_errors else float("nan"),
        median_error_m=float(np.median(fit_errors)) if fit_errors else float("nan"),
        max_error_m=float(np.max(fit_errors)) if fit_errors else float("nan"),
        mean_reprojection_px=float(np.mean(pixel_errors)) if pixel_errors else float("nan"),
        coverage_polygon=coverage_polygon,
        coverage_area_m2=coverage_area,
        condition_number=condition_number,
        ransac_threshold_px=float(ransac_threshold_px),
        warnings=warnings,
        notes=notes,
    )


def _point_entry(p: PointInput, predicted: np.ndarray, role: str) -> dict:
    finite = bool(np.all(np.isfinite(predicted)))
    error = (float(np.linalg.norm(predicted - np.array([p.world_x, p.world_y])))
             if finite else float("nan"))
    return {
        "reference_point_id": p.reference_point_id,
        "code": p.code,
        "name": p.name,
        "role": role,
        "measured": [p.world_x, p.world_y],
        "predicted": [round(float(predicted[0]), 4), round(float(predicted[1]), 4)] if finite else None,
        "error_m": round(error, 4) if finite else None,
        "error_cm": round(error * 100, 1) if finite else None,
        "pixel": [p.pixel_u, p.pixel_v],
    }


# -- held-out validation -------------------------------------------------

def evaluate_validation(result: MappingResult, threshold_m: float) -> dict:
    """Score the mapping against points that took no part in the fit."""
    held_out = [e for e in result.per_point
                if e["role"] == "validation" and e["error_m"] is not None]
    errors = [e["error_m"] for e in held_out]

    messages: list[str] = []
    passed = True

    if not errors:
        passed = False
        messages.append(
            "No validation points could be projected, so accuracy has not been measured "
            "independently. A mapping is only marked as checked when separately measured points "
            "confirm it."
        )
        summary = {"mean_m": None, "median_m": None, "max_m": None}
    else:
        mean_e = float(np.mean(errors))
        median_e = float(np.median(errors))
        max_e = float(np.max(errors))
        summary = {"mean_m": round(mean_e, 4), "median_m": round(median_e, 4),
                   "max_m": round(max_e, 4)}
        if mean_e > threshold_m:
            passed = False
            messages.append(
                f"Average error {mean_e * 100:.0f} cm is above the {threshold_m * 100:.0f} cm "
                "limit you set."
            )
        if max_e > threshold_m * 2:
            passed = False
            messages.append(
                f"The worst point is out by {max_e * 100:.0f} cm, more than twice the limit. "
                "Check that point's measurement and where it was clicked."
            )
        if len(errors) < 2:
            messages.append(
                "Only one validation point was usable. Two or three, spread out, make the check "
                "meaningful."
            )

    # Spread of the validation points, which bounds what the check speaks for.
    spread = None
    if len(held_out) >= 2:
        pts = np.array([e["measured"] for e in held_out], dtype=float)
        hull = convex_hull(pts)
        spread = {
            "count": len(held_out),
            "extent_m": [round(float(pts[:, 0].max() - pts[:, 0].min()), 2),
                         round(float(pts[:, 1].max() - pts[:, 1].min()), 2)],
            "area_m2": round(polygon_area(hull), 2),
        }
        if spread["area_m2"] < result.coverage_area_m2 * 0.1:
            messages.append(
                "The validation points sit close together, so this check only speaks for that "
                "small part of the area."
            )

    if passed and errors:
        messages.append(
            f"Within the {threshold_m * 100:.0f} cm limit across the area the validation points "
            "cover. That area is the extent of what has been checked."
        )

    return {
        "passed": passed,
        "threshold_m": threshold_m,
        "held_out": held_out,
        "held_out_count": len(errors),
        "summary": summary,
        "distribution": spread,
        "messages": messages,
        "interpretation": (
            "These points were measured separately and took no part in calculating the mapping, "
            "so this is an independent check. It applies to the area they cover, not the whole "
            "camera view."
        ),
    }
