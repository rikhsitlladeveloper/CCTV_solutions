# API reference

Base URL is the server root. Interactive docs are at `/docs` while the server
runs.

**Authentication.** Every route requires `Authorization: Bearer <token>` except
`POST /api/auth/login` and `GET /api/health`. Endpoints the browser reaches
through `<img>` or SVG `<image>` tags also accept `?token=<token>`, because tags
cannot send headers; those tokens are redacted from access logs.

**Errors.** `422` for validation failures, `404` for missing records, `409` for
conflicts needing confirmation, `502` for a camera that failed to respond. The
body is `{"detail": …}`, where detail is a string or a structured object
carrying `message` and, where useful, `required` or the offending ids.

**Units.** Metres and degrees throughout. Pixels are always in the stream's
native geometry.

---

## Authentication

### `POST /api/auth/login`
```json
{ "username": "operator", "password": "…" }
```
→ `{ "access_token": "…", "token_type": "bearer", "expires_at": 1790204900, "username": "operator" }`

### `GET /api/auth/me`
→ `{ "username": "operator" }`

---

## Cameras

### `GET /api/cameras`
Filters: `q`, `site_id`, `building_id`, `floor_id`, `area_id`,
`status` (`online|failed|untested|partial`),
`placement` (`placed|unplaced|needs_review`).

Returns `CameraOut[]`. **Never contains a password** — only `has_password`.

### `GET /api/cameras/summary`
→ `{ total, reachable, failed, untested, awaiting_placement, placements_need_review }`

### `POST /api/cameras`
```json
{
  "name": "Packaging North", "host": "10.112.22.5",
  "connection_type": "rtsp", "rtsp_port": 554, "stream_path": "/Streaming/Channels/101",
  "username": "admin", "password": "…", "area_id": 1,
  "installation_description": "Column B3", "mounting_height_m": 4.2
}
```
`host` must be a bare IP or hostname — a URL is rejected. The password is
encrypted on write. Saving an untested camera is allowed; it is recorded as
`untested`, never as online.

### `GET` / `PATCH` / `DELETE /api/cameras/{id}`

On `PATCH`, **an omitted or empty password keeps the stored one.** Clearing it
requires `"clear_password": true`. Changing any connection field resets the
camera to `untested`.

### `POST /api/cameras/test-connection`
Tests an unsaved camera. Same connection fields as create, plus `profile_token`.

→ `TestConnectionResult`:
```json
{
  "status": "partial", "ok": false, "login_ok": true, "stream_ok": false,
  "snapshot_supported": true, "summary": "ONVIF login succeeded; video stream did not verify.",
  "error": "ONVIF control channel is working, but the video stream failed: …",
  "tested_at": "…", "profiles": [ … ], "manufacturer": "…", "model": "…"
}
```
`login_ok` and `stream_ok` are separate on purpose.

### `POST /api/cameras/profiles`
ONVIF profile discovery for an unsaved camera.

### `POST /api/cameras/snapshot-preview`
A real JPEG for an unsaved camera, from the details supplied. Credentials are
used for the request only and not persisted.

### `POST /api/cameras/{id}/test` · `POST /api/cameras/{id}/profiles`
As above, for a saved camera; the result is recorded against it.

### `GET /api/cameras/{id}/snapshot`
→ `image/jpeg`. ONVIF snapshot endpoint when offered, otherwise one frame
decoded from RTSP.

### `POST` / `GET /api/cameras/{id}/photo`
Installation photo, PNG or JPEG.

---

## Locations

