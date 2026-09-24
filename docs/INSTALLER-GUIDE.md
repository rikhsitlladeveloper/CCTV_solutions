# Installer guide

Getting cameras from "on the wall" to "the system knows where things are on the
floor".

Almost every calibration problem is a measurement problem, not a software
problem. The one rule worth remembering: **the system only knows what you
measure.** Clicking a spot on a grid records where you *say* something is; it
does not measure anything.

## Two ways through, same data underneath

| | **Workspace** | **Guided setup** |
| --- | --- | --- |
| Where | the **Workspace** tab | the **Guided setup** tab |
| Shape | a drawing board: build the scene, drop cameras in, aim them | seven numbered steps, one screen each |
| Best for | laying out a site visually, seeing coverage, configuring what cameras do | working methodically through one camera's measurements |

They are two views of one set of records, not two systems. A camera mapped in
Guided setup shows its coverage in the Workspace immediately, and a camera
placed in the Workspace appears in Guided setup's camera list. Use whichever
suits the moment; you can switch at any point.

The Workspace is described first, because it is where most commissioning now
starts. The seven steps follow, and they remain the reference for the
measurement detail.

---

# The commissioning workspace

Open **Workspace**. Three panels: what is in the scene on the left, the scene
itself in the middle, and details of whatever is selected on the right.

Two modes, switched top right:

* **Setup** — everything can be edited.
* **Monitor** — the published scene, read-only, plus whatever live data exists.
  Today that is camera pictures and nothing else: no detection service ships
  with this system, and Monitor says so rather than drawing invented boxes.

## Create the scene

Pick an area, then build its floor:

* **Draw it.** Choose Wall, Machine, Rack, Conveyor and so on from the palette
  and click the scene. Boxes drop where you click; walls, conveyors and zones
  are drawn corner by corner.
* **Trace a floor plan.** Upload a plan image, set its scale by clicking two
  points a known distance apart, and draw over it.
* **Import a 3D model.** A GLB can be brought in and aligned to the floor.

**A floor plan is never required.** An empty grid with a few boxes on it is a
perfectly good scene, and most sites start that way.

Everything drawn this way is marked **estimated** — it records what you say is
there, at roughly the size you say it is. Only measured geometry is marked
measured, and the badge on the scene tells you which you are looking at. That
distinction is the whole reason the scene can be trusted: it never claims to be
a survey.

## Place and aim a camera

Open the **Cameras** tab on the left. Cameras appear in one of three groups:

* **Not in the scene** — nothing is known about where it is.
* **Mapped, not placed** — it has a working floor mapping, so positions in its
  picture already turn into real places, but where the camera itself hangs is
  unknown. Its mapped patch of floor is drawn in the scene, dashed.
* **Placed in this area** — its mount point and aim are known.

Press **Place**, click where the camera is mounted, and say how high it is and
what it is fixed to. No angles are typed in. Then **Aim here…** and click what
it should look at; the cone follows.

A placement made this way is **approximate** — you pointed at a spot, you did
not measure one. It is drawn dashed and labelled as such everywhere.

If the camera already has a solved mapping, the placement is saved but **not**
switched on, because a drag must never quietly replace measured geometry. You
are told this and offered the choice explicitly: keep the solved mapping, or use
the hand placement. Nothing is deleted either way, and every revision stays on
the camera's calibration page.

## Align live view

The **Align live view** button opens the camera picture beside the scene. You
point at the same landmark twice — once in the picture, once on the floor plan —
and the number and colour match on both sides.

The frame is deliberately frozen rather than live: a moving picture cannot be
clicked accurately, and a click has to be recorded against the exact frame it
was made on. **Refresh frame** takes a new one when the view has changed. Your
clicks are stored in the picture's own native pixels, so resizing the browser or
zooming changes nothing.

Two paths, chosen at the top:

* **Floor mapping** — turns a position in the picture into a place on the floor.
  Needs four or more landmarks that all lie flat on the same floor. Six or more,
  spread across the whole frame rather than clustered in one corner, work far
  better. This is what counting, zones and twin positions use.
* **Full camera alignment** — works out where the camera hangs and how it is
  angled, in three dimensions. Needs a lens calibration as well, and landmarks
  at more than one height, because points that all lie flat leave the answer
  ambiguous. This is what the 3D view, camera relationships and cross-camera
  tracking need.

The same landmarks serve both, so nothing is marked twice.

Once a mapping is live, the picture gains an overlay: one metre of real floor
projected through it. **Where the projected grid lies along the floor's own
lines, the mapping fits; where it drifts, it does not.** Red spurs show how far
each landmark's prediction misses its mark, in pixels. Both can be switched off.

## What should this camera do?

