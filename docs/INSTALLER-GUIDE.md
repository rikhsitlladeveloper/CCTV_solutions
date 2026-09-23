# Numenor installer guide — positioning and calibration

How to get cameras from "registered" to "validated" in a shared factory
coordinate system. Read the first section before touching anything: almost every
calibration problem is a measurement problem, not a software problem.

---

## 0. What the statuses mean

| Status | Meaning |
| --- | --- |
| **Unconfigured** | No position recorded. |
| **Approximate** | Typed or dragged by hand. Fine for a map pin; not a measurement. |
| **Calibrated, not validated** | Solved from reference points, but nothing independent has confirmed it. |
| **Validated** | Solved *and* checked against held-out points, within your thresholds, over the area those points cover. |
| **Needs recalibration** | Something it depended on changed: the frame, a reference point, or the stream geometry. |

Connection status and calibration status are separate. A camera can be online
and uncalibrated, or calibrated and unreachable.

---

## 1. Define the factory coordinate system

**Factory map → New frame.**

Pick a physical origin you can find again in two years: a column base, a door
threshold, a surveyed floor plate. Write down precisely what it is and which way
the axes run — that description is the only thing tying your numbers to the
building.

* Right-handed, metres. X and Y on the floor, Z up.
* Floor plane is Z = 0 by default.
* Grid extent is just the drawing area; it does not constrain where cameras go.
* Acceptance thresholds decide what counts as Validated. Defaults are 3 px
  reprojection, 0.25 m ground error, 6 reference points.

You do **not** need a floor plan. The grid is blank and metric. A plan image can
be aligned behind it later purely as a backdrop; doing so never moves a camera.

> Changing the origin, the floor height or the site after cameras are calibrated
> redefines what every coordinate means. Numenor refuses that edit unless you
> confirm it, and then marks every affected calibration **needs recalibration**
> rather than silently reinterpreting old numbers.

---

## 2. Survey reference points

**Factory map → Add reference point**, or the API.

These are the ground truth. Everything downstream inherits their error.

* Six or more per camera, spread across the area you care about — not clustered,
  not in a line.
* For full pose recovery, include points at **different heights** (bracket tops,
  rail tops). Points all on the floor leave a two-solution ambiguity.
* Record how you measured each one and its uncertainty. A 5 cm error in a
  reference point is a 5 cm error in everything solved from it.
* Give each a stable code (RP-01, RP-02…). Points are shared between cameras, so
  two cameras can be solved against different subsets of the same survey.

ArUco markers can assist picking points in the image, but a marker ID fixes
nothing in the world. Each marker still needs a surveyed pose or known corner
coordinates, and Numenor requires the marker's physical size before it will
accept the reference.

---

## 3. Choose a method per camera

**Camera → Position & Calibration.**

### A. Manual placement — "get it on the map"
Type X/Y/Z and roll/pitch/yaw. Saved as **Approximate**, permanently. Use it for
site overview, not for measuring anything.

Angle convention, which trips everyone up once:

```
R_world_camera = Rz(yaw) · Ry(pitch) · Rx(roll)
```

applied to the **optical** frame (X right, Y down, Z forward). All-zero rotation
points the camera *straight up at the ceiling*. A wall-mounted camera looking
level along world +Y is **roll −90°, pitch 0°, yaw 0°**. Yaw increases
counter-clockwise seen from above; map heading increases clockwise, so
`heading = −yaw`.

### B. Floor-plane calibration — "where on the floor is that?"
Maps image pixels to floor X/Y via a homography. Needs **no intrinsics**.

* Minimum 4 non-collinear floor points; 6+ spread out is much better.
* Produces **no camera position**, and Numenor will not invent one.
* Only valid for the one plane it was fitted to (normally Z = 0).
* Without intrinsics it fits raw pixels and lens distortion is *not* corrected.
  Accuracy degrades toward the image edges. The result is labelled accordingly.

### C. Full camera pose — "where is the camera, and where does it point?"
Recovers 6-DoF pose with solvePnP. **Requires intrinsics.**