`Site → Building → Floor → Area`.

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/api/locations/tree` | Whole hierarchy with camera counts |
| `POST` | `/api/locations/resolve` | Create/find a whole chain in one call; idempotent by name |
| `POST` | `/api/locations/sites` · `/buildings` · `/floors` · `/areas` | One level at a time |
| `DELETE` | `/api/locations/sites/{id}` · `/buildings/{id}` · `/floors/{id}` · `/areas/{id}` | `409` while cameras are assigned |

`resolve` accepts names or ids at each level and returns the resulting `Area`.

---

## Floor plans

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/api/floor-plans?floor_id=` | List |
| `POST` | `/api/floor-plans` | Multipart upload; PNG/JPEG; replaces the floor's plan |
| `GET` | `/api/floor-plans/{id}` | Metadata |
| `GET` | `/api/floor-plans/{id}/image` | The image itself; accepts `?token=` |
| `GET` | `/api/floor-plans/{id}/cameras` | Cameras placed on this plan |
| `PUT`/`DELETE` | `/api/floor-plans/{id}/scale` | Two points plus a real distance |
| `DELETE` | `/api/floor-plans/{id}` | |

Replacing the image increments `version`, flags every placement on that floor
`needs_review`, and clears any scale measured against the old image.

### `PUT` / `DELETE /api/cameras/{id}/placement`
```json
{ "floor_plan_id": 1, "norm_x": 0.26, "norm_y": 0.32, "heading_deg": 135,
  "fov_deg": 90, "view_distance_m": 12, "review_status": "confirmed" }
```
Coordinates are normalised to `[0,1]`, so resizing the viewer never moves a marker.

---

## Preview

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/api/preview/{camera_id}/start` | Reserves a session; returns `stream_url` |
| `GET` | `/api/preview/{session_id}/stream` | `multipart/x-mixed-replace` MJPEG |
| `POST` | `/api/preview/{session_id}/stop` | Tears down ffmpeg |
| `GET` | `/api/preview/sessions` | Active sessions and the cap |

`429` when every slot is in use. Abandoned sessions are reaped.

---

## Guided setup

The installer-facing flow. Everything here is expressed in measured floor
positions; no camera pose or rotation is ever required.

### Workspaces

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/api/setup/workspaces?floor_id=` | List |
| `POST` | `/api/setup/workspaces` | Create the area |
| `GET`/`PATCH` | `/api/setup/workspaces/{id}` | Read, edit |
| `POST` | `/api/setup/workspaces/{id}/cameras/{camera_id}` | Put a camera in the area |

```json
{
  "name": "Packaging bay", "floor_id": 3,
  "width_m": 24, "length_m": 16,
  "origin_description": "Inside corner of column A1 where it meets the floor.",
  "x_axis_description": "East along the north wall towards the loading doors.",
  "surface_description": "One continuous flat concrete floor.",
  "max_ground_error_m": 0.25, "min_reference_points": 6
}
```

**One frame per floor.** Creating a second area on a floor that already has one
returns `409` naming the existing workspace, because areas on a floor must share
an origin for their measurements to be comparable. A genuinely separate surface —
a mezzanine, a ramp — belongs on its own floor record.

`PATCH` returns `409` if the origin or axis description changes while mappings
exist; re-send with `confirm_redefinition: true` and every affected mapping is
marked stale.

Assigning a camera that already belongs elsewhere marks its old mapping stale and
reports `previous_mapping_marked_stale`.

### Floor points

| Method | Path |
| --- | --- |
| `GET`/`POST` | `/api/setup/workspaces/{id}/points` |
| `PATCH`/`DELETE` | `/api/setup/points/{point_id}` |

```json
{ "workspace_id": 1, "code": "FP-05", "name": "Drain cover, centre",
  "x": 9.0, "y": 8.0, "role": "calibration",
  "measurement_notes": "Tape from column A1.", "uncertainty_m": 0.005 }
```

`role` is `calibration` (used to build the mapping) or `validation` (held back,
never fitted, so it can test the result). The role belongs to the point, so the
split cannot drift between cameras.

Re-measuring `x`/`y`, changing `role`, or deleting a point marks every mapping
built from it stale.

### Matching

| Method | Path |
| --- | --- |
| `GET`/`PUT` | `/api/setup/cameras/{id}/matches` |
| `DELETE` | `/api/setup/cameras/{id}/matches/{observation_id}` |

