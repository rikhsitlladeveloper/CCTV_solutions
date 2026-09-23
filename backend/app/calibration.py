"""Calibration solvers: floor homography, camera pose, and checkerboard intrinsics.

Three deliberately separate capabilities, never conflated:

* :func:`solve_floor_homography` maps image pixels to floor X/Y. It needs no
  intrinsics and recovers no camera pose. A homography alone does not contain
  enough information to state where the camera is, so none is invented.
* :func:`solve_camera_pose` recovers a full 6-DoF pose, and requires intrinsics.
* :func:`calibrate_intrinsics_from_checkerboard` estimates K and distortion.

Every result carries the image geometry it was computed at and the pixel
convention it operates on, so downstream projection cannot apply it wrongly.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

import cv2
import numpy as np

from .geometry import GeometryError, Pose, orthonormalise, require_finite
from .intrinsics import Intrinsics, IntrinsicsError

# Which pixels a homography or pose was fitted against.
PIXELS_RAW = "raw"                  # pixels straight from the stream, distortion included
PIXELS_UNDISTORTED = "undistorted"  # pixels after undistortion with the stored intrinsics

MIN_HOMOGRAPHY_POINTS = 4
RECOMMENDED_HOMOGRAPHY_POINTS = 6
MIN_PNP_POINTS = 4
RECOMMENDED_PNP_POINTS = 6


class CalibrationError(ValueError):
    pass


class DegenerateConfiguration(CalibrationError):
    """The reference points cannot constrain the requested solution."""


# -- point-set diagnostics ----------------------------------------------

def _collinearity(points_2d: np.ndarray) -> float:
    """0 = perfectly collinear, 1 = well spread.

    The ratio of the smaller to the larger singular value of the centred point
    cloud, which is scale-free.
    """
    p = points_2d - points_2d.mean(axis=0)
    if p.shape[0] < 3:
        return 0.0
    sv = np.linalg.svd(p, compute_uv=False)
    if sv[0] < 1e-12:
        return 0.0
    return float(sv[1] / sv[0])


def _spread_summary(points_2d: np.ndarray) -> dict:
    mins = points_2d.min(axis=0)
    maxs = points_2d.max(axis=0)
    return {
        "count": int(points_2d.shape[0]),
        "bounds": {"min": mins.tolist(), "max": maxs.tolist()},
        "extent": (maxs - mins).tolist(),
        "collinearity_ratio": round(_collinearity(points_2d), 4),
    }


def _quadrant_coverage(points_2d: np.ndarray) -> int:
    """How many quadrants of the point cloud's own bounding box are occupied."""
    centre = (points_2d.min(axis=0) + points_2d.max(axis=0)) / 2.0
    quadrants = set()
    for x, y in points_2d:
        quadrants.add((x >= centre[0], y >= centre[1]))
    return len(quadrants)


# -- results -------------------------------------------------------------

@dataclass
class HomographyResult:
    H_image_to_floor: np.ndarray
    H_floor_to_image: np.ndarray
    pixel_convention: str
    plane_z: float
    image_width: int
    image_height: int
    inlier_indices: list[int]
    outlier_indices: list[int]
    reprojection_errors_px: list[float]
    mean_reprojection_error_px: float
    max_reprojection_error_px: float
    floor_residuals_m: list[float]
    mean_floor_residual_m: float
    point_spread: dict
    warnings: list[str] = field(default_factory=list)
    distortion_corrected: bool = False
    method: str = "homography_ransac"

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "H_image_to_floor": self.H_image_to_floor.tolist(),
            "H_floor_to_image": self.H_floor_to_image.tolist(),
            "pixel_convention": self.pixel_convention,
            "distortion_corrected": self.distortion_corrected,
            "plane_z": self.plane_z,
            "image_width": self.image_width,
            "image_height": self.image_height,
            "inlier_indices": self.inlier_indices,
            "outlier_indices": self.outlier_indices,
            "reprojection_errors_px": [round(e, 4) for e in self.reprojection_errors_px],
            "mean_reprojection_error_px": round(self.mean_reprojection_error_px, 4),
            "max_reprojection_error_px": round(self.max_reprojection_error_px, 4),
            "mean_floor_residual_m": round(self.mean_floor_residual_m, 5),
            "point_spread": self.point_spread,
            "warnings": self.warnings,
        }


