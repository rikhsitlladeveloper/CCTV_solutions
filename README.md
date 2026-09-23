# Numenor — Camera Registration and Location Setup

An on-premise dashboard for registering factory cameras, verifying that they
actually answer, and recording where they are installed — including an
approximate position and viewing direction on a floor plan.

Everything runs on your own hardware: the database, the uploaded floor plans,
the credential encryption key and all video processing. Nothing is sent to an
external service.

A guided seven-step setup takes an installer from a camera on the wall to a
system that reports positions in metres on the floor — **connect cameras, create
an area, add measured floor points, match them in each picture, calculate the
mapping, check the accuracy, connect the cameras together** — without ever asking
for a coordinate or a rotation. Full 3D calibration remains available under
Advanced.

**Scope.** Registration, connection testing, preview, physical location,
floor-plan placement, and camera geometry/calibration. It deliberately does *not*
do AI detection, person tracking, re-identification, or analytics. Calibration is
exported so those services can consume it.

---

## Quick start

```bash
./setup.sh     # creates the venv, installs dependencies, builds the frontend
./start.sh     # serves the API and the UI on http://127.0.0.1:8000
```

On first start-up the server creates an operator account and prints a randomly
generated password **once** to the console. Copy it before you lose the
scrollback — it is stored hashed and cannot be read back later:

```
====================================================================
  Numenor operator account created.
  username: operator
  password: <a random password appears here — yours will differ>
  This password is shown once. Set NUMENOR_ADMIN_PASSWORD to choose your own.
====================================================================
```

To pick your own password instead, set it before starting:

```bash
NUMENOR_ADMIN_PASSWORD='your-choice' ./start.sh
```

**Lost the password?** It is stored as a scrypt hash, so it cannot be
recovered — only replaced. Start once with `NUMENOR_ADMIN_PASSWORD` set as
above; that overwrites the account, and plain `./start.sh` works from then on.
Camera credentials are unaffected: they are encrypted with a separate key.

Requirements: Python 3.10+, Node 18+, and `ffmpeg` (used for snapshots and
browser-compatible preview).

### Development

```bash
./backend/run.sh                 # API on :8000
cd frontend && npm run dev       # Vite dev server on :5173, proxies /api to :8000
./backend/test.sh                # 145 backend tests
```

### Documentation

| | |
| --- | --- |
| [Installer guide](docs/INSTALLER-GUIDE.md) | Commissioning and calibrating cameras on site |
| [Operations](docs/OPERATIONS.md) | Install, configure, expose, back up, upgrade, troubleshoot |
| [Architecture](docs/ARCHITECTURE.md) | How it fits together, and the conventions everything derives from |
| [API reference](docs/API.md) | Every endpoint, with request and response shapes |
| [Security](docs/SECURITY.md) | Threat model, credential handling, network policy |
| [Development](docs/DEVELOPMENT.md) | Working on the code |

Full index: [docs/](docs/README.md).

---

## What the screens do

### Dashboard
Summary cards for registered, reachable, failed and awaiting-placement cameras,
each doubling as a filter. The table lists name, address, building, floor, area,
connection status, placement status and the last check, with search and filters
by location and status. Row actions: test, preview, place on map, edit, delete.

A camera is **never** shown as online because it was saved. It shows
“Not tested” until a real connection test succeeds, and afterwards
“Last verified at …”. If only the last test failed, it shows “Last checked …”.

### Registration wizard
1. **Camera & connection** — name, address, credentials, ONVIF or manual RTSP,
   ports and paths. Test the connection, fetch ONVIF stream profiles, pick one,
   and take a snapshot preview. ONVIF failure never blocks manual RTSP
   registration, and an unverified camera can be saved with a clear status.
2. **Physical location** — site, building, floor, area, installation
   description, optional mounting height and installation photo. Any name can be
   created inline.
3. **Position & direction** — select or upload a floor plan, click to place the
   marker, rotate the heading, and adjust the translucent viewing sector, with
   optional field-of-view angle and estimated viewing distance.
