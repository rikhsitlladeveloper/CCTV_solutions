# Development guide

---

## Setup

```bash
./setup.sh                       # venv, dependencies, frontend build

./backend/run.sh                 # API on :8000
cd frontend && npm run dev       # Vite on :5173, proxies /api to :8000
```

Point the dev server at a different backend with `NUMENOR_API`:

```bash
NUMENOR_API=http://127.0.0.1:8002 npm run dev
```

Seed something to work against:

```bash
backend/.venv/bin/python backend/seed_demo.py
```

---

## Tests

```bash
./backend/test.sh                # all 145
./backend/test.sh tests/test_geometry.py -v
./backend/test.sh -k homography
```

`test.sh` sets `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`. Without it, unrelated
system-wide pytest plugins on `PYTHONPATH` (a ROS install, for instance) get
loaded into the run and fail on missing imports.

| File | Tests | Covers |
| --- | ---: | --- |
| `test_calibration_api.py` | 33 | Advanced calibration API, revisions, validation, projection, export, redefinition protection, migrations, demo data |
| `test_setup_api.py` | 32 | The guided flow end to end: workspaces, points, matching, mapping jobs, accuracy, activation, staleness, zones, relationships, export |
| `test_geometry.py` | 32 | Transform inversion, RPY/quaternion/matrix round-trips, gimbal lock, heading sign, ray/plane edge cases, polygon clipping |
| `test_calibration.py` | 31 | Synthetic pose and homography recovery, distortion, outliers, planar ambiguity, degeneracy, checkerboard, intrinsics validation |
| `test_api.py` | 18 | Auth, camera CRUD, credential exposure, floor plans, placement review |
| `test_security.py` | 17 | Encryption, hashing, sanitising, network policy, log redaction |
| `test_floormapping.py` | 17 | The guided pipeline: recovery, mis-clicks, mis-measurements, distortion consistency, horizon, coverage, pre-flight checks |
| `test_media.py` | 8 | URL construction, ffmpeg error classification, preview session limits |
| `test_onvif.py` | 6 | Discovery, XAddr rewriting, WS-Security digest, auth failure |

Frontend type checking:

```bash
cd frontend && npx tsc --noEmit -p tsconfig.app.json
```

### How the tests avoid lying to you

Two techniques are used deliberately and are worth preserving:

**The ONVIF simulator recomputes the digest independently.**
`tests/onvif_simulator.py` reimplements the WS-Security password digest from the
nonce and timestamp and compares. If the client's digest generation broke, the
simulator would reject it — the test would fail rather than pass against a
permissive fake.

**Calibration is tested against synthetic scenes with known ground truth.**
A pose is chosen, reference points are projected through real intrinsics, noise
is added, and the solver must recover the original pose. This proves the
mathematics and the conventions are self-consistent. It proves nothing about
accuracy on real hardware, and the tests say so.

---

## Where to change what

| Task | Start here |
| --- | --- |
| A coordinate or rotation convention | `backend/app/geometry.py`, then run `test_geometry.py` |
| The guided mapping pipeline | `backend/app/floormapping.py` |
| Installer wording or the step flow | `frontend/src/pages/SetupPage.tsx` |
| The relationship graph | `backend/app/relationships.py`, `frontend/src/pages/RelationshipsPage.tsx` |
| A solver | `backend/app/calibration.py` |
| Projection or validation scoring | `backend/app/positioning.py` |
| The intrinsic model | `backend/app/intrinsics.py` |
| A new endpoint | `backend/app/routers/`, schema in `schemas*.py` |
| A schema change | `backend/app/models.py` **and** a migration |
| Camera connection behaviour | `backend/app/probe.py`, `onvif.py`, `media.py` |
| The metric map | `frontend/src/components/FactoryGrid.tsx` |
| The 3D view | `frontend/src/components/Scene3D.tsx` |

Routers stay thin: validate, call a service, serialise. Keep arithmetic out of
them — it needs to be testable without HTTP.

---

## Adding a migration

`Base.metadata.create_all` adds new *tables* but never alters existing ones, so
any added column needs a migration step.

```python
# backend/app/migrations.py

def _m004_camera_tags(conn: Connection) -> None:
    _add_column(conn, "cameras", "tags_json", "TEXT")

MIGRATIONS = [
    ...,
    Migration(4, "camera tags", _m004_camera_tags),
]
```

