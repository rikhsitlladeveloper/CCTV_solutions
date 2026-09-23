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
