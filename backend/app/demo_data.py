"""Synthetic demonstration data.

Everything this module creates is generated from known ground-truth poses and
labelled as synthetic. It exists to exercise and demonstrate the geometry, and to
give the UI something to draw. It is **not** evidence that any real camera has
been calibrated or that any accuracy figure applies to real hardware.

Cameras are given addresses in the TEST-NET-1 documentation range (192.0.2.0/24),
which no real device answers on, so a demo camera can never be mistaken for a
working connection.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from .calibration import solve_camera_pose, solve_floor_homography
from .geometry import Pose, pose_from_look_at
from .intrinsics import Intrinsics
from .models import (
    Area, Building, CalibrationMethod, CalibrationRevision, CalibrationStatus, Camera,
    CameraIntrinsics, ConnectionType, CoordinateSystem, Floor, ObservationRole, PixelConvention,
    PointObservation, Site, TestStatus, WorldReferencePoint,
)
from .positioning import intrinsics_from_record

DEMO_SYSTEM_NAME = "Demo Plant (synthetic)"
DEMO_SITE_NAME = "Demo Plant (synthetic)"
SYNTHETIC_NOTE = (
    "SYNTHETIC DEMO DATA. Generated from known ground-truth geometry, not measured from real "
    "hardware. Accuracy figures here describe the simulation only."
)

WIDTH, HEIGHT = 1920, 1080
PIXEL_NOISE_PX = 0.4          # a plausible hand-clicking error, for realism


@dataclass
class DemoCamera:
    name: str
    host: str
    eye: tuple[float, float, float]
    look_at: tuple[float, float, float]
    method: str                # "pnp" or "homography"
    area: str


DEMO_CAMERAS = [
    DemoCamera("Demo Packaging North", "192.0.2.11", (2.0, 1.0, 5.2), (12.0, 10.0, 0.0), "pnp", "Packaging"),
    DemoCamera("Demo Packaging South", "192.0.2.12", (22.0, 2.0, 5.0), (10.0, 12.0, 0.0), "pnp", "Packaging"),
    DemoCamera("Demo Assembly East", "192.0.2.13", (23.0, 20.0, 4.6), (9.0, 8.0, 0.0), "pnp", "Assembly"),
    DemoCamera("Demo Dispatch Bay", "192.0.2.14", (3.0, 21.0, 4.2), (14.0, 9.0, 0.0), "homography", "Dispatch"),
]

# Surveyed points, spread across the working area and at varied heights so that
# pose recovery is well conditioned rather than planar-ambiguous.
DEMO_POINTS = [
    ("RP-01", "Column A1 base",        2.0,  2.0, 0.00),
    ("RP-02", "Column A2 base",       12.0,  2.0, 0.00),
    ("RP-03", "Column A3 base",       22.0,  2.0, 0.00),
    ("RP-04", "Column B1 base",        2.0, 12.0, 0.00),
    ("RP-05", "Column B2 base",       12.0, 12.0, 0.00),
    ("RP-06", "Column B3 base",       22.0, 12.0, 0.00),
    ("RP-07", "Column C1 base",        2.0, 20.0, 0.00),
    ("RP-08", "Column C2 base",       12.0, 20.0, 0.00),
    ("RP-09", "Column C3 base",       22.0, 20.0, 0.00),
    ("RP-10", "Floor marker mid-aisle", 7.0,  7.0, 0.00),
    ("RP-11", "Floor marker dispatch", 17.0, 16.0, 0.00),
    ("RP-12", "Column B2 bracket",    12.0, 12.0, 2.40),
    ("RP-13", "Column A2 bracket",    12.0,  2.0, 2.10),
    ("RP-14", "Column C2 bracket",    12.0, 20.0, 2.25),
    ("RP-15", "Rail top, mid-aisle",   7.0,  7.0, 1.60),
]


def _demo_intrinsics() -> Intrinsics:
    K = np.array([[1380.0, 0.0, 958.0],
                  [0.0, 1378.0, 543.0],
                  [0.0, 0.0, 1.0]])
    distortion = np.array([-0.284, 0.083, 0.0006, -0.0004, -0.011])
    return Intrinsics.from_payload(K, distortion, WIDTH, HEIGHT, model="pinhole",
                                   source="checkerboard",
                                   notes=SYNTHETIC_NOTE)


def is_seeded(db: Session) -> bool:
    return db.scalar(select(CoordinateSystem.id)
                     .where(CoordinateSystem.name == DEMO_SYSTEM_NAME)) is not None


def remove_demo_data(db: Session) -> dict:
    """Delete everything the seeder created, leaving real data untouched."""
    removed = {"cameras": 0, "reference_points": 0, "coordinate_systems": 0, "sites": 0}

    cs = db.scalars(select(CoordinateSystem)
                    .where(CoordinateSystem.name == DEMO_SYSTEM_NAME)).first()
    if cs:
        cameras = db.scalars(select(Camera).where(Camera.coordinate_system_id == cs.id)).all()
        for camera in cameras:
            db.delete(camera)
            removed["cameras"] += 1
        removed["reference_points"] = len(cs.reference_points)
        db.delete(cs)
        removed["coordinate_systems"] = 1

    site = db.scalars(select(Site).where(Site.name == DEMO_SITE_NAME)).first()
    if site:
        db.delete(site)
        removed["sites"] = 1

    db.commit()
    return removed


def seed_demo_data(db: Session, rng_seed: int = 20260923) -> dict:
    """Create the synthetic factory. Returns a summary including ground truth."""
    if is_seeded(db):
        return {"created": False, "reason": f"'{DEMO_SYSTEM_NAME}' already exists."}

    rng = np.random.default_rng(rng_seed)

    site = Site(name=DEMO_SITE_NAME)
    db.add(site)
    db.flush()
    building = Building(site_id=site.id, name="Hall 1 (synthetic)")
    db.add(building)
    db.flush()
    floor = Floor(building_id=building.id, name="Ground Floor")
    db.add(floor)
    db.flush()
    areas = {}
    for name in ("Packaging", "Assembly", "Dispatch"):
        area = Area(floor_id=floor.id, name=name)
        db.add(area)
        db.flush()
        areas[name] = area

    cs = CoordinateSystem(
        site_id=site.id,
        name=DEMO_SYSTEM_NAME,
        origin_description=(
            "SYNTHETIC. Origin at the inside corner of columns A1/B1 at floor level. "
            "X runs east along the packaging aisle, Y runs north, Z is up."
        ),
        notes=SYNTHETIC_NOTE,
        floor_plane_z=0.0,
        grid_min_x=-2.0, grid_max_x=28.0, grid_min_y=-2.0, grid_max_y=26.0,
        grid_spacing_m=1.0,
        max_reprojection_error_px=3.0,
        max_ground_error_m=0.25,
        min_reference_points=6,
    )
    db.add(cs)
    db.flush()

    points: dict[str, WorldReferencePoint] = {}
    for code, name, x, y, z in DEMO_POINTS:
        point = WorldReferencePoint(
            coordinate_system_id=cs.id, code=code, name=f"{name} (synthetic)",
            x=x, y=y, z=z,
            description=SYNTHETIC_NOTE,
            measurement_notes="Exact by construction; no survey uncertainty in synthetic data.",
            uncertainty_m=0.0,
        )
        db.add(point)
        db.flush()
        points[code] = point

    intr = _demo_intrinsics()
    summary_cameras = []

    for spec in DEMO_CAMERAS:
        truth: Pose = pose_from_look_at(spec.eye, spec.look_at)

        camera = Camera(
            name=spec.name,
            host=spec.host,
            connection_type=ConnectionType.rtsp,
            rtsp_port=554,
            stream_path="/synthetic",
            manufacturer="Synthetic Optics",
            model="DEMO-1",
            notes=SYNTHETIC_NOTE,
            area_id=areas[spec.area].id,
            coordinate_system_id=cs.id,
            installation_description=f"Synthetic mount at {spec.eye} m.",
            mounting_height_m=spec.eye[2],
            # Never claim a demo camera is reachable.
            last_test_status=TestStatus.untested,
            last_test_detail="Synthetic demo camera; the address is in the documentation range and "
                             "answers nothing.",
        )
        db.add(camera)
        db.flush()

        intrinsics_record = CameraIntrinsics(
            camera_id=camera.id,
            label="Synthetic checkerboard calibration",
            model="pinhole",
            camera_matrix_json=json.dumps(intr.camera_matrix.tolist()),
            distortion_json=json.dumps(intr.distortion.tolist()),
            width=WIDTH, height=HEIGHT,
            source="checkerboard",
            rms_reprojection_error_px=0.21,
            view_count=14,
            calibrated_at=datetime.now(timezone.utc),
            lens_description="Synthetic 4 mm fixed",
            notes=SYNTHETIC_NOTE,
            is_active=True,
        )
        db.add(intrinsics_record)
        db.flush()

        # Which surveyed points this camera can actually see.
        visible: list[tuple[WorldReferencePoint, np.ndarray]] = []
        for point in points.values():
            cam_pt = truth.world_to_camera_points([[point.x, point.y, point.z]])
            if cam_pt[0, 2] <= 0.5:
                continue
            pixels, in_front = intr.project_camera_points(cam_pt)
            u, v = pixels[0]
            if not in_front[0] or not intr.contains_pixel(u, v, margin=-20):
                continue
            noisy = np.array([u, v]) + rng.normal(0.0, PIXEL_NOISE_PX, 2)
            if not intr.contains_pixel(noisy[0], noisy[1]):
                continue
            visible.append((point, noisy))

        if len(visible) < 6:
            db.delete(camera)
            continue

        # Reserve a third of the visible points for independent validation.
        holdout_count = max(2, len(visible) // 3)
        holdout_ids = {p.id for p, _ in visible[-holdout_count:]}

        for point, pixel in visible:
            db.add(PointObservation(
                camera_id=camera.id,
                reference_point_id=point.id,
                pixel_u=float(pixel[0]), pixel_v=float(pixel[1]),
                image_width=WIDTH, image_height=HEIGHT,
                role=ObservationRole.holdout if point.id in holdout_ids else ObservationRole.fit,
                source="manual",
                notes="Synthetic observation generated from ground truth.",
            ))
        db.flush()

        fit = [(p, px) for p, px in visible if p.id not in holdout_ids]
        revision = _solve_demo_revision(db, camera, cs, intrinsics_record, intr, spec, fit, truth)
        summary_cameras.append({
            "camera": spec.name,
            "method": spec.method,
            "ground_truth_position_m": [round(c, 3) for c in spec.eye],
            "recovered_position_m": (
                [round(float(c), 3) for c in (revision.position_x, revision.position_y, revision.position_z)]
                if revision.position_x is not None else None
            ),
            "observations": len(visible),
            "held_out": holdout_count,
            "status": revision.status.value,
        })

    db.commit()
    return {
        "created": True,
        "warning": SYNTHETIC_NOTE,
        "coordinate_system": DEMO_SYSTEM_NAME,
        "coordinate_system_id": cs.id,
        "reference_points": len(points),
        "cameras": summary_cameras,
    }


def _solve_demo_revision(db: Session, camera: Camera, cs: CoordinateSystem,
                         intrinsics_record: CameraIntrinsics, intr: Intrinsics,
                         spec: DemoCamera, fit, truth: Pose) -> CalibrationRevision:
    """Solve the demo camera the same way the API would, so nothing is hand-faked."""
    world = np.array([[p.x, p.y, p.z] for p, _ in fit], dtype=float)
    pixels = np.array([px for _, px in fit], dtype=float)

    if spec.method == "pnp":
        result = solve_camera_pose(world, pixels, intr, WIDTH, HEIGHT)
        q = result.pose.quaternion
        revision = CalibrationRevision(
            camera_id=camera.id, coordinate_system_id=cs.id,
            coordinate_system_revision=cs.definition_revision,
            revision_number=1,
            method=CalibrationMethod.pnp,
            status=CalibrationStatus.calibrated_unvalidated,
            position_x=float(result.pose.position[0]),
            position_y=float(result.pose.position[1]),
            position_z=float(result.pose.position[2]),
            quat_w=float(q[0]), quat_x=float(q[1]), quat_y=float(q[2]), quat_z=float(q[3]),
            plane_z=0.0,
            pixel_convention=PixelConvention.raw,
            distortion_corrected=True,
            source_image_width=WIDTH, source_image_height=HEIGHT,
            intrinsics_id=intrinsics_record.id,
            approx_range_m=40.0,
            solver=result.solver,
            metrics_json=json.dumps(result.to_dict()),
            warnings_json=json.dumps(result.warnings + [SYNTHETIC_NOTE]),
            notes=SYNTHETIC_NOTE,
            created_by="demo-seeder",
            is_active=True,
            activated_at=datetime.now(timezone.utc),
        )
    else:
        floor_mask = np.abs(world[:, 2]) < 1e-6
        result = solve_floor_homography(pixels[floor_mask], world[floor_mask][:, :2],
                                        WIDTH, HEIGHT, intrinsics=intr, plane_z=0.0)
        revision = CalibrationRevision(
            camera_id=camera.id, coordinate_system_id=cs.id,
            coordinate_system_revision=cs.definition_revision,
            revision_number=1,
            method=CalibrationMethod.homography,
            status=CalibrationStatus.calibrated_unvalidated,
            homography_json=json.dumps(result.H_image_to_floor.tolist()),
            homography_inverse_json=json.dumps(result.H_floor_to_image.tolist()),
            plane_z=0.0,
            pixel_convention=PixelConvention.undistorted,
            distortion_corrected=result.distortion_corrected,
            source_image_width=WIDTH, source_image_height=HEIGHT,
            intrinsics_id=intrinsics_record.id,
            solver=result.method,
            metrics_json=json.dumps(result.to_dict()),
            warnings_json=json.dumps(result.warnings + [SYNTHETIC_NOTE]),
            notes=SYNTHETIC_NOTE + " Floor-plane only: this camera has no recovered pose.",
            created_by="demo-seeder",
            is_active=True,
            activated_at=datetime.now(timezone.utc),
        )

    db.add(revision)
    db.flush()
    return revision
