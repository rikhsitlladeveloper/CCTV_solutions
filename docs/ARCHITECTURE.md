# Architecture

Numenor is a single on-premise service: a FastAPI backend that also serves the
built React frontend, with SQLite for storage and ffmpeg for media. There is no
external dependency at runtime — no cloud service, no message broker, no GPU.

```
                    browser (React + TypeScript)
                              │  HTTPS/HTTP over LAN or Tailscale
                              ▼
        ┌─────────────────────────────────────────────┐
        │  FastAPI (uvicorn)                          │
        │                                             │
        │  routers/ ── auth, cameras, locations,      │
        │              floorplans, preview,           │
        │              coordinates, calibration,      │
        │              factorymap                     │
        │       │                                     │
        │       ├── probe ── onvif ──┐                │
        │       ├── media ── ffmpeg ─┤ camera network │
        │       ├── calibration ── OpenCV             │
        │       ├── positioning ── geometry           │
        │       │                                     │
        │  netguard  (every outbound call passes here)│
        │  crypto / security  (keys outside the DB)   │
        └───────────────┬─────────────────────────────┘
                        │
              SQLite ───┴─── data/  (floor plans, photos)
```

---

## Backend modules

| Module | Lines | Responsibility |
| --- | ---: | --- |
| `geometry.py` | ~540 | Frames, rotations, poses, ray/plane intersection. **The single source of every convention.** |
| `floormapping.py` | 500 | The guided pipeline: image-to-floor homography, coverage, held-out scoring. |
| `scene.py` | 408 | The factory scene: object palette and footprints, visual placement to pose, function availability, readiness. |
| `relationships.py` | 240 | Graph validation, overlap suggestion, polygon intersection. |
| `markers.py` | 220 | Optional ArUco assistance; proposes matches, never asserts positions. |
| `jobs.py` | 100 | In-process job runner so long work has one honest progress pattern. |
| `calibration.py` | 643 | Solvers: floor homography, solvePnP pose, checkerboard intrinsics. |
| `positioning.py` | 597 | Projection with a stored calibration, validation scoring, export, multi-camera comparison. |
| `intrinsics.py` | 258 | The pinhole model, distortion, and binding a calibration to one image geometry. |
| `models.py` | 504 | SQLAlchemy tables. |
| `media.py` | 367 | ffmpeg snapshots, MJPEG preview sessions, credential sanitising. |
| `onvif.py` | 293 | Minimal ONVIF SOAP client with WS-Security digest. |
| `probe.py` | 179 | Connection testing; separates login success from stream success. |
| `netguard.py` | 108 | Outbound host and port policy. |
| `security.py` | 167 | Operator auth: scrypt hashing, HMAC bearer tokens. |
| `crypto.py` | 52 | Fernet encryption of camera passwords. |
| `migrations.py` | 119 | Versioned, idempotent schema migrations. |
| `demo_data.py` | 342 | Synthetic factory, solved through the real solvers. |

Routers are thin: they validate, call a service module, and serialise. The
arithmetic lives in `geometry`, `calibration`, `intrinsics` and `positioning`,
which have no knowledge of HTTP or the database.

---

## Why `geometry.py` is the only source of conventions

The 2D map, the 3D view, the solvers, the projection tools and the export all
need to agree about what "where the camera points" means. If each derived its
own answer they would drift, and the drift would be invisible — a mirrored
camera still looks plausible on a map.

So one module defines it, and everything else imports from there:

* **World frame** — right-handed, metres. X and Y on the floor, Z up.
* **Camera optical frame** — OpenCV: X right, Y down, Z forward.
* **Authoritative pose** — `T_world_camera`, stored as a normalised quaternion
  plus the camera centre. RPY is an editable *view* of it, never the storage.
* **RPY composition** — `R_world_camera = Rz(yaw) · Ry(pitch) · Rx(roll)`.
* **Map heading** — 0° toward world +Y, increasing clockwise.

Two consequences are counter-intuitive enough that they are asserted in tests so
they cannot silently change:

1. **Zero rotation looks straight up.** Identity aligns the optical axes with the
   world axes, so +Z optical (forward) points along +Z world (up). A level
   wall-mounted camera is roll −90°.
2. **Yaw and heading run opposite ways.** Yaw is right-handed about +Z, so from
   above it increases counter-clockwise; map heading increases clockwise. Hence
   `heading = (−yaw) mod 360`. Substituting one for the other mirrors every
   camera on the map.

`CONVENTIONS` is a dictionary in that module, served at
`GET /api/coordinate-systems/conventions` and rendered in the UI, so the
documentation and the code cannot disagree.

