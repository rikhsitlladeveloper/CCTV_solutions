"""Coordinate conventions and rigid-body transforms.

Everything geometric in Numenor derives from this module, so the 2D map, the 3D
view, the solvers and the projection tools cannot drift apart.

Frames
------
**Factory world frame** — right-handed, metres.
  * ``X`` and ``Y`` lie on the factory floor.
  * ``Z`` points up.
  * The floor plane is ``Z = 0`` by default.

**Camera optical frame** — the OpenCV convention, right-handed, metres.
  * ``X`` right across the image.
  * ``Y`` down the image.
  * ``Z`` forward, along the optical axis, into the scene.

Authoritative pose
------------------
``T_world_camera`` is a 4x4 homogeneous transform that maps a point expressed in
the *optical camera* frame into the *world* frame::

    p_world = T_world_camera @ p_camera

Its rotation block is ``R_world_camera`` and its translation block is the camera
centre in world coordinates. The inverse, ``T_camera_world``, is what OpenCV's
``solvePnP`` returns (as ``rvec``/``tvec``). The two are never mixed implicitly:
every function name states which direction it works in.

Roll / pitch / yaw
------------------
RPY is an *editable representation* of the orientation; the stored authority is
a normalised quaternion. The conversion is fixed and explicit::

    R_world_camera = Rz(yaw) @ Ry(pitch) @ Rx(roll)

with each elementary rotation right-handed about the named world axis, and all
angles converted to radians internally. Degrees are a UI-level unit only.

Note what zero rotation means under this convention: ``R = I`` aligns the
*optical* axes with the *world* axes, so the camera's forward axis (+Z optical)
points straight **up** along world +Z, and the image's "down" direction (+Y
optical) points along world +Y. That is a camera staring at the ceiling — it is
not a typical wall-mounted orientation. A camera on a wall at height h looking
horizontally along world +Y has roll = -90 deg, pitch = 0, yaw = 0.

Map heading
-----------
The 2D map draws world +Y up the screen and world +X to the right. A marker's
heading is the camera's optical +Z axis projected onto the world XY plane,
reported in the same convention the floor-plan editor already uses: 0 deg points
to the top of the map (world +Y) and increases clockwise.

Mind the sign: yaw is a right-handed rotation about world +Z, so viewed from
above it increases *counter-clockwise*, while map heading increases *clockwise*.
For a level camera the two are related by ``heading = (-yaw) mod 360``. They are
deliberately not the same number, and neither is silently substituted for the
other.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# Exposed through the API so the UI documents exactly what the code does.
CONVENTIONS = {
    "world_frame": "Right-handed. X and Y on the factory floor, Z up. Units: metres.",
    "camera_frame": "OpenCV optical frame. X right, Y down, Z forward along the optical axis.",
    "authoritative_pose": (
        "T_world_camera, a 4x4 transform mapping optical-camera coordinates into world "
        "coordinates. Stored as a normalised quaternion plus the camera centre in world metres."
    ),
    "rpy_convention": "R_world_camera = Rz(yaw) @ Ry(pitch) @ Rx(roll); degrees in the UI, radians internally.",
    "zero_rotation_note": (
        "Zero roll/pitch/yaw aligns the optical axes with the world axes, so the camera looks "
        "straight up along world +Z. It is not a default wall-mount orientation."
    ),
    "map_heading": "0 degrees points to world +Y (top of the map) and increases clockwise.",
    "yaw_vs_heading": (
        "Yaw is a right-handed rotation about world +Z, so seen from above it increases "
        "counter-clockwise. Map heading increases clockwise. The two therefore run in opposite "
        "directions: for a level camera, heading = (-yaw) mod 360."
    ),
    "floor_plane": "Z = 0 unless a different plane height is given explicitly.",
    "opengl_note": (
        "Graphics engines look down -Z with +Y up. Converting an optical pose for three.js "
        "requires R_gl = R_world_camera @ diag(1, -1, -1)."
    ),
}

_EPS = 1e-9


class GeometryError(ValueError):
    """Raised for malformed matrices, invalid rotations or non-finite values."""


# -- validation ----------------------------------------------------------

def require_finite(values, label: str) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if not np.all(np.isfinite(arr)):
        raise GeometryError(f"{label} contains non-finite values (NaN or infinity).")
    return arr


def is_rotation_matrix(R: np.ndarray, tol: float = 1e-6) -> bool:
    R = np.asarray(R, dtype=float)
    if R.shape != (3, 3) or not np.all(np.isfinite(R)):
        return False
    if not np.allclose(R.T @ R, np.eye(3), atol=tol):
        return False
    return abs(np.linalg.det(R) - 1.0) <= max(tol, 1e-6)


def require_rotation_matrix(R, label: str = "Rotation matrix") -> np.ndarray:
    R = require_finite(R, label)
    if R.shape != (3, 3):
        raise GeometryError(f"{label} must be 3x3, got {R.shape}.")
    if not is_rotation_matrix(R):
        raise GeometryError(
            f"{label} is not a valid rotation: it must be orthonormal with determinant +1."
        )
    return R


def orthonormalise(R: np.ndarray) -> np.ndarray:
    """Snap a nearly-orthonormal matrix onto SO(3) via SVD."""
    R = require_finite(R, "Rotation matrix")
    u, _, vt = np.linalg.svd(R)
    out = u @ vt
    if np.linalg.det(out) < 0:          # reflection -> flip the smallest axis
        u[:, -1] *= -1
        out = u @ vt
    return out


# -- elementary rotations ------------------------------------------------

def rot_x(radians: float) -> np.ndarray:
    c, s = math.cos(radians), math.sin(radians)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=float)


def rot_y(radians: float) -> np.ndarray:
    c, s = math.cos(radians), math.sin(radians)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=float)


def rot_z(radians: float) -> np.ndarray:
    c, s = math.cos(radians), math.sin(radians)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=float)


def rpy_to_matrix(roll_deg: float, pitch_deg: float, yaw_deg: float) -> np.ndarray:
    """R_world_camera = Rz(yaw) @ Ry(pitch) @ Rx(roll), angles in degrees."""
    require_finite([roll_deg, pitch_deg, yaw_deg], "Roll/pitch/yaw")
    return (rot_z(math.radians(yaw_deg))
            @ rot_y(math.radians(pitch_deg))
            @ rot_x(math.radians(roll_deg)))


def matrix_to_rpy(R) -> tuple[float, float, float]:
    """Inverse of :func:`rpy_to_matrix`, in degrees.

    At pitch = +/-90 deg the decomposition is degenerate (gimbal lock): roll and
    yaw trade off against each other. We pin roll to 0 and fold the rotation into
    yaw, which round-trips to the same matrix even though the angles differ from
    the ones originally entered.
    """
    R = require_rotation_matrix(R)
    sin_pitch = -R[2, 0]
    sin_pitch = min(1.0, max(-1.0, sin_pitch))
    pitch = math.asin(sin_pitch)

    if abs(abs(sin_pitch) - 1.0) < 1e-8:
        roll = 0.0
        yaw = math.atan2(-R[0, 1], R[1, 1]) if sin_pitch > 0 else math.atan2(R[0, 1], R[1, 1])
    else:
        roll = math.atan2(R[2, 1], R[2, 2])
        yaw = math.atan2(R[1, 0], R[0, 0])
    return math.degrees(roll), math.degrees(pitch), math.degrees(yaw)


# -- quaternions ---------------------------------------------------------
# Stored and exchanged as (w, x, y, z) with w first.

def quaternion_normalise(q) -> np.ndarray:
    q = require_finite(q, "Quaternion")
    if q.shape != (4,):
        raise GeometryError("A quaternion must have four components (w, x, y, z).")
    norm = float(np.linalg.norm(q))
    if norm < _EPS:
        raise GeometryError("A quaternion cannot have zero length.")
    q = q / norm
    # Fix the sign so that q and -q (the same rotation) store identically.
    if q[0] < 0:
        q = -q
    return q


def matrix_to_quaternion(R) -> np.ndarray:
    R = require_rotation_matrix(R)
    trace = R[0, 0] + R[1, 1] + R[2, 2]
    if trace > 0:
        s = math.sqrt(trace + 1.0) * 2
        w = 0.25 * s
        x = (R[2, 1] - R[1, 2]) / s
        y = (R[0, 2] - R[2, 0]) / s
        z = (R[1, 0] - R[0, 1]) / s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = math.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
        w = (R[2, 1] - R[1, 2]) / s
        x = 0.25 * s
        y = (R[0, 1] + R[1, 0]) / s
        z = (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = math.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
        w = (R[0, 2] - R[2, 0]) / s
        x = (R[0, 1] + R[1, 0]) / s
        y = 0.25 * s
        z = (R[1, 2] + R[2, 1]) / s
    else:
        s = math.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
        w = (R[1, 0] - R[0, 1]) / s
        x = (R[0, 2] + R[2, 0]) / s
        y = (R[1, 2] + R[2, 1]) / s
        z = 0.25 * s
    return quaternion_normalise(np.array([w, x, y, z], dtype=float))


def quaternion_to_matrix(q) -> np.ndarray:
    w, x, y, z = quaternion_normalise(q)
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z),     2 * (x * z + w * y)],
        [2 * (x * y + w * z),     1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y),     2 * (y * z + w * x),     1 - 2 * (x * x + y * y)],
    ], dtype=float)


def quaternion_angle_between(q1, q2) -> float:
    """Smallest rotation angle between two orientations, in degrees."""
    a = quaternion_normalise(q1)
    b = quaternion_normalise(q2)
    dot = min(1.0, max(-1.0, abs(float(np.dot(a, b)))))
    return math.degrees(2.0 * math.acos(dot))


# -- poses ---------------------------------------------------------------

@dataclass(frozen=True)
class Pose:
    """A camera pose. ``rotation`` is R_world_camera; ``position`` is the camera
    centre in world metres."""

    rotation: np.ndarray          # 3x3, R_world_camera
    position: np.ndarray          # (3,), camera centre in world coordinates

    @staticmethod
    def from_rpy(x: float, y: float, z: float,
                 roll_deg: float, pitch_deg: float, yaw_deg: float) -> "Pose":
        return Pose(rpy_to_matrix(roll_deg, pitch_deg, yaw_deg),
                    require_finite([x, y, z], "Camera position"))

    @staticmethod
    def from_quaternion(x: float, y: float, z: float, q) -> "Pose":
        return Pose(quaternion_to_matrix(q), require_finite([x, y, z], "Camera position"))

    @staticmethod
    def from_matrix(T_world_camera) -> "Pose":
        T = require_finite(T_world_camera, "T_world_camera")
        if T.shape != (4, 4):
            raise GeometryError(f"T_world_camera must be 4x4, got {T.shape}.")
        if not np.allclose(T[3], [0, 0, 0, 1], atol=1e-6):
            raise GeometryError("The bottom row of a rigid transform must be [0, 0, 0, 1].")
        return Pose(require_rotation_matrix(T[:3, :3]), T[:3, 3].copy())

    @staticmethod
    def from_cv_world_to_camera(rvec, tvec) -> "Pose":
        """Convert OpenCV's world-to-camera result into the stored camera-to-world pose.

            R_wc = R_cw^T
            camera_position_world = -R_wc @ t_cw
        """
        import cv2

        rvec = require_finite(rvec, "rvec").reshape(3, 1)
        tvec = require_finite(tvec, "tvec").reshape(3, 1)
        R_cw, _ = cv2.Rodrigues(rvec)
        R_wc = R_cw.T
        position = (-R_wc @ tvec).ravel()
        return Pose(orthonormalise(R_wc), position)

    # -- derived forms ---------------------------------------------------

    @property
    def T_world_camera(self) -> np.ndarray:
        T = np.eye(4)
        T[:3, :3] = self.rotation
        T[:3, 3] = self.position
        return T

    @property
    def T_camera_world(self) -> np.ndarray:
        """The inverse transform: world coordinates into optical-camera coordinates."""
        T = np.eye(4)
        T[:3, :3] = self.rotation.T
        T[:3, 3] = -self.rotation.T @ self.position
        return T

    @property
    def quaternion(self) -> np.ndarray:
        return matrix_to_quaternion(self.rotation)

    @property
    def rpy_degrees(self) -> tuple[float, float, float]:
        return matrix_to_rpy(self.rotation)

    def cv_world_to_camera(self) -> tuple[np.ndarray, np.ndarray]:
        """``(rvec, tvec)`` in OpenCV's world-to-camera form."""
        import cv2

        R_cw = self.rotation.T
        t_cw = -R_cw @ self.position
        rvec, _ = cv2.Rodrigues(R_cw)
        return rvec.reshape(3, 1), t_cw.reshape(3, 1)

    @property
    def forward_world(self) -> np.ndarray:
        """The optical +Z axis expressed in world coordinates."""
        return self.rotation @ np.array([0.0, 0.0, 1.0])

    @property
    def map_heading_deg(self) -> float | None:
        """Heading on the 2D map: 0 deg = world +Y, increasing clockwise.

        Returns ``None`` when the camera points almost straight up or down, where
        a horizontal heading is not meaningful.
        """
        f = self.forward_world
        if math.hypot(f[0], f[1]) < 1e-6:
            return None
        return math.degrees(math.atan2(f[0], f[1])) % 360.0

    def world_to_camera_points(self, points_world) -> np.ndarray:
        p = require_finite(points_world, "World points").reshape(-1, 3)
        return (self.rotation.T @ (p - self.position).T).T

    def camera_to_world_points(self, points_camera) -> np.ndarray:
        p = require_finite(points_camera, "Camera points").reshape(-1, 3)
        return (self.rotation @ p.T).T + self.position