@dataclass
class PoseResult:
    pose: Pose
    pixel_convention: str
    image_width: int
    image_height: int
    inlier_indices: list[int]
    outlier_indices: list[int]
    reprojection_errors_px: list[float]
    mean_reprojection_error_px: float
    max_reprojection_error_px: float
    point_spread: dict
    solver: str
    planar_points: bool
    ambiguity_note: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        roll, pitch, yaw = self.pose.rpy_degrees
        return {
            "method": "solvepnp",
            "solver": self.solver,
            "position_m": {"x": float(self.pose.position[0]),
                           "y": float(self.pose.position[1]),
                           "z": float(self.pose.position[2])},
            "rpy_deg": {"roll": roll, "pitch": pitch, "yaw": yaw},
            "quaternion_wxyz": self.pose.quaternion.tolist(),
            "T_world_camera": self.pose.T_world_camera.tolist(),
            "map_heading_deg": self.pose.map_heading_deg,
            "pixel_convention": self.pixel_convention,
            "image_width": self.image_width,
            "image_height": self.image_height,
            "inlier_indices": self.inlier_indices,
            "outlier_indices": self.outlier_indices,
            "reprojection_errors_px": [round(e, 4) for e in self.reprojection_errors_px],
            "mean_reprojection_error_px": round(self.mean_reprojection_error_px, 4),
            "max_reprojection_error_px": round(self.max_reprojection_error_px, 4),
            "point_spread": self.point_spread,
            "planar_points": self.planar_points,
            "ambiguity_note": self.ambiguity_note,
            "warnings": self.warnings,
        }


# -- floor homography ----------------------------------------------------