---

## Request flows

### Connection test

```
POST /api/cameras/{id}/test
   → probe.test_camera
       → netguard.resolve_camera_host      vet + pin the IP
       → ONVIF: GetSystemDateAndTime, GetDeviceInformation,
                GetCapabilities, GetProfiles, GetStreamUri
       → media.grab_rtsp_frame             ffmpeg decodes one frame
   → status: online | partial | auth_failed | unreachable | timeout | error
```

`partial` exists because an ONVIF login succeeding says nothing about whether
video works. The two are probed separately and reported separately.

### Live preview

RTSP is not playable in a browser, and nothing here pretends otherwise.

```
POST /api/preview/{id}/start   → reserve a session, resolve the RTSP URL
GET  /api/preview/{sid}/stream → ffmpeg RTSP → MJPEG → multipart/x-mixed-replace
POST /api/preview/{sid}/stop   → terminate the process
```

Sessions are capped, idle-reaped (30 s default), lifetime-capped (10 min), and
torn down on client disconnect. ffmpeg is always launched with an argument list,
never a shell, so nothing in a camera record can be interpolated into a command.

### The guided floor mapping

Most installations never need a camera pose. They need to turn a pixel into a
floor position, and that is a flat-plane homography:

```
PUT  /api/setup/cameras/{id}/matches       pixels ↔ surveyed points
POST /api/setup/cameras/{id}/calculate-mapping
   → read the image geometry
   → undistort with stored intrinsics if they match, else keep raw pixels
   → findHomography(floor → image, RANSAC)   threshold genuinely in pixels
   → refit on inliers alone
   → check conditioning, invertibility, degeneracy
   → coverage polygon = convex hull of the inliers
   → new revision, inactive
POST .../check-accuracy      held-out points only
POST .../activate-mapping    explicit
```

`H_image_to_floor` maps homogeneous image coordinates to floor XY. Exactly one
pixel space is used — `raw` or `undistorted` with the stored camera matrix —
recorded on the result and applied identically in calibration, projection,
validation and export. Normalised camera coordinates are never mixed in.

**A homography is not a pose.** It describes one plane and cannot say where the
camera is, so no XYZ or orientation is derived from it. Cameras mapped this way
appear on the map as their *covered floor area*, not as a marker.

### Solving a pose

```
PUT  /api/cameras/{id}/observations          pixels ↔ surveyed points
POST /api/cameras/{id}/calibration/pose
   → intrinsics.require_matches              refuse a geometry mismatch
   → calibration.solve_camera_pose
       degeneracy checks → solvePnPRansac → refineLM
       → reject solutions with points behind the camera
   → Pose.from_cv_world_to_camera            R_wc = R_cwᵀ, C = −R_wc·t_cw
   → new CalibrationRevision (inactive)
POST .../revisions/{rid}/activate            explicit
POST .../revisions/{rid}/validate            against held-out points
```

A solve never activates itself. The working calibration keeps running until
someone switches over, and no revision is ever overwritten.

---

## Data model

```
Site ─< Building ─< Floor ─< Area ─< Camera
                     │                 │
                     └─ FloorPlan      ├─ CameraPlacement   (floor-plan marker)
                                       ├─ CameraIntrinsics  (per image geometry)
                                       ├─ PointObservation ─> WorldReferencePoint
                                       └─ CalibrationRevision ─< ValidationResult
                                                │
CoordinateSystem ─< WorldReferencePoint         │
       └────────────────────────────────────────┘
```

### Three independent notions of "verified"

These are deliberately separate columns and are never conflated in the UI:

| Field | Question it answers |
| --- | --- |
| `Camera.last_test_status` | Did the backend reach the device? |
| `CameraPlacement.review_status` | Did a human confirm the marker against the current plan image? |
| `CalibrationRevision.status` | Was the geometry solved, and independently validated? |

A camera can be online and uncalibrated, or calibrated and unreachable.

### Three relationship states

The graph distinguishes them deliberately:

* **unknown** — no record; nothing established either way.
* **allowed** — an overlap or transition someone entered.
* **excluded** — an explicit record that two views have no *direct* association.
  Travel via other cameras remains possible.

A missing record is never read as a confirmed impossibility.

### Coordinate-system integrity

Coordinates only mean something relative to a physical origin. Three guards keep
that honest:

* Editing a frame's origin, floor height or site is refused unless
  `confirm_redefinition` is set; then `definition_revision` increments and every
  active calibration in the frame becomes `needs_recalibration`.
