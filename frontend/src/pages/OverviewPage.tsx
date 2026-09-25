import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import FloorPlanCanvas, { type Marker } from "../components/FloorPlanCanvas";
import PreviewPanel from "../components/PreviewPanel";
import { Notice, Spinner, StatusBadge, locationSummary } from "../components/ui";
import { ApiError, api } from "../lib/api";
import type { Camera, FloorPlan } from "../lib/types";
import type { CameraFunction } from "../lib/sceneTypes";
import type { FactoryOverview, FactoryEvent, MonitoringStateName } from "../lib/monitoringTypes";

/**
 * The supervisor's main screen.
 *
 * Two distinctions are load-bearing here and are never blurred.
 *
 * *Connected is not monitored.* A camera can stream perfectly and be analysing
 * nothing, so the health tiles count them separately and the map marks them
 * differently.
 *
 * *Absent is not zero.* A production count that cannot be produced is shown as
 * "Unavailable" with the reason. Printing 0 would assert that the line made
 * nothing, which is a different and much stronger claim.
 */

const STATE_LABEL: Record<MonitoringStateName, string> = {
  draft: "Draft",
  connected: "Connected",
  configured: "Configured",
  validation_pending: "Validation pending",
  active: "Active",
};

const STATE_TONE: Record<MonitoringStateName, string> = {
  draft: "badge-neutral",
  connected: "badge-neutral",
  configured: "badge-accent",
  validation_pending: "badge-warn",
  active: "badge-ok",
};

