import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Notice, Spinner } from "../components/ui";
import { ApiError, api, getToken } from "../lib/api";
import type { Camera, Site } from "../lib/types";
import type { EventKind, FactoryEvent, ReviewDecision } from "../lib/monitoringTypes";

/**
 * The incident inbox: what the analytics reported, and what a person made of it.
 *
 * Two separate questions are tracked, and the interface never merges them.
 * *Was the detection correct?* is Confirm or Dismiss. *Has somebody dealt with
 * this?* is Acknowledge. A supervisor can acknowledge an event they have
 * dismissed — they saw it, and it was wrong — and the record keeps both facts
 * with who said so and when.
 *
 * Event text states what was observed. It never draws a conclusion about what
 * that meant for the machine or the line.
 */

const KIND_OPTIONS: Array<{ value: EventKind | ""; label: string }> = [
  { value: "", label: "Any type" },
  { value: "restricted_entry", label: "Restricted-zone entry" },
  { value: "line_crossing", label: "Line crossing" },
  { value: "station_occupancy", label: "Station occupancy" },
  { value: "dwell_time", label: "Dwell time" },
  { value: "ppe_violation", label: "PPE check" },
];

const WINDOWS = [
  { value: "", label: "Any time" },
  { value: "1", label: "Last hour" },
  { value: "8", label: "Last 8 hours" },
  { value: "24", label: "Last 24 hours" },
  { value: "168", label: "Last 7 days" },
];