```json
{ "image_width": 1920, "image_height": 1080, "replace_existing": true,
  "matches": [{ "reference_point_id": 12, "pixel_u": 812.5, "pixel_v": 640.0 }] }
```

Pixels are in the source image's own coordinates, so browser zoom and window size
are irrelevant. `GET` also returns `unmatched_points`, live `issues` (near-collinear,
clustered in one part of the frame, duplicates, mixed image sizes) and
`ready_to_calculate`.

### Calculate, check, activate

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/api/setup/cameras/{id}/calculate-mapping` | `202` with a job to poll |
| `GET` | `/api/setup/jobs/{job_id}` | Progress, then result or a plain failure |
| `POST` | `/api/setup/cameras/{id}/check-accuracy` | Held-out points only |
| `POST` | `/api/setup/cameras/{id}/activate-mapping` | Explicit |
| `POST` | `/api/setup/cameras/{id}/project` | Pixel ↔ floor position |
| `POST` | `/api/setup/consistency-check` | Same spot in several cameras |

`calculate-mapping` takes `image_width`, `image_height` and an optional
`tolerance_px`. **There is no solver to choose.** The result carries the
homography and its inverse, which points were used and which were rejected by
name, the covered floor polygon, the conditioning, the pixel convention and any
warnings.

A failed job explains itself:

```json
{ "status": "failed",
  "error": "A mapping needs at least 4 calibration points; 2 are matched.",
  "hint": "Match more measured floor points, or change a validation point to a calibration point." }
```

`check-accuracy` scores only points whose role is `validation`. It records a
result and reports mean, median and worst error in metres, the number and spread
of the points, and what the check does and does not cover. It never activates
anything.

`activate-mapping` returns `409` for a stale mapping, naming what changed.

`project` accepts either `{pixel_u, pixel_v}` or `{x, y}` and flags
`within_checked_area: false` when the answer is extrapolation beyond the measured
points.

`consistency-check` is labelled **Cross-camera consistency** without a surveyed
position, and **Accuracy against a measured position** when given one.

---

## Markers

| Method | Path |
| --- | --- |
| `GET` | `/api/markers/dictionaries` |
| `POST` | `/api/cameras/{id}/detect-markers` |
| `POST` | `/api/cameras/{id}/accept-markers` |

Detection returns *proposals* with per-marker issues: unknown IDs, duplicate
physical IDs in one frame, markers too small or too oblique, insufficient spread.
Nothing becomes an observation until accepted. A marker ID identifies which
marker it is, never where it is.

---

## Zones

| Method | Path |
| --- | --- |
| `GET`/`POST` | `/api/cameras/{id}/zones` |
| `PATCH`/`DELETE` | `/api/zones/{zone_id}` |

```json
{ "name": "Dispatch doorway", "kind": "entrance",
  "image_polygon": [[700, 560], [1220, 570], [1240, 860], [690, 850]],
  "image_width": 1920, "image_height": 1080 }
```

`kind` is `monitored`, `entrance` or `exit`. Zones need no calibration; when the
camera has an active mapping the zone also gets a floor polygon, tagged with the
revision that produced it. Redrawing a zone marks dependent relationships
`needs_review`. Deleting one in use returns `409`.

---

## Camera relationships

| Method | Path |
| --- | --- |
| `GET` | `/api/relationships?workspace_id=&suggest=` |
| `POST` | `/api/relationships` |
| `POST` | `/api/relationships/accept-suggestion` |
| `PATCH`/`DELETE` | `/api/relationships/{id}` |

```json
{ "kind": "transition", "camera_a_id": 1, "camera_b_id": 3,
  "zone_a_id": 7, "zone_b_id": 9,
  "min_travel_seconds": 4, "max_travel_seconds": 25,
  "verification": "unverified" }