def solve_floor_homography(image_points, floor_points, image_width: int, image_height: int,
                           intrinsics: Intrinsics | None = None,
                           plane_z: float = 0.0,
                           ransac_threshold_px: float = 3.0,
                           holdout_indices: list[int] | None = None) -> HomographyResult:
    """Fit an image-to-floor homography with robust estimation.

    ``floor_points`` are measured world X/Y on the plane ``Z = plane_z``.

    If ``intrinsics`` are supplied the image points are undistorted first and the
    result is recorded as operating on *undistorted* pixels. Without them the fit
    runs on raw pixels: a usable approximation for a modest-distortion lens over a
    limited area, but distortion is **not** corrected, and the result says so.
    """
    img = require_finite(image_points, "Image points").reshape(-1, 2).astype(np.float64)
    flr = require_finite(floor_points, "Floor points").reshape(-1, 2).astype(np.float64)

    if img.shape[0] != flr.shape[0]:
        raise CalibrationError(
            f"Got {img.shape[0]} image points and {flr.shape[0]} floor points; they must pair up."
        )

    holdout = sorted(set(holdout_indices or []))
    for i in holdout:
        if not 0 <= i < img.shape[0]:
            raise CalibrationError(f"Held-out index {i} is outside the point list.")
    fit_idx = [i for i in range(img.shape[0]) if i not in set(holdout)]

    if len(fit_idx) < MIN_HOMOGRAPHY_POINTS:
        raise CalibrationError(
            f"A homography needs at least {MIN_HOMOGRAPHY_POINTS} correspondences to fit "
            f"(after holding out {len(holdout)}); got {len(fit_idx)}."
        )

    warnings: list[str] = []
    if len(fit_idx) < RECOMMENDED_HOMOGRAPHY_POINTS:
        warnings.append(
            f"Only {len(fit_idx)} points were used for fitting. At least "
            f"{RECOMMENDED_HOMOGRAPHY_POINTS}, spread across the working area, give a more "
            "trustworthy fit and leave points over for validation."
        )

    # Degeneracy: collinear points in either frame cannot define a homography.
    for label, pts in (("image", img[fit_idx]), ("floor", flr[fit_idx])):
        ratio = _collinearity(pts)
        if ratio < 0.02:
            raise DegenerateConfiguration(
                f"The {label} points are effectively collinear (spread ratio {ratio:.4f}). "
                "A homography needs at least four points in a general position, not along a line."
            )
        if ratio < 0.12:
            warnings.append(
                f"The {label} points are nearly collinear (spread ratio {ratio:.3f}); "
                "the fit will be poorly conditioned away from that line."
            )

    if _quadrant_coverage(flr[fit_idx]) < 3:
        warnings.append(
            "The floor points cluster in part of the area. Spreading them across the region "
            "you care about makes the fit valid over more of it."
        )

    pixel_convention = PIXELS_RAW
    distortion_corrected = False
    work_img = img.copy()
    if intrinsics is not None:
        intrinsics.require_matches(image_width, image_height)
        if intrinsics.has_distortion:
            work_img = intrinsics.undistort_points(img)
            distortion_corrected = True
        pixel_convention = PIXELS_UNDISTORTED
    else:
        warnings.append(
            "Fitted on raw pixels with no distortion model. Lens distortion is NOT corrected, so "
            "accuracy degrades toward the image edges. Treat this as an explicitly approximate "
            "floor mapping valid only near the measured points."
        )

    # Fit floor -> image, so RANSAC's reprojection threshold is genuinely in pixels.
    # (findHomography measures error in the destination space; fitting image -> floor
    # would silently reinterpret a pixel threshold as a distance in metres.)
    H_floor_to_image, mask = cv2.findHomography(
        flr[fit_idx], work_img[fit_idx],
        method=cv2.RANSAC, ransacReprojThreshold=ransac_threshold_px,
        maxIters=5000, confidence=0.999,
    )
    if H_floor_to_image is None or not np.all(np.isfinite(H_floor_to_image)):
        raise CalibrationError(
            "No homography could be fitted to these correspondences. Check that the image points "
            "and floor points are listed in the same order."
        )
    if abs(float(np.linalg.det(H_floor_to_image))) < 1e-12:
        raise DegenerateConfiguration("The fitted homography is singular and cannot be inverted.")
    H = np.linalg.inv(H_floor_to_image)
    H = H / H[2, 2] if abs(H[2, 2]) > 1e-12 else H

    mask = (mask.ravel().astype(bool) if mask is not None
            else np.ones(len(fit_idx), dtype=bool))
    inliers = [fit_idx[i] for i, keep in enumerate(mask) if keep]
    outliers = [fit_idx[i] for i, keep in enumerate(mask) if not keep]
    if len(inliers) < MIN_HOMOGRAPHY_POINTS:
        raise CalibrationError(
            f"Robust fitting kept only {len(inliers)} inliers, below the {MIN_HOMOGRAPHY_POINTS} "
            "needed. The correspondences are probably mismatched or mismeasured."
        )
    if outliers:
        warnings.append(
            f"{len(outliers)} correspondence(s) were rejected as outliers: indices {outliers}. "
            "Re-check those measurements or image clicks."
        )

    H_inv = H_floor_to_image

    # Residuals, reported in both directions and units.
    floor_pred = _apply_homography(H, work_img)
    floor_residuals = np.linalg.norm(floor_pred - flr, axis=1)
    image_pred = _apply_homography(H_inv, flr)
    pixel_residuals = np.linalg.norm(image_pred - work_img, axis=1)

    return HomographyResult(
        H_image_to_floor=H,
        H_floor_to_image=H_inv,
        pixel_convention=pixel_convention,
        plane_z=float(plane_z),
        image_width=int(image_width),
        image_height=int(image_height),
        inlier_indices=inliers,
        outlier_indices=outliers,
        reprojection_errors_px=[float(e) for e in pixel_residuals],
        mean_reprojection_error_px=float(np.nanmean(pixel_residuals[inliers])) if inliers else float("nan"),
        max_reprojection_error_px=float(np.nanmax(pixel_residuals[inliers])) if inliers else float("nan"),
        floor_residuals_m=[float(e) for e in floor_residuals],
        mean_floor_residual_m=float(np.nanmean(floor_residuals[inliers])) if inliers else float("nan"),
        point_spread={"image": _spread_summary(img[fit_idx]), "floor": _spread_summary(flr[fit_idx])},
        warnings=warnings,
        distortion_corrected=distortion_corrected,
    )


def _apply_homography(H: np.ndarray, points: np.ndarray) -> np.ndarray:
    pts = np.asarray(points, dtype=float).reshape(-1, 2)
    homogeneous = np.column_stack([pts, np.ones(len(pts))])
    out = (H @ homogeneous.T).T
    w = out[:, 2:3]
    with np.errstate(divide="ignore", invalid="ignore"):
        result = out[:, :2] / w
    result[np.abs(w.ravel()) < 1e-12] = np.nan
    return result


def homography_horizon_line(H: np.ndarray) -> np.ndarray:
    """The image line where the homography's scale factor vanishes.

    Pixels on this line have no floor position: it is the projection of the
    plane's horizon. Returned as ``(a, b, c)`` for ``a*u + b*v + c = 0``.
    """
    return np.asarray(H, dtype=float)[2, :].copy()