def pose_from_look_at(position, target, up=(0.0, 0.0, 1.0)) -> Pose:
    """Build an optical-frame pose that looks from ``position`` toward ``target``.

    Convenience for synthetic data and for seeding a manual placement; the
    resulting +Z optical axis points at the target and +Y points "down" in the
    image relative to ``up``.
    """
    position = require_finite(position, "Camera position")
    target = require_finite(target, "Look-at target")
    forward = target - position
    norm = np.linalg.norm(forward)
    if norm < _EPS:
        raise GeometryError("The look-at target coincides with the camera position.")
    forward = forward / norm

    up = require_finite(up, "Up vector")
    if np.linalg.norm(np.cross(forward, up)) < 1e-8:
        # Looking along the up axis: pick any perpendicular reference.
        up = np.array([0.0, 1.0, 0.0]) if abs(forward[1]) < 0.9 else np.array([1.0, 0.0, 0.0])

    right = np.cross(forward, up)
    right = right / np.linalg.norm(right)
    down = np.cross(forward, right)
    R = np.column_stack([right, down, forward])   # optical X, Y, Z in world coords
    return Pose(orthonormalise(R), position)


# -- plane intersection --------------------------------------------------

class ProjectionError(ValueError):
    """Raised when a ray cannot meaningfully meet the requested plane."""