```

Three kinds: `overlap`, `transition` (directed; the reverse is a separate
record) and `excluded`.

Rejected with `422`: self-links, a transition without both travel bounds, a
minimum above the maximum, a zone belonging to the wrong camera, a metric overlap
across different coordinate systems, duplicates, and an exclusion contradicting an
existing link.

`GET` also returns overlap `suggestions` derived from mapped coverage. Accepting
one records it as `suggested_by_geometry` and **unverified** — geometry cannot see
walls or racking.

The `summary` separates `pairs_with_a_record` from `pairs_unknown` and states
that an absent record means unknown, not impossible.

---

## Factory overview

### `GET /api/overview?building_id=&floor_id=&hours=`

Everything the supervisor's main screen needs, in one request: camera health,
events awaiting review, production counts, cameras grouped by building and
area, and any floor plans.

`health` counts **connected** and **monitoring** separately and never adds them
together — a camera can stream perfectly and be analysing nothing.

`production.value` is `null` whenever the number cannot honestly be produced,
with `unavailable_reason` saying why. It is never `0`: a zero would assert the
line produced nothing, which is a different and much stronger claim.

`groups` is always populated, so a site with no floor plan still has a usable
structure to browse rather than a flat list of thirty cameras.

---

## Events

An event is something an analytic reported. **Nothing in this deployment
produces them**: no inference service ships with the system. Rows arrive either
from an external processing service through the ingest endpoint, or as sample
data seeded by an explicit command.

| Method | Path |
| --- | --- |
| `GET` | `/api/events?camera_id=&area_id=&kind=&decision=&acknowledged=&since_hours=&include_samples=&limit=&offset=` |
| `GET` | `/api/events/{id}` |
| `POST` | `/api/events` |
| `POST` | `/api/events/{id}/review` |
| `POST` | `/api/events/{id}/acknowledge` |
| `GET` | `/api/events/{id}/snapshot` |

### Two independent questions

Review and acknowledgement are stored and returned separately, because they
answer different things and different people ask them:

* **`decision`** — `unreviewed`, `confirmed` or `dismissed`. *Was the detection
  correct?*
* **`acknowledged_at` / `acknowledged_by`** — *Has somebody seen or handled it?*

An event can be dismissed and acknowledged at once: it was wrong, and a person
dealt with it. Reviewing never implies acknowledging, and acknowledging never
changes a decision.

### Ingest

```json
{ "camera_id": 4, "function_id": 2, "kind": "restricted_entry",
  "started_at": "2026-09-25T13:24:08Z", "duration_s": 4.5,
  "facts": ["A person was inside Conveyor Access for 4.5 s."] }