def homography_image_to_floor(H: np.ndarray, u: float, v: float) -> np.ndarray:
    """One pixel -> floor X/Y. Raises when the point maps to the horizon."""
    out = _apply_homography(H, [[u, v]])[0]
    if not np.all(np.isfinite(out)):
        raise CalibrationError(
            "This pixel maps to the horizon line of the homography, where floor position is "
            "undefined. Pick a point lower in the image, on the floor."
        )
    return out


# -- camera pose ---------------------------------------------------------

def solve_camera_pose(world_points, image_points, intrinsics: Intrinsics,
                      image_width: int, image_height: int,
                      use_ransac: bool = True,
                      reprojection_threshold_px: float = 4.0,
                      holdout_indices: list[int] | None = None) -> PoseResult:
    """Recover ``T_world_camera`` from measured world XYZ and image pixels.

    Picks a solver appropriate to the point geometry, refines the result, and
    rejects solutions whose geometry is invalid (points behind the camera).
    """
    world = require_finite(world_points, "World points").reshape(-1, 3).astype(np.float64)
    img = require_finite(image_points, "Image points").reshape(-1, 2).astype(np.float64)
    if world.shape[0] != img.shape[0]:
        raise CalibrationError(
            f"Got {world.shape[0]} world points and {img.shape[0]} image points; they must pair up."
        )

    intrinsics.require_matches(image_width, image_height)

    holdout = set(holdout_indices or [])
    fit_idx = [i for i in range(world.shape[0]) if i not in holdout]
    if len(fit_idx) < MIN_PNP_POINTS:
        raise CalibrationError(
            f"Pose estimation needs at least {MIN_PNP_POINTS} correspondences to fit "
            f"(after holding out {len(holdout)}); got {len(fit_idx)}."
        )

    warnings: list[str] = []
    if len(fit_idx) < RECOMMENDED_PNP_POINTS:
        warnings.append(
            f"Only {len(fit_idx)} points were used. At least {RECOMMENDED_PNP_POINTS}, spread in "
            "depth as well as across the image, make the pose better conditioned."
        )

    w_fit = world[fit_idx]
    i_fit = img[fit_idx]

    # Degeneracy checks.
    if _collinearity(i_fit) < 0.02:
        raise DegenerateConfiguration(
            "The image points are effectively collinear, which cannot constrain a pose."
        )
    centred = w_fit - w_fit.mean(axis=0)
    sv = np.linalg.svd(centred, compute_uv=False)
    if sv[0] < 1e-9:
        raise DegenerateConfiguration("All world points coincide.")
    if sv[1] / sv[0] < 0.02:
        raise DegenerateConfiguration(
            "The world points lie along a single line. Pose recovery needs points spread over "
            "at least a plane."
        )

    planar = bool(sv[2] / sv[0] < 1e-3)
    ambiguity_note = None
    if planar:
        ambiguity_note = (
            "All reference points lie on one plane. Planar pose recovery has a well-known "
            "two-solution ambiguity: a mirrored pose can reproject almost identically. The "
            "solution was chosen by reprojection error and by requiring every point to sit in "
            "front of the camera, but adding points off the plane (different heights) is the only "
            "way to remove the ambiguity properly."
        )
        warnings.append(ambiguity_note)
        solver_flag = cv2.SOLVEPNP_IPPE if len(fit_idx) >= 4 else cv2.SOLVEPNP_ITERATIVE
        solver_name = "SOLVEPNP_IPPE"
    else:
        solver_flag = cv2.SOLVEPNP_SQPNP
        solver_name = "SOLVEPNP_SQPNP"

    K = intrinsics.camera_matrix.astype(np.float64)
    dist = intrinsics._dist_for_cv()

    inliers_idx: list[int]
    if use_ransac and len(fit_idx) >= 5:
        ok, rvec, tvec, inl = cv2.solvePnPRansac(
            w_fit, i_fit, K, dist,
            reprojectionError=reprojection_threshold_px,
            iterationsCount=2000, confidence=0.999,
            flags=cv2.SOLVEPNP_ITERATIVE if planar else cv2.SOLVEPNP_EPNP,
        )
        if not ok or rvec is None:
            raise CalibrationError(
                "Robust pose estimation failed. Check that world points and image points are "
                "listed in the same order and that the intrinsics match this stream."
            )
        keep = sorted(int(i) for i in inl.ravel()) if inl is not None else list(range(len(fit_idx)))
        solver_name = f"solvePnPRansac({'ITERATIVE' if planar else 'EPNP'})"
    else:
        ok, rvec, tvec = cv2.solvePnP(w_fit, i_fit, K, dist, flags=solver_flag)
        if not ok:
            raise CalibrationError("Pose estimation did not converge on these correspondences.")
        keep = list(range(len(fit_idx)))

    if len(keep) < MIN_PNP_POINTS:
        raise CalibrationError(
            f"Only {len(keep)} points survived robust fitting, below the {MIN_PNP_POINTS} needed."
        )

    # Refine on the inliers with iterative (Levenberg-Marquardt) minimisation.
    refine_world = w_fit[keep]
    refine_img = i_fit[keep]
    try:
        rvec, tvec = cv2.solvePnPRefineLM(refine_world, refine_img, K, dist, rvec, tvec)
        solver_name += " + refineLM"
    except cv2.error:
        warnings.append("Iterative refinement failed; reporting the unrefined estimate.")

    if not (np.all(np.isfinite(rvec)) and np.all(np.isfinite(tvec))):
        raise CalibrationError("Pose estimation produced non-finite values.")

    pose = Pose.from_cv_world_to_camera(rvec, tvec)

    # Geometry validation: every fitted point must be in front of the camera.
    cam_pts = pose.world_to_camera_points(w_fit[keep])
    behind = int(np.sum(cam_pts[:, 2] <= 0))
    if behind:
        raise CalibrationError(
            f"The recovered pose puts {behind} of {len(keep)} reference point(s) behind the "
            "camera, which is geometrically impossible. The correspondences are likely mismatched, "
            "or the intrinsics do not belong to this stream."
        )

    all_cam = pose.world_to_camera_points(world)
    pixels, in_front = intrinsics.project_camera_points(all_cam)
    errors = np.full(world.shape[0], np.nan)
    errors[in_front] = np.linalg.norm(pixels[in_front] - img[in_front], axis=1)

    fit_errors = errors[[fit_idx[i] for i in keep]]
    inlier_global = [fit_idx[i] for i in keep]
    outlier_global = [i for i in fit_idx if i not in set(inlier_global)]
    if outlier_global:
        warnings.append(
            f"{len(outlier_global)} correspondence(s) rejected as outliers: indices {outlier_global}."
        )

    if pose.position[2] < 0:
        warnings.append(
            f"The recovered camera height is {pose.position[2]:.2f} m, below the floor plane. "
            "That usually means the world points were entered with a different axis convention."
        )

    return PoseResult(
        pose=pose,
        pixel_convention=PIXELS_RAW,     # solvePnP consumes distorted pixels plus dist coeffs
        image_width=int(image_width),
        image_height=int(image_height),
        inlier_indices=inlier_global,
        outlier_indices=outlier_global,
        reprojection_errors_px=[float(e) if np.isfinite(e) else float("nan") for e in errors],
        mean_reprojection_error_px=float(np.nanmean(fit_errors)),
        max_reprojection_error_px=float(np.nanmax(fit_errors)),
        point_spread={"world": _spread_summary(w_fit[:, :2]), "image": _spread_summary(i_fit)},
        solver=solver_name,
        planar_points=planar,
        ambiguity_note=ambiguity_note,
        warnings=warnings,
    )


