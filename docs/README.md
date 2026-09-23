# Numenor documentation

On-premise camera registration, connection testing, and positioning in a shared
metric factory coordinate system.

| Document | For | Read it when |
| --- | --- | --- |
| [Installer guide](INSTALLER-GUIDE.md) | Installers, commissioning engineers | You are on site putting cameras into the system and calibrating them |
| [Operations](OPERATIONS.md) | Whoever runs the server | Installing, configuring, backing up, upgrading, or something is broken |
| [Architecture](ARCHITECTURE.md) | Developers, reviewers | You need to understand how it fits together and why |
| [API reference](API.md) | Integrators | You are calling the API or consuming a calibration |
| [Security](SECURITY.md) | Security review, IT | You need to know what is protected and what is not |
| [Development](DEVELOPMENT.md) | Contributors | You are changing the code |
| [Export example](calibration-export-example.json) | Downstream services | You are consuming calibration output |

The project [README](../README.md) is the overview.

---

## If you are new to this

**Positioning a camera, start to finish:** the [installer
guide](INSTALLER-GUIDE.md) is the sequence — define a coordinate system, survey
reference points, choose a method, solve, validate.

**Understanding the numbers:** three things are easy to conflate and are kept
apart everywhere. Connection status says the camera answered. Calibration status
says the geometry was solved. Validation says held-out survey points confirmed
it. See [Architecture → three independent notions of
verified](ARCHITECTURE.md#three-independent-notions-of-verified).

**The rotation convention will catch you once.** Zero roll/pitch/yaw points a
camera at the ceiling, not along the floor, and yaw runs opposite to map
heading. Both are explained in [Architecture → why geometry.py is the only
source of conventions](ARCHITECTURE.md#why-geometrypy-is-the-only-source-of-conventions),
and served live at `GET /api/coordinate-systems/conventions`.

**Consuming calibration downstream:** [`calibration-export-example.json`](calibration-export-example.json)
is a real export from the synthetic demo factory, including the conventions
needed to interpret `T_world_camera` and the limitations that apply to it.

---

## Scope

Numenor covers camera registration, connection testing, preview, physical
location, floor-plan placement, and camera geometry and calibration.

It deliberately does **not** do AI detection, person tracking,
re-identification, or analytics. Calibration is exported so those services can
consume it.