def intersect_ray_with_plane(origin_world, direction_world, plane_z: float = 0.0,
                             max_distance_m: float = 1000.0) -> np.ndarray:
    """Intersect a world-space ray with the horizontal plane ``Z = plane_z``.

    Rejects rays parallel to the plane, intersections behind the camera, and
    non-finite results, rather than returning a plausible-looking wrong answer.
    """
    origin = require_finite(origin_world, "Ray origin").reshape(3)
    direction = require_finite(direction_world, "Ray direction").reshape(3)

    norm = float(np.linalg.norm(direction))
    if norm < _EPS:
        raise ProjectionError("The ray has no direction.")
    direction = direction / norm

    denom = direction[2]
    if abs(denom) < 1e-9:
        raise ProjectionError(
            "This ray runs parallel to the floor plane, so it never meets it. "
            "The point is at or near the horizon."
        )

    t = (plane_z - origin[2]) / denom
    if t <= 0:
        raise ProjectionError(
            "The floor plane lies behind the camera along this ray. "
            "The point is above the horizon."
        )
    if t > max_distance_m:
        raise ProjectionError(
            f"The intersection is {t:.0f} m away, beyond the {max_distance_m:.0f} m limit. "
            "The ray is nearly parallel to the floor."
        )

    point = origin + t * direction
    if not np.all(np.isfinite(point)):
        raise ProjectionError("The intersection is not a finite point.")
    return point