```

The rule's wording is snapshotted onto the event as `rule_summary`, so editing
the rule afterwards cannot rewrite the reason a past event fired.

`is_sample` is **not** part of the ingest contract and is always stored as
`false`. A real service cannot mark its output as a demonstration, and a
demonstration cannot pass itself off as real.

`facts` are observations, never conclusions: *"No product crossed the line for
3 minutes"*, not *"the machine has stopped"*.

---

## Monitoring lifecycle

| Method | Path |
| --- | --- |
| `GET` | `/api/cameras/{id}/monitoring` |
| `POST` | `/api/cameras/{id}/monitoring/request-validation` |
| `POST` | `/api/cameras/{id}/monitoring/activate` |
| `POST` | `/api/cameras/{id}/monitoring/deactivate` |
| `GET` | `/api/cameras/{id}/rule-summaries` |

Five states: `draft` → `connected` → `configured` → `validation_pending` →
`active`. The first three are **derived** from evidence that already exists — a
passed connection test, a saved analytic with a usable region — so a stored
flag can never contradict the thing it claims. Only the last two are stored,
because asking for validation and going live are deliberate acts.

Activation is refused with `422` and a list of `blockers` when anything is
missing: an unproven connection, no analytics, an unusable region, or no area
assigned. **Floor-plan calibration is deliberately not a blocker** — camera-only
analytics are complete without one, and demanding a map would block the common
case for no benefit.

`rule-summaries` returns each analytic as one plain sentence, matching what is
stored on any event it produces:

> During Shift A, create an event when a person remains inside Conveyor Access
> for more than 2 seconds.

---

## Setup progress

| Method | Path |
| --- | --- |
| `GET` / `PUT` / `DELETE` | `/api/cameras/{id}/setup-progress` |

Where an installer got to in the setup wizard: the current step, which steps are
done, and any answers not yet committed to their real tables. Held on the server
rather than in the browser, so progress survives a reload, a different machine,
or a different person picking the job up.

---

## ONVIF discovery

### `POST /api/discovery/onvif?seconds=`

Multicast WS-Discovery, run on this host. It is a question devices choose to
answer, not a scan: nothing is contacted that did not reply first, and no
credentials are sent or needed.

Devices already registered are flagged with `already_registered` and their
camera id, so the same camera is not added twice.

Finding nothing means **"not found this way"**, never "not present" — a camera
on another VLAN, or a network that blocks multicast, simply will not answer. The
`note` in the response says so, and the interface repeats it rather than
implying the network is empty.

---

## Factory scene

The visual scene behind the commissioning workspace. One scene per workspace.

| Method | Path | |
| --- | --- | --- |
| `GET` | `/api/scenes?workspace_id=` | List |
| `POST` | `/api/scenes` | Create |
| `GET`/`PATCH` | `/api/scenes/{id}` | The working draft |
| `POST` | `/api/scenes/{id}/publish` | Snapshot the draft as the published scene |
| `GET` | `/api/scenes/{id}/published` | The last published snapshot |
| `POST` | `/api/scenes/{id}/objects` | Add an object |
| `PUT` | `/api/scenes/{id}/objects` | Replace the whole working set (makes undo trivial) |
| `PATCH`/`DELETE` | `/api/scenes/{id}/objects/{oid}` | One object |
| `GET` | `/api/scene-palette` | Object kinds with default sizes and colours |
| `POST` | `/api/scenes/{id}/assets` | Upload a floor plan or GLB |
| `PUT` | `/api/scenes/{id}/assets/{aid}/scale` | Set a plan's scale from two points and a distance |
| `PUT` | `/api/scenes/{id}/assets/{aid}/model-alignment` | Set a GLB's scale, up-axis and floor offset |
| `GET` | `/api/scenes/{id}/assets/{aid}/file` | The stored image or model |
| `DELETE` | `/api/scenes/{id}/assets/{aid}` | Remove an asset |

Objects carry `provenance`: `estimated` (drawn) or `measured`. A scene's objects come back with the scene itself; there is no separate list
endpoint. A scene's own `geometry_provenance` is `measured` only when every
object in it is. A GLB is
checked for the `glTF` magic bytes before it is stored.

Draft and published are separate snapshots, so editing a scene never changes
what Monitor mode is showing until you publish.

### `POST /api/cameras/{id}/place`

Visual placement. The installer gives a point, a height, a mount type and
something to look at; no angles are typed in.

```json
{ "workspace_id": 1, "x": 12.5, "y": 0.4, "height_m": 3.5,
  "target_x": 12.0, "target_y": 8.0, "mount_type": "wall",
  "illustrative_hfov_deg": 78, "illustrative_range_m": 15 }
