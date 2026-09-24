import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError, api } from "../lib/api";
import CameraImagePicker from "../components/CameraImagePicker";
import { Guidance } from "../components/setupUi";
import { Modal, Notice, Spinner } from "../components/ui";
import type { Workspace } from "../lib/setupTypes";
import type { Capability, CommissioningSession, Readiness } from "../lib/sceneTypes";
import type { Camera } from "../lib/types";

const MARK: Record<Capability["state"], { glyph: string; cls: string; word: string }> = {
  ready: { glyph: "✓", cls: "cap-ready", word: "Ready" },
  partial: { glyph: "◐", cls: "cap-partial", word: "Partly done" },
  blocked: { glyph: "!", cls: "cap-blocked", word: "Not started" },
  unavailable: { glyph: "—", cls: "cap-unavailable", word: "Not available here" },
};

export default function ReadinessPage() {
  const [params, setParams] = useSearchParams();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState<number | null>(
    params.get("workspace") ? Number(params.get("workspace")) : null);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [sessions, setSessions] = useState<CommissioningSession[]>([]);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [walkthrough, setWalkthrough] = useState<CommissioningSession | null>(null);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    api.listWorkspaces().then((list) => {
      setWorkspaces(list);
      if (!workspaceId && list.length) setWorkspaceId(list[0].id);
      if (!list.length) setLoading(false);
    }).catch(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const load = useCallback(async () => {
    if (!workspaceId) return;
    setLoading(true);
    try {
      const [r, s, c] = await Promise.all([
        api.readiness(workspaceId),
        api.listSessions(workspaceId),
        api.listCameras(),
      ]);
      setReadiness(r);
      setSessions(s);
      setCameras(c.filter((x) => x.coordinate_system_id === workspaceId));
      setParams((p) => { p.set("workspace", String(workspaceId)); return p; }, { replace: true });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not load readiness.");
    } finally { setLoading(false); }
  }, [workspaceId, setParams]);

  useEffect(() => { load(); }, [load]);

  if (loading && !readiness) return <main className="page"><Spinner label="Checking…" /></main>;
  if (!workspaces.length) {
    return (
      <main className="page">
        <Notice tone="info" title="Nothing to report yet">
          Create an area and a scene first. <Link to="/workspace">Open the workspace</Link>.
        </Notice>
      </main>
    );
  }

  return (
    <main className="page">
      <div className="page-head">
        <div className="grow">
          <h1>What is ready</h1>
          <p className="page-sub">
            Each capability on its own, with the next useful thing to do about it.
          </p>
        </div>
        <select value={workspaceId ?? ""} aria-label="Area"
                onChange={(e) => setWorkspaceId(Number(e.target.value))}>
          {workspaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
        </select>
      </div>

      {error && <Notice tone="danger" title="Something went wrong">{error}</Notice>}

      {readiness && (
        <>
          <Guidance>
            Partial commissioning is normal and useful. A camera can be set up to count people in
            its picture while its floor geometry is still approximate — those are separate things,
            so they are reported separately.
          </Guidance>

          {readiness.capabilities.map((cap) => {
            const mark = MARK[cap.state];
            return (
              <div className="capability" key={cap.label}>
                <div className={`capability-mark ${mark.cls}`} aria-hidden="true">{mark.glyph}</div>
                <div className="grow">
                  <div className="row">
                    <strong className="grow">{cap.label}</strong>
                    <span className="small muted">{mark.word}</span>
                  </div>
                  <div className="small muted" style={{ marginTop: 3 }}>{cap.detail}</div>
                  {cap.next_step && (
                    <div className="small" style={{ marginTop: 6, color: "var(--accent)" }}>
                      Next: {cap.next_step}
                    </div>
                  )}
                </div>
              </div>
            );
          })}

          <div className="card card-pad" style={{ marginTop: 16 }}>
            <strong>Why there is no single “setup complete” badge</strong>
            <p className="small muted" style={{ marginTop: 6 }}>{readiness.interpretation}</p>
          </div>

          <div className="card card-pad" style={{ marginTop: 12 }}>
            <strong>Digital twin, not a simulation</strong>
            <p className="small muted" style={{ marginTop: 6 }}>{readiness.simulation_note}</p>
          </div>

          <div className="card" style={{ marginTop: 16 }}>
            <div className="card-head">
              <h2 className="grow">Walk-through tests</h2>
              <button className="btn btn-sm" type="button" disabled={!cameras.length}
                      onClick={() => setStarting(true)}>
                Start a session
              </button>
            </div>
            <div className="card-pad">
              {sessions.length === 0 ? (
                <p className="small muted" style={{ margin: 0 }}>
                  None yet. A walk-through records what you saw, where, and when — with a real
                  error figure wherever you give a measured position.
                </p>
              ) : (
                <div className="table-wrap">
                  <table className="data">
                    <thead>
                      <tr><th>Session</th><th>Started</th><th>Checkpoints</th>
                          <th>Measured</th><th /></tr>
                    </thead>
                    <tbody>
                      {sessions.map((s) => (
                        <tr key={s.id}>
                          <td className="cell-name">{s.name}</td>
                          <td className="small">
                            {new Date(s.started_at).toLocaleString(undefined,
                              { dateStyle: "short", timeStyle: "short" })}
                            {!s.ended_at && <span className="badge badge-accent"
                                                  style={{ marginLeft: 6 }}>open</span>}
                          </td>
                          <td>{s.accuracy.checkpoints}</td>
                          <td>
                            {s.accuracy.with_measured_position > 0
                              ? <>{s.accuracy.mean_error_m != null
                                    ? `${(s.accuracy.mean_error_m * 100).toFixed(0)} cm mean`
                                    : "—"}</>
                              : <span className="faint">observations only</span>}
                          </td>
                          <td style={{ textAlign: "right" }}>
                            <button className="btn btn-sm" type="button"
                                    onClick={() => setWalkthrough(s)}>Open</button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        </>
      )}

      {starting && workspaceId && (
        <StartSessionModal
          workspaceId={workspaceId} cameras={cameras}
          onClose={() => setStarting(false)}
          onStarted={async (session) => { setStarting(false); await load(); setWalkthrough(session); }}
          onError={setError}
        />
      )}

      {walkthrough && (
        <WalkthroughModal
          session={walkthrough} cameras={cameras}
          onClose={() => { setWalkthrough(null); load(); }}
          onUpdated={setWalkthrough}
          onError={setError}
        />
      )}
    </main>
  );
}

function StartSessionModal({ workspaceId, cameras, onClose, onStarted, onError }: {
  workspaceId: number; cameras: Camera[]; onClose: () => void;
  onStarted: (s: CommissioningSession) => void; onError: (m: string) => void;
}) {
  const [name, setName] = useState(`Walk-through ${new Date().toLocaleDateString()}`);
  const [chosen, setChosen] = useState<number[]>(cameras.map((c) => c.id));
  const [busy, setBusy] = useState(false);

  return (
    <Modal title="Start a walk-through" onClose={onClose}
           footer={<>
             <button className="btn" type="button" onClick={onClose}>Cancel</button>
             <button className="btn btn-primary" type="button" disabled={busy || !name.trim()}
                     onClick={async () => {
                       setBusy(true);
                       try {
                         onStarted(await api.startSession({
                           workspace_id: workspaceId, name: name.trim(), camera_ids: chosen }));
                       } catch (e) {
                         onError(e instanceof ApiError ? e.message : "Could not start.");
                         setBusy(false);
                       }
                     }}>Start</button>
           </>}>
      <Notice tone="info" title="What a walk-through is for">
        Stand on a known spot, mark it in each camera, and record what you see. Where you give a
        measured position, you get a real error in centimetres. Without one, it is an observation —
        useful, but not an accuracy figure.
      </Notice>
      <div className="field">
        <label htmlFor="sess-name">Name</label>
        <input id="sess-name" type="text" value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="field">
        <span className="field-label">Cameras</span>
        {cameras.map((c) => (
          <label key={c.id} className="row small" style={{ gap: 6 }}>
            <input type="checkbox" checked={chosen.includes(c.id)}
                   onChange={(e) => setChosen((prev) =>
                     e.target.checked ? [...prev, c.id] : prev.filter((x) => x !== c.id))} />
            {c.name}
          </label>
        ))}
      </div>
    </Modal>
  );
}

function WalkthroughModal({ session, cameras, onClose, onUpdated, onError }: {
  session: CommissioningSession; cameras: Camera[];
  onClose: () => void; onUpdated: (s: CommissioningSession) => void; onError: (m: string) => void;
}) {
  const inSession = cameras.filter((c) => session.camera_ids.includes(c.id));
  const [cameraId, setCameraId] = useState<number | null>(inSession[0]?.id ?? null);
  const [label, setLabel] = useState("");
  const [pixel, setPixel] = useState<{ u: number; v: number } | null>(null);
  const [frame, setFrame] = useState<{ w: number; h: number } | null>(null);
  const [measured, setMeasured] = useState({ x: "", y: "" });
  const [observation, setObservation] = useState("");
  const [busy, setBusy] = useState(false);

  const record = async () => {
    setBusy(true);
    try {
      const updated = await api.addCheckpoint(session.id, {
        camera_id: cameraId,
        label: label.trim() || "Checkpoint",
        pixel_u: pixel?.u ?? null, pixel_v: pixel?.v ?? null,
        image_width: pixel ? frame?.w ?? null : null,
        image_height: pixel ? frame?.h ?? null : null,
        measured_x: measured.x ? Number(measured.x) : null,
        measured_y: measured.y ? Number(measured.y) : null,
        observation: observation || null,
      });
      onUpdated(updated);
      setLabel(""); setPixel(null); setMeasured({ x: "", y: "" }); setObservation("");
    } catch (e) {
      onError(e instanceof ApiError ? e.message : "Could not record that.");
    } finally { setBusy(false); }
  };

  return (
    <Modal title={session.name} wide onClose={onClose}
           footer={<>
             {!session.ended_at && (
               <button className="btn" type="button"
                       onClick={async () => onUpdated(await api.endSession(session.id))}>
                 End session
               </button>
             )}
             <div className="grow" />
             <button className="btn" type="button" onClick={onClose}>Close</button>
           </>}>
      <Notice tone="info" title="How this is scored">{session.accuracy.interpretation}</Notice>

      <div className="field">
        <label htmlFor="wt-cam">Camera</label>
        <select id="wt-cam" value={cameraId ?? ""}
                onChange={(e) => { setCameraId(Number(e.target.value)); setPixel(null); }}>
          {inSession.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
      </div>

      {cameraId && (
        <CameraImagePicker
          cameraId={cameraId} height={300}
          marks={pixel ? [{ id: "cp", u: pixel.u, v: pixel.v, label: label || "checkpoint" }] : []}
          selectedId={null}
          onImageLoaded={(w, h) => setFrame({ w, h })}
          onPick={(u, v) => setPixel({ u, v })}
          hint="Click where you are standing, if the camera can see you."
        />
      )}

      <div className="field-row" style={{ marginTop: 12 }}>
        <div className="field">
          <label htmlFor="wt-label">What is this checkpoint?</label>
          <input id="wt-label" type="text" value={label} placeholder="Standing on the aisle marker"
                 onChange={(e) => setLabel(e.target.value)} />
        </div>
      </div>
      <div className="field-row">
        <div className="field">
          <label htmlFor="wt-mx">Measured X (m, optional)</label>
          <input id="wt-mx" type="number" step="0.01" value={measured.x}
                 onChange={(e) => setMeasured((m) => ({ ...m, x: e.target.value }))} />
        </div>
        <div className="field">
          <label htmlFor="wt-my">Measured Y (m, optional)</label>
          <input id="wt-my" type="number" step="0.01" value={measured.y}
                 onChange={(e) => setMeasured((m) => ({ ...m, y: e.target.value }))} />
          <span className="hint">Give these and you get a real error figure.</span>
        </div>
      </div>
      <div className="field">
        <label htmlFor="wt-obs">What did you see?</label>
        <input id="wt-obs" type="text" value={observation}
               placeholder="Marker clearly visible; slight glare from the skylight."
               onChange={(e) => setObservation(e.target.value)} />
      </div>
      <button className="btn btn-primary" type="button" disabled={busy} onClick={record}>
        {busy ? "Recording…" : "Record checkpoint"}
      </button>

      {session.checkpoints.length > 0 && (
        <>
          <div className="section-title">Recorded ({session.checkpoints.length})</div>
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th>When</th><th>Camera</th><th>What</th><th>Projected</th>
                         <th>Measured</th><th>Out by</th></tr></thead>
              <tbody>
                {session.checkpoints.map((cp) => (
                  <tr key={cp.id}>
                    <td className="small">
                      {new Date(cp.recorded_at).toLocaleTimeString(undefined,
                        { timeStyle: "medium" })}
                    </td>
                    <td className="small">{cp.camera_name ?? "—"}</td>
                    <td className="small">
                      {cp.label}
                      {cp.observation && <div className="faint">{cp.observation}</div>}
                    </td>
                    <td className="mono small">
                      {cp.projected_x != null
                        ? `${cp.projected_x.toFixed(2)}, ${cp.projected_y!.toFixed(2)}`
                        : <span className="faint">no mapping</span>}
                    </td>
                    <td className="mono small">
                      {cp.measured_x != null
                        ? `${cp.measured_x.toFixed(2)}, ${cp.measured_y!.toFixed(2)}`
                        : <span className="faint">—</span>}
                    </td>
                    <td>
                      {cp.error_m != null
                        ? <strong>{(cp.error_m * 100).toFixed(0)} cm</strong>
                        : <span className="faint small">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Modal>
  );
}