Each camera carries a list of functions. Counting lines and zone polygons are
drawn directly on its picture, with numbered handles you can drag; the shape is
stored in the frame's own pixels together with the frame size it was drawn
against, so a later change of stream resolution is detected rather than silently
mis-scaling the shape.

Counting and zone watching work **on the picture** and need no calibration at
all. Twin positions and cross-camera tracking need a floor mapping, and say so
until one exists.

Every function shows an honest status. **No detection service ships with this
system.** Settings are stored and exported so a processing service can pick them
up later; nothing here is analysing video, and the interface will not pretend
that it is.

## Camera links

Tick **Camera links** to draw the relationships between cameras onto the scene:
shared floor as a shaded patch, walking routes as a line. Verified links are
solid green; anything only suggested by geometry stays dashed amber, because a
guess must never read as a fact.

Click one to get both cameras' pictures side by side. Walk the shared area and
watch for the same person in both — if a link says they overlap and you cannot
see that, the link is wrong however good the arithmetic looked. Record what you
saw on the **Connections** page.

## See what is ready

**Readiness** lists nine capabilities and what each one still needs. It
separates flatly:

* what is **configured** (a counting line exists);
* what is **possible** (the geometry supports it);
* what is **running** (nothing, today — no processing service is connected).

A walk-through session records checkpoints as you walk the floor: where the
system thinks you are, where you actually were, and the difference. That is the
only measurement of accuracy that involves the real building rather than the
same numbers the mapping was fitted to.

---

# Guided setup, step by step

Open **Guided setup** and work straight through it.

---

## 0. The three things a camera can be

Shown separately everywhere, because none of them implies another.

| Badge | Means |
| --- | --- |
| **Connection** — Not tested / Reachable / Failed | Whether the camera answers. |
| **Calibration** — Not set up / Approximate / Mapping ready / Needs redoing | Whether it knows how its picture relates to the floor. |
| **Validation** — Not checked / Accuracy checked / Check failed | Whether that mapping has been tested against points it never saw. |

A camera can be reachable and uncalibrated, or calibrated and offline.

---

## Step 1 — Connect cameras

Add every camera watching the area and press **Check** on each. You need its
address, login, and either ONVIF or the RTSP stream path.

You do not need to know where the camera is mounted, its height, or which way it
points. None of that is ever typed in.

---

## Step 2 — Create the area

The area is the patch of floor these cameras watch. You are asked for:

* a name;
* the floor it is on;
* roughly how wide and long it is, in metres (approximate is fine — it only
  sizes the grid);
* **where you measure from** — a corner you can find again;
* **which way the first direction runs.**

Those last two matter more than anything else on the page. Every measurement you
take afterwards is relative to them, so write them down as if for someone
arriving in two years: *"the inside corner of column A1 where it meets the
floor"*, *"east along the north wall towards the loading doors"*.

**One area per floor.** Everything on a floor shares an origin so their
measurements can be compared. If you try to add a second area to a floor that
already has one, you are told to use the existing one.

A separate area *is* right when a surface cannot share one flat mapping: a
mezzanine, a raised platform, a ramp. Those need their own origin.

No floor plan is needed. The grid starts blank. A plan image can be laid behind
it later purely as a backdrop.

---

## Step 3 — Add floor points

Measure fixed marks on the floor — column bases, drain covers, the corners of
painted bays — and record how far each one is from your origin, along your two
directions.

* **Six to ten** spread across the area, for building the mapping.
* **Two or three more**, marked as *checking* points. These are never used to
  build the mapping, which is exactly what makes them able to test it.
* Spread them out. Points in a line, or clustered in one corner, cannot describe
  the rest of the floor.

Each point records how you measured it and, optionally, how accurate that
measurement is. Nothing built on a point can be better than the point itself.

> Clicking the grid places a pin where you say a mark is. Replace the numbers
> with tape-measure or laser values before calibrating against it.

---

## Step 4 — Match points

For each camera: pick a measured point from the list, then click exactly where
it appears in the picture.

* Zoom and pan the picture as you like — clicks are stored against the
  picture's own pixels, so display size changes nothing.
* **✥** moves a point you have already placed, **×** removes it, **Undo** steps
  back.
* **Refresh picture** grabs a fresh frame.
* Your work saves as you go; closing the browser loses nothing.

Cameras do not need the same points. Each matches whatever it can actually see
from the shared list.

The panel warns you as you go if points are nearly in a line, bunched in one
part of the frame, duplicated, or matched against a different image size than
before.

### Markers (optional)

**Detect markers** finds ArUco markers in the picture and proposes matches.
Every proposal is reviewed before it counts, and a marker ID only says *which*
marker it is — its position still comes from your survey. Markers must lie flat
on the floor being mapped. The manual workflow works fine without them.

---

## Step 5 — Calculate mapping

