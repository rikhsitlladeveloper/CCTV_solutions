# Security

What Numenor protects, how, and what it deliberately does not claim.

---

## Threat model

An on-premise camera manager is an attractive target for a specific reason: it
holds working credentials for every camera on the site, and it can be told to
make network requests on someone else's behalf. The design assumes:

* The operator UI is reachable by people who should not all be trusted equally.
* Camera credentials are reused elsewhere on the site more often than anyone
  admits.
* A "camera address" field is an obvious way to try to make the server fetch
  something it shouldn't.
* Logs, screenshots and exports get shared far more widely than the database.

Out of scope: a local root attacker on the server (they can read the key file),
physical access to the camera network, and vulnerabilities in ffmpeg or OpenCV
themselves.

---

## Camera credentials

**Encrypted at rest.** Passwords are encrypted with Fernet (AES-128-CBC with an
HMAC, from the `cryptography` package). The ciphertext is in the database; the
key is not.

**The key lives outside the database and outside the repository.** It is read
from `NUMENOR_SECRET_KEY`, or from `credential.key` in `NUMENOR_SECRET_DIR`
(`~/.numenor` by default), created `0600` on first use. Anyone who copies the
database still cannot decrypt anything.

**No API response can carry a password.** `CameraOut` exposes `has_password: bool`
and nothing more. There is no endpoint that returns a credential, and the
serialisation lives in one module (`serializers.py`) so this cannot be
accidentally undone in a router.

**Editing never round-trips a secret.** The edit form starts blank; an omitted or
empty password means *keep the stored one*. Removing it requires an explicit
`clear_password: true`. The browser never receives the existing password, so it
cannot leak from frontend state, a screenshot or a crash report.

**Credential-bearing URLs are sanitised everywhere.** RTSP URLs necessarily
contain `user:pass@`. Every string that could reach a log, an error message or an
API response passes through `media.sanitize()`, which strips the userinfo:

```
rtsp://admin:hunter2@192.168.1.9:554/live  →  rtsp://192.168.1.9:554/live
```

Stored test results, ffmpeg stderr and ONVIF faults all go through it. Tests
assert this.

**No credentials in seed data or examples.** `.env.example` ships with everything
commented out. The only passwords in the repository are fixtures for the
simulated ONVIF device in `backend/tests/`.

---

## Operator authentication

* Passwords hashed with **scrypt** (n=2^14, r=8, p=1), per-password salt.
* Sessions are **HMAC-SHA256 signed bearer tokens** with an expiry, verified in
  constant time. No server-side session store.
* A wrong username still runs the KDF, so it is not measurably faster than a
  wrong password.
* The account file is `0600` outside the repository. If no password is supplied,
  one is generated and printed to the console exactly once.
* Every configuration and preview route requires authentication. Only
  `/api/health` and `/api/auth/login` are public, and health reveals nothing.

### Tokens in URLs

`<img>` and SVG `<image>` cannot send an `Authorization` header, so the snapshot,
MJPEG stream and floor-plan image endpoints also accept `?token=`. This is a real
exposure — URLs reach logs and referrers — mitigated by:

* A logging filter that rewrites `token=…` to `token=REDACTED` in
  `uvicorn.access`, `uvicorn.error` and application logs.
* Short-lived tokens (12 h default, `NUMENOR_SESSION_TTL`).
* Same-origin requests, so no cross-site referrer.

If you front the app with a proxy, make sure it redacts query strings too.

---

## Outbound request policy (SSRF)

The camera address field is a request-forgery primitive unless it is constrained.
Every outbound call passes `netguard.resolve_camera_host()` before a socket
opens.

**Allowed:** RFC1918 private ranges and link-local, which is where cameras live.

**Refused:**

| | Why |
| --- | --- |
| Loopback | Not a camera; reaches services on the server itself |
| `169.254.169.254`, `fd00:ec2::254` | Cloud metadata |
| Multicast, reserved, unspecified | Not valid camera addresses |
| Public addresses | Unless explicitly allowlisted |
| Ports 22, 3306, 5432, 6379, 27017, 9200, 11211, 445, 3389, … | Infrastructure, not cameras |
| Anything that is not a bare host | `http://…`, `user@host`, `host/path` are rejected at validation |

**The resolved IP is pinned.** The connection is made to the address that was
vetted, not by re-resolving the name, which closes the DNS-rebinding window
between check and connect.

**Redirects are not followed.** An ONVIF snapshot endpoint cannot bounce the
server somewhere else.

**Nothing is ever scanned.** Numenor only contacts endpoints an operator
explicitly registered or typed. There is no discovery sweep, by design.

Widening the policy is deliberate and narrow:

```bash
NUMENOR_EXTRA_CIDRS=203.0.113.44/32     # one camera on a public IP
NUMENOR_ALLOW_PUBLIC_HOSTS=1            # all public addresses — avoid
NUMENOR_ALLOW_LOOPBACK=1                # test rigs only
```

> A camera exposed on a public IP is a poor idea regardless: ONVIF and RTSP send
> video unencrypted and internet-facing cameras are brute-forced constantly. A
> VPN or Tailscale link to the camera's site is the better answer.

---

## Media process handling

ffmpeg is always launched with an **argument list, never a shell**, so nothing in
a camera record — name, path, credentials — can be interpolated into a command
line.

Preview processes are bounded: a concurrency cap, an idle reaper for abandoned
sessions, a hard lifetime cap, and explicit teardown on stop or client
disconnect. Starting a second preview for one camera tears down the first.
`GET /api/preview/sessions` shows what is running.

Credential-bearing RTSP URLs exist only in process memory, keyed by session, and
are dropped once the stream attaches. **No RTSP URL is ever sent to the browser.**

---

## Uploads

Floor plans and photos must be PNG or JPEG. The declared content type is not
trusted — the bytes are decoded and verified with Pillow, and a size limit is
enforced during the read rather than after. Files are stored under the data
directory with generated names, never the client's filename.

---

## Input validation

Pydantic schemas reject malformed matrices, invalid rotations, non-finite
numbers and under-constrained point sets at the edge.

One subtlety worth recording: Pydantic echoes the offending value back in its
error detail, and a NaN cannot be encoded as JSON — which turned a clean 422 into
a 500. A custom handler now makes non-finite values safe for serialisation, so
rejecting bad geometry always produces a proper validation error.

---

## The calibration export

The export is designed to be handed to other services, so it carries **no
credentials, host addresses or stream URLs** — geometry only, plus the
conventions needed to interpret it and the limitations that apply. A test asserts
the absence of each of those.

---

## What this does not provide

* **No TLS of its own.** Bind to localhost and use Tailscale or a reverse proxy.
* **One operator account.** No roles, no per-user audit trail. Calibration
  revisions record `created_by`, but that is the session name, not an identity
  system.
* **No rate limiting** on the login endpoint. Reachability is the control.
* **No protection from a local root attacker**, who can read the key file.
* **No secret rotation tooling.** Replacing `credential.key` means re-entering
  every camera password.

---

## Reporting a problem

Open a private advisory on the repository rather than a public issue. Include the
class of problem and how to reproduce it; please do not include working
credentials from a live site.
