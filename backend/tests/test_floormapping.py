"""The guided floor-mapping pipeline, against synthetic scenes with known truth."""
from __future__ import annotations

import numpy as np
import pytest

from app.calibration import PIXELS_RAW, PIXELS_UNDISTORTED
from app.floormapping import (
    MappingError, PointInput, apply_homography, calculate_floor_mapping, convex_hull,
    evaluate_validation, inspect_points, point_in_polygon, polygon_area,
    project_floor_to_image, project_image_to_floor,
)
from app.geometry import Pose, pose_from_look_at
from app.intrinsics import Intrinsics

WIDTH, HEIGHT = 1920, 1080


def intrinsics(distortion=None) -> Intrinsics:
    K = np.array([[1400.0, 0, 960.0], [0, 1400.0, 540.0], [0, 0, 1.0]])
    return Intrinsics.from_payload(K, distortion if distortion is not None else np.zeros(5),
                                   WIDTH, HEIGHT)


def scene_pose() -> Pose:
    return pose_from_look_at([2.0, -3.0, 5.0], [8.0, 8.0, 0.0])


def make_points(pose: Pose, intr: Intrinsics, coords, role="calibration",
                start_id=1, noise_px=0.0, rng=None) -> list[PointInput]:
    out = []
    for i, (x, y) in enumerate(coords):
        cam = pose.world_to_camera_points([[x, y, 0.0]])
        pixels, in_front = intr.project_camera_points(cam)
        assert in_front[0], f"({x},{y}) is behind the camera"
        u, v = pixels[0]
        if noise_px and rng is not None:
            u, v = np.array([u, v]) + rng.normal(0, noise_px, 2)
        out.append(PointInput(
            reference_point_id=start_id + i, code=f"RP-{start_id + i:02d}",
            name=f"Point {start_id + i}", world_x=float(x), world_y=float(y),
            pixel_u=float(u), pixel_v=float(v), role=role))
    return out


GRID = [(2, 2), (8, 2), (14, 2), (2, 8), (8, 8), (14, 8), (2, 14), (8, 14), (14, 14)]
HELD_OUT = [(5, 5), (11, 11), (5, 11)]


# -- recovery ------------------------------------------------------------

def test_recovers_a_known_mapping_exactly():
    pose, intr = scene_pose(), intrinsics()
    points = make_points(pose, intr, GRID)

    result = calculate_floor_mapping(points, WIDTH, HEIGHT, intrinsics=intr)

    assert result.pixel_convention == PIXELS_RAW   # no distortion to remove
    assert len(result.used_point_ids) == len(GRID)
    assert result.rejected_point_ids == []
    assert result.mean_error_m < 1e-6
    assert result.max_error_m < 1e-6
    assert result.condition_number < 1e7

    # Every point the pipeline was given round-trips.
    for x, y in GRID:
        u, v = project_floor_to_image(result.H_floor_to_image, x, y)
        gx, gy = project_image_to_floor(result.H_image_to_floor, u, v)
        assert abs(gx - x) < 1e-6 and abs(gy - y) < 1e-6


def test_held_out_points_are_predicted_without_being_fitted():
    pose, intr = scene_pose(), intrinsics()
    points = make_points(pose, intr, GRID) + \
        make_points(pose, intr, HELD_OUT, role="validation", start_id=50)

    result = calculate_floor_mapping(points, WIDTH, HEIGHT, intrinsics=intr)

    # Validation points must not appear among the fitted ones.
    assert not set(result.used_point_ids) & {50, 51, 52}

    validation = evaluate_validation(result, threshold_m=0.1)
    assert validation["held_out_count"] == 3
    assert validation["passed"] is True
    assert validation["summary"]["max_m"] < 1e-6
    assert "took no part" in validation["interpretation"]


def test_validation_fails_when_a_point_is_mismeasured():
    pose, intr = scene_pose(), intrinsics()
    held = make_points(pose, intr, HELD_OUT, role="validation", start_id=50)
    held[0].world_x += 0.9          # surveyed wrongly by 90 cm
    points = make_points(pose, intr, GRID) + held

    result = calculate_floor_mapping(points, WIDTH, HEIGHT, intrinsics=intr)
    validation = evaluate_validation(result, threshold_m=0.1)

    assert validation["passed"] is False
    assert validation["summary"]["max_m"] > 0.5
    assert any("worst point" in m or "above the" in m for m in validation["messages"])