def clip_polygon_to_rect(polygon, min_x: float, min_y: float, max_x: float, max_y: float):
    """Sutherland-Hodgman clip of a polygon against an axis-aligned rectangle.

    Returns ``(clipped_polygon, was_clipped)``. A camera can genuinely see far
    beyond the declared factory area along a shallow sight line; clipping keeps
    the drawing inside the area the frame actually describes, and the flag says
    that is what happened.
    """
    if not polygon:
        return [], False

    def inside(p, edge):
        x, y = p
        return {"left": x >= min_x, "right": x <= max_x,
                "bottom": y >= min_y, "top": y <= max_y}[edge]

    def intersect(p1, p2, edge):
        (x1, y1), (x2, y2) = p1, p2
        if edge in ("left", "right"):
            xe = min_x if edge == "left" else max_x
            t = (xe - x1) / (x2 - x1) if abs(x2 - x1) > 1e-12 else 0.0
            return (xe, y1 + t * (y2 - y1))
        ye = min_y if edge == "bottom" else max_y
        t = (ye - y1) / (y2 - y1) if abs(y2 - y1) > 1e-12 else 0.0
        return (x1 + t * (x2 - x1), ye)

    output = list(polygon)
    for edge in ("left", "right", "bottom", "top"):
        if not output:
            break
        current, output = output, []
        for i, point in enumerate(current):
            prev = current[i - 1]
            if inside(point, edge):
                if not inside(prev, edge):
                    output.append(intersect(prev, point, edge))
                output.append(point)
            elif inside(prev, edge):
                output.append(intersect(prev, point, edge))

    was_clipped = len(output) != len(polygon) or any(
        p not in polygon for p in output)
    return output, was_clipped


