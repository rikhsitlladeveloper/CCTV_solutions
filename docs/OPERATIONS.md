# Operations guide

Installing, configuring, backing up and upgrading a Numenor deployment.

---

## Requirements

| | |
| --- | --- |
| OS | Linux (developed and deployed on Ubuntu 22.04) |
| Python | 3.10+ |
| Node | 20+ — **build machine only**, not needed on the server |
| ffmpeg | 4.4+ — required for snapshots and preview |
| Disk | ~400 MB for the virtualenv and dependencies, plus floor plans and photos |
| Network | Reachability to the cameras. No internet access needed at runtime. |

---

## Install

```bash
git clone git@github.com:rikhsitlladeveloper/CCTV_solutions.git
cd CCTV_solutions
./setup.sh          # venv, dependencies, frontend build
./start.sh          # serves API and UI on 127.0.0.1:8000
```

**Do not run `setup.sh` with sudo.** It writes `frontend/dist/` as the invoking
user; running it as root leaves a build directory your normal user cannot
rebuild over.

On first start the server creates an operator account and prints a generated
password **once**:

```
====================================================================
  Numenor operator account created.
  username: operator
  password: <generated>
  This password is shown once. Set NUMENOR_ADMIN_PASSWORD to choose your own.
====================================================================
```

To choose your own, or to reset a lost one:

```bash
NUMENOR_ADMIN_PASSWORD='your-choice' ./start.sh
```

That overwrites the account; plain `./start.sh` works afterwards. The password is
scrypt-hashed and cannot be recovered, only replaced. Camera credentials are
unaffected — they use a separate key.

---

## Servers without Node

The frontend is built to static files that the backend serves. If the server has
no Node (Ubuntu 22.04 only offers Node 12, and Vite needs 20+), build elsewhere
and ship `frontend/dist/`:

```bash
# on the build machine
cd frontend && npm run build
rsync -az --exclude '.git/' --exclude 'backend/.venv/' --exclude 'backend/data/' \
      --exclude 'node_modules/' --exclude '__pycache__/' \
      ./ user@server:~/CCTV_solutions/
```

`backend/data/` is excluded so a deployment never overwrites live data.

---

## Configuration

Copy `.env.example` to `.env`. Every setting has a working default.

### Secrets

| Variable | Default | Notes |
| --- | --- | --- |
| `NUMENOR_SECRET_DIR` | `~/.numenor` | Holds `credential.key`, `session.key`, `operator.json`, all `0600` |
| `NUMENOR_SECRET_KEY` | — | Fernet key for camera passwords, if you'd rather inject it |
| `NUMENOR_SESSION_KEY` | — | HMAC key for session tokens |
| `NUMENOR_ADMIN_USER` | `operator` | |
| `NUMENOR_ADMIN_PASSWORD` | — | Unset means generate once and print |
| `NUMENOR_SESSION_TTL` | `43200` | Token lifetime, seconds |

**Back up `credential.key`.** Without it, stored camera passwords cannot be
decrypted. It is not in the database and not in the repository, by design.

### Storage

| Variable | Default |
| --- | --- |
| `NUMENOR_DATA_DIR` | `backend/data` |
| `NUMENOR_DATABASE_URL` | `sqlite:///<data dir>/numenor.db` |
| `NUMENOR_MAX_UPLOAD_BYTES` | `26214400` (25 MB) |

Point `NUMENOR_DATABASE_URL` at PostgreSQL if you outgrow SQLite.

### Camera network policy

| Variable | Default | Notes |
| --- | --- | --- |
| `NUMENOR_EXTRA_CIDRS` | — | Comma-separated ranges to allow beyond RFC1918 |
| `NUMENOR_ALLOW_PUBLIC_HOSTS` | `0` | Allows **all** public addresses — prefer a narrow CIDR |
| `NUMENOR_ALLOW_LOOPBACK` | `0` | Development and test rigs only |

A camera on a public IP is refused by default. Allowlist the single address:

```bash
NUMENOR_EXTRA_CIDRS=203.0.113.44/32
```

See [SECURITY.md](SECURITY.md) for why this exists.

### Timeouts and limits

| Variable | Default | Meaning |
| --- | --- | --- |
| `NUMENOR_CONNECT_TIMEOUT` | `4` | TCP connect, seconds |
| `NUMENOR_ONVIF_TIMEOUT` | `8` | ONVIF SOAP exchange |
| `NUMENOR_SNAPSHOT_TIMEOUT` | `15` | Snapshot / first frame |
| `NUMENOR_MAX_PREVIEW_SESSIONS` | `4` | Concurrent ffmpeg previews |
| `NUMENOR_PREVIEW_IDLE_TIMEOUT` | `30` | Reap an unattached session |
| `NUMENOR_PREVIEW_MAX_LIFETIME` | `600` | Hard cap per session |
| `NUMENOR_FFMPEG` | `ffmpeg` | Path to the binary |

Each preview is one ffmpeg transcode. Four concurrent sessions on a modest
server is a reasonable starting point; raise it only if CPU headroom allows.

### Server

| Variable | Default |
| --- | --- |
| `NUMENOR_HOST` | `127.0.0.1` |
| `NUMENOR_PORT` | `8000` |
| `NUMENOR_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` |

---

## Exposing the service

It binds to localhost by default. This is an on-premise tool with no TLS of its
own; do not put it on a public interface.

**Tailscale (recommended).** Bind to the tailnet address only:

```bash
NUMENOR_HOST=100.x.y.z ./start.sh
```