export default function IncidentsPage() {
  const [params, setParams] = useSearchParams();
  const [events, setEvents] = useState<FactoryEvent[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [note, setNote] = useState("");
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [sites, setSites] = useState<Site[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [cameraId, setCameraId] = useState(params.get("camera") ?? "");
  const [areaId, setAreaId] = useState(params.get("area") ?? "");
  const [kind, setKind] = useState<EventKind | "">((params.get("kind") as EventKind) ?? "");
  const [decision, setDecision] = useState<ReviewDecision | "">(
    (params.get("decision") as ReviewDecision) ?? "");
  const [sinceHours, setSinceHours] = useState(params.get("since") ?? "");
  const [selectedId, setSelectedId] = useState<number | null>(
    params.get("event") ? Number(params.get("event")) : null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const page = await api.listEvents({
        camera_id: cameraId || undefined,
        area_id: areaId || undefined,
        kind: kind || undefined,
        decision: decision || undefined,
        since_hours: sinceHours || undefined,
        limit: 200,
      });
      setEvents(page.items);
      setCounts(page.counts as unknown as Record<string, number>);
      setNote(page.note);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not load events.");
    } finally { setLoading(false); }
  }, [cameraId, areaId, kind, decision, sinceHours]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    Promise.all([api.listCameras(), api.tree()])
      .then(([cams, tree]) => { setCameras(cams); setSites(tree); })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    setParams((p) => {
      const set = (k: string, v: string) => { if (v) p.set(k, v); else p.delete(k); };
      set("camera", cameraId); set("area", areaId); set("kind", kind);
      set("decision", decision); set("since", sinceHours);
      set("event", selectedId ? String(selectedId) : "");
      return p;
    }, { replace: true });
  }, [cameraId, areaId, kind, decision, sinceHours, selectedId, setParams]);

  const areas = useMemo(() => sites.flatMap((s) =>
    s.buildings.flatMap((b) => b.floors.flatMap((f) =>
      f.areas.map((a) => ({ id: a.id, label: `${b.name} · ${f.name} · ${a.name}` }))))), [sites]);

  const selected = events.find((e) => e.id === selectedId) ?? null;

  const onReviewed = (updated: FactoryEvent) => {
    setEvents((list) => list.map((e) => (e.id === updated.id ? updated : e)));
    // The counters on the overview read the same records, so refresh ours too.
    load();
  };

  return (
    <main className="page">
      <div className="page-head">
        <div className="grow">
          <h1>Incidents</h1>
          <p className="sub">What the analytics reported, and what a person made of it.</p>
        </div>
        <div className="row" style={{ gap: 6 }}>
          <span className="badge badge-warn">{counts.awaiting_review ?? 0} awaiting review</span>
          <span className="badge badge-neutral">{counts.unacknowledged ?? 0} unacknowledged</span>
        </div>
      </div>

      {note && <Notice tone="info" title="Where these came from">{note}</Notice>}
      {error && <Notice tone="danger" title="Could not load">{error}</Notice>}

      <div className="card" style={{ marginBottom: 14 }}>
        <div className="toolbar">
          <select aria-label="Time" value={sinceHours}
                  onChange={(e) => setSinceHours(e.target.value)}>
            {WINDOWS.map((w) => <option key={w.value} value={w.value}>{w.label}</option>)}
          </select>
          <select aria-label="Area" value={areaId} onChange={(e) => setAreaId(e.target.value)}>
            <option value="">Any area</option>
            {areas.map((a) => <option key={a.id} value={a.id}>{a.label}</option>)}
          </select>
          <select aria-label="Camera" value={cameraId}
                  onChange={(e) => setCameraId(e.target.value)}>
            <option value="">Any camera</option>
            {cameras.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <select aria-label="Event type" value={kind}
                  onChange={(e) => setKind(e.target.value as EventKind | "")}>
            {KIND_OPTIONS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
          </select>
          <select aria-label="Review status" value={decision}
                  onChange={(e) => setDecision(e.target.value as ReviewDecision | "")}>
            <option value="">Any status</option>
            <option value="unreviewed">Awaiting review</option>
            <option value="confirmed">Confirmed</option>
            <option value="dismissed">Dismissed</option>
          </select>
          <div className="grow" />
          <button className="btn btn-sm" type="button" onClick={load}>Refresh</button>
        </div>
      </div>

      <div className="inbox-split">
        <section className="card" style={{ padding: 0, overflow: "hidden" }}>
          {loading ? <div style={{ padding: 24 }}><Spinner label="Loading events…" /></div>
            : events.length === 0 ? (
              <div style={{ padding: 24 }}>
                <strong>No events match</strong>
                <p className="small muted" style={{ marginTop: 6 }}>
                  Either nothing has been reported for these filters, or no detection service
                  is connected to report anything. Both look the same from here, which is why
                  the banner above says which it is.
                </p>
              </div>
            ) : (
              <ul className="event-list">
                {events.map((event) => (
                  <li key={event.id}>
                    <button type="button"
                            className={`event-row${event.id === selectedId ? " is-selected" : ""}`}
                            onClick={() => setSelectedId(event.id)}>
                      <span className={`event-dot decision-${event.decision}`} aria-hidden="true" />
                      <span className="grow">
                        <strong>{event.kind_label}</strong>
                        <span className="small faint" style={{ display: "block" }}>
                          {event.camera_name}{event.area ? ` · ${event.area}` : ""}
                        </span>
                      </span>
                      <span className="event-meta small faint">
                        {new Date(event.started_at).toLocaleString()}
                        <span style={{ display: "block" }}>
                          {event.decision === "unreviewed" ? "Awaiting review"
                            : event.decision === "confirmed" ? "Confirmed" : "Dismissed"}
                          {event.acknowledged ? " · acknowledged" : ""}
                        </span>
                      </span>
                      {event.is_sample && <span className="badge badge-neutral">sample</span>}
                    </button>
                  </li>
                ))}
              </ul>
            )}
        </section>

        <aside>
          {selected
            ? <EventDetail event={selected} onReviewed={onReviewed} />
            : <div className="card card-pad">
                <strong>No event selected</strong>
                <p className="small muted" style={{ marginTop: 6 }}>
                  Pick one on the left to see its evidence, the rule that produced it, and to
                  record a decision.
                </p>
              </div>}
        </aside>
      </div>
    </main>
  );
}

function EventDetail({ event, onReviewed }: {
  event: FactoryEvent; onReviewed: (e: FactoryEvent) => void;
}) {
  const [notes, setNotes] = useState(event.notes ?? "");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { setNotes(event.notes ?? ""); setError(null); }, [event.id, event.notes]);

  const act = async (what: "confirmed" | "dismissed" | "acknowledge" | "unacknowledge") => {
    setBusy(what); setError(null);
    try {
      const updated = what === "acknowledge"
        ? await api.acknowledgeEvent(event.id, { acknowledged: true, notes })
        : what === "unacknowledge"
          ? await api.acknowledgeEvent(event.id, { acknowledged: false, notes })
          : await api.reviewEvent(event.id, { decision: what, notes });
      onReviewed(updated);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not save that.");
    } finally { setBusy(null); }
  };

  return (
    <div className="card card-pad">
      <div className="row" style={{ alignItems: "baseline" }}>
        <strong className="grow">{event.kind_label}</strong>
        {event.is_sample && <span className="badge badge-neutral">sample data</span>}
      </div>
      <div className="small faint" style={{ marginBottom: 10 }}>
        {event.camera_name}{event.area ? ` · ${event.area}` : ""}
        {event.building ? ` · ${event.building}` : ""}
      </div>

      <Evidence event={event} />

      <dl className="kv small" style={{ marginTop: 12 }}>
        <dt>Started</dt>
        <dd>{new Date(event.started_at).toLocaleString()}</dd>
        <dt>Duration</dt>
        <dd>{event.duration_s != null ? `${event.duration_s} s` : "Unknown"}</dd>
        <dt>Triggering rule</dt>
        <dd>{event.rule_summary}</dd>
      </dl>

      <div className="section">
        <h4>What was observed</h4>
        {event.facts.length === 0 ? (
          <p className="small muted">No observations were recorded with this event.</p>
        ) : (
          <ul className="plain-list small">
            {event.facts.map((fact, i) => <li key={i}>{fact}</li>)}
          </ul>
        )}
      </div>

      <div className="section">
        <h4>Review</h4>
        <p className="hint" style={{ marginTop: 0 }}>
          Confirm or dismiss says whether the detection was <em>correct</em>. Acknowledging says
          somebody has <em>handled</em> it. They are recorded separately.
        </p>

        <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
          <button className={`btn btn-sm${event.decision === "confirmed" ? " btn-primary" : ""}`}
                  type="button" disabled={!!busy} onClick={() => act("confirmed")}>
            {busy === "confirmed" ? "Saving…" : "Confirm"}
          </button>
          <button className={`btn btn-sm${event.decision === "dismissed" ? " btn-primary" : ""}`}
                  type="button" disabled={!!busy} onClick={() => act("dismissed")}>
            {busy === "dismissed" ? "Saving…" : "Dismiss"}
          </button>
          <button className={`btn btn-sm${event.acknowledged ? " btn-primary" : ""}`}
                  type="button" disabled={!!busy}
                  onClick={() => act(event.acknowledged ? "unacknowledge" : "acknowledge")}>
            {busy?.startsWith("ack") || busy?.startsWith("unack") ? "Saving…"
              : event.acknowledged ? "Acknowledged" : "Acknowledge"}
          </button>
        </div>

        <dl className="kv small" style={{ marginTop: 10 }}>
          <dt>Decision</dt>
          <dd>
            {event.decision === "unreviewed" ? "Not reviewed yet"
              : `${event.decision === "confirmed" ? "Confirmed" : "Dismissed"} by `
                + `${event.decided_by ?? "someone"}`}
            {event.decided_at && (
              <span className="faint"> · {new Date(event.decided_at).toLocaleString()}</span>
            )}
          </dd>
          <dt>Acknowledged</dt>
          <dd>
            {event.acknowledged
              ? <>{event.acknowledged_by ?? "someone"}
                  {event.acknowledged_at && (
                    <span className="faint"> · {new Date(event.acknowledged_at).toLocaleString()}</span>
                  )}</>
              : "Not yet"}
          </dd>
        </dl>

        <div className="field" style={{ marginTop: 8 }}>
          <label htmlFor="ev-notes">Review notes</label>
          <textarea id="ev-notes" rows={3} value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                    placeholder="What did you see when you checked?" />
          <span className="hint">Saved with the next Confirm, Dismiss or Acknowledge.</span>
        </div>

        {error && <Notice tone="danger" title="Could not save">{error}</Notice>}
      </div>

      <Link className="btn btn-sm btn-block" to={`/cameras/${event.camera_id}`}>
        Open {event.camera_name}
      </Link>
    </div>
  );
}