```

Returns the resulting pose, the aim described in words, the floor polygon the
view cone covers, and `activated`.

**`activated` is `false` when the camera already had a solved calibration.** The
placement is still saved, as its own revision, but it is not switched on: a drag
must never silently replace measured geometry. `warnings` says so in words. To
choose it deliberately, activate the revision explicitly with
`POST /api/cameras/{id}/calibration/revisions/{rid}/activate`. Nothing is ever
deleted, so the move is reversible.

`illustrative_hfov_deg` draws the cone and **does not touch the camera's zoom**.
Where measured intrinsics exist they are used instead, and `fov_source` says
which.

Rejected with `422`: aiming a camera at its own mount point, which gives no
direction.

---

## Camera functions

What a camera is meant to do. Never blocked by missing calibration: counting and
zone watching work on the picture.

| Method | Path |
| --- | --- |
| `GET` | `/api/function-catalogue` |
| `GET`/`POST` | `/api/cameras/{id}/functions` |
| `PATCH`/`DELETE` | `/api/functions/{fid}` |

```json
{ "kind": "people_counting", "name": "Main doorway", "space": "image",
  "image_width": 1280, "image_height": 720,
  "config": { "line": [[201, 501], [1080, 478]], "direction": "a_to_b" } }
```

Geometry is stored in the frame's own native pixels together with the frame size
it was drawn against, so a later change of stream resolution is detected rather
than silently mis-scaling the shape.

Counting needs a two-point `line` and a `direction`; zones need a `polygon` of
three or more points. Both are rejected with `422` otherwise.

Every function carries a `status`. **`processing_available` is `false` for every
kind today** — no detection service ships with this system. Settings are stored
and exported for one to pick up later; the API does not pretend otherwise, and
neither does the interface.

---

## Readiness

### `GET /api/readiness?workspace_id=`

Nine capabilities, each with a state (`ready`, `partial`, `blocked`,
`unavailable`), what it depends on, and the next step.

It separates three different things that are easy to conflate: what is
**configured**, what is **geometrically possible**, and what is **running**. A
counting line that exists, on a camera that is mapped, with no processing
service connected, is `configured` and `possible` and not `running`.

`simulation_note` states plainly what the digital twin is and is not: a record
of where things are, not a simulation of how they behave.

---

## Commissioning sessions

A walk-through: the only accuracy measurement that involves the real building
rather than the numbers the mapping was fitted to.

| Method | Path |
| --- | --- |
| `GET`/`POST` | `/api/sessions?workspace_id=` |
| `POST` | `/api/sessions/{id}/checkpoints` |
| `POST` | `/api/sessions/{id}/end` |
| `DELETE` | `/api/sessions/{id}` |

Sessions come back in full from the list, checkpoints included, so there is no
separate detail endpoint. A checkpoint records where the system thought you
were, where you actually were, and the difference. The session's `accuracy` summary counts only checkpoints
that carry a measured position, because the rest cannot score anything.

---

## Site geometry export

### `GET /api/export/site-geometry?workspace_id=`

Everything a tracking service needs: the workspace and its origin description,
reference points, each camera's floor mapping with its pixel convention, source
image size, covered area and accuracy record, all zones, the relationship graph,
and explicit limitations.

No credentials, host addresses or stream URLs. Nothing about people.

---

## Coordinate systems

### `GET /api/coordinate-systems/conventions`
The authoritative frame, rotation and heading definitions. The UI renders these
rather than restating them, so documentation and code cannot diverge.

### `GET` / `POST /api/coordinate-systems`
```json
{
  "name": "Plant 1 floor frame",
  "origin_description": "Inside corner of column A1 at floor level. X east, Y north.",
  "floor_plane_z": 0,
  "grid_min_x": -5, "grid_max_x": 50, "grid_min_y": -5, "grid_max_y": 50,
  "grid_spacing_m": 1,
  "max_reprojection_error_px": 3, "max_ground_error_m": 0.25, "min_reference_points": 6
}
```
The three threshold fields decide what counts as Validated.

### `PATCH /api/coordinate-systems/{id}`
Cosmetic edits apply freely. Changing `floor_plane_z`, `origin_description` or
`site_id` while calibrations exist returns **`409`**:

```json
{
  "detail": {
    "message": "Changing ['floor_plane_z'] redefines what coordinates in 'Plant 1' mean. …",
    "affected_calibrations": 4, "affected_cameras": [1, 2, 3, 7],
    "required": "Re-send with confirm_redefinition=true …"
  }
}
```
With `confirm_redefinition: true`, `definition_revision` increments and every
affected calibration becomes `needs_recalibration`.

---

## Reference points

| Method | Path |
| --- | --- |
| `GET` | `/api/reference-points?coordinate_system_id=` |
| `POST` | `/api/reference-points` |
| `GET`/`PATCH`/`DELETE` | `/api/reference-points/{id}` |

```json
{ "coordinate_system_id": 1, "code": "RP-01", "name": "Column A1 base",
  "x": 2.0, "y": 2.0, "z": 0.0, "uncertainty_m": 0.005,
  "measurement_notes": "Tape from column A1, laser height." }