def test_noise_degrades_gracefully():
    rng = np.random.default_rng(7)
    pose, intr = scene_pose(), intrinsics()
    points = make_points(pose, intr, GRID, noise_px=1.0, rng=rng) + \
        make_points(pose, intr, HELD_OUT, role="validation", start_id=50, noise_px=1.0, rng=rng)

    result = calculate_floor_mapping(points, WIDTH, HEIGHT, intrinsics=intr)
    validation = evaluate_validation(result, threshold_m=0.25)

    assert result.mean_error_m < 0.10, result.mean_error_m
    assert validation["held_out_count"] == 3


# -- robustness ----------------------------------------------------------

def test_a_mis_clicked_point_is_rejected_and_named():
    pose, intr = scene_pose(), intrinsics()
    points = make_points(pose, intr, GRID)
    points[4].pixel_u += 260        # clicked the wrong feature
    points[4].pixel_v -= 180

    result = calculate_floor_mapping(points, WIDTH, HEIGHT, intrinsics=intr)

    assert points[4].reference_point_id in result.rejected_point_ids
    assert points[4].reference_point_id not in result.used_point_ids
    assert any(points[4].code in w for w in result.warnings)
    # The refit excludes it, so the remaining points stay accurate.
    assert result.mean_error_m < 1e-5


def test_a_mis_measured_point_is_rejected():
    pose, intr = scene_pose(), intrinsics()
    points = make_points(pose, intr, GRID)
    points[2].world_x += 3.0        # typo in the survey

    result = calculate_floor_mapping(points, WIDTH, HEIGHT, intrinsics=intr)
    assert points[2].reference_point_id in result.rejected_point_ids


def test_collinear_points_are_refused_with_a_hint():
    pose, intr = scene_pose(), intrinsics()
    points = make_points(pose, intr, [(2, 4), (5, 4), (8, 4), (11, 4), (14, 4)])

    with pytest.raises(MappingError) as exc:
        calculate_floor_mapping(points, WIDTH, HEIGHT, intrinsics=intr)
    assert "flat surface" in str(exc.value) or "unstable" in str(exc.value) \
        or "fits these points" in str(exc.value)


def test_too_few_points_explains_what_to_do():
    pose, intr = scene_pose(), intrinsics()
    points = make_points(pose, intr, GRID[:3])

    with pytest.raises(MappingError) as exc:
        calculate_floor_mapping(points, WIDTH, HEIGHT, intrinsics=intr)
    assert "at least 4" in exc.value.message
    assert exc.value.hint


# -- distortion ----------------------------------------------------------

def test_distortion_is_removed_when_a_lens_calibration_exists():
    pose = scene_pose()
    distorted = intrinsics(distortion=np.array([-0.31, 0.11, 0.0006, -0.0004, -0.02]))
    points = make_points(pose, distorted, GRID) + \
        make_points(pose, distorted, HELD_OUT, role="validation", start_id=50)

    with_lens = calculate_floor_mapping(points, WIDTH, HEIGHT, intrinsics=distorted)
    assert with_lens.pixel_convention == PIXELS_UNDISTORTED
    assert with_lens.distortion_corrected is True
    # Undistortion is an iterative inversion, so this is sub-millimetre rather
    # than exact. A tenth of a millimetre is far below any survey error.
    assert with_lens.mean_error_m < 1e-3
    assert evaluate_validation(with_lens, 0.05)["summary"]["max_m"] < 1e-3

    # The same clicks without a lens calibration must not claim correction,
    # and are measurably worse.
    without = calculate_floor_mapping(points, WIDTH, HEIGHT, intrinsics=None)
    assert without.pixel_convention == PIXELS_RAW
    assert without.distortion_corrected is False
    assert any("NOT been corrected" in w for w in without.warnings)
    assert evaluate_validation(without, 0.05)["summary"]["max_m"] > \
        evaluate_validation(with_lens, 0.05)["summary"]["max_m"]


def test_pixel_convention_is_applied_consistently_in_both_directions():
    """Whatever space the fit used, projection must use the same one."""
    pose = scene_pose()
    distorted = intrinsics(distortion=np.array([-0.28, 0.09, 0.0, 0.0, 0.0]))
    points = make_points(pose, distorted, GRID)
    result = calculate_floor_mapping(points, WIDTH, HEIGHT, intrinsics=distorted)

    for p in points:
        undistorted = distorted.undistort_points([[p.pixel_u, p.pixel_v]])[0]
        gx, gy = project_image_to_floor(result.H_image_to_floor, *undistorted)
        assert abs(gx - p.world_x) < 1e-3 and abs(gy - p.world_y) < 1e-3