4. **Review & save** — the full summary with the password masked, and an edit
   link back to each step.

A floor plan is optional throughout. Register now, place later.

### Floor-plan editor
PNG/JPEG upload, zoom, pan, fit and reset, labelled markers, a side panel for
the selected camera, drag-to-move, rotate-by-handle, a list of cameras not yet
placed, and one plan per building floor. Optionally set a map scale by clicking
two points and entering the real distance between them.

### Camera detail
Name and location, connection status with the last test timestamp, snapshot and
live preview, stream/profile details, the marker and direction on the plan,
installation notes, and edit/retest actions. Connection status, factory
calibration status and floor-plan placement are shown as three separate facts.

### Guided setup
The default installer flow. Each step states what to do in plain terms, warns
about the mistakes that actually happen (points in a line, clustered in one
corner, matched against the wrong stream size), and keeps the technical detail
behind Advanced disclosures. Calculating a mapping runs as a job with progress
and a plain-language failure; nothing is put into service until explicitly
activated.

### Camera relationships
Which views share ground, and which exits lead where, with plausible walking
times. Three states are distinguishable: unknown, allowed and explicitly
excluded — a missing record is never read as impossible. Zones can be drawn
straight on a camera picture with no calibration at all.

### Factory map
Every camera in one blank metric grid — no floor plan required. Zoom, pan, fit to
grid or to cameras, camera markers with heading and geometric floor coverage,
surveyed reference points, and an optional 3D view with coordinate axes and
viewing frustums. A floor-plan image can be aligned behind the grid as a backdrop;
doing so never moves a world coordinate.

### Position & calibration
Per camera: choose manual placement, floor-plane calibration or full pose
recovery; import or measure intrinsics; mark surveyed reference points in the
image; solve; validate against held-out points; test projections both ways; and
export. Every solve is a new revision, and nothing becomes active until you say so.

### Multi-camera geometry check
Mark the same physical ground point in several cameras and compare where each
calibration places it, in metres.

---

## Things the UI is careful about

**Approximate viewing area, not coverage.** The shaded sector is drawn from the
heading and field-of-view you type in. It is an indication of where a camera
points. It is not calibrated coverage and it does not measure visible floor
space. Metric distances are only ever shown once a map scale has been set, and
a map scale is a property of the drawing, not a camera calibration.

**Connection verified ≠ location verified.** These are separate fields.
`Camera.last_test_status` records whether the backend reached the device;
`CameraPlacement.review_status` records whether a human confirmed the marker
against the current plan image.

**A successful ONVIF login is not a working stream.** The probe checks the
control channel and the video stream separately. A camera that authenticates but
cannot deliver a frame is reported as *Login only*, never as online, with an
explanation of what failed.

**Replacing a plan image invalidates review, not placement.** Coordinates are
kept, every marker on that floor is flagged **needs review**, and any map scale
measured against the old image is cleared. Nothing is silently carried over as
verified.

**Heading is defined once.** 0° points to the top of the floor plan and
increases clockwise, in the database, the API and every part of the UI.

**Markers survive resizing.** Positions are stored as normalized image
coordinates in `[0,1]`, so the viewer can be any size.

**Status is never colour alone.** Every badge pairs its colour with a word and a
glyph.

**Fitting error is not accuracy.** The reprojection error of a solve says how
well it reproduced its own input. Held-out reference points — never used to
solve — are the only independent check, and they only speak for the area they
cover. Numenor reports both separately and invents no confidence percentage.

**A homography is not a camera pose.** Floor-plane calibration maps pixels to one
plane. It cannot say where the camera is, and no XYZ position is fabricated from it.

**Intrinsics belong to one image geometry.** A calibration is bound to the exact
resolution, crop, rotation and lens state it was measured at. Reusing it on a
different stream is refused rather than silently rescaled.

**Geometric coverage is not visibility.** Floor footprints are where the image
border meets the floor plane. They ignore machinery, racking, walls and people.

**Consistency is not accuracy.** The multi-camera check shows whether cameras
agree with each other. They can agree and all be wrong.

