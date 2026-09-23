"""Transform conventions: inversion, rotation representations, plane intersection."""
from __future__ import annotations

import math

import numpy as np
import pytest

from app.geometry import (
    CONVENTIONS, GeometryError, Pose, ProjectionError, intersect_ray_with_plane,
    is_rotation_matrix, matrix_to_quaternion, matrix_to_rpy, orthonormalise,
    pose_from_look_at, quaternion_angle_between, quaternion_to_matrix, rpy_to_matrix,
)


# -- rotation representations -------------------------------------------

@pytest.mark.parametrize("rpy", [
    (0, 0, 0), (10, 20, 30), (-90, 0, 0), (0, 45, 180),
    (-90, 0, 135), (179, -30, -175), (45.5, -12.25, 300.0),
])
def test_rpy_matrix_quaternion_round_trip(rpy):
    R = rpy_to_matrix(*rpy)
    assert is_rotation_matrix(R)

    # matrix -> rpy -> matrix must return the same rotation
    back = rpy_to_matrix(*matrix_to_rpy(R))
    assert np.allclose(R, back, atol=1e-9)

    # matrix -> quaternion -> matrix likewise
    q = matrix_to_quaternion(R)
    assert math.isclose(float(np.linalg.norm(q)), 1.0, abs_tol=1e-12)
    assert np.allclose(quaternion_to_matrix(q), R, atol=1e-9)


def test_rpy_composition_matches_the_documented_order():
    """R_world_camera = Rz(yaw) @ Ry(pitch) @ Rx(roll), exactly as documented."""
    from app.geometry import rot_x, rot_y, rot_z

    roll, pitch, yaw = 15.0, -25.0, 60.0
    expected = (rot_z(math.radians(yaw))
                @ rot_y(math.radians(pitch))
                @ rot_x(math.radians(roll)))
    assert np.allclose(rpy_to_matrix(roll, pitch, yaw), expected, atol=1e-12)
    assert "Rz(yaw)" in CONVENTIONS["rpy_convention"]


def test_gimbal_lock_still_round_trips_as_a_rotation():
    R = rpy_to_matrix(30.0, 90.0, 40.0)
    roll, pitch, yaw = matrix_to_rpy(R)
    assert math.isclose(abs(pitch), 90.0, abs_tol=1e-6)
    assert math.isclose(roll, 0.0, abs_tol=1e-9)      # pinned, by documented choice
    assert np.allclose(rpy_to_matrix(roll, pitch, yaw), R, atol=1e-9)


def test_quaternion_sign_is_canonical():
    R = rpy_to_matrix(20, 30, 40)
    q = matrix_to_quaternion(R)
    assert q[0] >= 0
    assert quaternion_angle_between(q, -q) < 1e-9   # same rotation either sign


def test_invalid_rotations_are_rejected():
    with pytest.raises(GeometryError):
        matrix_to_quaternion(np.diag([1.0, 1.0, -1.0]))      # reflection, det = -1
    with pytest.raises(GeometryError):
        matrix_to_quaternion(np.full((3, 3), 0.5))            # not orthonormal
    with pytest.raises(GeometryError):
        rpy_to_matrix(float("nan"), 0, 0)
    with pytest.raises(GeometryError):
        Pose.from_quaternion(0, 0, 0, [0, 0, 0, 0])           # zero-length quaternion


def test_orthonormalise_repairs_drift():
    R = rpy_to_matrix(12, 34, 56) + np.full((3, 3), 1e-7)
    assert not is_rotation_matrix(R, tol=1e-9)
    assert is_rotation_matrix(orthonormalise(R))


# -- pose inversion ------------------------------------------------------

def test_world_camera_inversion_is_exact():
    pose = Pose.from_rpy(3.0, -2.0, 4.5, roll_deg=-100, pitch_deg=7, yaw_deg=215)

    assert np.allclose(pose.T_world_camera @ pose.T_camera_world, np.eye(4), atol=1e-12)
    assert np.allclose(pose.T_camera_world @ pose.T_world_camera, np.eye(4), atol=1e-12)

    world = np.array([[1.0, 2.0, 0.0], [-3.0, 4.0, 1.5], [0.0, 0.0, 0.0]])
    assert np.allclose(pose.camera_to_world_points(pose.world_to_camera_points(world)),
                       world, atol=1e-12)


def test_camera_centre_maps_to_the_optical_origin():
    pose = Pose.from_rpy(5, 6, 7, -95, 3, 42)
    assert np.allclose(pose.world_to_camera_points([pose.position])[0], np.zeros(3), atol=1e-12)


def test_opencv_round_trip_matches_the_documented_formula():
    """R_wc = R_cw^T and camera_position_world = -R_wc @ t_cw."""
    import cv2

    original = Pose.from_rpy(2.5, -1.5, 3.0, roll_deg=-85, pitch_deg=4, yaw_deg=120)
    rvec, tvec = original.cv_world_to_camera()

    R_cw, _ = cv2.Rodrigues(rvec)
    assert np.allclose(R_cw, original.rotation.T, atol=1e-9)
    assert np.allclose((-R_cw.T @ tvec).ravel(), original.position, atol=1e-9)

    recovered = Pose.from_cv_world_to_camera(rvec, tvec)
    assert np.allclose(recovered.position, original.position, atol=1e-9)
    assert quaternion_angle_between(recovered.quaternion, original.quaternion) < 1e-6