```

`code` is unique within a frame. An `aruco_marker_id` without
`aruco_marker_size_m` is rejected — a marker ID alone fixes nothing in the world.
Changing `x`/`y`/`z` flags calibrations solved from that point.

---

## Intrinsics

### `GET` / `POST /api/cameras/{id}/intrinsics`
```json
{
  "label": "Bench calibration", "model": "pinhole",
  "camera_matrix": [[1380,0,958],[0,1378,543],[0,0,1]],
  "distortion_coefficients": [-0.284, 0.083, 0.0006, -0.0004, -0.011],
  "image_width": 1920, "image_height": 1080,
  "image_rotation_deg": 0, "crop": null,
  "lens_description": "4 mm fixed", "rms_reprojection_error_px": 0.21,
  "activate": true
}
```

Rejected with `422`: a non-pinhole model (fisheye is named explicitly), a K that
is not upper-triangular or normalised, non-positive focal lengths, a principal
point far outside the image, a distortion vector of an invalid length, or any
non-finite number.

### `POST /api/cameras/{id}/intrinsics/checkerboard`
Multipart `files`, query `inner_cols`, `inner_rows`, `square_size_m`, `activate`.

→ per-view reprojection errors, pose-diversity analysis (tilt and distance
range), warnings, and the stored intrinsics. A square inner-corner count is
rejected as rotationally ambiguous.

### `POST /api/cameras/{id}/intrinsics/{iid}/activate` · `DELETE …/{iid}`
`409` on delete if an active calibration uses them.

---

## Observations

### `GET` / `PUT /api/cameras/{id}/observations`
```json
{ "observations": [
    { "reference_point_id": 3, "pixel_u": 812.5, "pixel_v": 640.0,
      "image_width": 1920, "image_height": 1080, "role": "fit" } ],
  "replace_existing": false }
```

`role` is `fit` or `holdout`. **Held-out points are never used to solve**, which
makes them the only independent accuracy check. Pixels outside the stated image
are rejected. All observations for a camera must reference one coordinate system.

`PUT` upserts by reference point; `replace_existing: true` clears the camera's
observations first.

### `DELETE /api/cameras/{id}/observations/{observation_id}`
Removes one observation.

---

## Calibration

### `POST /api/cameras/{id}/calibration/manual`
```json
{ "coordinate_system_id": 1, "x": 5, "y": 5, "z": 4,
  "roll_deg": -90, "pitch_deg": 0, "yaw_deg": 30,
  "approx_hfov_deg": 78, "approx_range_m": 12, "activate": true }
```
Always stored as `approximate`. `approx_hfov_deg` is a nominal figure for
drawing, never presented as measured intrinsics.

### `POST /api/cameras/{id}/calibration/homography`
```json
{ "coordinate_system_id": 1, "image_width": 1920, "image_height": 1080,
  "plane_z": 0, "use_intrinsics": true, "ransac_threshold_px": 3,
  "holdout_reference_point_ids": [], "activate": false }
