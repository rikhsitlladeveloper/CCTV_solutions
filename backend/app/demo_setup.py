"""A synthetic worked example of the guided setup flow.

Three cameras looking at one shared floor, calibrated by the same pipeline an
installer uses, with one confirmed overlap and one directed transition.

Everything is generated from known geometry and labelled SYNTHETIC. The accuracy
figures describe the simulation and say nothing about real hardware. Camera
addresses are in the documentation range (192.0.2.0/24), which answers nothing,
so a demo camera can never be mistaken for a working connection.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import floormapping as fm
from .geometry import Pose, pose_from_look_at
from .intrinsics import Intrinsics
from .models import (
    Area, Building, CalibrationMethod, CalibrationRevision, CalibrationStatus, Camera,
    CameraIntrinsics, CameraRelationship, ConnectionType, CoordinateSystem, Floor, MonitoredZone,
    ObservationRole, PixelConvention, PointObservation, PointRole, RelationshipKind, Site,
    TestStatus, ValidationResult, VerificationStatus, WorldReferencePoint, ZoneKind,
)

DEMO_SITE = "Demo Line (synthetic)"
DEMO_WORKSPACE = "Demo Line floor (synthetic)"
SYNTHETIC = (
    "SYNTHETIC DEMO DATA. Generated from known geometry, not measured from real hardware. "
    "Accuracy figures here describe the simulation only."
)

WIDTH, HEIGHT = 1920, 1080
CLICK_NOISE_PX = 0.6        # a realistic hand-clicking error

# A 24 x 16 m bay. Calibration points on a four-by-three survey grid, so
# neighbouring cameras share a band of floor rather than a single line.
# Validation points sit between them, deliberately elsewhere, so they check the
# mapping rather than restate it.
CALIBRATION_POINTS = [
    ("FP-01", "Floor marker, NW corner", 2.0, 2.0),
    ("FP-02", "Column base, W mid", 2.0, 8.0),
    ("FP-03", "Floor marker, SW corner", 2.0, 14.0),
    ("FP-04", "Floor marker, N bay 2", 9.0, 2.0),
    ("FP-05", "Drain cover, centre W", 9.0, 8.0),
    ("FP-06", "Floor marker, S bay 2", 9.0, 14.0),
    ("FP-07", "Floor marker, N bay 3", 15.0, 2.0),
    ("FP-08", "Drain cover, centre E", 15.0, 8.0),
    ("FP-09", "Floor marker, S bay 3", 15.0, 14.0),
    ("FP-10", "Floor marker, NE corner", 22.0, 2.0),
    ("FP-11", "Column base, E mid", 22.0, 8.0),
    ("FP-12", "Floor marker, SE corner", 22.0, 14.0),
]
VALIDATION_POINTS = [
    ("VP-01", "Paint mark, NW aisle", 6.0, 5.0),
    ("VP-02", "Bolt head, centre", 12.0, 11.0),
    ("VP-03", "Paint mark, NE aisle", 18.5, 5.5),
    ("VP-04", "Anchor point, N centre", 11.0, 4.5),
    ("VP-05", "Paint mark, SE aisle", 17.0, 12.0),
]

DEMO_CAMERAS = [
    {"name": "Demo Line West", "host": "192.0.2.21",
     "eye": (2.0, 8.0, 5.6), "look": (19.0, 8.0, 0.0), "area": "Line West"},
    {"name": "Demo Line East", "host": "192.0.2.22",
     "eye": (22.0, 8.0, 5.4), "look": (5.0, 8.0, 0.0), "area": "Line East"},
    {"name": "Demo Dispatch Door", "host": "192.0.2.23",
     "eye": (12.0, 15.2, 5.0), "look": (12.0, 3.0, 0.0), "area": "Dispatch"},
]


def _intrinsics() -> Intrinsics:
    K = np.array([[1390.0, 0.0, 959.0], [0.0, 1388.0, 541.0], [0.0, 0.0, 1.0]])
    return Intrinsics.from_payload(K, np.array([-0.276, 0.079, 0.0005, -0.0003, -0.009]),
                                   WIDTH, HEIGHT, source="checkerboard", notes=SYNTHETIC)


def is_seeded(db: Session) -> bool:
    return db.scalar(select(CoordinateSystem.id)
                     .where(CoordinateSystem.name == DEMO_WORKSPACE)) is not None


def remove_demo_setup(db: Session) -> dict:
    removed = {"cameras": 0, "workspaces": 0, "sites": 0, "relationships": 0}
    cs = db.scalars(select(CoordinateSystem)
                    .where(CoordinateSystem.name == DEMO_WORKSPACE)).first()
    if cs:
        cameras = db.scalars(select(Camera).where(Camera.coordinate_system_id == cs.id)).all()
        ids = [c.id for c in cameras]
        if ids:
            for r in db.scalars(select(CameraRelationship).where(
                    CameraRelationship.camera_a_id.in_(ids))).all():
                db.delete(r)
                removed["relationships"] += 1
        for camera in cameras:
            db.delete(camera)
            removed["cameras"] += 1
        db.delete(cs)
        removed["workspaces"] = 1
    site = db.scalars(select(Site).where(Site.name == DEMO_SITE)).first()
    if site:
        db.delete(site)
        removed["sites"] = 1
    db.commit()
    return removed


def seed_demo_setup(db: Session, rng_seed: int = 20260924) -> dict:
    """Build the worked example. Returns a summary including the ground truth."""
    if is_seeded(db):
        return {"created": False, "reason": f"'{DEMO_WORKSPACE}' already exists."}

    rng = np.random.default_rng(rng_seed)

    site = Site(name=DEMO_SITE)
    db.add(site); db.flush()
    building = Building(site_id=site.id, name="Assembly Building (synthetic)")
    db.add(building); db.flush()
    floor = Floor(building_id=building.id, name="Ground Floor")
    db.add(floor); db.flush()

    areas = {}
    for name in ("Line West", "Line East", "Dispatch"):
        area = Area(floor_id=floor.id, name=name)
        db.add(area); db.flush()
        areas[name] = area

    cs = CoordinateSystem(
        name=DEMO_WORKSPACE, site_id=site.id, floor_id=floor.id,
        origin_description=("SYNTHETIC. Origin at the north-west corner of the bay floor, where "
                            "the painted boundary meets the wall."),
        x_axis_description="X runs east along the north wall; Y runs south into the bay.",
        surface_description="One continuous flat concrete floor across the whole bay.",
        workspace_width_m=24.0, workspace_length_m=16.0,
        grid_min_x=-2.0, grid_max_x=26.0, grid_min_y=-2.0, grid_max_y=18.0,
        grid_spacing_m=1.0, floor_plane_z=0.0,
        max_ground_error_m=0.15, min_reference_points=6,
        notes=SYNTHETIC,
    )
    db.add(cs); db.flush()

    points: dict[str, WorldReferencePoint] = {}
    for code, name, x, y in CALIBRATION_POINTS:
        p = WorldReferencePoint(
            coordinate_system_id=cs.id, code=code, name=f"{name} (synthetic)",
            x=x, y=y, z=0.0, role=PointRole.calibration,
            description=SYNTHETIC, uncertainty_m=0.005,
            measurement_notes="Exact by construction in the simulation.")
        db.add(p); db.flush()
        points[code] = p
    for code, name, x, y in VALIDATION_POINTS:
        p = WorldReferencePoint(
            coordinate_system_id=cs.id, code=code, name=f"{name} (synthetic)",
            x=x, y=y, z=0.0, role=PointRole.validation,
            description=SYNTHETIC + " Reserved for checking, never fitted against.",
            uncertainty_m=0.005,
            measurement_notes="Exact by construction in the simulation.")
        db.add(p); db.flush()
        points[code] = p

    intr = _intrinsics()
    summary_cameras = []
    created: dict[str, Camera] = {}

    for spec in DEMO_CAMERAS:
        truth: Pose = pose_from_look_at(spec["eye"], spec["look"])
        camera = Camera(
            name=spec["name"], host=spec["host"],
            connection_type=ConnectionType.rtsp, rtsp_port=554, stream_path="/synthetic",
            manufacturer="Synthetic Optics", model="DEMO-2", notes=SYNTHETIC,
            area_id=areas[spec["area"]].id, coordinate_system_id=cs.id,
            installation_description=f"Synthetic mount at {spec['eye']} m.",
            last_test_status=TestStatus.untested,
            last_test_detail="Synthetic demo camera; its address answers nothing.",
        )
        db.add(camera); db.flush()
        created[spec["name"]] = camera

        db.add(CameraIntrinsics(
            camera_id=camera.id, label="Synthetic lens calibration", model="pinhole",
            camera_matrix_json=json.dumps(intr.camera_matrix.tolist()),
            distortion_json=json.dumps(intr.distortion.tolist()),
            width=WIDTH, height=HEIGHT, source="checkerboard",
            rms_reprojection_error_px=0.23, view_count=15,
            calibrated_at=datetime.now(timezone.utc),
            lens_description="Synthetic 4 mm fixed", notes=SYNTHETIC, is_active=True))
        db.flush()

        # Which surveyed points this camera can actually see.
        visible: list[tuple[WorldReferencePoint, np.ndarray]] = []
        for point in points.values():
            cam_pt = truth.world_to_camera_points([[point.x, point.y, 0.0]])
            if cam_pt[0, 2] <= 0.5:
                continue
            pixels, in_front = intr.project_camera_points(cam_pt)
            u, v = pixels[0]
            if not in_front[0] or not intr.contains_pixel(u, v, margin=-30):
                continue
            noisy = np.array([u, v]) + rng.normal(0.0, CLICK_NOISE_PX, 2)
            if not intr.contains_pixel(noisy[0], noisy[1]):
                continue
            visible.append((point, noisy))

        for point, pixel in visible:
            db.add(PointObservation(
                camera_id=camera.id, reference_point_id=point.id,
                pixel_u=float(pixel[0]), pixel_v=float(pixel[1]),
                image_width=WIDTH, image_height=HEIGHT,
                role=(ObservationRole.holdout if point.role == PointRole.validation
                      else ObservationRole.fit),
                source="manual", notes="Synthetic match generated from known geometry."))
        db.flush()

        # Run the same pipeline the UI does - nothing is hand-faked.
        inputs = [fm.PointInput(
            reference_point_id=p.id, code=p.code, name=p.name,
            world_x=p.x, world_y=p.y, pixel_u=float(px[0]), pixel_v=float(px[1]),
            role=p.role.value) for p, px in visible]
        result = fm.calculate_floor_mapping(inputs, WIDTH, HEIGHT, intrinsics=intr, plane_z=0.0)
        validation = fm.evaluate_validation(result, cs.max_ground_error_m)

        revision = CalibrationRevision(
            camera_id=camera.id, coordinate_system_id=cs.id,
            coordinate_system_revision=cs.definition_revision, revision_number=1,
            method=CalibrationMethod.homography,
            status=(CalibrationStatus.validated if validation["passed"]
                    else CalibrationStatus.calibrated_unvalidated),
            homography_json=json.dumps(result.H_image_to_floor.tolist()),
            homography_inverse_json=json.dumps(result.H_floor_to_image.tolist()),
            plane_z=0.0,
            pixel_convention=PixelConvention.undistorted,
            distortion_corrected=result.distortion_corrected,
            source_image_width=WIDTH, source_image_height=HEIGHT,
            coverage_polygon_json=json.dumps(result.coverage_polygon),
            solver="guided floor mapping (RANSAC + refit on inliers)",
            metrics_json=json.dumps(result.to_dict()),
            warnings_json=json.dumps(result.warnings + [SYNTHETIC]),
            notes=SYNTHETIC, created_by="demo-seeder",
            is_active=True, activated_at=datetime.now(timezone.utc))
        db.add(revision); db.flush()

        db.add(ValidationResult(
            revision_id=revision.id,
            fit_reprojection_error_px=result.mean_reprojection_px,
            holdout_ground_error_m=validation["summary"]["mean_m"],
            holdout_max_error_m=validation["summary"]["max_m"],
            holdout_point_count=validation["held_out_count"],
            fit_point_count=len(result.used_point_ids),
            inlier_count=len(result.used_point_ids),
            outlier_count=len(result.rejected_point_ids),
            passed=validation["passed"],
            thresholds_json=json.dumps({"acceptance_threshold_m": cs.max_ground_error_m}),
            details_json=json.dumps(validation["held_out"]),
            reviewer="demo-seeder",
            notes=SYNTHETIC))
        db.flush()

        summary_cameras.append({
            "camera": spec["name"],
            "matched_points": len(visible),
            "fitted": len(result.used_point_ids),
            "held_out": validation["held_out_count"],
            "mean_held_out_error_cm": (round(validation["summary"]["mean_m"] * 100, 2)
                                       if validation["summary"]["mean_m"] is not None else None),
            "covered_area_m2": round(result.coverage_area_m2, 1),
            "accuracy_check_passed": validation["passed"],
        })

    # Zones, drawn in image space as an installer would.
    west, east, door = (created["Demo Line West"], created["Demo Line East"],
                        created["Demo Dispatch Door"])
    west_exit = MonitoredZone(
        camera_id=west.id, name="East end of the bay", kind=ZoneKind.exit,
        image_polygon_json=json.dumps([[1350, 620], [1800, 640], [1820, 900], [1330, 880]]),
        image_width=WIDTH, image_height=HEIGHT, coordinate_system_id=cs.id,
        notes=SYNTHETIC)
    door_entrance = MonitoredZone(
        camera_id=door.id, name="Dispatch doorway", kind=ZoneKind.entrance,
        image_polygon_json=json.dumps([[700, 560], [1220, 570], [1240, 860], [690, 850]]),
        image_width=WIDTH, image_height=HEIGHT, coordinate_system_id=cs.id,
        notes=SYNTHETIC)
    db.add_all([west_exit, door_entrance]); db.flush()

    # One confirmed overlap and one directed transition.
    west_cov = json.loads(db.get(CalibrationRevision,
                                 next(r.id for r in west.calibration_revisions)).coverage_polygon_json)
    east_cov = json.loads(db.get(CalibrationRevision,
                                 next(r.id for r in east.calibration_revisions)).coverage_polygon_json)
    shared = rel_overlap(west_cov, east_cov)

    db.add(CameraRelationship(
        kind=RelationshipKind.overlap,
        camera_a_id=west.id, camera_b_id=east.id, coordinate_system_id=cs.id,
        overlap_polygon_json=json.dumps(shared) if shared else None,
        suggested_by_geometry=True,
        verification=VerificationStatus.verified,
        verified_by="demo-seeder", verified_at=datetime.now(timezone.utc),
        notes=SYNTHETIC + " Confirmed on site in the scenario: the two views share the middle "
                          "of the bay.",
        created_by="demo-seeder"))

    db.add(CameraRelationship(
        kind=RelationshipKind.transition,
        camera_a_id=west.id, camera_b_id=door.id, coordinate_system_id=cs.id,
        zone_a_id=west_exit.id, zone_b_id=door_entrance.id,
        min_travel_seconds=4.0, max_travel_seconds=25.0,
        verification=VerificationStatus.verified,
        verified_by="demo-seeder", verified_at=datetime.now(timezone.utc),
        notes=SYNTHETIC + " Walking from the east end of the bay to the dispatch doorway took "
                          "between 4 and 25 seconds in the scenario.",
        created_by="demo-seeder"))

    db.commit()
    return {
        "created": True,
        "warning": SYNTHETIC,
        "workspace": DEMO_WORKSPACE,
        "workspace_id": cs.id,
        "calibration_points": len(CALIBRATION_POINTS),
        "validation_points": len(VALIDATION_POINTS),
        "cameras": summary_cameras,
        "relationships": [
            {"kind": "overlap", "between": ["Demo Line West", "Demo Line East"],
             "overlap_area_m2": round(fm.polygon_area(shared), 1) if shared else None},
            {"kind": "transition", "from": "Demo Line West", "to": "Demo Dispatch Door",
             "travel_seconds": [4.0, 25.0]},
        ],
    }


def rel_overlap(a: list, b: list) -> list:
    from .relationships import polygon_intersection
    return [[round(p[0], 3), round(p[1], 3)] for p in polygon_intersection(a, b)]