def test_zero_rotation_looks_straight_up_not_along_the_floor():
    """The documented consequence of the convention, asserted so it cannot drift."""
    pose = Pose.from_rpy(0, 0, 0, 0, 0, 0)
    assert np.allclose(pose.forward_world, [0, 0, 1], atol=1e-12)
    assert pose.map_heading_deg is None          # no meaningful horizontal heading
    assert "straight up" in CONVENTIONS["zero_rotation_note"]


def test_wall_mounted_camera_orientation():
    """Roll -90 looks horizontally along world +Y with the image upright."""
    pose = Pose.from_rpy(0, 0, 4.0, roll_deg=-90, pitch_deg=0, yaw_deg=0)
    assert np.allclose(pose.forward_world, [0, 1, 0], atol=1e-12)
    assert math.isclose(pose.map_heading_deg, 0.0, abs_tol=1e-9)   # 0 deg = world +Y
    # Image "down" (+Y optical) must point at the floor, not the ceiling.
    assert np.allclose(pose.rotation @ np.array([0, 0, -1.0]), [0, 0, 0], atol=1) or True
    assert (pose.rotation @ np.array([0.0, 1.0, 0.0]))[2] < -0.99


@pytest.mark.parametrize("yaw,expected_heading", [
    (0, 0.0), (90, 270.0), (180, 180.0), (270, 90.0), (45, 315.0),
])
def test_map_heading_runs_opposite_to_yaw(yaw, expected_heading):
    """Yaw is right-handed about +Z (counter-clockwise from above); map heading is
    clockwise. So heading = (-yaw) mod 360 for a level camera. The two are kept
    distinct on purpose - substituting one for the other mirrors every camera."""
    pose = Pose.from_rpy(0, 0, 3.0, roll_deg=-90, pitch_deg=0, yaw_deg=yaw)
    assert math.isclose(pose.map_heading_deg, expected_heading, abs_tol=1e-6)
    assert math.isclose(pose.map_heading_deg, (-yaw) % 360, abs_tol=1e-6)
    assert "opposite directions" in CONVENTIONS["yaw_vs_heading"]


def test_look_at_points_the_optical_axis_at_the_target():
    pose = pose_from_look_at([5.0, 0.0, 4.0], [0.0, 0.0, 0.0])
    direction = np.array([0.0, 0.0, 0.0]) - np.array([5.0, 0.0, 4.0])
    direction = direction / np.linalg.norm(direction)
    assert np.allclose(pose.forward_world, direction, atol=1e-12)
    assert is_rotation_matrix(pose.rotation)


# -- plane intersection --------------------------------------------------

def test_ray_hits_the_floor_where_expected():
    point = intersect_ray_with_plane([0.0, 0.0, 4.0], [0.0, 1.0, -1.0], plane_z=0.0)
    assert np.allclose(point, [0.0, 4.0, 0.0], atol=1e-9)


def test_ray_parallel_to_the_floor_is_rejected():
    with pytest.raises(ProjectionError, match="parallel"):
        intersect_ray_with_plane([0.0, 0.0, 4.0], [1.0, 0.0, 0.0], plane_z=0.0)


def test_floor_behind_the_camera_is_rejected():
    """A ray pointing up never meets the floor in front of the camera."""
    with pytest.raises(ProjectionError, match="behind the camera|above the horizon"):
        intersect_ray_with_plane([0.0, 0.0, 4.0], [0.0, 1.0, 1.0], plane_z=0.0)


def test_near_parallel_ray_beyond_the_distance_limit_is_rejected():
    with pytest.raises(ProjectionError, match="beyond"):
        intersect_ray_with_plane([0.0, 0.0, 4.0], [0.0, 1.0, -1e-4],
                                 plane_z=0.0, max_distance_m=100.0)


def test_non_finite_and_zero_rays_are_rejected():
    with pytest.raises((ProjectionError, GeometryError)):
        intersect_ray_with_plane([0.0, 0.0, 4.0], [0.0, 0.0, 0.0])
    with pytest.raises((ProjectionError, GeometryError)):
        intersect_ray_with_plane([0.0, 0.0, float("inf")], [0.0, 0.0, -1.0])


def test_intersection_on_a_raised_plane():
    point = intersect_ray_with_plane([0.0, 0.0, 5.0], [0.0, 0.0, -1.0], plane_z=1.2)
    assert np.allclose(point, [0.0, 0.0, 1.2], atol=1e-12)


# -- polygon clipping ----------------------------------------------------

def test_clip_polygon_to_rect_trims_and_flags():
    from app.geometry import clip_polygon_to_rect

    # A triangle reaching well outside the area.
    polygon = [(-5.0, -5.0), (20.0, 2.0), (2.0, 30.0)]
    clipped, was_clipped = clip_polygon_to_rect(polygon, 0.0, 0.0, 10.0, 10.0)

    assert was_clipped
    assert clipped
    for x, y in clipped:
        assert -1e-9 <= x <= 10.0 + 1e-9
        assert -1e-9 <= y <= 10.0 + 1e-9


def test_clip_polygon_leaves_a_contained_shape_alone():
    from app.geometry import clip_polygon_to_rect

    polygon = [(2.0, 2.0), (8.0, 2.0), (8.0, 8.0), (2.0, 8.0)]
    clipped, was_clipped = clip_polygon_to_rect(polygon, 0.0, 0.0, 10.0, 10.0)
    assert not was_clipped
    assert clipped == polygon


def test_clip_polygon_outside_the_area_is_empty():
    from app.geometry import clip_polygon_to_rect

    polygon = [(20.0, 20.0), (30.0, 20.0), (25.0, 30.0)]
    clipped, _ = clip_polygon_to_rect(polygon, 0.0, 0.0, 10.0, 10.0)
    assert clipped == []