```
Needs ≥ 4 non-collinear correspondences. Produces `H_image_to_floor` and its
inverse, inliers, outliers, residuals in both pixels and metres — and **no
pose**. Without intrinsics the fit runs on raw pixels and is recorded as
`distortion_corrected: false`.

### `POST /api/cameras/{id}/calibration/pose`
```json
{ "coordinate_system_id": 1, "image_width": 1920, "image_height": 1080,
  "use_ransac": true, "reprojection_threshold_px": 4, "activate": false }
```
Requires active intrinsics matching the stated geometry; `422` with an
explanation and the homography alternative otherwise. Detects collinear and
planar configurations, flags planar ambiguity, and rejects any solution placing
reference points behind the camera.

### `GET /api/cameras/{id}/calibration/revisions`
Full history. Exactly one is active.

### `POST …/revisions/{rid}/adjust`
A hand correction on a solved pose. Saved as a new **unvalidated, inactive**
revision with `parent_revision_id` set, because the solver's validation no longer
applies.

### `POST …/revisions/{rid}/activate`
`{ "confirm": true }`. Activation is always explicit; previous revisions are kept.

### `POST …/revisions/{rid}/validate`
```json
{ "reviewer": "installer", "notes": "…" }
```
→ `{ validation, revision }`. The validation separates:

* `fit_reprojection_error_px` — how well the solve reproduced its own input.
* `holdout_ground_error_m` — error against points the solver never saw.

with an `interpretation` block stating that the first is not evidence of
real-world accuracy. No confidence percentage is produced.

### `GET /api/cameras/{id}/calibration/export?revision_id=`
Self-describing JSON: conventions, frame, `T_world_camera`, intrinsics, metrics,
validation, reference points and explicit limitations. **Contains no credentials,
host addresses or stream URLs.** See
[`calibration-export-example.json`](calibration-export-example.json).

---

## Projection

### `POST /api/cameras/{id}/projection/image-to-world`
`{ "pixel_u": 812, "pixel_v": 640, "plane_z": null }`
→ `{ "world": [x, y, z], "method": "pose_ray_plane", "distance_from_camera_m": …,
     "caveats": [...], "is_approximate": false }`

`422` for a pixel on or above the horizon, a plane behind the camera, a pixel
outside the calibrated image, or a plane a homography cannot describe.

### `POST /api/cameras/{id}/projection/world-to-image`
`{ "x": 9, "y": 6, "z": 0 }` → `{ "pixel": [u, v], "in_image": true, … }`.
`422` for a point behind the camera.

### `GET /api/cameras/{id}/projection/overlay?grid_spacing_m=&extent_m=`
Reference points with their marked and projected pixels and per-point
reprojection error, plus a projected floor grid.

---

## Factory map

### `GET /api/factory-map/{system_id}`
Everything needed to draw one frame: the coordinate system, every camera with
pose, heading, FOV and clipped floor footprint, reference points, an aligned
floor plan if any, and the conventions.

### `GET /api/factory-map/{system_id}/coverage`
Footprint areas, plus an `interpretation` making clear this is geometric
coverage that ignores machinery, racking, walls and people.

### `POST /api/factory-map/check-ground-point`
```json
{ "coordinate_system_id": 1, "label": "Aisle marker", "plane_z": 0,
  "marks": [ { "camera_id": 1, "pixel_u": 900, "pixel_v": 800 },
             { "camera_id": 2, "pixel_u": 640, "pixel_v": 710 } ] }
```
→ per-camera world positions, pairwise disagreement in metres, centroid, and an
`interpretation` stating this is a **consistency check, not an accuracy
measurement** — cameras can agree and all be wrong.

### `PUT /api/factory-map/floor-plans/{plan_id}/alignment`
`{ "coordinate_system_id": 1, "metres_per_pixel": 0.02, "origin_x": 0, "origin_y": 0, "rotation_deg": 0 }`

Positions a plan image as a backdrop. No world coordinate changes.

---

## Health

### `GET /api/health`
→ `{ "status": "ok", "service": "numenor", "mode": "on-premise" }`. Public, and
deliberately reveals nothing else.