Reachable from any tailnet device, invisible to the LAN, WireGuard-encrypted.
Verify with `ss -tln | grep 8000` — it should show only that address.

Better still, if `tailscale serve` is enabled on your tailnet, keep the app on
localhost and let Tailscale terminate HTTPS with a real certificate:

```bash
sudo tailscale serve --bg 8000
```

**LAN with a reverse proxy.** Put nginx or Caddy in front with TLS, and keep the
app on `127.0.0.1`. Pass through `/api/preview/*/stream` without buffering — the
app already sends `X-Accel-Buffering: no`.

---

## Running as a service

```ini
# ~/.config/systemd/user/numenor.service
[Unit]
Description=Numenor camera setup
After=network-online.target

[Service]
Type=exec
WorkingDirectory=%h/CCTV_solutions
Environment=NUMENOR_HOST=127.0.0.1
Environment=NUMENOR_PORT=8000
ExecStart=%h/CCTV_solutions/backend/run.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now numenor
loginctl enable-linger "$USER"      # survive logout and reboot
journalctl --user -u numenor -f
```

Put secrets in an `EnvironmentFile=` with `0600` permissions rather than inline.

---

## Backups

Three things matter, and they are separate:

| What | Where | Why |
| --- | --- | --- |
| Database | `$NUMENOR_DATA_DIR/numenor.db` | Cameras, locations, calibrations |
| Uploads | `$NUMENOR_DATA_DIR/{floorplans,photos}` | Plan images, installation photos |
| **Encryption key** | `$NUMENOR_SECRET_DIR/credential.key` | Without it, camera passwords are unrecoverable |

SQLite runs in WAL mode, so **`cp numenor.db` is not a valid backup** — recent
writes live in the `-wal` file. Use the backup API:

```bash
backend/.venv/bin/python - <<'PY'
import sqlite3, datetime
stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
src = sqlite3.connect("backend/data/numenor.db")
dst = sqlite3.connect(f"/backup/numenor-{stamp}.db")
src.backup(dst); dst.close(); src.close()
print("ok")
PY
```

Restore by stopping the service, replacing the database (and deleting any stale
`-wal`/`-shm`), and restarting.

---

## Upgrading

```bash
# 1. back up the database (above)
# 2. build the frontend on a machine with Node
cd frontend && npm run build
# 3. sync, excluding runtime data
rsync -az --exclude '.git/' --exclude 'backend/.venv/' --exclude 'backend/data/' \
      --exclude 'node_modules/' --exclude '__pycache__/' ./ user@server:~/CCTV_solutions/
# 4. dependencies, then tests, then restart
ssh user@server 'cd CCTV_solutions && backend/.venv/bin/pip install -q -r backend/requirements-dev.txt'
ssh user@server 'cd CCTV_solutions && ./backend/test.sh -q'
ssh user@server 'systemctl --user restart numenor'
```

Schema migrations run automatically at startup and are logged:

```
numenor.migrations: Applied 3 migration(s): 001 camera coordinate system reference; …
```

They are versioned in `schema_migrations`, idempotent, and additive — a database
from the first release upgrades in place. Run the test suite on the server before
restarting; it catches a missing dependency before the service goes down.

---

## Monitoring

```bash
curl -s localhost:8000/api/health            # liveness
curl -s -H "Authorization: Bearer $T" localhost:8000/api/cameras/summary
curl -s -H "Authorization: Bearer $T" localhost:8000/api/preview/sessions
```

Watch for: preview sessions stuck at the cap (clients not stopping cleanly),
cameras drifting to `failed`, and calibrations turning `needs_recalibration`
after someone edits a coordinate system.

Session tokens are redacted from logs (`token=REDACTED`), and error messages pass
through a sanitiser that strips `user:pass@` from URLs.

---

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| Cannot sign in | The password is hashed and unrecoverable. Restart once with `NUMENOR_ADMIN_PASSWORD` set. |
| "Address rejected … resolves to the public address" | The camera is on a public IP. Allowlist it narrowly with `NUMENOR_EXTRA_CIDRS=<ip>/32`. |
| "The camera refused the RTSP connection" | Wrong port. Check whether the stream is on 554 or a NAT-mapped port. |
| "rejected the supplied username or password" | Credentials. Note that RTSP URLs percent-encode specials — enter `M@x…`, not `M%40x…`. |
| "The stream path was not found" | Wrong path. Hikvision main stream is usually `/Streaming/Channels/101`, substream `102`. |
| Snapshot works, preview does not | A preview slot is free but ffmpeg cannot sustain the transcode. Check CPU and `NUMENOR_MAX_PREVIEW_SESSIONS`. |
| "calibrated for 1920x1080, but the image is 1280x720" | The stream profile changed. Recalibrate at the new geometry. |
| `ModuleNotFoundError: numpy` | Dependencies not installed after an upgrade. Re-run the pip step. |
| 3D view blank or "lost its graphics context" | No hardware acceleration, common over remote desktops. The 2D grid needs no GPU. |
| Build fails with `EACCES … unlink dist/` | `setup.sh` was run as root. `sudo rm -rf frontend/dist` and rebuild as your user. |

---

## Demo data

```bash
backend/.venv/bin/python backend/seed_demo.py            # create
backend/.venv/bin/python backend/seed_demo.py --remove   # delete
```

A synthetic factory: four cameras at known ground-truth poses, fifteen reference
points, solved through the real solvers. Every record is labelled SYNTHETIC and
the cameras use documentation-range addresses (`192.0.2.0/24`) that answer
nothing. Remove it before handing the system over.