# -- checkerboard intrinsics --------------------------------------------

@dataclass
class CheckerboardCapture:
    name: str
    corners: np.ndarray          # (N, 1, 2) refined image corners
    width: int
    height: int


def detect_checkerboard(image_bgr: np.ndarray, inner_cols: int, inner_rows: int,
                        name: str = "frame") -> CheckerboardCapture | None:
    """Find and sub-pixel refine checkerboard inner corners in one image."""
    if image_bgr is None or image_bgr.size == 0:
        raise CalibrationError(f"'{name}' could not be decoded as an image.")
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY) if image_bgr.ndim == 3 else image_bgr
    pattern = (int(inner_cols), int(inner_rows))

    flags = cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE
    found, corners = cv2.findChessboardCorners(gray, pattern, flags=flags)
    if not found:
        return None

    corners = cv2.cornerSubPix(
        gray, corners.astype(np.float32), (11, 11), (-1, -1),
        (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, 0.001),
    )
    return CheckerboardCapture(name=name, corners=corners,
                               width=gray.shape[1], height=gray.shape[0])


def calibrate_intrinsics_from_checkerboard(captures: list[CheckerboardCapture],
                                           inner_cols: int, inner_rows: int,
                                           square_size_m: float) -> dict:
    """Estimate K and pinhole distortion from detected checkerboard views."""
    if square_size_m <= 0:
        raise CalibrationError("The checkerboard square size must be positive, in metres.")
    if len(captures) < 3:
        raise CalibrationError(
            f"Intrinsic calibration needs at least 3 usable views; got {len(captures)}. "
            "Ten or more, at varied angles and distances, give a far better result."
        )

    sizes = {(c.width, c.height) for c in captures}
    if len(sizes) > 1:
        raise CalibrationError(
            f"All calibration images must share one resolution; found {sorted(sizes)}. "
            "Intrinsics are only valid for a single image geometry."
        )
    width, height = sizes.pop()

    objp = np.zeros((inner_rows * inner_cols, 3), np.float32)
    objp[:, :2] = np.mgrid[0:inner_cols, 0:inner_rows].T.reshape(-1, 2)
    objp *= float(square_size_m)

    object_points = [objp for _ in captures]
    image_points = [c.corners.astype(np.float32) for c in captures]

    rms, K, dist, rvecs, tvecs = cv2.calibrateCamera(
        object_points, image_points, (width, height), None, None,
    )
    if not np.all(np.isfinite(K)) or not np.all(np.isfinite(dist)):
        raise CalibrationError("Calibration produced non-finite parameters.")

    per_view = []
    for i, cap in enumerate(captures):
        projected, _ = cv2.projectPoints(object_points[i], rvecs[i], tvecs[i], K, dist)
        err = float(np.sqrt(np.mean(np.sum(
            (projected.reshape(-1, 2) - image_points[i].reshape(-1, 2)) ** 2, axis=1))))
        per_view.append({"name": cap.name, "rms_reprojection_error_px": round(err, 4)})

    diversity = _pose_diversity(rvecs, tvecs)
    warnings = list(diversity["warnings"])
    if len(captures) < 10:
        warnings.append(
            f"Only {len(captures)} views were used. Ten or more is the usual minimum for a "
            "calibration you would rely on."
        )
    if rms > 1.0:
        warnings.append(
            f"The overall RMS reprojection error is {rms:.2f} px, which is high. Check for blurred "
            "images, a non-flat board, or a wrong square size."
        )

    intrinsics = Intrinsics.from_payload(
        camera_matrix=K, distortion=dist.ravel(), width=width, height=height,
        model="pinhole", source="checkerboard",
        notes=f"Estimated from {len(captures)} checkerboard views, {inner_cols}x{inner_rows} inner "
              f"corners at {square_size_m} m.",
        metadata={
            "calibrated_at": datetime.now(timezone.utc).isoformat(),
            "view_count": len(captures),
            "square_size_m": square_size_m,
            "inner_cols": inner_cols,
            "inner_rows": inner_rows,
        },
    )

    return {
        "intrinsics": intrinsics,
        "rms_reprojection_error_px": round(float(rms), 4),
        "per_view": per_view,
        "pose_diversity": diversity,
        "warnings": warnings,
    }


