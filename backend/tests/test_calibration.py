"""Solver behaviour against synthetic scenes with known ground truth.

Synthetic recovery proves the mathematics and the conventions are self-consistent.
It says nothing about accuracy on real cameras, which depends on measurement
quality in the field.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from app.calibration import (
    PIXELS_RAW, PIXELS_UNDISTORTED, CalibrationError, DegenerateConfiguration,
    calibrate_intrinsics_from_checkerboard, detect_checkerboard,
    homography_image_to_floor, solve_camera_pose, solve_floor_homography,
)
from app.geometry import Pose, intersect_ray_with_plane, pose_from_look_at, quaternion_angle_between
from app.intrinsics import GeometryMismatch, Intrinsics, IntrinsicsError, intrinsics_from_fov

WIDTH, HEIGHT = 1920, 1080


def make_intrinsics(distortion=None) -> Intrinsics:
    K = np.array([[1400.0, 0.0, 960.0],
                  [0.0, 1400.0, 540.0],
                  [0.0, 0.0, 1.0]])
    return Intrinsics.from_payload(K, distortion if distortion is not None else np.zeros(5),
                                   WIDTH, HEIGHT)


def make_scene_pose() -> Pose:
    """A camera 4.5 m up, looking down at the floor from a corner."""
    return pose_from_look_at([2.0, -3.0, 4.5], [6.0, 6.0, 0.0])


def floor_grid(nx=4, ny=4, x0=2.0, x1=10.0, y0=1.0, y1=9.0) -> np.ndarray:
    xs = np.linspace(x0, x1, nx)
    ys = np.linspace(y0, y1, ny)
    return np.array([[x, y, 0.0] for x in xs for y in ys])


def project(pose: Pose, intr: Intrinsics, world: np.ndarray) -> np.ndarray:
    pixels, in_front = intr.project_camera_points(pose.world_to_camera_points(world))
    assert np.all(in_front), "synthetic setup should keep every point in front of the camera"
    return pixels


# -- full pose recovery --------------------------------------------------

def test_pose_recovery_from_synthetic_projection_is_exact():
    truth = make_scene_pose()
    intr = make_intrinsics()
    world = np.vstack([floor_grid(), [[4.0, 5.0, 1.8], [8.0, 3.0, 2.4], [5.0, 7.0, 0.9]]])
    pixels = project(truth, intr, world)

    result = solve_camera_pose(world, pixels, intr, WIDTH, HEIGHT)

    assert np.allclose(result.pose.position, truth.position, atol=1e-4)
    assert quaternion_angle_between(result.pose.quaternion, truth.quaternion) < 1e-3
    assert result.mean_reprojection_error_px < 1e-3
    assert not result.planar_points
    assert result.outlier_indices == []
    assert result.pixel_convention == PIXELS_RAW


def test_pose_recovery_with_lens_distortion():
    """Distortion must be modelled, not ignored: solvePnP takes distorted pixels."""
    truth = make_scene_pose()
    intr = make_intrinsics(distortion=np.array([-0.32, 0.12, 0.001, -0.0008, -0.02]))
    world = np.vstack([floor_grid(5, 5), [[5.0, 5.0, 2.0], [7.0, 2.0, 1.2]]])
    pixels = project(truth, intr, world)

    result = solve_camera_pose(world, pixels, intr, WIDTH, HEIGHT)
    assert np.allclose(result.pose.position, truth.position, atol=1e-3)
    assert result.mean_reprojection_error_px < 1e-2

    # Ignoring distortion on the same pixels must give a visibly worse pose.
    naive = solve_camera_pose(world, pixels, make_intrinsics(), WIDTH, HEIGHT)
    assert np.linalg.norm(naive.pose.position - truth.position) > 0.05


def test_pose_recovery_survives_gross_outliers():
    truth = make_scene_pose()
    intr = make_intrinsics()
    world = np.vstack([floor_grid(4, 4), [[5.0, 5.0, 2.0], [3.0, 8.0, 1.1]]])
    pixels = project(truth, intr, world)
    pixels[3] += [220.0, -180.0]          # mis-clicked point
    pixels[11] += [-260.0, 140.0]

    result = solve_camera_pose(world, pixels, intr, WIDTH, HEIGHT)
    assert set(result.outlier_indices) >= {3, 11}
    assert np.allclose(result.pose.position, truth.position, atol=1e-2)


def test_planar_points_are_flagged_as_ambiguous():
    truth = make_scene_pose()
    intr = make_intrinsics()
    world = floor_grid(3, 3)              # every point on Z = 0
    pixels = project(truth, intr, world)

    result = solve_camera_pose(world, pixels, intr, WIDTH, HEIGHT)
    assert result.planar_points
    assert result.ambiguity_note and "ambiguity" in result.ambiguity_note
    assert any("ambiguity" in w for w in result.warnings)


def test_collinear_world_points_are_rejected():
    intr = make_intrinsics()
    truth = make_scene_pose()
    world = np.array([[x, 2.0 * x + 1.0, 0.0] for x in np.linspace(2, 9, 6)])
    pixels = project(truth, intr, world)

    with pytest.raises(DegenerateConfiguration, match="single line|collinear"):
        solve_camera_pose(world, pixels, intr, WIDTH, HEIGHT)


def test_too_few_points_are_rejected():
    intr = make_intrinsics()
    with pytest.raises(CalibrationError, match="at least 4"):
        solve_camera_pose(np.zeros((3, 3)), np.zeros((3, 2)), intr, WIDTH, HEIGHT)


def test_mismatched_point_counts_are_rejected():
    intr = make_intrinsics()
    with pytest.raises(CalibrationError, match="pair up"):
        solve_camera_pose(floor_grid(), np.zeros((4, 2)), intr, WIDTH, HEIGHT)


def test_intrinsics_from_another_resolution_are_refused():
    """A 1920x1080 calibration must not be silently reused on a 1280x720 substream."""
    truth = make_scene_pose()
    intr = make_intrinsics()
    world = np.vstack([floor_grid(), [[5.0, 5.0, 2.0]]])
    pixels = project(truth, intr, world)

    with pytest.raises(GeometryMismatch, match="calibrated for 1920x1080"):
        solve_camera_pose(world, pixels, intr, 1280, 720)


ASYMMETRIC_WORLD = np.array([
    [2.0, 1.0, 0.0], [9.0, 2.0, 0.0], [4.0, 8.0, 0.0], [7.0, 6.0, 0.0],
    [3.0, 4.0, 1.9], [8.0, 7.0, 2.6], [5.0, 2.0, 1.1], [6.0, 9.0, 0.4],
])


@pytest.mark.parametrize("shift", [1, 3, 5])
def test_scrambled_correspondences_fail_loudly(shift):
    """Mismatched pairings must raise, not return a confident wrong pose.

    A symmetric grid is deliberately avoided here: reversing a symmetric layout is
    a genuine symmetry and yields a legitimate pose, which would make this test
    assert the wrong thing.
    """
    truth = make_scene_pose()
    intr = make_intrinsics()
    pixels = project(truth, intr, ASYMMETRIC_WORLD)
    scrambled = pixels[np.roll(np.arange(len(pixels)), shift)]

    with pytest.raises(CalibrationError):
        solve_camera_pose(ASYMMETRIC_WORLD, scrambled, intr, WIDTH, HEIGHT)


def test_points_behind_the_camera_never_yield_pixels():
    """The guard that rejects behind-camera geometry rests on this projection rule."""
    intr = make_intrinsics()
    points = np.array([[0.0, 0.0, 5.0], [0.0, 0.0, -5.0], [0.0, 0.0, 0.0]])
    pixels, in_front = intr.project_camera_points(points)

    assert in_front.tolist() == [True, False, False]
    assert np.all(np.isfinite(pixels[0]))
    assert np.all(np.isnan(pixels[1:]))


def test_a_valid_pose_keeps_every_reference_point_in_front():
    truth = make_scene_pose()
    intr = make_intrinsics()
    pixels = project(truth, intr, ASYMMETRIC_WORLD)
    result = solve_camera_pose(ASYMMETRIC_WORLD, pixels, intr, WIDTH, HEIGHT)

    depths = result.pose.world_to_camera_points(ASYMMETRIC_WORLD)[:, 2]
    assert np.all(depths > 0)


# -- floor homography ----------------------------------------------------

def test_homography_recovers_floor_positions_and_validates_on_held_out_points():
    truth = make_scene_pose()
    intr = make_intrinsics()
    world = floor_grid(4, 4)
    pixels = project(truth, intr, world)

    holdout = [2, 7, 11]
    result = solve_floor_homography(pixels, world[:, :2], WIDTH, HEIGHT,
                                    intrinsics=intr, holdout_indices=holdout)

    assert result.pixel_convention == PIXELS_UNDISTORTED
    assert result.mean_reprojection_error_px < 1e-3
    assert not result.outlier_indices

    # Held-out points were never fitted, so they are genuine validation.
    for i in holdout:
        got = homography_image_to_floor(result.H_image_to_floor, *pixels[i])
        assert np.linalg.norm(got - world[i, :2]) < 1e-3


def test_homography_without_intrinsics_is_labelled_raw_and_uncorrected():
    truth = make_scene_pose()
    intr = make_intrinsics(distortion=np.array([-0.3, 0.1, 0.0, 0.0, 0.0]))
    world = floor_grid(4, 4)
    pixels = project(truth, intr, world)

    result = solve_floor_homography(pixels, world[:, :2], WIDTH, HEIGHT, intrinsics=None)

    assert result.pixel_convention == PIXELS_RAW
    assert result.distortion_corrected is False
    assert any("NOT corrected" in w for w in result.warnings)
    # A real distorted lens leaves residual error the homography cannot absorb.
    assert result.mean_floor_residual_m > 1e-4


def test_homography_reports_outliers():
    truth = make_scene_pose()
    intr = make_intrinsics()
    world = floor_grid(4, 4)
    pixels = project(truth, intr, world)
    pixels[5] += [150.0, 120.0]

    result = solve_floor_homography(pixels, world[:, :2], WIDTH, HEIGHT, intrinsics=intr)
    assert 5 in result.outlier_indices
    assert any("outlier" in w for w in result.warnings)


def test_collinear_correspondences_are_rejected():
    truth = make_scene_pose()
    intr = make_intrinsics()
    world = np.array([[x, 3.0, 0.0] for x in np.linspace(2, 9, 5)])
    pixels = project(truth, intr, world)

    with pytest.raises(DegenerateConfiguration, match="collinear"):
        solve_floor_homography(pixels, world[:, :2], WIDTH, HEIGHT, intrinsics=intr)


def test_homography_needs_four_points_after_holdout():
    truth = make_scene_pose()
    intr = make_intrinsics()
    world = floor_grid(2, 3)      # six points
    pixels = project(truth, intr, world)

    with pytest.raises(CalibrationError, match="at least 4"):
        solve_floor_homography(pixels, world[:, :2], WIDTH, HEIGHT,
                               intrinsics=intr, holdout_indices=[0, 1, 2])


def test_horizon_pixel_has_no_floor_position():
    """On the horizon line the scale factor vanishes, so floor position is undefined."""
    from app.calibration import homography_horizon_line

    truth = make_scene_pose()
    intr = make_intrinsics()
    world = floor_grid(4, 4)
    pixels = project(truth, intr, world)
    result = solve_floor_homography(pixels, world[:, :2], WIDTH, HEIGHT, intrinsics=intr)

    a, b, c = homography_horizon_line(result.H_image_to_floor)
    assert abs(b) > 1e-12
    u = 960.0
    v = -(a * u + c) / b            # exactly on the horizon line

    with pytest.raises(CalibrationError, match="horizon"):
        homography_image_to_floor(result.H_image_to_floor, u, v)

    # A point well below the horizon resolves normally.
    assert np.all(np.isfinite(homography_image_to_floor(result.H_image_to_floor, u, v + 400.0)))


# -- pose-based projection agrees with the homography --------------------

def test_pose_projection_and_homography_agree_on_the_floor():
    """Two independent routes to the same floor point must land together."""
    truth = make_scene_pose()
    intr = make_intrinsics()
    world = floor_grid(4, 4)
    pixels = project(truth, intr, world)
    result = solve_floor_homography(pixels, world[:, :2], WIDTH, HEIGHT, intrinsics=intr)

    for u, v in [(900.0, 800.0), (1200.0, 700.0), (700.0, 950.0)]:
        ray_cam = intr.pixel_to_camera_ray(u, v)
        via_pose = intersect_ray_with_plane(truth.position, truth.rotation @ ray_cam, 0.0)
        via_homography = homography_image_to_floor(result.H_image_to_floor, u, v)
        assert np.linalg.norm(via_pose[:2] - via_homography) < 1e-3


# -- intrinsics validation ----------------------------------------------

def test_fisheye_model_is_rejected_not_treated_as_pinhole():
    K = np.array([[900.0, 0, 960.0], [0, 900.0, 540.0], [0, 0, 1.0]])
    with pytest.raises(IntrinsicsError, match="fisheye"):
        Intrinsics.from_payload(K, np.zeros(4), WIDTH, HEIGHT, model="fisheye")


@pytest.mark.parametrize("K,message", [
    (np.array([[1400.0, 0, 960], [0, 1400, 540], [0, 0, 2.0]]), "K\\[2\\]\\[2\\]"),
    (np.array([[-1400.0, 0, 960], [0, 1400, 540], [0, 0, 1.0]]), "positive"),
    (np.array([[1400.0, 0, 9600], [0, 1400, 540], [0, 0, 1.0]]), "principal point"),
    (np.array([[1400.0, 0, 960], [5.0, 1400, 540], [0, 0, 1.0]]), "upper-triangular"),
])
def test_malformed_camera_matrices_are_rejected(K, message):
    with pytest.raises(IntrinsicsError, match=message):
        Intrinsics.from_payload(K, np.zeros(5), WIDTH, HEIGHT)


def test_distortion_vector_length_is_validated():
    K = np.array([[1400.0, 0, 960], [0, 1400, 540], [0, 0, 1.0]])
    with pytest.raises(IntrinsicsError, match="coefficients"):
        Intrinsics.from_payload(K, np.zeros(7), WIDTH, HEIGHT)


def test_pure_resize_rescales_but_a_crop_does_not():
    intr = make_intrinsics()
    scaled = intr.scaled_to(960, 540)
    assert math.isclose(scaled.camera_matrix[0, 0], 700.0)
    assert math.isclose(scaled.fov_degrees[0], intr.fov_degrees[0], abs_tol=1e-9)

    with pytest.raises(GeometryMismatch, match="crop or letterbox"):
        intr.scaled_to(1280, 1024)


def test_approximate_fov_intrinsics_are_labelled_as_such():
    intr = intrinsics_from_fov(1920, 1080, 90.0)
    assert intr.source == "approximate_fov"
    assert "Not a measured calibration" in intr.notes


# -- checkerboard --------------------------------------------------------

def _render_checkerboard(intr: Intrinsics, pose: Pose, cols: int, rows: int, square: float):
    """Render a synthetic checkerboard view by projecting an ideal board."""
    import cv2

    img = np.full((intr.height, intr.width), 255, dtype=np.uint8)
    # Draw squares as projected quads so corner detection has real structure.
    for r in range(rows + 1):
        for c in range(cols + 1):
            if (r + c) % 2:
                continue
            quad = np.array([
                [c * square, r * square, 0.0],
                [(c + 1) * square, r * square, 0.0],
                [(c + 1) * square, (r + 1) * square, 0.0],
                [c * square, (r + 1) * square, 0.0],
            ])
            cam = pose.world_to_camera_points(quad)
            if np.any(cam[:, 2] <= 0.01):
                return None
            pixels, _ = intr.project_camera_points(cam)
            if not np.all(np.isfinite(pixels)):
                return None
            cv2.fillConvexPoly(img, pixels.astype(np.int32), 0)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


def test_checkerboard_calibration_recovers_intrinsics_and_flags_pose_diversity():
    cols, rows, square = 9, 6, 0.025
    truth = make_intrinsics(distortion=np.array([-0.12, 0.03, 0.0, 0.0, 0.0]))

    captures = []
    targets = [
        ([0.10, 0.06, 0.45], [0.10, 0.06, 0.0]),
        ([0.02, 0.02, 0.40], [0.11, 0.07, 0.0]),
        ([0.20, 0.01, 0.42], [0.09, 0.08, 0.0]),
        ([0.05, 0.14, 0.50], [0.12, 0.05, 0.0]),
        ([0.16, 0.13, 0.38], [0.10, 0.06, 0.0]),
        ([0.11, 0.05, 0.62], [0.10, 0.07, 0.0]),
        ([0.03, 0.09, 0.55], [0.13, 0.06, 0.0]),
        ([0.19, 0.08, 0.58], [0.08, 0.06, 0.0]),
    ]
    for i, (eye, look) in enumerate(targets):
        pose = pose_from_look_at(eye, look)
        img = _render_checkerboard(truth, pose, cols, rows, square)
        if img is None:
            continue
        cap = detect_checkerboard(img, cols, rows, name=f"view{i}")
        if cap:
            captures.append(cap)

    assert len(captures) >= 4, f"synthetic renderer produced only {len(captures)} detections"

    result = calibrate_intrinsics_from_checkerboard(captures, cols, rows, square)
    estimated = result["intrinsics"]

    assert result["rms_reprojection_error_px"] < 1.0
    assert estimated.width == WIDTH and estimated.height == HEIGHT
    assert estimated.source == "checkerboard"
    # Focal length within a few percent of truth from synthetic views.
    assert abs(estimated.camera_matrix[0, 0] - 1400.0) / 1400.0 < 0.08
    assert "pose_diversity" in result
    assert isinstance(result["pose_diversity"]["sufficient"], bool)


def test_checkerboard_needs_several_views():
    fake = detect_checkerboard(
        _render_checkerboard(make_intrinsics(), pose_from_look_at([0.1, 0.06, 0.45], [0.1, 0.06, 0.0]), 9, 6, 0.025),
        9, 6)
    assert fake is not None
    with pytest.raises(CalibrationError, match="at least 3"):
        calibrate_intrinsics_from_checkerboard([fake], 9, 6, 0.025)


def test_mixed_resolution_calibration_images_are_rejected():
    from app.calibration import CheckerboardCapture

    a = CheckerboardCapture("a", np.zeros((54, 1, 2), np.float32), 1920, 1080)
    b = CheckerboardCapture("b", np.zeros((54, 1, 2), np.float32), 1280, 720)
    c = CheckerboardCapture("c", np.zeros((54, 1, 2), np.float32), 1920, 1080)
    with pytest.raises(CalibrationError, match="one resolution"):
        calibrate_intrinsics_from_checkerboard([a, b, c], 9, 6, 0.025)