export default function OverviewPage() {
  const [overview, setOverview] = useState<FactoryOverview | null>(null);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [plan, setPlan] = useState<FloorPlan | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [functions, setFunctions] = useState<CameraFunction[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [body, list] = await Promise.all([api.overview(), api.listCameras()]);
      setOverview(body);
      setCameras(list);
      const first = body.floor_plans[0];
      setPlan(first ? await api.getFloorPlan(first.id).catch(() => null) : null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not load the factory overview.");
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  // The detail panel follows the selection wherever it was made.
  useEffect(() => {
    if (selectedId == null) { setFunctions([]); return; }
    let cancelled = false;
    api.listFunctions(selectedId)
      .then((fs) => { if (!cancelled) setFunctions(fs); })
      .catch(() => { if (!cancelled) setFunctions([]); });
    return () => { cancelled = true; };
  }, [selectedId]);

  const selected = cameras.find((c) => c.id === selectedId) ?? null;

  const markers: Marker[] = useMemo(() => cameras
    .filter((c) => c.placement && plan && c.placement.floor_plan_id === plan.id)
    .map((c) => ({
      id: c.id,
      label: c.name,
      norm_x: c.placement!.norm_x,
      norm_y: c.placement!.norm_y,
      heading_deg: c.placement!.heading_deg ?? 0,
      fov_deg: c.placement!.fov_deg ?? null,
      view_distance_m: c.placement!.view_distance_m ?? null,
      needs_review: c.placement!.review_status === "needs_review",
    })), [cameras, plan]);

  if (loading) return <Spinner label="Loading the factory overview…" />;
  if (error) return <Notice tone="danger" title="Could not load">{error}</Notice>;
  if (!overview) return null;

  const { health, events, production } = overview;

  return (
    <main className="page">
      <div className="page-head">
        <div className="grow">
          <h1>Factory overview</h1>
          <p className="sub">Camera health, events waiting for someone, and where everything is.</p>
        </div>
        <Link className="btn btn-primary" to="/setup-camera">＋ Set up a camera</Link>
      </div>

      <div className="tile-row">
        <Tile label="Cameras online" value={`${health.online}`}
              foot={`of ${health.total} registered`} tone={health.online ? "ok" : "neutral"} />
        <Tile label="Offline" value={`${health.offline}`}
              foot={health.untested ? `${health.untested} never tested` : "all tested"}
              tone={health.offline ? "danger" : "neutral"} />
        <Tile label="Degraded" value={`${health.degraded}`}
              foot="logged in, stream unverified"
              tone={health.degraded ? "warn" : "neutral"} />
        <Tile label="Monitoring active" value={`${health.monitoring_active}`}
              foot={`${health.with_analytics} have analytics configured`}
              tone={health.monitoring_active ? "ok" : "neutral"} />
        <Tile label="Awaiting review" value={`${events.awaiting_review}`}
              foot={`in the last ${events.window_hours} h`}
              tone={events.awaiting_review ? "warn" : "neutral"}
              to="/incidents" />
        <Tile label="Production count"
              value={production.available && production.value != null
                ? `${production.value}` : "Unavailable"}
              foot={production.unavailable_reason ?? "for the selected line and shift"}
              tone="neutral" muted={!production.available} />
      </div>

      <p className="small muted" style={{ marginTop: -4, marginBottom: 16 }}>{health.note}</p>

      <div className="overview-split">
        <section>
          {plan ? (
            <div className="card" style={{ padding: 0, overflow: "hidden" }}>
              <FloorPlanCanvas
                plan={plan} markers={markers} selectedId={selectedId}
                onSelect={setSelectedId} height={520} readOnly
              />
            </div>
          ) : (
            <NoPlan groups={overview.groups} selectedId={selectedId} onSelect={setSelectedId} />
          )}

          {plan && markers.length < cameras.length && (
            <p className="hint" style={{ marginTop: 8 }}>
              {cameras.length - markers.length} camera
              {cameras.length - markers.length === 1 ? " is" : "s are"} not on this plan.
              They are listed by area below and work exactly the same.
            </p>
          )}

          {plan && (
            <AreaGroups groups={overview.groups} selectedId={selectedId}
                        onSelect={setSelectedId} compact />
          )}
        </section>

        <aside>
          <CameraDetail camera={selected} functions={functions} />
          <RecentIncidents events={overview.recent_events} />
        </aside>
      </div>
    </main>
  );
}

function Tile({ label, value, foot, tone, muted, to }: {
  label: string; value: string; foot: string; tone: string; muted?: boolean; to?: string;
}) {
  const body = (
    <>
      <span className="tile-label">{label}</span>
      <span className={`tile-value tone-${tone}${muted ? " is-muted" : ""}`}>{value}</span>
      <span className="tile-foot">{foot}</span>
    </>
  );
  return to
    ? <Link className="tile tile-link" to={to}>{body}</Link>
    : <div className="tile">{body}</div>;
}

function NoPlan({ groups, selectedId, onSelect }: {
  groups: FactoryOverview["groups"]; selectedId: number | null;
  onSelect: (id: number) => void;
}) {
  return (
    <>
      <Notice tone="info" title="No floor plan for this factory yet">
        Cameras are grouped by building and area below. Everything works without a plan —
        a plan only adds a picture to point at.
        {" "}<Link to="/floor-plans">Add a floor plan</Link>
      </Notice>
      <AreaGroups groups={groups} selectedId={selectedId} onSelect={onSelect} />
    </>
  );
}

function AreaGroups({ groups, selectedId, onSelect, compact }: {
  groups: FactoryOverview["groups"]; selectedId: number | null;
  onSelect: (id: number) => void; compact?: boolean;
}) {
  if (!groups.length) {
    return <Notice tone="info" title="No cameras registered yet">
      <Link to="/setup-camera">Set up the first camera</Link>.
    </Notice>;
  }
  return (
    <div className={compact ? "area-groups is-compact" : "area-groups"}>
      {groups.map((group) => (
        <div className="card card-pad" key={`${group.building}:${group.area}`}>
          <div className="row" style={{ alignItems: "baseline" }}>
            <strong className="grow">{group.area}</strong>
            <span className="small faint">
              {group.building}{group.floor ? ` · ${group.floor}` : ""}
            </span>
          </div>
          <div className="camera-chips">
            {group.cameras.map((camera) => (
              <button key={camera.id} type="button"
                      className={`camera-chip${camera.id === selectedId ? " is-selected" : ""}`}
                      onClick={() => onSelect(camera.id)}>
                <span className={`dot dot-${camera.connection === "online" ? "ok"
                  : camera.connection === "partial" ? "warn"
                  : camera.connection === "untested" ? "neutral" : "danger"}`} />
                <span className="grow">{camera.name}</span>
                <span className={`badge ${STATE_TONE[camera.monitoring]}`}>
                  {STATE_LABEL[camera.monitoring]}
                </span>
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function CameraDetail({ camera, functions }: {
  camera: Camera | null; functions: CameraFunction[];
}) {
  if (!camera) {
    return (
      <div className="card card-pad">
        <strong>No camera selected</strong>
        <p className="small muted" style={{ marginTop: 6 }}>
          Pick a camera on the plan or in an area list to see its preview, what it is
          watching for, and whether it is reachable.
        </p>
      </div>
    );
  }
  return (
    <div className="card card-pad">
      <div className="row" style={{ alignItems: "baseline", marginBottom: 4 }}>
        <strong className="grow">{camera.name}</strong>
        <StatusBadge status={camera.last_test_status} />
      </div>
      <div className="small faint" style={{ marginBottom: 10 }}>{locationSummary(camera)}</div>

      <PreviewPanel cameraId={camera.id} compact />

      <div className="section" style={{ marginTop: 12 }}>
        <h4>Analytics</h4>
        {functions.length === 0 ? (
          <p className="small muted">
            None configured. This camera is connected but is not watching for anything.
          </p>
        ) : (
          <ul className="plain-list small">
            {functions.map((f) => (
              <li key={f.id} className="row" style={{ gap: 6 }}>
                <span className="grow">{f.name}</span>
                <span className={`badge ${f.status.state === "running" ? "badge-ok"
                  : f.status.state === "blocked" ? "badge-danger" : "badge-neutral"}`}>
                  {f.status.summary}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="row" style={{ gap: 6, marginTop: 12 }}>
        <Link className="btn btn-sm btn-primary" to={`/setup-camera?camera=${camera.id}`}>
          Configure
        </Link>
        <Link className="btn btn-sm" to={`/cameras/${camera.id}`}>Details</Link>
        <Link className="btn btn-sm" to={`/incidents?camera=${camera.id}`}>Events</Link>
      </div>
    </div>
  );
}

function RecentIncidents({ events }: { events: FactoryEvent[] }) {
  return (
    <div className="card card-pad" style={{ marginTop: 14 }}>
      <div className="row" style={{ alignItems: "baseline", marginBottom: 6 }}>
        <strong className="grow">Recent events</strong>
        <Link className="small" to="/incidents">All events</Link>
      </div>
      {events.length === 0 ? (
        <p className="small muted">
          Nothing recorded. No detection service is connected to this deployment, so events
          only appear if one posts them or sample data is loaded.
        </p>
      ) : (
        <ul className="plain-list">
          {events.map((event) => (
            <li key={event.id} className="incident-row">
              <Link to={`/incidents?event=${event.id}`} className="grow">
                <strong className="small">{event.kind_label}</strong>
                <span className="small faint" style={{ display: "block" }}>
                  {event.camera_name}
                  {event.area ? ` · ${event.area}` : ""} · {new Date(event.started_at).toLocaleString()}
                </span>
              </Link>
              {event.is_sample && <span className="badge badge-neutral">sample</span>}
              {event.decision === "unreviewed" && <span className="badge badge-warn">new</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