Press **Calculate mapping**. There is nothing to choose.

The system fits the mapping, discards any point that disagrees with the rest,
and refits without it. You get:

* the floor area your points cover — where the mapping actually works;
* which points were used and which were thrown out, by name;
* how closely the mapping matches the points it was built from;
* a plain explanation if it cannot be done.

A rejected point almost always means a mis-click or a mistyped measurement.
Fix the cause rather than accepting the result.

**Nothing is switched on.** The camera keeps whatever it had until you activate
the new mapping in the next step.

> How well a mapping matches its own points is not proof of accuracy. Step 6 is
> the actual test.

---

## Step 6 — Check accuracy

Compares your *checking* points — the ones held back in step 3 — against where
the mapping puts them.

You see each point's measured position, the predicted position, and the miss in
centimetres, plus the typical and worst error across all of them. Set the
acceptable limit yourself; 25 cm suits general monitoring.

Then press **Use this mapping** to put it into service. That is always a
separate, deliberate action, and earlier versions are kept.

You can save a mapping that has not been checked, or that failed. It just will
not be labelled as checked.

> The result speaks for the area your checking points cover, not the whole view.

---

## Step 7 — Connect cameras

Record how the views relate, for a tracking service to use later. Three kinds:

* **See the same ground** — both cameras cover a shared patch of floor. The
  system suggests these from the mapped areas, but suggestions must be confirmed
  on site: geometry cannot see racking.
* **People walk from one to the other** — a direction, optionally from a
  specific exit zone to a specific entrance zone, with the fastest and slowest
  plausible walking times. Walk it and time it. Each direction is its own record.
* **No direct link** — states that two views have no direct association. People
  can still travel between them through other cameras.

A pair with **no record is unknown, not impossible.** Only an explicit exclusion
says otherwise.

**Zones** are shapes drawn straight on the picture — a doorway, an aisle mouth.
They need no calibration; if the camera has a mapping, the zone also gets a
position on the floor.

Finally, **Download the layout** exports the mappings and this topology as JSON
for a downstream service. It contains no credentials.

---

## Cross-checking several cameras

**Accuracy check** lets you mark the same physical spot in several cameras and
compare where each one puts it.

* Without a surveyed position for that spot, this is labelled **cross-camera
  consistency**: it shows whether the cameras agree, and they can agree while all
  being wrong.
* Give it a surveyed position, or pick a reference point, and it becomes a real
  accuracy measurement.

Walking through the area is a useful sanity check but never a substitute for
measured validation.

---

## When something changes

The system marks a mapping **Needs redoing** and refuses to activate it when:

* a floor point is re-measured or deleted;
* a point changes between building and checking;
* the area's origin or direction description is edited (which needs explicit
  confirmation first);
* a camera moves to a different area.

Redrawing a zone marks any relationship built on it for rechecking.

Nothing is silently reused.

---

## Advanced

The **Advanced** sections hold the technical detail: the transform matrices, the
pixel convention, the fit tolerance, and measurement uncertainty. Full 3D
calibration — camera XYZ, roll/pitch/yaw, lens calibration, solver choice — is
still available on each camera's **Position & Calibration** page, and is
documented in [ARCHITECTURE.md](ARCHITECTURE.md) and [API.md](API.md).

Use the 3D route when you need to know where a camera physically is, or to
project points at heights other than the floor. For floor tracking, the guided
mapping is enough and needs no lens calibration.

---

## Demo

```bash
backend/.venv/bin/python backend/seed_demo.py            # guided example
backend/.venv/bin/python backend/seed_demo.py --remove
backend/.venv/bin/python backend/seed_demo.py --which pose    # the 3D example
```

Three synthetic cameras on a shared floor, twelve measured points, three
checking points, one confirmed overlap and one walking route — all solved
through the real pipeline. Everything is labelled SYNTHETIC and the cameras use
documentation addresses that answer nothing. Remove it before handover.

---

## Troubleshooting

| What you see | What it means |
| --- | --- |
| "At least 4 calibration points" | Match more measured points, or turn a checking point into a building point. |
| A point was left out of the mapping | It disagreed with the rest. Re-check that measurement and where you clicked. |
| "nearly in a straight line" | Spread the points across the area, not along an aisle. |
| "cover only 20% of the frame" | The mapping will be poor outside that patch. Match points nearer the edges. |
| "different image size" | The stream resolution changed. Re-match on the current picture. |
| "on the horizon line" | You clicked at or above the vanishing line, where the floor is undefined. |
| "Needs redoing" | Something it depends on changed. Recalculate. |
| Accuracy check fails on one point | Usually that point's own measurement. Check it before blaming the mapping. |
| No picture in the matching step | The camera is not answering. Go back to step 1. |