**Placing a pin is not measuring.** Clicking the grid records where you say a
mark is. The UI says so where it matters.

**An absent relationship is unknown, not impossible.** Only an explicit exclusion
states that two views have no direct association, and even then people can travel
between them via other cameras.

---

## Preview and streaming

RTSP is not playable in a standard browser, and Numenor never pretends
otherwise — no RTSP URL is ever placed in an HTML `<video>` element.

* **Snapshot** — the ONVIF snapshot endpoint when the camera offers one,
  otherwise a single JPEG frame decoded from RTSP by ffmpeg.
* **Live preview** — ffmpeg transcodes RTSP to MJPEG on the server and the
  browser consumes it as `multipart/x-mixed-replace`.

Preview processes are bounded: a per-server session cap
(`NUMENOR_MAX_PREVIEW_SESSIONS`, default 4), an idle timeout that reaps
abandoned sessions (default 30 s), a maximum lifetime (default 10 min), and an
explicit stop endpoint the UI calls when you close the preview or navigate away.
Starting a second preview for the same camera tears the first one down.

ffmpeg is always launched with an argument list — never through a shell — so
nothing stored on a camera record can be interpolated into a command line.

---

## Credential handling

* Camera passwords are encrypted at rest with Fernet (the `cryptography`
  package). Ciphertext goes in the database; the key does not.
* The key is read from `NUMENOR_SECRET_KEY`, or from `credential.key` in
  `NUMENOR_SECRET_DIR` (`~/.numenor` by default) — outside the database and
  outside the source tree. It is created `0600` on first start-up.
  **Back it up: without it, stored camera passwords cannot be decrypted.**
* No API response contains a password or a credential-bearing URL. `CameraOut`
  exposes `has_password` and nothing more. Error messages and log lines pass
  through a sanitizer that strips `user:pass@` from any URL.
* Editing a camera with the password field left blank keeps the stored password.
  Removing it is a separate, explicit action (`clear_password`).
* Every configuration and preview route requires authentication. Endpoints the
  browser reaches through `<img>` tags accept the same session token as a query
  parameter, because tags cannot send headers.
* Operator passwords are hashed with scrypt. Seed data and `.env.example`
  contain no secrets.

### Outbound request policy

Numenor only ever contacts an endpoint an operator explicitly registered or
typed into the wizard. It does not scan or enumerate the factory network.

Before any socket is opened, the host is resolved and vetted: private and
link-local addresses are allowed (that is where cameras live), loopback,
multicast and reserved addresses are refused, cloud metadata addresses are
blocked outright, and public addresses are refused unless you opt in via
`NUMENOR_ALLOW_PUBLIC_HOSTS` or `NUMENOR_EXTRA_CIDRS`. Ports belonging to
unrelated infrastructure (SSH, databases, brokers) are rejected. Connections are
made to the resolved IP rather than re-resolving the name, and HTTP redirects
are not followed — so a camera record cannot be turned into a general-purpose
fetcher.

---

## Data model

| Record | Fields |
| --- | --- |
| **Camera** | id, name, host, connection type, ONVIF port/path, RTSP port/stream path, username, encrypted password, selected profile (token, name, resolution, encoding), snapshot support, manufacturer, model, notes, area reference, installation description, mounting height, installation photo, last test status/timestamp/sanitized error/detail, created and updated timestamps |
| **Location** | Site → Building → Floor → Area, unique by name within the parent |
| **FloorPlan** | floor reference, image reference, pixel dimensions, optional scale (px/metre plus the two reference points and their real distance), version, timestamps |
| **CameraPlacement** | camera id, floor-plan id, normalized x/y, heading, optional mounting height, field-of-view angle and estimated viewing distance, review status, timestamps |
| **CoordinateSystem** | factory world frame: name, origin description, floor plane Z, grid extent and spacing, acceptance thresholds, definition revision |
| **WorldReferencePoint** | surveyed X/Y/Z in one frame, code, description, measurement notes, uncertainty, optional ArUco details |
| **CameraIntrinsics** | K, distortion model and coefficients, calibrated width/height/rotation/crop, lens and zoom state, source, RMS error, calibration date, active flag |
| **PointObservation** | reference point seen at a pixel in one camera, with the image geometry it was marked on and whether it is used for fitting or held out |
| **CalibrationRevision** | method, status, pose (quaternion + position) and/or floor homography, pixel convention, source image geometry, solver, metrics, warnings, active flag, parent revision |
| **ValidationResult** | fitting reprojection error, held-out ground error, point and inlier counts, thresholds used, reviewer, timestamp |

