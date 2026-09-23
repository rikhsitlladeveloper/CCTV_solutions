# Installer guide

Getting cameras from "on the wall" to "the system knows where things are on the
floor", in seven steps. Open **Setup** and work straight through it.

Almost every calibration problem is a measurement problem, not a software
problem. The one rule worth remembering: **the system only knows what you
measure.** Clicking a spot on a grid records where you *say* something is; it
does not measure anything.

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