def _pose_diversity(rvecs, tvecs) -> dict:
    """Flag insufficient variety in board orientations and distances."""
    angles = []
    for rvec in rvecs:
        R, _ = cv2.Rodrigues(rvec)
        # Tilt of the board normal away from the optical axis.
        normal = R @ np.array([0.0, 0.0, 1.0])
        angles.append(math.degrees(math.acos(min(1.0, abs(float(normal[2]))))))
    distances = [float(np.linalg.norm(t)) for t in tvecs]

    angle_spread = float(np.max(angles) - np.min(angles)) if angles else 0.0
    distance_spread = float(np.max(distances) - np.min(distances)) if distances else 0.0

    warnings = []
    if angle_spread < 20.0:
        warnings.append(
            f"The board was held at nearly one orientation (tilt range {angle_spread:.0f} deg). "
            "Distortion and focal length are poorly separated without varied tilt; aim for a "
            "30-60 deg range."
        )
    if distance_spread < 0.3:
        warnings.append(
            f"All views are at nearly the same distance (range {distance_spread:.2f} m). "
            "Vary the distance to constrain the focal length."
        )
    if max(angles, default=0.0) < 10.0:
        warnings.append("No view shows a meaningfully tilted board; add oblique views.")

    return {
        "tilt_angles_deg": [round(a, 1) for a in angles],
        "tilt_range_deg": round(angle_spread, 1),
        "distances_m": [round(d, 3) for d in distances],
        "distance_range_m": round(distance_spread, 3),
        "sufficient": not warnings,
        "warnings": warnings,
    }