* Re-measuring a reference point flags calibrations solved from it.
* Moving a camera to a different frame invalidates calibrations from the old one.
* Changing a point's role changes the fitting/validation split, so mappings using
  it go stale.
* Redrawing a zone marks relationships built on it for review.

Nothing is silently reinterpreted, and a stale mapping cannot be activated.

---

## Frontend

React 19 + TypeScript, built by Vite, served as static files by the backend.

| Area | Module | Lines |
| --- | --- | ---: |
| Commissioning workspace | `pages/WorkspacePage.tsx` | ~1816 |
| Align live view | `pages/AlignPage.tsx` | ~623 |
| Capability readiness | `pages/ReadinessPage.tsx` | ~389 |
| Guided setup | `pages/SetupPage.tsx` | ~1503 |
| Camera relationships | `pages/RelationshipsPage.tsx` | ~600 |
| Position & Calibration (advanced) | `pages/CameraCalibrationPage.tsx` | 1321 |
| Registration wizard | `pages/RegisterWizardPage.tsx` | 998 |
| Factory map | `pages/FactoryMapPage.tsx` | 543 |
| Camera detail | `pages/CameraDetailPage.tsx` | 529 |
| Floor-plan editor | `pages/FloorPlanEditorPage.tsx` | 498 |
| Metric grid (Konva) | `components/FactoryGrid.tsx` | 333 |
| Floor-plan canvas (SVG) | `components/FloorPlanCanvas.tsx` | 321 |
| 3D view (Three.js) | `components/Scene3D.tsx` | 224 |
| Image point picker | `components/CameraImagePicker.tsx` | 216 |
| Scene canvas, 2D (Konva) | `components/SceneCanvas2D.tsx` | 517 |
| Scene view, 3D (Three.js) | `components/SceneView3D.tsx` | 297 |
| Line and zone drawing | `components/PictureGeometryEditor.tsx` | 224 |

Three rendering technologies, each chosen for its job: SVG for the floor-plan
editor (few elements, crisp text), Konva for the metric grid (many markers,
continuous pan/zoom), Three.js for the 3D view.

`Scene3D` is lazy-loaded — Three.js is roughly 250 kB gzipped and most sessions
never open it, so the main bundle stays at ~222 kB gzipped.

### Installer vocabulary

The default flow states everything in what an installer measures and clicks.
Coordinates, rotations, matrices, solver settings and transform conventions live
behind **Advanced** disclosures or on the advanced calibration page. Three badges
are kept separate — connection, calibration, validation — because conflating them
is how a camera ends up trusted for something it was never checked for.

### Two frontend gotchas worth knowing

* **3D labels are sprites, not 3D text and not DOM.** drei's `<Text>` fetches a
  font over the network and suspends the whole canvas until it resolves — on an
  offline factory install, a permanently blank view. drei's `<Html>` avoids that
  but mounts a second React root per label, which throws
  `NotFoundError: removeChild` when the canvas unmounts mid-render (switching to
  Monitor, or back to 2D). `SceneView3D` paints label text into a 2D canvas and
  shows it as a sprite: no network, no DOM, nothing to tear down.
* **Graphics-engine cameras differ from optical cameras.** Three.js looks down
  −Z with +Y up; OpenCV looks down +Z with +Y down. The 3D views build frustums
  from the optical basis vectors explicitly rather than hiding the flip.
* **With a placement tool armed, the scene's shape layers stop listening.**
  Konva shapes cancel bubbling when clicked, so an armed "place camera" click
  that landed on a machine — exactly where cameras point — would otherwise be
  swallowed and do nothing at all.

---

## Security boundaries

Detailed in [SECURITY.md](SECURITY.md). In outline:

* Camera passwords are Fernet-encrypted; the key lives outside the database and
  outside the repository. No API response can carry a password or a
  credential-bearing URL.
* Every outbound request passes `netguard`: private ranges allowed, loopback,
  metadata and public addresses refused, infrastructure ports blocked, the
  resolved IP pinned, redirects not followed.
* All configuration and preview routes require authentication.

---

## Deliberate constraints

| Constraint | Reason |
| --- | --- |
| No floor plan required | Cameras are positioned on a blank metric grid. A plan is only ever a backdrop. |
| A homography yields no pose | It genuinely does not contain that information, so none is fabricated. |
| Intrinsics bound to one geometry | A calibration is not transferable across resolution, crop, rotation or zoom. |
| Pinhole only | Fisheye coefficients applied as pinhole produce confidently wrong output. |
| Fitting error ≠ accuracy | Reported separately from held-out ground error; no confidence percentage is invented. |
| Coverage is geometric | Footprints ignore occlusion entirely. |