function Evidence({ event }: { event: FactoryEvent }) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setSrc(null); setFailed(false);
    if (!event.has_snapshot) return;
    let url: string | null = null;
    let cancelled = false;
    const token = getToken();
    fetch(`/api/events/${event.id}/snapshot`,
          { headers: token ? { Authorization: `Bearer ${token}` } : undefined })
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error("no evidence"))))
      .then((blob) => {
        if (cancelled) return;
        url = URL.createObjectURL(blob);
        setSrc(url);
      })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; if (url) URL.revokeObjectURL(url); };
  }, [event.id, event.has_snapshot]);

  if (!event.has_snapshot) {
    return (
      <div className="evidence-empty">
        No clip or snapshot was stored with this event.
      </div>
    );
  }
  if (failed) {
    return (
      <div className="evidence-empty">
        The evidence file is no longer on disk.
      </div>
    );
  }
  if (!src) return <div className="evidence-empty">Loading evidence…</div>;

  return (
    <figure className="evidence">
      <img src={src} alt={`Evidence for ${event.kind_label}`} />
      {event.overlay && event.image_width && event.image_height && (
        <svg viewBox={`0 0 ${event.image_width} ${event.image_height}`}
             preserveAspectRatio="xMidYMid meet">
          {event.overlay.map((shape, i) => {
            const box = shape as { x?: number; y?: number; w?: number; h?: number; label?: string };
            if (box.x == null || box.y == null || box.w == null || box.h == null) return null;
            return (
              <g key={i}>
                <rect x={box.x} y={box.y} width={box.w} height={box.h}
                      fill="none" stroke="#1d4ed8" strokeWidth={3} />
                {box.label && (
                  <text x={box.x} y={box.y - 6} fontSize={18} fill="#fff"
                        stroke="#101620" strokeWidth={4} paintOrder="stroke">{box.label}</text>
                )}
              </g>
            );
          })}
        </svg>
      )}
    </figure>
  );
}