# -- projection edge cases ----------------------------------------------

def test_the_horizon_has_no_floor_position():
    pose, intr = scene_pose(), intrinsics()
    result = calculate_floor_mapping(make_points(pose, intr, GRID), WIDTH, HEIGHT, intrinsics=intr)

    a, b, c = result.H_image_to_floor[2, :]
    u = 960.0
    v = -(a * u + c) / b                     # exactly on the horizon line
    with pytest.raises(MappingError, match="horizon"):
        project_image_to_floor(result.H_image_to_floor, u, v)


def test_non_finite_input_is_refused():
    pose, intr = scene_pose(), intrinsics()
    points = make_points(pose, intr, GRID)
    points[0].pixel_u = float("nan")
    with pytest.raises(MappingError, match="finite"):
        calculate_floor_mapping(points, WIDTH, HEIGHT, intrinsics=intr)


def test_browser_zoom_cannot_change_the_result():
    """Observations are stored in source pixels, so display scale is irrelevant."""
    pose, intr = scene_pose(), intrinsics()
    points = make_points(pose, intr, GRID)
    baseline = calculate_floor_mapping(points, WIDTH, HEIGHT, intrinsics=intr)

    # Whatever the browser did to show the image, the stored pixels are identical,
    # so the mapping must be bit-for-bit the same.
    again = calculate_floor_mapping(points, WIDTH, HEIGHT, intrinsics=intr)
    assert np.allclose(baseline.H_image_to_floor, again.H_image_to_floor, atol=0, rtol=0)


# -- coverage ------------------------------------------------------------

def test_coverage_polygon_bounds_what_the_mapping_speaks_for():
    pose, intr = scene_pose(), intrinsics()
    result = calculate_floor_mapping(make_points(pose, intr, GRID), WIDTH, HEIGHT, intrinsics=intr)

    assert polygon_area(result.coverage_polygon) > 100      # the 12 x 12 m grid
    assert point_in_polygon(8, 8, result.coverage_polygon)
    assert not point_in_polygon(40, 40, result.coverage_polygon)


def test_convex_hull_and_area_are_sane():
    square = np.array([[0, 0], [4, 0], [4, 3], [0, 3]], dtype=float)
    hull = convex_hull(square)
    assert len(hull) == 4
    assert abs(polygon_area(hull) - 12.0) < 1e-6


# -- pre-flight checks ---------------------------------------------------

def test_inspect_points_flags_common_mistakes():
    pose, intr = scene_pose(), intrinsics()

    few = inspect_points(make_points(pose, intr, GRID[:3]), WIDTH, HEIGHT)
    assert any(i["code"] == "too_few_points" and i["level"] == "error" for i in few)

    line = inspect_points(make_points(pose, intr, [(2, 4), (5, 4), (8, 4), (11, 4)]), WIDTH, HEIGHT)
    assert any(i["code"].startswith("collinear") or i["code"].startswith("near_collinear")
               for i in line)

    clustered = inspect_points(
        make_points(pose, intr, [(7, 7), (7.3, 7), (7, 7.3), (7.3, 7.3)]), WIDTH, HEIGHT)
    assert any(i["code"] == "small_image_region" for i in clustered)

    dupes = make_points(pose, intr, GRID)
    dupes.append(PointInput(99, "RP-99", "Duplicate", dupes[0].world_x, dupes[0].world_y,
                            dupes[0].pixel_u, dupes[0].pixel_v, "calibration"))
    found = inspect_points(dupes, WIDTH, HEIGHT)
    assert any(i["code"] == "duplicate_world" for i in found)
    assert any(i["code"] == "duplicate_image" for i in found)

    assert any(i["code"] == "no_validation_points" for i in inspect_points(
        make_points(pose, intr, GRID), WIDTH, HEIGHT))


def test_clean_point_set_produces_no_errors():
    pose, intr = scene_pose(), intrinsics()
    points = make_points(pose, intr, GRID) + \
        make_points(pose, intr, HELD_OUT, role="validation", start_id=50)
    issues = inspect_points(points, WIDTH, HEIGHT)
    assert not [i for i in issues if i["level"] == "error"]