* Minimum 4 points; 6+ at varied heights strongly preferred.
* Planar-only point sets are flagged as ambiguous.
* Solutions putting reference points behind the camera are rejected outright.

---

## 4. Intrinsics (only for method C)

**Position & Calibration → Intrinsics.**

Either import a validated JSON calibration, or run the guided checkerboard
workflow: 9×6 inner corners is a good board, ten or more views at varied tilts
and distances covering the whole frame.

Non-negotiable: intrinsics are bound to one exact image geometry — resolution,
crop, rotation, lens and zoom. Numenor refuses to apply a 1920×1080 calibration
to a 1280×720 substream rather than rescaling it behind your back. If you change
the stream profile, recalibrate.

Only the OpenCV pinhole model is supported. Fisheye calibrations are rejected,
not approximated — applying fisheye coefficients as pinhole ones produces
confidently wrong numbers.

---

## 5. Mark reference points in the image

**Position & Calibration → Reference points.**

Load a frame, pick a point from the registry, click where it appears. Clicks are
recorded in the stream's native pixels.

**Mark at least two points as "held out".** Held-out points are never used to
solve, which makes them the only independent check you have. Without them,
Numenor will report a fitting error but will not call the calibration validated.

---

## 6. Solve, review, activate

Solving creates a **new revision**; it does not activate it. Your working
calibration keeps running until you explicitly switch over. Nothing is ever
overwritten.

Review before activating: the solver reports inliers, outliers, point spread and
any degeneracy it detected. A rejected outlier usually means a mis-click or a
mistyped survey coordinate — fix the cause rather than accepting the fit.

A hand correction on top of a solved pose is saved as a separate, unvalidated
revision, because the solver's validation no longer applies to it.

---

## 7. Validate

**Revisions & validation → Validate.**

Two numbers are reported, and they are not interchangeable:

* **Fitting reprojection error (px)** — how well the solve reproduced its own
  input. A solver can fit its input beautifully and still be wrong. On its own
  this is *not* evidence of real-world accuracy.
* **Held-out ground error (m)** — distance on the floor between where held-out
  points actually are and where the calibration puts them. This is the real
  number, and it applies only to the area those points cover.

Numenor does not produce a confidence percentage, because it has no basis for
one.

---

## 8. Cross-check several cameras

**Geometry check.** Mark the same physical floor spot in two or more cameras and
compare where each places it.

Disagreement in metres tells you the calibrations are inconsistent. Agreement
does **not** prove either is correct — they can share a common error. Only a
surveyed coordinate measures accuracy.

Floor footprints on the map are pure geometry: where the image border meets the
floor plane. They ignore machinery, racking, walls and people. Treat them as
geometric coverage, never as a guarantee that anything is visible.

---

## 9. Export

**Revisions → Export**, or `GET /api/cameras/{id}/calibration/export`.

Self-describing JSON containing the conventions, the frame, `T_world_camera`,
intrinsics, metrics, validation and explicit limitations. It carries no
credentials, host addresses or stream URLs. See
[`calibration-export-example.json`](calibration-export-example.json).

---

## 10. Demo data

```bash
./backend/.venv/bin/python backend/seed_demo.py            # create
./backend/.venv/bin/python backend/seed_demo.py --remove   # delete
```

Builds a synthetic factory with four cameras at known ground-truth poses and
fifteen reference points, then solves them through the real solvers. Every record
is labelled SYNTHETIC and the cameras use documentation-range addresses that
answer nothing. It demonstrates the pipeline; it says nothing about real hardware.

---

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| "needs camera intrinsics" | Method C without intrinsics. Import them, or use floor-plane calibration. |
| "calibrated for 1920x1080, but the image is …" | Stream geometry changed. Recalibrate at the new size. |
| "effectively collinear" | Reference points lie along a line. Spread them out. |
| "puts N reference points behind the camera" | Image clicks and survey points are mismatched or out of order. |
| "maps to the horizon" | You clicked at or above the vanishing line, where floor position is undefined. |
| Ambiguity warning on a planar set | All points at one height. Add points at different heights. |
| 3D view blank or "lost its graphics context" | No hardware acceleration (common over remote desktops). The 2D grid needs no GPU. |