Schema changes are applied by a small versioned migration runner
(`backend/app/migrations.py`) recorded in a `schema_migrations` table, so a
database from the first release upgrades in place rather than needing a rebuild.

SQLite by default, through SQLAlchemy — point `NUMENOR_DATABASE_URL` at
PostgreSQL if you outgrow it.

---

## API

All routes require `Authorization: Bearer <token>` except `/api/health` and
`/api/auth/login`.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/auth/login` | Obtain a session token |
| GET | `/api/cameras` | List, with `q`, `site_id`, `building_id`, `floor_id`, `area_id`, `status`, `placement` filters |
| GET | `/api/cameras/summary` | Dashboard counters |
| POST | `/api/cameras` | Register a camera |
| GET/PATCH/DELETE | `/api/cameras/{id}` | Read, edit, delete |
| POST | `/api/cameras/test-connection` | Test an unsaved camera |
| POST | `/api/cameras/profiles` | ONVIF profile discovery for an unsaved camera |
| POST | `/api/cameras/snapshot-preview` | Snapshot for an unsaved camera |
| POST | `/api/cameras/{id}/test` | Test a saved camera and record the result |
| POST | `/api/cameras/{id}/profiles` | ONVIF profile discovery for a saved camera |
| GET | `/api/cameras/{id}/snapshot` | JPEG snapshot |
| PUT/DELETE | `/api/cameras/{id}/placement` | Set or clear the map placement |
| POST/GET | `/api/cameras/{id}/photo` | Installation photo |
| POST | `/api/preview/{id}/start` | Open a preview session |
| GET | `/api/preview/{session}/stream` | MJPEG stream |
| POST | `/api/preview/{session}/stop` | Tear the session down |
| GET | `/api/preview/sessions` | Active sessions and the cap |
| GET | `/api/locations/tree` | Full site → area tree |
| POST | `/api/locations/resolve` | Create/find a whole location chain in one call |
| POST | `/api/locations/{sites,buildings,floors,areas}` | Create one level |
| DELETE | `/api/locations/{…}/{id}` | Delete (refused while cameras are assigned) |
| GET/POST | `/api/floor-plans` | List, upload or replace a floor's plan |
| GET | `/api/floor-plans/{id}` · `/image` · `/cameras` | Metadata, image, placed cameras |
| PUT/DELETE | `/api/floor-plans/{id}/scale` | Set or clear the map scale |

### Positioning and calibration

| Method | Path | Purpose |
| --- | --- | --- |
| GET/POST | `/api/coordinate-systems` | List or create factory frames |
| GET | `/api/coordinate-systems/conventions` | The authoritative frame/rotation conventions |
| GET/PATCH/DELETE | `/api/coordinate-systems/{id}` | Read, edit (redefinition needs confirmation), delete |
| GET/POST | `/api/reference-points` | Surveyed point registry |
| GET/PATCH/DELETE | `/api/reference-points/{id}` | Read, re-measure, delete |
| GET/POST | `/api/cameras/{id}/intrinsics` | List or import intrinsics |
| POST | `/api/cameras/{id}/intrinsics/checkerboard` | Guided checkerboard calibration |
| POST/DELETE | `/api/cameras/{id}/intrinsics/{iid}[/activate]` | Activate or remove |
| GET/PUT | `/api/cameras/{id}/observations` | Image ↔ reference-point observations |
| POST | `/api/cameras/{id}/calibration/manual` | Save an approximate pose |
| POST | `/api/cameras/{id}/calibration/homography` | Solve a floor homography |
| POST | `/api/cameras/{id}/calibration/pose` | Solve a full camera pose |
| GET | `/api/cameras/{id}/calibration/revisions` | Revision history |
| POST | `/api/cameras/{id}/calibration/revisions/{rid}/adjust` | Hand correction as a new revision |
| POST | `/api/cameras/{id}/calibration/revisions/{rid}/activate` | Explicit activation |
| POST | `/api/cameras/{id}/calibration/revisions/{rid}/validate` | Check against held-out points |
| GET | `/api/cameras/{id}/calibration/export` | Documented calibration JSON |
| POST | `/api/cameras/{id}/projection/image-to-world` | Pixel → floor position |
| POST | `/api/cameras/{id}/projection/world-to-image` | World point → pixel |
| GET | `/api/cameras/{id}/projection/overlay` | Reference points and a projected floor grid |
| GET | `/api/factory-map/{system_id}` | Every camera and point in one frame |
| GET | `/api/factory-map/{system_id}/coverage` | Geometric floor coverage |
| POST | `/api/factory-map/check-ground-point` | Multi-camera consistency check |
| PUT | `/api/factory-map/floor-plans/{id}/alignment` | Align a plan image into world coordinates |

Interactive docs are served at `/docs` while the server is running.

See the [installer guide](docs/INSTALLER-GUIDE.md) for the field workflow and the
[export example](docs/calibration-export-example.json) for the output format.

---

## Configuration

Copy `.env.example` to `.env`. Every setting has a safe default; the file
documents each one and contains no secrets.

---

## Tests

```bash
./backend/test.sh
```

194 tests. Registration and media: credential encryption and exposure, the
outbound host policy, ONVIF discovery against a simulated device (WS-Security
digest verification, `XAddr` rewriting, auth failures), ffmpeg error
classification, preview session limits and cleanup, camera CRUD, keep-vs-clear
password rules, verification reset on connection change, floor-plan upload
validation and review flagging after a plan replacement.

Geometry and calibration: camera-to-world/world-to-camera inversion,
RPY/quaternion/matrix round-trips including gimbal lock, the yaw-vs-heading sign
relationship, synthetic pose recovery with and without distortion, outlier
rejection, planar ambiguity detection, degenerate point sets, homography recovery
with held-out validation, agreement between pose projection and homography,
parallel and behind-camera rays, polygon clipping, resolution/crop mismatch
refusal, fisheye rejection, malformed matrices and non-finite input, revision
persistence and explicit activation, coordinate-system redefinition protection,
multi-camera comparison, credential-free export, and in-place schema migration
of a first-release database.

Guided setup: known homography recovery, robust fitting with wrong
correspondences, collinear and clustered point sets, distortion-aware coordinate
consistency, forward and inverse projection, display-size independence of stored
pixels, the strict separation of fitting and validation points, revision
activation and stale handling, cameras using different subsets of one registry,
directed transitions and graph validation, zones without calibration, and
re-saving matches without losing them.

## Limitations

* **The ONVIF client and RTSP media path have been exercised against a simulated
  ONVIF device and a real RTSP server, not against every camera model.** Real
  devices vary in their SOAP dialects.
* **Calibration accuracy has been verified against synthetic scenes only.**
  Recovery lands within millimetres of ground truth in simulation. Real-world
  accuracy depends almost entirely on how well your reference points were
  surveyed, and is measured per installation by held-out points.
* **No fisheye support.** Fisheye calibrations are rejected rather than
  approximated as pinhole.
* **A floor homography is limited to one plane**, and cannot project points at
  other heights.
* **Floor footprints are geometric**: they ignore occlusion entirely.
* **The 3D view needs WebGL.** Without hardware acceleration it reports the lost
  context and directs you to the 2D grid, which needs no GPU.
* **ArUco detection is not implemented.** The data model carries marker fields so
  markers can assist point selection later; the manual workflow does not depend
  on it.
