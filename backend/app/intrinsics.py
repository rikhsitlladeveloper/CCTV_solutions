"""Camera intrinsics: the pinhole model, distortion, and image-geometry binding.

Intrinsics are only valid for the exact image geometry they were calibrated at.
A calibration taken at 1920x1080 says nothing about the same sensor delivering a
cropped or rotated 1280x720 substream, so every set of intrinsics records its
own width, height, rotation and crop, and a mismatch is reported rather than
silently tolerated.

Only the OpenCV pinhole model is supported. Fisheye calibrations use a different
projection function entirely (``cv2.fisheye``); feeding fisheye coefficients to
the pinhole path produces confidently wrong numbers, so they are rejected.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from .geometry import GeometryError, require_finite

SUPPORTED_MODELS = {"pinhole"}

# cv2.undistortPoints inverts the distortion model iteratively and defaults to a
# handful of iterations, which leaves a fraction of a pixel of residual. That is
# harmless on screen but shows up as sub-millimetre floor error once a pixel is
# mapped through a homography, so the loop is run to convergence instead.
_UNDISTORT_CRITERIA = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 60, 1e-9)
# OpenCV's pinhole distortion vector: k1 k2 p1 p2 [k3 [k4 k5 k6 [s1..s4 [taux tauy]]]]
VALID_DISTORTION_LENGTHS = {0, 4, 5, 8, 12, 14}


class IntrinsicsError(ValueError):
    pass


class GeometryMismatch(IntrinsicsError):
    """The intrinsics do not describe the image geometry they are being used on."""


@dataclass
class Intrinsics:
    """A validated pinhole calibration bound to one image geometry."""

    camera_matrix: np.ndarray            # 3x3 K
    distortion: np.ndarray               # (n,) OpenCV pinhole coefficients
    width: int
    height: int
    model: str = "pinhole"
    rotation_deg: int = 0                # image rotation the calibration assumes
    crop: tuple[int, int, int, int] | None = None   # x, y, w, h applied before use
    source: str | None = None
    notes: str | None = None
    metadata: dict = field(default_factory=dict)

    # -- construction ----------------------------------------------------

    @staticmethod
    def from_payload(camera_matrix, distortion, width: int, height: int,
                     model: str = "pinhole", rotation_deg: int = 0,
                     crop=None, source: str | None = None,
                     notes: str | None = None, metadata: dict | None = None) -> "Intrinsics":
        model = (model or "pinhole").strip().lower()
        if model in ("fisheye", "equidistant", "kannala-brandt", "kannala_brandt", "omnidir"):
            raise IntrinsicsError(
                f"The '{model}' distortion model is not supported. Numenor implements the OpenCV "
                "pinhole model only; applying fisheye coefficients as pinhole ones would give "
                "wrong results rather than an error. Re-calibrate with the pinhole model, or "
                "undistort the stream upstream and import the resulting pinhole intrinsics."
            )
        if model not in SUPPORTED_MODELS:
            raise IntrinsicsError(
                f"Unknown distortion model '{model}'. Supported: {sorted(SUPPORTED_MODELS)}."
            )

        K = require_finite(camera_matrix, "Camera matrix").reshape(3, 3) \
            if np.asarray(camera_matrix).size == 9 else None
        if K is None:
            raise IntrinsicsError("The camera matrix K must have nine elements (3x3).")
        if not np.isclose(K[2, 2], 1.0, atol=1e-6):
            raise IntrinsicsError("K[2][2] must be 1. The camera matrix is not normalised.")
        if not (np.isclose(K[1, 0], 0, atol=1e-9) and np.isclose(K[2, 0], 0, atol=1e-9)
                and np.isclose(K[2, 1], 0, atol=1e-9)):
            raise IntrinsicsError("K must be upper-triangular: K[1][0], K[2][0] and K[2][1] must be 0.")
        fx, fy = float(K[0, 0]), float(K[1, 1])
        if fx <= 0 or fy <= 0:
            raise IntrinsicsError("Focal lengths fx and fy must be positive.")

        width, height = int(width), int(height)
        if width < 2 or height < 2:
            raise IntrinsicsError("Calibration image width and height must be positive.")

        cx, cy = float(K[0, 2]), float(K[1, 2])
        if not (-0.5 * width <= cx <= 1.5 * width and -0.5 * height <= cy <= 1.5 * height):
            raise IntrinsicsError(
                f"The principal point ({cx:.1f}, {cy:.1f}) lies far outside a {width}x{height} "
                "image. Check that K matches the stated resolution."
            )

        dist = require_finite(distortion if distortion is not None else [], "Distortion coefficients").ravel()
        if dist.size not in VALID_DISTORTION_LENGTHS:
            raise IntrinsicsError(
                f"A pinhole distortion vector must have one of {sorted(VALID_DISTORTION_LENGTHS)} "
                f"coefficients, got {dist.size}."
            )

        if rotation_deg not in (0, 90, 180, 270):
            raise IntrinsicsError("Image rotation must be 0, 90, 180 or 270 degrees.")

        if crop is not None:
            crop = tuple(int(v) for v in crop)
            if len(crop) != 4:
                raise IntrinsicsError("Crop must be (x, y, width, height).")
            if crop[2] <= 0 or crop[3] <= 0:
                raise IntrinsicsError("Crop width and height must be positive.")

        return Intrinsics(camera_matrix=K, distortion=dist, width=width, height=height,
                          model=model, rotation_deg=rotation_deg, crop=crop,
                          source=source, notes=notes, metadata=metadata or {})

    # -- image-geometry binding -----------------------------------------

    @property
    def geometry_key(self) -> str:
        crop = "full" if not self.crop else "x".join(str(v) for v in self.crop)
        return f"{self.width}x{self.height}@rot{self.rotation_deg}/crop:{crop}"

    def require_matches(self, width: int, height: int,
                        rotation_deg: int = 0, crop=None) -> None:
        """Refuse to use intrinsics on image geometry they were not calibrated for."""
        crop = tuple(int(v) for v in crop) if crop else None
        if (int(width), int(height)) != (self.width, self.height) \
                or int(rotation_deg) != self.rotation_deg or crop != self.crop:
            other_crop = "full" if not crop else "x".join(str(v) for v in crop)
            raise GeometryMismatch(
                f"These intrinsics were calibrated for {self.geometry_key}, but the image is "
                f"{width}x{height}@rot{rotation_deg}/crop:{other_crop}. Scaling a calibration "
                "across resolutions is only valid for a pure resize, and is never valid across a "
                "crop or rotation. Re-calibrate at this geometry, or import intrinsics for it."
            )

    def scaled_to(self, width: int, height: int) -> "Intrinsics":
        """Rescale for a pure resize of the same field of view.

        Only meaningful when the stream is the identical image resampled: the
        aspect ratio must match, and no crop or rotation may be involved.
        """
        if self.crop is not None or self.rotation_deg != 0:
            raise GeometryMismatch(
                "Intrinsics with a crop or rotation cannot be rescaled; the mapping is not a "
                "pure resize. Re-calibrate at the target geometry."
            )
        src_aspect = self.width / self.height
        dst_aspect = width / height
        if abs(src_aspect - dst_aspect) > 1e-3:
            raise GeometryMismatch(
                f"Aspect ratio {dst_aspect:.4f} does not match the calibrated {src_aspect:.4f}. "
                "This is a crop or letterbox, not a resize, so the calibration cannot be scaled."
            )
        sx = width / self.width
        sy = height / self.height
        K = self.camera_matrix.copy()
        K[0, 0] *= sx
        K[0, 2] *= sx
        K[1, 1] *= sy
        K[1, 2] *= sy
        return Intrinsics(camera_matrix=K, distortion=self.distortion.copy(),
                          width=int(width), height=int(height), model=self.model,
                          rotation_deg=self.rotation_deg, crop=None,
                          source=self.source,
                          notes=f"Rescaled from {self.geometry_key} (pure resize).",
                          metadata=dict(self.metadata))

    # -- projection ------------------------------------------------------

    @property
    def has_distortion(self) -> bool:
        return bool(self.distortion.size) and bool(np.any(np.abs(self.distortion) > 1e-12))

    def undistort_points(self, pixels) -> np.ndarray:
        """Distorted pixels -> undistorted pixels in the same K."""
        pts = require_finite(pixels, "Image points").reshape(-1, 1, 2).astype(np.float64)
        out = cv2.undistortPointsIter(pts, self.camera_matrix, self._dist_for_cv(),
                                      None, self.camera_matrix, _UNDISTORT_CRITERIA)
        return out.reshape(-1, 2)

    def pixel_to_camera_ray(self, u: float, v: float) -> np.ndarray:
        """A distorted image pixel -> unit ray in the optical camera frame."""
        pts = np.array([[[float(u), float(v)]]], dtype=np.float64)
        normalised = cv2.undistortPointsIter(pts, self.camera_matrix, self._dist_for_cv(),
                                             None, None, _UNDISTORT_CRITERIA)
        x, y = normalised.reshape(2)
        ray = np.array([x, y, 1.0], dtype=float)
        norm = float(np.linalg.norm(ray))
        if not np.isfinite(norm) or norm < 1e-12:
            raise IntrinsicsError(f"Pixel ({u}, {v}) does not produce a valid camera ray.")
        return ray / norm

    def project_camera_points(self, points_camera) -> tuple[np.ndarray, np.ndarray]:
        """Optical-frame points -> pixels. Returns ``(pixels, in_front)``."""
        pts = require_finite(points_camera, "Camera points").reshape(-1, 3)
        in_front = pts[:, 2] > 1e-9
        pixels = np.full((pts.shape[0], 2), np.nan)
        if np.any(in_front):
            projected, _ = cv2.projectPoints(
                pts[in_front].astype(np.float64),
                np.zeros(3), np.zeros(3),
                self.camera_matrix, self._dist_for_cv(),
            )
            pixels[in_front] = projected.reshape(-1, 2)
        return pixels, in_front

    def contains_pixel(self, u: float, v: float, margin: float = 0.0) -> bool:
        return (-margin <= u <= self.width - 1 + margin
                and -margin <= v <= self.height - 1 + margin)

    def _dist_for_cv(self) -> np.ndarray:
        return self.distortion.astype(np.float64).reshape(1, -1) if self.distortion.size \
            else np.zeros((1, 5), dtype=np.float64)

    # -- serialisation ---------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "camera_matrix": self.camera_matrix.tolist(),
            "distortion_coefficients": self.distortion.tolist(),
            "image_width": self.width,
            "image_height": self.height,
            "image_rotation_deg": self.rotation_deg,
            "crop": list(self.crop) if self.crop else None,
            "geometry_key": self.geometry_key,
            "source": self.source,
            "notes": self.notes,
            "metadata": self.metadata,
        }

    @property
    def fov_degrees(self) -> tuple[float, float]:
        """Horizontal and vertical field of view implied by K, in degrees."""
        fx, fy = self.camera_matrix[0, 0], self.camera_matrix[1, 1]
        h = 2 * np.degrees(np.arctan2(self.width / 2.0, fx))
        v = 2 * np.degrees(np.arctan2(self.height / 2.0, fy))
        return float(h), float(v)


def intrinsics_from_fov(width: int, height: int, horizontal_fov_deg: float) -> Intrinsics:
    """Build *approximate* intrinsics from a nominal field of view.

    This is a placeholder for visualisation only: it assumes a perfect pinhole
    with zero distortion and a centred principal point. It must never be
    presented as a measured calibration.
    """
    if not (1.0 < horizontal_fov_deg < 179.0):
        raise IntrinsicsError("Horizontal field of view must be between 1 and 179 degrees.")
    fx = (width / 2.0) / np.tan(np.radians(horizontal_fov_deg) / 2.0)
    K = np.array([[fx, 0, (width - 1) / 2.0],
                  [0, fx, (height - 1) / 2.0],
                  [0, 0, 1.0]], dtype=float)
    return Intrinsics(camera_matrix=K, distortion=np.zeros(5), width=int(width),
                      height=int(height), model="pinhole",
                      source="approximate_fov",
                      notes=("Approximated from a nominal field of view. Not a measured "
                             "calibration: zero distortion and a centred principal point are "
                             "assumed."))