def frustum_floor_polygon(pose: Pose, intrinsics, plane_z: float = 0.0,
                          max_distance_m: float = 60.0, samples_per_edge: int = 8):
    """Where the image border lands on the floor plane, as a world polygon.

    Returns ``(polygon, clipped)``. ``clipped`` is True when part of the image
    does not reach the floor (it points at or above the horizon), which means the
    polygon is a partial footprint rather than the whole view.
    """
    width = intrinsics.width
    height = intrinsics.height
    edge = []
    n = max(2, samples_per_edge)
    for i in range(n):
        f = i / (n - 1)
        edge.append((f * (width - 1), 0.0))
    for i in range(n):
        f = i / (n - 1)
        edge.append((width - 1.0, f * (height - 1)))
    for i in range(n):
        f = i / (n - 1)
        edge.append(((1 - f) * (width - 1), height - 1.0))
    for i in range(n):
        f = i / (n - 1)
        edge.append((0.0, (1 - f) * (height - 1)))

    polygon = []
    clipped = False
    for (u, v) in edge:
        try:
            direction = intrinsics.pixel_to_camera_ray(u, v)
            world_dir = pose.rotation @ direction
            point = intersect_ray_with_plane(pose.position, world_dir, plane_z, max_distance_m)
        except (ProjectionError, GeometryError):
            clipped = True
            continue
        polygon.append((float(point[0]), float(point[1])))
    return polygon, clipped
