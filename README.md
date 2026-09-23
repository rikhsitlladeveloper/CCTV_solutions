# Numenor — Camera Registration and Location Setup

An on-premise dashboard for registering factory cameras, verifying that they
actually answer, and recording where they are installed — including an
approximate position and viewing direction on a floor plan.

Everything runs on your own hardware: the database, the uploaded floor plans,
the credential encryption key and all video processing. Nothing is sent to an
external service.

**Scope.** This tool covers registration, connection testing, preview, physical
location and floor-plan placement. It deliberately does *not* do AI detection,
person tracking, recognition, analytics, or floor-plane calibration.

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
./backend/run.sh                 # API on :8000 with reload-free uvicorn
cd frontend && npm run dev       # Vite dev server on :5173, proxies /api to :8000
./backend/test.sh                # 48 backend tests
```

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
installation notes, and edit/retest actions.

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

Interactive docs are served at `/docs` while the server is running.

---

## Configuration

Copy `.env.example` to `.env`. Every setting has a safe default; the file
documents each one and contains no secrets.

---

## Tests

```bash
./backend/test.sh
```

48 tests covering credential encryption and exposure, the outbound host policy,
ONVIF discovery against a simulated device (including WS-Security digest
verification, `XAddr` rewriting and auth failures), ffmpeg error classification,
preview session limits and cleanup, camera CRUD, the keep-vs-clear password
rules, verification reset on connection changes, floor-plan upload validation,
and the review flagging that follows a plan replacement.
