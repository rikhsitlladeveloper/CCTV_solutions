"""Optional ArUco assistance for matching floor points.

Marker detection only ever *proposes* correspondences. A marker ID is a label,
not a location: it tells you which marker you are looking at and nothing about
where that marker is in the building. World coordinates come from the surveyed
reference points, exactly as they do for manual matching.

Every proposal is reviewed before it becomes an observation, and the whole
manual workflow keeps working with no markers at all.

Note this is a different job from ChArUco *lens* calibration: that moves a board
around to learn the lens, and its board pose is irrelevant. Markers here are
fixed to the floor and surveyed.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import cv2
import numpy as np

log = logging.getLogger("numenor.markers")

# Dictionaries offered in the UI. 4x4 is easiest to print and detect at range;
# larger families reduce the chance of a false ID at the cost of resolution.
SUPPORTED_DICTIONARIES: dict[str, int] = {
    "DICT_4X4_50": cv2.aruco.DICT_4X4_50,
    "DICT_4X4_100": cv2.aruco.DICT_4X4_100,
    "DICT_4X4_250": cv2.aruco.DICT_4X4_250,
    "DICT_5X5_50": cv2.aruco.DICT_5X5_50,
    "DICT_5X5_100": cv2.aruco.DICT_5X5_100,
    "DICT_5X5_250": cv2.aruco.DICT_5X5_250,
    "DICT_6X6_50": cv2.aruco.DICT_6X6_50,
    "DICT_6X6_250": cv2.aruco.DICT_6X6_250,
    "DICT_APRILTAG_36h11": cv2.aruco.DICT_APRILTAG_36h11,
}

# Below this the corner positions are too coarse to be worth a correspondence.
MIN_MARKER_SIDE_PX = 18.0
# A marker seen very obliquely has poorly-conditioned corners.
MAX_ASPECT_SKEW = 3.0


class MarkerError(ValueError):
    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint


@dataclass
class DetectedMarker:
    marker_id: int
    corners_px: list[list[float]]     # 4 corners, clockwise from top-left
    centre_px: list[float]
    side_px: float
    known: bool = False
    accepted: bool = False
    reference_point_id: int | None = None
    reference_code: str | None = None
    corner_index: int | None = None
    world_xy: list[float] | None = None
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "marker_id": self.marker_id,
            "corners_px": [[round(c, 2) for c in corner] for corner in self.corners_px],
            "centre_px": [round(c, 2) for c in self.centre_px],
            "side_px": round(self.side_px, 1),
            "known": self.known,
            "accepted": self.accepted,
            "reference_point_id": self.reference_point_id,
            "reference_code": self.reference_code,
            "corner_index": self.corner_index,
            "world_xy": self.world_xy,
            "issues": self.issues,
        }


def detect_markers(image_bgr: np.ndarray, dictionary: str,
                   known_points: list[dict]) -> dict:
    """Find markers and pair them with surveyed points of the same ID.

    ``known_points`` are registry entries carrying ``aruco_marker_id`` and, where
    given, ``aruco_corner_index``. A point with a corner index is matched to that
    corner; without one, the marker centre is used.
    """
    if dictionary not in SUPPORTED_DICTIONARIES:
        raise MarkerError(
            f"'{dictionary}' is not a supported marker family.",
            hint=f"Choose one of: {', '.join(sorted(SUPPORTED_DICTIONARIES))}.",
        )
    if image_bgr is None or image_bgr.size == 0:
        raise MarkerError("The image could not be read.")

    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY) if image_bgr.ndim == 3 else image_bgr
    aruco_dict = cv2.aruco.getPredefinedDictionary(SUPPORTED_DICTIONARIES[dictionary])
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    detector = cv2.aruco.ArucoDetector(aruco_dict, params)

    corners, ids, _ = detector.detectMarkers(gray)

    # Index the registry by marker id. More than one surveyed point may refer to
    # the same marker when separate corners were measured.
    by_id: dict[int, list[dict]] = {}
    for point in known_points:
        marker_id = point.get("aruco_marker_id")
        if marker_id is not None:
            by_id.setdefault(int(marker_id), []).append(point)

    detected: list[DetectedMarker] = []
    seen_ids: dict[int, int] = {}

    if ids is not None:
        for quad, marker_id in zip(corners, ids.ravel()):
            pts = quad.reshape(4, 2).astype(float)
            marker_id = int(marker_id)
            sides = [float(np.linalg.norm(pts[i] - pts[(i + 1) % 4])) for i in range(4)]
            side = float(np.mean(sides))
            entry = DetectedMarker(
                marker_id=marker_id,
                corners_px=[[float(x), float(y)] for x, y in pts],
                centre_px=[float(pts[:, 0].mean()), float(pts[:, 1].mean())],
                side_px=side,
            )

            seen_ids[marker_id] = seen_ids.get(marker_id, 0) + 1

            if side < MIN_MARKER_SIDE_PX:
                entry.issues.append(
                    f"Only {side:.0f} px across — too small for a reliable corner position. "
                    "Move the marker closer or print it larger."
                )
            if max(sides) / max(1e-6, min(sides)) > MAX_ASPECT_SKEW:
                entry.issues.append(
                    "Seen at a very oblique angle, so its corners are poorly resolved."
                )

            candidates = by_id.get(marker_id, [])
            if not candidates:
                entry.issues.append(
                    f"Marker {marker_id} is not in the reference-point registry, so its world "
                    "position is unknown. Survey it and add it before using it."
                )
            else:
                entry.known = True
                point = candidates[0]
                entry.reference_point_id = point["id"]
                entry.reference_code = point["code"]
                entry.world_xy = [point["x"], point["y"]]
                corner_index = point.get("aruco_corner_index")
                if corner_index is not None:
                    entry.corner_index = int(corner_index)
                    entry.centre_px = entry.corners_px[int(corner_index)]
                if len(candidates) > 1:
                    entry.issues.append(
                        f"{len(candidates)} registry points claim marker {marker_id}. Give each "
                        "one a distinct corner index, or fix the duplicate."
                    )
            detected.append(entry)

    # The same physical ID appearing twice means two printed copies — ambiguous.
    for entry in detected:
        if seen_ids.get(entry.marker_id, 0) > 1:
            entry.known = False
            entry.issues.append(
                f"Marker {entry.marker_id} appears {seen_ids[entry.marker_id]} times in this "
                "image. Duplicate printed markers cannot be told apart; remove one."
            )

    usable = [d for d in detected if d.known and not d.issues]
    for entry in usable:
        entry.accepted = False      # proposals only; the installer confirms

    warnings: list[str] = []
    if not detected:
        warnings.append(
            "No markers of that family were found. Check the dictionary, the lighting, and that "
            "the markers are fully visible and in focus."
        )
    elif not usable:
        warnings.append(
            "Markers were found, but none can be used yet. See the notes against each one."
        )
    elif len(usable) < 4:
        warnings.append(
            f"{len(usable)} usable marker(s). At least 4 well-spread correspondences are needed "
            "for a mapping — add more markers or match the rest by hand."
        )

    if len(usable) >= 3:
        pts = np.array([d.centre_px for d in usable])
        centred = pts - pts.mean(axis=0)
        sv = np.linalg.svd(centred, compute_uv=False)
        if sv[0] > 1e-9 and sv[1] / sv[0] < 0.15:
            warnings.append(
                "The usable markers lie nearly in a straight line, which cannot define a mapping. "
                "Spread them across the floor."
            )
        span_x = (pts[:, 0].max() - pts[:, 0].min()) / max(1, gray.shape[1])
        span_y = (pts[:, 1].max() - pts[:, 1].min()) / max(1, gray.shape[0])
        if span_x < 0.3 or span_y < 0.3:
            warnings.append(
                "The markers cover only a small part of the frame. The mapping will be poor "
                "elsewhere in the view."
            )

    return {
        "dictionary": dictionary,
        "image_width": int(gray.shape[1]),
        "image_height": int(gray.shape[0]),
        "detected": [d.to_dict() for d in detected],
        "usable_count": len(usable),
        "warnings": warnings,
        "note": (
            "These are proposals. A marker ID identifies which marker it is, not where it is — "
            "the world position comes from the surveyed registry entry. Review each pairing "
            "before accepting it, and make sure every marker used for floor mapping lies flat "
            "on the floor being mapped."
        ),
    }