Steps must be idempotent and additive. `_add_column` checks first, so re-running
is harmless. Versions are recorded in `schema_migrations`, and
`test_calibration_api.py::test_migrations_upgrade_a_pre_calibration_database`
runs them against a hand-built first-release database.

Avoid destructive changes: SQLite cannot drop a column without rebuilding the
table, and an operator's data is not worth the convenience.

---

## Conventions this codebase keeps

These exist because getting them wrong produces output that *looks* right.

**One source for geometry.** Every frame, rotation and heading definition lives
in `geometry.py` and is exported through `CONVENTIONS`, which the API serves and
the UI renders. Do not restate a convention in a comment or a component — import
it.

**Never fabricate a value you do not have.** A homography does not determine a
camera pose, so none is stored. Approximate FOV is not intrinsics. No confidence
percentage is invented.

**Separate what is separate.** Connection status, calibration status and
floor-plan placement are three fields. Fitting error and held-out error are two
numbers with different meanings, never averaged.

**Fail loudly on ambiguity.** Mismatched image geometry, fisheye coefficients,
collinear points, rays above the horizon and points behind the camera are all
errors with an explanation, not best-effort guesses.

**Nothing becomes authoritative implicitly.** Solving creates an inactive
revision. Activation is a separate call. Prior revisions are kept.

**Colour is never the only signal.** Every badge carries a word and a glyph.

**The default flow speaks the installer's language.** Coordinates, rotations,
matrices and solver settings belong behind an `<Advanced>` disclosure or on the
advanced calibration page. If a normal step needs the word "homography" to make
sense, the step needs rewriting, not a glossary.

**Deleting rows from a relationship needs a refresh.** After `db.delete()` plus
`flush()`, the collection still holds the doomed objects; reusing one as an
"existing" record makes the save silently do nothing. `db.refresh(camera)` before
rebuilding the index. Two endpoints were fixed for exactly this, and there are
regression tests for both.

---

## Frontend notes

**Three renderers, each for a reason.** SVG for the floor-plan editor (few
elements, crisp text), Konva for the metric grid (many markers, continuous
pan/zoom), Three.js for the 3D view.

**No network fonts in the 3D view.** drei's `<Text>` fetches a font from a CDN
and suspends the whole canvas until it resolves — which on an offline install
means a permanently blank view. Labels use drei `<Html>`. This cost an hour to
diagnose; please do not reintroduce it.

**Graphics cameras are not optical cameras.** Three.js looks down −Z with +Y up;
OpenCV looks down +Z with +Y down. `Scene3D` builds frustums from the optical
basis vectors explicitly.

**Keep Three.js lazy.** It is ~250 kB gzipped in its own chunk. Importing it from
a shared module would put it in the main bundle for every user.

**Normalised coordinates.** Floor-plan markers store `[0,1]` image coordinates so
resizing never moves them. World positions are metres and never derive from a
displayed image.

---

## Working with cameras you do not have

**A real RTSP source.** [mediamtx](https://github.com/bluenviron/mediamtx) plus
ffmpeg gives a genuine RTSP server with authentication:

```bash
./mediamtx mediamtx.yml &
ffmpeg -re -stream_loop -1 -f lavfi -i "testsrc=size=1280x720:rate=15" \
       -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p \
       -f rtsp -rtsp_transport tcp rtsp://<lan-ip>:8554/test
```

Use a LAN address, not loopback — the network policy refuses loopback unless
`NUMENOR_ALLOW_LOOPBACK=1`.

**A fake ONVIF device.** `backend/tests/onvif_simulator.py` runs standalone and
speaks the operations the client uses, with real digest verification.

---

## Known rough edges

* `ffmpeg` names its RTSP socket timeout `-stimeout` in 4.x and `-timeout` in
  5+. `media._rtsp_timeout_flag()` probes the binary once and caches the answer;
  do not hard-code either.
* The bundle warns about chunk size. The main bundle is ~222 kB gzipped; the
  warning is about the lazy Three.js chunk.
* There is no end-to-end browser test suite in the repository. UI verification
  has been done with ad-hoc Puppeteer scripts against a running instance.
