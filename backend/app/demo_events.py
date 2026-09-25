"""Sample events, for demonstrating the incident inbox.

Every row is written with ``is_sample=True`` and stays labelled as sample data
everywhere it is shown. Nothing here came from inference — no detection service
ships with this system — and the seeder is a deliberate command-line action, so
sample rows can never appear on a production install by accident.

The wording follows the same rule as a real event: state what was observed,
never what it meant. "No product crossed the line for 3 minutes" is an
observation. "The machine has stopped" is a conclusion, and is not ours to draw.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Camera, CameraFunction, Event, EventKind, FunctionKind, ReviewDecision
from .monitoring import EVENT_KIND_FOR_FUNCTION, rule_summary

# Observations, never conclusions.
FACTS: dict[EventKind, list[str]] = {
    EventKind.restricted_entry: [
        "A person was inside the region for {duration:.1f} s.",
        "The region was entered from the walkway side.",
    ],
    EventKind.line_crossing: [
        "One crossing was recorded from side A to side B.",
        "No further crossing was recorded for 3 minutes afterwards.",
    ],
    EventKind.station_occupancy: [
        "The station was occupied continuously for {duration:.0f} s.",
    ],
    EventKind.dwell_time: [
        "A person remained in view for {duration:.0f} s without leaving the region.",
    ],
    EventKind.ppe_violation: [
        "A person was detected inside the region.",
        "No high-visibility vest was matched on that person.",
    ],
}


def sample_count(db: Session) -> int:
    return len(list(db.scalars(select(Event).where(Event.is_sample.is_(True)))))


def remove_sample_events(db: Session) -> dict:
    rows = list(db.scalars(select(Event).where(Event.is_sample.is_(True))))
    for row in rows:
        db.delete(row)
    db.commit()
    return {"removed_sample_events": len(rows)}


def seed_sample_events(db: Session, per_function: int = 4, rng_seed: int = 20260925) -> dict:
    """Add sample events for every configured analytic that could produce them.

    Cameras with no analytics get nothing: an event with no rule behind it would
    be less honest than an empty inbox.
    """
    rng = random.Random(rng_seed)
    now = datetime.now(timezone.utc)

    functions = list(db.scalars(select(CameraFunction)))
    if not functions:
        return {"created": 0,
                "note": "No analytics are configured, so there are no rules to sample."}

    created = 0
    for function in functions:
        kind = EVENT_KIND_FOR_FUNCTION.get(function.kind)
        if kind is None:
            continue
        camera = db.get(Camera, function.camera_id)
        if camera is None:
            continue
        summary = rule_summary(function, camera)

        for i in range(per_function):
            minutes = rng.randint(5, 20 * 60)
            duration = round(rng.uniform(1.5, 40.0), 1)
            facts = [t.format(duration=duration) for t in FACTS.get(kind, [])]

            event = Event(
                camera_id=camera.id, function_id=function.id, kind=kind,
                started_at=now - timedelta(minutes=minutes),
                duration_s=duration,
                rule_summary=summary,
                facts_json=json.dumps(facts),
                image_width=function.image_width, image_height=function.image_height,
                is_sample=True,
            )
            # A spread of review states, so the inbox demonstrates all of them.
            # Acknowledgement is set independently of the decision, because in
            # real use the two do not move together.
            if i % 4 == 1:
                event.decision = ReviewDecision.confirmed
                event.decided_by = "sample"
                event.decided_at = event.started_at + timedelta(minutes=3)
            elif i % 4 == 2:
                event.decision = ReviewDecision.dismissed
                event.decided_by = "sample"
                event.decided_at = event.started_at + timedelta(minutes=5)
                event.notes = "Sample row: recorded as a false detection."
            if i % 3 == 0:
                event.acknowledged_by = "sample"
                event.acknowledged_at = event.started_at + timedelta(minutes=1)

            db.add(event)
            created += 1

    db.commit()
    return {
        "created": created,
        "note": ("Sample events only. Nothing was produced by inference; every row is "
                 "flagged is_sample and labelled in the interface."),
    }
