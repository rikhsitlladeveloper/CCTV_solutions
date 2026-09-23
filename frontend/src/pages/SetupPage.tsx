import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError, api } from "../lib/api";
import CameraImagePicker, { type ImageMark } from "../components/CameraImagePicker";
import FactoryGrid from "../components/FactoryGrid";
import {
  Advanced, CameraStatusRow, ErrorValue, Guidance, IssueList, StepProgress,
} from "../components/setupUi";
import { Modal, Notice, Spinner } from "../components/ui";
import type { Camera, Site } from "../lib/types";
import type { CalibrationRevision, FactoryMap } from "../lib/calibrationTypes";
import type {
  AccuracyResponse, FloorPoint, Job, MappingResult, MatchStatus, Workspace,
} from "../lib/setupTypes";

const STEPS = [
  "Connect cameras",
  "Create area",
  "Add floor points",
  "Match points",
  "Calculate mapping",
  "Check accuracy",
  "Connect cameras",
];

export default function SetupPage() {
  const [params, setParams] = useSearchParams();
  const [step, setStep] = useState(Number(params.get("step") ?? 0));
  const [furthest, setFurthest] = useState(Number(params.get("step") ?? 0));
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [points, setPoints] = useState<FloorPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);

  const say = (message: string) => { setFlash(message); setTimeout(() => setFlash(null), 6000); };

  const goTo = useCallback((next: number) => {
    setStep(next);
    setFurthest((f) => Math.max(f, next));
    setParams((p) => { p.set("step", String(next)); return p; }, { replace: true });
  }, [setParams]);

  const loadAll = useCallback(async () => {
    try {
      const [list, cams] = await Promise.all([api.listWorkspaces(), api.listCameras()]);
      setWorkspaces(list);
      setCameras(cams);
      const wanted = params.get("workspace");
      const chosen = list.find((w) => String(w.id) === wanted) ?? list[0] ?? null;
      setWorkspace(chosen);
      if (chosen) setPoints(await api.listFloorPoints(chosen.id));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not load the setup.");
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { loadAll(); }, [loadAll]);

  const refreshPoints = useCallback(async () => {
    if (workspace) setPoints(await api.listFloorPoints(workspace.id));
  }, [workspace]);

  const refreshCameras = useCallback(async () => setCameras(await api.listCameras()), []);

  const selectWorkspace = async (ws: Workspace) => {
    setWorkspace(ws);
    setParams((p) => { p.set("workspace", String(ws.id)); return p; }, { replace: true });
    setPoints(await api.listFloorPoints(ws.id));
  };

  if (loading) return <main className="page"><Spinner label="Loading…" /></main>;

  return (
    <main className="page page-wide">
      <div className="page-head">
        <div className="grow">
          <h1>Camera setup</h1>
          <p className="page-sub">
            Seven steps from a camera on the wall to a system that knows where things are
            on the floor.
          </p>
        </div>
        {workspace && (
          <select value={workspace.id} aria-label="Area"
                  onChange={(e) => {
                    const ws = workspaces.find((w) => w.id === Number(e.target.value));
                    if (ws) selectWorkspace(ws);
                  }}>
            {workspaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
        )}
      </div>

      {error && <Notice tone="danger" title="Something went wrong">{error}</Notice>}
      {flash && <Notice tone="ok" title={flash} />}

      <StepProgress steps={STEPS} current={step} furthest={furthest} onJump={goTo} />

      {step === 0 && (
        <StepConnect cameras={cameras} onRefresh={refreshCameras} onNext={() => goTo(1)} />
      )}
      {step === 1 && (
        <StepArea
          workspaces={workspaces}
          workspace={workspace}
          onSelect={selectWorkspace}
          onCreated={async (ws) => {
            setWorkspaces((prev) => [...prev, ws]);
            await selectWorkspace(ws);
            say("Area created.");
            goTo(2);
          }}
          onError={setError}
          onNext={() => goTo(2)}
        />
      )}
      {step === 2 && workspace && (
        <StepFloorPoints
          workspace={workspace}
          points={points}
          cameras={cameras}
          onChanged={refreshPoints}
          onError={setError}
          onNext={() => goTo(3)}
          onSay={say}
        />
      )}
      {step === 3 && workspace && (
        <StepMatch
          workspace={workspace}
          points={points}
          cameras={cameras}
          onCamerasChanged={refreshCameras}
          onError={setError}
          onNext={() => goTo(4)}
          onSay={say}
        />
      )}
      {step === 4 && workspace && (
        <StepCalculate workspace={workspace} cameras={cameras}
                       onError={setError} onNext={() => goTo(5)} onSay={say} />
      )}
      {step === 5 && workspace && (
        <StepAccuracy workspace={workspace} cameras={cameras}
                      onError={setError} onNext={() => goTo(6)} onSay={say} />
      )}
      {step === 6 && workspace && <StepRelationships workspace={workspace} />}

      {!workspace && step > 1 && (
        <Notice tone="info" title="Create an area first">
          Step 2 sets up the area these cameras are watching.
        </Notice>
      )}
    </main>
  );
}

/* ---------------- Step 1: connect cameras ---------------- */

function StepConnect({ cameras, onRefresh, onNext }: {
  cameras: Camera[]; onRefresh: () => Promise<void>; onNext: () => void;
}) {
  const [testing, setTesting] = useState<number | null>(null);
  const reachable = cameras.filter((c) => c.last_test_status === "online");

  return (
    <div className="card card-pad">
      <Guidance>
        Add every camera that watches this area and check each one answers. You only need the
        camera's address and login — where it sits on the floor comes later, and you never have
        to type any coordinates for the camera itself.
      </Guidance>

      {cameras.length === 0 ? (
        <div className="empty">
          <div className="empty-mark" aria-hidden="true">＋</div>
          <h2>No cameras yet</h2>
          <p>Register the cameras watching this area, then come back here.</p>
          <Link className="btn btn-primary" to="/cameras/new">Register a camera</Link>
        </div>
      ) : (
        <>
          <div className="row" style={{ marginBottom: 12 }}>
            <strong>{reachable.length} of {cameras.length} answering</strong>
            <div className="grow" />
            <Link className="btn btn-sm" to="/cameras/new">＋ Register another</Link>
          </div>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr><th>Camera</th><th>Address</th><th>Where</th><th>Connection</th><th /></tr>
              </thead>
              <tbody>
                {cameras.map((camera) => (
                  <tr key={camera.id}>
                    <td><Link className="cell-name" to={`/cameras/${camera.id}`}>{camera.name}</Link></td>
                    <td className="mono small">{camera.host}</td>
                    <td className="small">{camera.location.area ?? <span className="faint">—</span>}</td>
                    <td><CameraStatusRow connection={camera.last_test_status} revision={null} compact /></td>
                    <td style={{ textAlign: "right" }}>
                      <button className="btn btn-sm" type="button" disabled={testing === camera.id}
                              onClick={async () => {
                                setTesting(camera.id);
                                try { await api.testCamera(camera.id); await onRefresh(); }
                                finally { setTesting(null); }
                              }}>
                        {testing === camera.id ? "Checking…" : "Check"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {reachable.length === 0 && (
            <Notice tone="warn" title="None are answering yet">
              You can still carry on and fix connections later, but you will not be able to match
              points without a picture from the camera.
            </Notice>
          )}
          <div className="row" style={{ marginTop: 16 }}>
            <button className="btn btn-primary" type="button" onClick={onNext}>
              Next: create the area
            </button>
          </div>
        </>
      )}
    </div>
  );
}

/* ---------------- Step 2: create the area ---------------- */

function StepArea({ workspaces, workspace, onSelect, onCreated, onError, onNext }: {
  workspaces: Workspace[];
  workspace: Workspace | null;
  onSelect: (w: Workspace) => void;
  onCreated: (w: Workspace) => void;
  onError: (m: string) => void;
  onNext: () => void;
}) {
  const [sites, setSites] = useState<Site[]>([]);
  const [creating, setCreating] = useState(!workspace);
  const [form, setForm] = useState({
    name: "", floor_id: "", width_m: "20", length_m: "15",
    origin_description: "", x_axis_description: "", surface_description: "",
    max_ground_error_m: "0.25",
  });
  const [busy, setBusy] = useState(false);
  const [conflict, setConflict] = useState<string | null>(null);

  useEffect(() => { api.tree().then(setSites).catch(() => setSites([])); }, []);
  const floors = useMemo(
    () => sites.flatMap((s) => s.buildings.flatMap((b) =>
      b.floors.map((f) => ({ id: f.id, label: `${s.name} · ${b.name} · ${f.name}` })))),
    [sites]);

  const set = (k: keyof typeof form, v: string) => setForm((f) => ({ ...f, [k]: v }));

  const submit = async () => {
    setBusy(true); setConflict(null);
    try {
      onCreated(await api.createWorkspace({
        name: form.name.trim(), floor_id: Number(form.floor_id),
        width_m: Number(form.width_m), length_m: Number(form.length_m),
        origin_description: form.origin_description.trim(),
        x_axis_description: form.x_axis_description.trim(),
        surface_description: form.surface_description || null,
        max_ground_error_m: Number(form.max_ground_error_m),
      }));
    } catch (e) {
      if (e instanceof ApiError && e.message.includes("share a single origin")) setConflict(e.message);
      else onError(e instanceof ApiError ? e.message : "Could not create the area.");
      setBusy(false);
    }
  };

  const valid = form.name.trim() && form.floor_id && form.origin_description.trim()
    && form.x_axis_description.trim();

  return (
    <div className="card card-pad">
      <Guidance>
        An area is the patch of floor these cameras watch, measured in metres. Pick a corner you
        can find again and describe it — every measurement you take later is from that corner.
        You do not need a floor plan; the grid starts blank.
      </Guidance>

      {workspaces.length > 0 && !creating && (
        <>
          <div className="section-title">Existing areas</div>
          <div className="chip-list" style={{ marginBottom: 14 }}>
            {workspaces.map((w) => (
              <button key={w.id} type="button"
                      className={`chip${workspace?.id === w.id ? " is-selected" : ""}`}
                      onClick={() => onSelect(w)}>
                <span className="grow"><strong>{w.name}</strong> · {w.floor_label}</span>
                <span className="small faint">
                  {w.width_m} × {w.length_m} m · {w.point_count} points
                </span>
              </button>
            ))}
          </div>
          <div className="row">
            <button className="btn" type="button" onClick={() => setCreating(true)}>
              ＋ New area
            </button>
            <button className="btn btn-primary" type="button" disabled={!workspace} onClick={onNext}>
              Next: add floor points
            </button>
          </div>
        </>
      )}

      {creating && (
        <>
          {conflict && (
            <Notice tone="warn" title="That floor already has an area">
              {conflict}
            </Notice>
          )}
          <div className="field-row">
            <div className="field">
              <label htmlFor="ws-name">What is this area called?</label>
              <input id="ws-name" type="text" value={form.name} placeholder="Packaging bay"
                     onChange={(e) => set("name", e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="ws-floor">Which floor is it on?</label>
              <select id="ws-floor" value={form.floor_id} onChange={(e) => set("floor_id", e.target.value)}>
                <option value="">Select…</option>
                {floors.map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}
              </select>
              <span className="hint">
                Areas on one floor share the same origin, so their measurements line up.
              </span>
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label htmlFor="ws-width">Roughly how wide is it? (metres)</label>
              <input id="ws-width" type="number" step="0.5" value={form.width_m}
                     onChange={(e) => set("width_m", e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="ws-length">Roughly how long? (metres)</label>
              <input id="ws-length" type="number" step="0.5" value={form.length_m}
                     onChange={(e) => set("length_m", e.target.value)} />
              <span className="hint">Approximate is fine — it only sizes the grid you draw on.</span>
            </div>
          </div>

          <div className="field">
            <label htmlFor="ws-origin">Where is the corner you measure from?</label>
            <textarea id="ws-origin" value={form.origin_description}
                      placeholder="The inside corner of column A1 where it meets the floor."
                      onChange={(e) => set("origin_description", e.target.value)} />
            <span className="hint">
              Be specific enough that someone else could find it in a year's time.
            </span>
          </div>

          <div className="field">
            <label htmlFor="ws-axis">Which way does the first direction run?</label>
            <textarea id="ws-axis" value={form.x_axis_description}
                      placeholder="Along the north wall towards the loading doors."
                      onChange={(e) => set("x_axis_description", e.target.value)} />
            <span className="hint">
              Measurements run along this direction and at right angles to it.
            </span>
          </div>

          <Advanced title="Advanced: surface and accuracy limit">
            <div className="field">
              <label htmlFor="ws-surface">Surface notes</label>
              <input id="ws-surface" type="text" value={form.surface_description}
                     placeholder="One flat concrete floor across the whole bay."
                     onChange={(e) => set("surface_description", e.target.value)} />
              <span className="hint">
                Floor mapping assumes one flat surface. A mezzanine, a ramp or a raised platform
                needs its own area, because a single flat mapping cannot describe both.
              </span>
            </div>
            <div className="field">
              <label htmlFor="ws-thresh">Accept a mapping when the error is under (metres)</label>
              <input id="ws-thresh" type="number" step="0.05" value={form.max_ground_error_m}
                     onChange={(e) => set("max_ground_error_m", e.target.value)} />
              <span className="hint">
                Checked against separately measured points. 0.25 m suits general monitoring;
                tighten it if you need better.
              </span>
            </div>
          </Advanced>

          <div className="row" style={{ marginTop: 16 }}>
            {workspaces.length > 0 && (
              <button className="btn" type="button" onClick={() => setCreating(false)}>Cancel</button>
            )}
            <button className="btn btn-primary" type="button" disabled={!valid || busy} onClick={submit}>
              {busy ? "Creating…" : "Create area"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}

/* ---------------- Step 3: add measured floor points ---------------- */

function StepFloorPoints({ workspace, points, cameras, onChanged, onError, onNext, onSay }: {
  workspace: Workspace;
  points: FloorPoint[];
  cameras: Camera[];
  onChanged: () => Promise<void>;
  onError: (m: string) => void;
  onNext: () => void;
  onSay: (m: string) => void;
}) {
  const [adding, setAdding] = useState<{ x: number; y: number } | null>(null);
  const [editing, setEditing] = useState<FloorPoint | null>(null);
  const [map, setMap] = useState<FactoryMap | null>(null);

  useEffect(() => {
    api.factoryMap(workspace.id).then(setMap).catch(() => setMap(null));
  }, [workspace.id, points.length]);

  const calibration = points.filter((p) => p.role === "calibration");
  const validation = points.filter((p) => p.role === "validation");
  const enough = calibration.length >= 4;
  const comfortable = calibration.length >= 6 && validation.length >= 2;

  return (
    <>
      <Guidance tone={enough ? "info" : "warn"}>
        Measure some fixed marks on the floor — column bases, drain covers, painted lines — and
        record where each one is, in metres from the corner you chose.
        <strong> Clicking the grid does not measure anything</strong>; it only puts a pin where you
        say the mark is. Aim for six to ten spread across the area, plus two or three extra kept
        aside to check the result.
      </Guidance>

      <div className="match-layout">
        <div className="card card-pad">
          <div className="row" style={{ marginBottom: 10 }}>
            <strong className="grow">Measured points ({points.length})</strong>
            <span className="small muted">
              {calibration.length} for the mapping · {validation.length} for checking
            </span>
          </div>

          {map ? (
            <FactoryGrid
              map={map}
              selectedCameraId={null}
              height={420}
              showFootprints={false}
              onWorldClick={(x, y) => setAdding({ x, y })}
            />
          ) : <Spinner label="Loading the grid…" />}

          <p className="hint" style={{ marginTop: 8 }}>
            Click anywhere on the grid to start adding a point there, then correct the numbers to
            your measured values.
          </p>
        </div>

        <div className="stack" style={{ gap: 14 }}>
          <div className="card card-pad">
            <div className="row" style={{ marginBottom: 10 }}>
              <strong className="grow">Point list</strong>
              <button className="btn btn-sm" type="button" onClick={() => setAdding({ x: 0, y: 0 })}>
                ＋ Add
              </button>
            </div>
            {points.length === 0 ? (
              <p className="small muted">Nothing measured yet.</p>
            ) : (
              <div className="chip-list" style={{ maxHeight: 340 }}>
                {points.map((point) => (
                  <button key={point.id} type="button"
                          className={`point-row ${point.role === "validation" ? "is-validation" : ""}`}
                          onClick={() => setEditing(point)}>
                    <span className="grow">
                      <strong>{point.code}</strong> {point.name}
                    </span>
                    <span className="mono small faint">
                      {point.x.toFixed(2)}, {point.y.toFixed(2)}
                    </span>
                    <span className={`badge ${point.role === "validation" ? "badge-warn" : "badge-accent"}`}>
                      {point.role === "validation" ? "check" : "map"}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="card card-pad">
            <strong>How you are doing</strong>
            <div className="progress-track" style={{ margin: "10px 0" }}>
              <div className="progress-fill"
                   style={{ width: `${Math.min(100, (calibration.length / 8) * 100)}%` }} />
            </div>
            <ul className="small muted" style={{ paddingLeft: 18, margin: 0 }}>
              <li style={{ color: enough ? "var(--ok)" : undefined }}>
                {enough ? "✓" : "○"} At least 4 mapping points ({calibration.length})
              </li>
              <li style={{ color: calibration.length >= 6 ? "var(--ok)" : undefined }}>
                {calibration.length >= 6 ? "✓" : "○"} Six or more is better
              </li>
              <li style={{ color: validation.length >= 2 ? "var(--ok)" : undefined }}>
                {validation.length >= 2 ? "✓" : "○"} Two or more checking points ({validation.length})
              </li>
            </ul>
            {!comfortable && (
              <p className="hint" style={{ marginTop: 10 }}>
                Checking points are never used to build the mapping, which is exactly what makes
                them a real test of it.
              </p>
            )}
          </div>

          <button className="btn btn-primary btn-block" type="button" disabled={!enough}
                  onClick={onNext}>
            Next: match points in the cameras
          </button>
          {!enough && <p className="hint">Add at least four mapping points to continue.</p>}
        </div>
      </div>

      {(adding || editing) && (
        <FloorPointModal
          workspace={workspace}
          point={editing}
          at={adding}
          cameras={cameras}
          onClose={() => { setAdding(null); setEditing(null); }}
          onSaved={async (message) => {
            setAdding(null); setEditing(null);
            await onChanged();
            onSay(message);
          }}
          onError={onError}
        />
      )}
    </>
  );
}

function FloorPointModal({ workspace, point, at, cameras, onClose, onSaved, onError }: {
  workspace: Workspace;
  point: FloorPoint | null;
  at: { x: number; y: number } | null;
  cameras: Camera[];
  onClose: () => void;
  onSaved: (message: string) => void;
  onError: (m: string) => void;
}) {
  const [form, setForm] = useState({
    code: point?.code ?? `FP-${String(Date.now()).slice(-4)}`,
    name: point?.name ?? "",
    x: String(point?.x ?? at?.x.toFixed(2) ?? "0"),
    y: String(point?.y ?? at?.y.toFixed(2) ?? "0"),
    role: point?.role ?? "calibration",
    measurement_notes: point?.measurement_notes ?? "",
    uncertainty_m: point?.uncertainty_m != null ? String(point.uncertainty_m) : "",
  });
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const set = (k: keyof typeof form, v: string) => setForm((f) => ({ ...f, [k]: v }));

  const usedBy = point ? cameras.filter((c) => point.observed_by_camera_ids.includes(c.id)) : [];

  const submit = async () => {
    setBusy(true);
    try {
      const body = {
        workspace_id: workspace.id,
        code: form.code.trim(), name: form.name.trim(),
        x: Number(form.x), y: Number(form.y), role: form.role,
        measurement_notes: form.measurement_notes || null,
        uncertainty_m: form.uncertainty_m ? Number(form.uncertainty_m) : null,
      };
      if (point) {
        await api.updateFloorPoint(point.id, body);
        onSaved(usedBy.length
          ? `Saved. ${usedBy.length} camera mapping(s) now need redoing.`
          : "Point saved.");
      } else {
        await api.createFloorPoint(workspace.id, body);
        onSaved("Point added.");
      }
    } catch (e) {
      onError(e instanceof ApiError ? e.message : "Could not save the point.");
      setBusy(false);
    }
  };

  return (
    <Modal title={point ? `Edit ${point.code}` : "Add a measured floor point"} onClose={onClose}
           footer={<>
             {point && (
               <button className="btn btn-danger" type="button"
                       onClick={() => setConfirmDelete(true)}>Delete</button>
             )}
             <div className="grow" />
             <button className="btn" type="button" onClick={onClose}>Cancel</button>
             <button className="btn btn-primary" type="button" disabled={busy || !form.name.trim()}
                     onClick={submit}>{busy ? "Saving…" : "Save"}</button>
           </>}>
      {confirmDelete && (
        <Notice tone="danger" title="Delete this point?">
          {usedBy.length > 0 && `${usedBy.length} camera mapping(s) use it and will need redoing. `}
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn btn-sm btn-danger" type="button"
                    onClick={async () => {
                      await api.deleteFloorPoint(point!.id);
                      onSaved("Point deleted.");
                    }}>Yes, delete</button>
            <button className="btn btn-sm" type="button"
                    onClick={() => setConfirmDelete(false)}>Cancel</button>
          </div>
        </Notice>
      )}

      <Notice tone="warn" title="These numbers must be measured">
        Take them with a tape or a laser from the corner you chose. A position typed from memory
        makes everything built on it wrong, quietly.
      </Notice>

      <div className="field-row">
        <div className="field">
          <label htmlFor="fp-code">Short code</label>
          <input id="fp-code" type="text" value={form.code} onChange={(e) => set("code", e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="fp-name">What is it?</label>
          <input id="fp-name" type="text" value={form.name} placeholder="Column B3 base"
                 onChange={(e) => set("name", e.target.value)} />
        </div>
      </div>

      <div className="field-row">
        <div className="field">
          <label htmlFor="fp-x">Distance along the first direction (m)</label>
          <input id="fp-x" type="number" step="0.01" value={form.x} onChange={(e) => set("x", e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="fp-y">Distance at right angles (m)</label>
          <input id="fp-y" type="number" step="0.01" value={form.y} onChange={(e) => set("y", e.target.value)} />
        </div>
      </div>

      <div className="field">
        <span className="field-label">What is this point for?</span>
        <div className="radio-row">
          <label className={`radio-card${form.role === "calibration" ? " is-selected" : ""}`}>
            <input type="radio" name="fp-role" checked={form.role === "calibration"}
                   onChange={() => set("role", "calibration")} />
            <span>
              <strong>Build the mapping</strong>
              <span>Used to work out how the picture relates to the floor.</span>
            </span>
          </label>
          <label className={`radio-card${form.role === "validation" ? " is-selected" : ""}`}>
            <input type="radio" name="fp-role" checked={form.role === "validation"}
                   onChange={() => set("role", "validation")} />
            <span>
              <strong>Check the mapping</strong>
              <span>Held back, never used to build it, so it can test the result honestly.</span>
            </span>
          </label>
        </div>
      </div>

      <div className="field">
        <label htmlFor="fp-notes">How did you measure it?</label>
        <input id="fp-notes" type="text" value={form.measurement_notes}
               placeholder="Tape from column A1, along the painted line."
               onChange={(e) => set("measurement_notes", e.target.value)} />
      </div>

      <Advanced title="Advanced: measurement uncertainty">
        <div className="field">
          <label htmlFor="fp-unc">How accurate is this measurement? (metres)</label>
          <input id="fp-unc" type="number" step="0.005" value={form.uncertainty_m} placeholder="0.01"
                 onChange={(e) => set("uncertainty_m", e.target.value)} />
          <span className="hint">
            Recorded alongside the point. Nothing built on it can be more accurate than this.
          </span>
        </div>
      </Advanced>

      {usedBy.length > 0 && (
        <Notice tone="warn" title="Changing this affects existing mappings">
          {usedBy.map((c) => c.name).join(", ")} already use this point. Moving it marks their
          mappings as needing to be redone.
        </Notice>
      )}
    </Modal>
  );
}

/* ---------------- Step 4: match points in camera images ---------------- */

function StepMatch({ workspace, points, cameras, onCamerasChanged, onError, onNext, onSay }: {
  workspace: Workspace;
  points: FloorPoint[];
  cameras: Camera[];
  onCamerasChanged: () => Promise<void>;
  onError: (m: string) => void;
  onNext: () => void;
  onSay: (m: string) => void;
}) {
  // Already in this area, or on its floor, or not yet in any area. The last case
  // matters on an upgraded install, where existing cameras predate workspaces.
  const inArea = cameras.filter((c) =>
    c.coordinate_system_id === workspace.id
    || (workspace.floor_id !== null && c.location.floor_id === workspace.floor_id)
    || c.coordinate_system_id === null);
  const [cameraId, setCameraId] = useState<number | null>(inArea[0]?.id ?? null);
  const [status, setStatus] = useState<MatchStatus | null>(null);
  const [target, setTarget] = useState<number | null>(null);
  const [frame, setFrame] = useState<{ w: number; h: number } | null>(null);
  const [draft, setDraft] = useState<Record<number, { u: number; v: number }>>({});
  const [undoStack, setUndoStack] = useState<Array<Record<number, { u: number; v: number }>>>([]);
  const [moving, setMoving] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [markers, setMarkers] = useState<null | Awaited<ReturnType<typeof api.detectMarkers>>>(null);
  const [assigned, setAssigned] = useState(false);

  const camera = cameras.find((c) => c.id === cameraId) ?? null;

  const load = useCallback(async () => {
    if (!cameraId) return;
    try { setStatus(await api.getMatches(cameraId)); }
    catch { setStatus(null); }
  }, [cameraId]);

  useEffect(() => {
    (async () => {
      if (!cameraId) return;
      if (camera && camera.coordinate_system_id !== workspace.id) {
        try {
          await api.assignCameraToWorkspace(workspace.id, cameraId);
          setAssigned(true);
          await onCamerasChanged();
        } catch (e) {
          onError(e instanceof ApiError ? e.message : "Could not add the camera to this area.");
        }
      }
      await load();
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cameraId, workspace.id]);

  const matched = status?.matches ?? [];
  const matchedIds = new Set(matched.map((m) => m.reference_point_id));
  const pending = Object.keys(draft).length;

  const marks: ImageMark[] = [
    ...matched.map((m) => ({
      id: m.observation_id, u: m.pixel_u, v: m.pixel_v, label: m.code,
      role: m.role === "validation" ? ("holdout" as const) : ("fit" as const),
    })),
    ...Object.entries(draft).map(([pointId, pos]) => {
      const point = points.find((p) => p.id === Number(pointId));
      return { id: `draft-${pointId}`, u: pos.u, v: pos.v,
               label: `${point?.code ?? pointId} (unsaved)` };
    }),
  ];

  const save = async () => {
    if (!cameraId || !frame) {
      onError("Load a picture from the camera first, so clicks have a known image size.");
      return;
    }
    setBusy(true);
    try {
      // A draft entry for an already-matched point is a move, so it wins.
      const merged = new Map<number, { pixel_u: number; pixel_v: number }>();
      matched.forEach((m) => merged.set(m.reference_point_id,
                                        { pixel_u: m.pixel_u, pixel_v: m.pixel_v }));
      Object.entries(draft).forEach(([id, p]) =>
        merged.set(Number(id), { pixel_u: p.u, pixel_v: p.v }));
      const all = [...merged.entries()].map(([reference_point_id, pixel]) =>
        ({ reference_point_id, ...pixel }));
      setStatus(await api.saveMatches(cameraId, {
        image_width: frame.w, image_height: frame.h, matches: all, replace_existing: true }));
      setDraft({});
      setUndoStack([]);
      onSay("Matches saved.");
    } catch (e) {
      onError(e instanceof ApiError ? e.message : "Could not save the matches.");
    } finally {
      setBusy(false);
    }
  };

  const removeMatch = async (observationId: number) => {
    if (!cameraId) return;
    await api.deleteMatch(cameraId, observationId);
    await load();
  };

  const ready = cameras.filter((c) => c.coordinate_system_id === workspace.id).length > 0;

  return (
    <>
      <Guidance>
        For each camera, pick a measured point on the right and then click exactly where it
        appears in the picture. Six or more, spread across the whole frame, work far better than
        a cluster in one corner. Your clicks are stored against the picture's own pixels, so
        zooming or resizing the browser changes nothing.
      </Guidance>

      {assigned && (
        <Notice tone="ok" title="Camera added to this area">
          Its measurements will use this area's origin.
        </Notice>
      )}

      {inArea.length === 0 && (
        <Notice tone="warn" title="No cameras to match yet">
          No camera is in this area or waiting to be assigned. Register cameras in step 1, or
          check that this area is attached to the right floor.
        </Notice>
      )}

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="toolbar">
          <select value={cameraId ?? ""} aria-label="Camera"
                  onChange={(e) => { setCameraId(Number(e.target.value)); setDraft({}); setTarget(null); }}>
            <option value="">Choose a camera…</option>
            {inArea.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          {status && (
            <span className="small muted">
              {status.calibration_matched} for the mapping · {status.validation_matched} for checking
            </span>
          )}
          <div className="grow" />
          {undoStack.length > 0 && (
            <button className="btn btn-sm" type="button"
                    onClick={() => {
                      setDraft(undoStack[undoStack.length - 1]);
                      setUndoStack((st) => st.slice(0, -1));
                    }}>
              Undo
            </button>
          )}
          {pending > 0 && (
            <button className="btn btn-sm btn-primary" type="button" disabled={busy} onClick={save}>
              Save {pending} change{pending > 1 ? "s" : ""}
            </button>
          )}
          <button className="btn btn-sm" type="button" disabled={!cameraId}
                  onClick={async () => {
                    if (!cameraId) return;
                    try { setMarkers(await api.detectMarkers(cameraId, "DICT_4X4_50")); }
                    catch (e) { onError(e instanceof ApiError ? e.message : "Marker detection failed."); }
                  }}>
            Detect markers
          </button>
        </div>
      </div>

      {camera && (
        <div className="match-layout">
          <div>
            <CameraImagePicker
              cameraId={camera.id}
              marks={marks}
              selectedId={null}
              height={520}
              onImageLoaded={(w, h) => setFrame({ w, h })}
              onPick={(target || moving)
                ? (u, v) => {
                    const id = target ?? moving!;
                    setUndoStack((st) => [...st, draft]);
                    setDraft((d) => ({ ...d, [id]: { u, v } }));
                    setTarget(null);
                    setMoving(null);
                  }
                : undefined}
              hint={target
                ? `Click where ${points.find((p) => p.id === target)?.code} appears.`
                : moving
                  ? `Click the new spot for ${points.find((p) => p.id === moving)?.code}.`
                  : "Choose a point on the right, then click it in the picture."}
            />
            {status && <IssueList issues={status.issues} />}
          </div>

          <div className="stack" style={{ gap: 14 }}>
            <div className="card card-pad">
              <strong>Points to find ({points.length - matchedIds.size - pending})</strong>
              <p className="hint" style={{ marginTop: 4 }}>
                A camera only needs the points it can actually see.
              </p>
              <div className="chip-list" style={{ maxHeight: 220 }}>
                {points.filter((p) => !matchedIds.has(p.id) && !(p.id in draft)).map((p) => (
                  <button key={p.id} type="button"
                          className={`point-row${target === p.id ? " is-target" : ""}${p.role === "validation" ? " is-validation" : ""}`}
                          onClick={() => setTarget(p.id)}>
                    <span className="grow"><strong>{p.code}</strong> {p.name}</span>
                    <span className="mono small faint">{p.x.toFixed(1)}, {p.y.toFixed(1)}</span>
                  </button>
                ))}
              </div>
            </div>

            <div className="card card-pad">
              <strong>Matched here ({matched.length})</strong>
              {matched.length === 0 ? (
                <p className="small muted" style={{ marginTop: 6 }}>Nothing matched yet.</p>
              ) : (
                <div className="chip-list" style={{ maxHeight: 240, marginTop: 8 }}>
                  {matched.map((m) => {
                    const moved = draft[m.reference_point_id];
                    return (
                      <div key={m.observation_id}
                           className={`point-row is-matched${m.role === "validation" ? " is-validation" : ""}${moving === m.reference_point_id ? " is-target" : ""}`}>
                        <span className="grow"><strong>{m.code}</strong></span>
                        <span className="mono small faint">
                          {(moved?.u ?? m.pixel_u).toFixed(0)},{(moved?.v ?? m.pixel_v).toFixed(0)}
                          {moved && <span style={{ color: "var(--warn)" }}> moved</span>}
                        </span>
                        <button className="btn btn-sm btn-ghost" type="button" title="Move this one"
                                onClick={() => { setMoving(m.reference_point_id); setTarget(null); }}>
                          ✥
                        </button>
                        <button className="btn btn-sm btn-ghost" type="button"
                                title="Remove this match"
                                onClick={() => removeMatch(m.observation_id)}>×</button>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            <button className="btn btn-primary btn-block" type="button"
                    disabled={!ready} onClick={onNext}>
              Next: calculate the mapping
            </button>
          </div>
        </div>
      )}

      {markers && cameraId && (
        <MarkerReviewModal
          detection={markers}
          onClose={() => setMarkers(null)}
          onAccept={async (accept) => {
            try {
              await api.acceptMarkers(cameraId, {
                image_width: markers.image_width, image_height: markers.image_height, accept });
              setMarkers(null);
              await load();
              onSay(`${accept.length} marker match(es) added.`);
            } catch (e) {
              onError(e instanceof ApiError ? e.message : "Could not accept the markers.");
            }
          }}
        />
      )}
    </>
  );
}

function MarkerReviewModal({ detection, onClose, onAccept }: {
  detection: NonNullable<Awaited<ReturnType<typeof api.detectMarkers>>>;
  onClose: () => void;
  onAccept: (accept: Array<{ reference_point_id: number; pixel_u: number; pixel_v: number }>) => void;
}) {
  const usable = detection.detected.filter((d) => d.known && d.issues.length === 0);
  const [chosen, setChosen] = useState<Set<number>>(new Set(usable.map((d) => d.marker_id)));

  const toggle = (id: number) => setChosen((prev) => {
    const next = new Set(prev);
    next.has(id) ? next.delete(id) : next.add(id);
    return next;
  });

  return (
    <Modal title="Markers found" wide onClose={onClose}
           footer={<>
             <button className="btn" type="button" onClick={onClose}>Cancel</button>
             <button className="btn btn-primary" type="button" disabled={chosen.size === 0}
                     onClick={() => onAccept(usable
                       .filter((d) => chosen.has(d.marker_id))
                       .map((d) => ({ reference_point_id: d.reference_point_id!,
                                      pixel_u: d.centre_px[0], pixel_v: d.centre_px[1] })))}>
               Accept {chosen.size} match(es)
             </button>
           </>}>
      <Notice tone="info" title="Check each one before accepting">{detection.note}</Notice>
      {detection.warnings.map((w, i) => (
        <Notice key={i} tone="warn" title="Note">{w}</Notice>
      ))}

      {detection.detected.length === 0 ? (
        <p className="muted">Nothing detected.</p>
      ) : (
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>Use</th><th>Marker</th><th>Matches</th><th>Where in picture</th><th>Notes</th></tr></thead>
            <tbody>
              {detection.detected.map((d) => (
                <tr key={`${d.marker_id}-${d.centre_px[0]}`}>
                  <td>
                    {d.known && d.issues.length === 0 ? (
                      <input type="checkbox" checked={chosen.has(d.marker_id)}
                             onChange={() => toggle(d.marker_id)} />
                    ) : <span className="faint small">—</span>}
                  </td>
                  <td className="mono">#{d.marker_id}</td>
                  <td>
                    {d.reference_code
                      ? <>{d.reference_code}{d.corner_index != null && ` corner ${d.corner_index}`}</>
                      : <span className="faint">not in your measured points</span>}
                  </td>
                  <td className="mono small">
                    {d.centre_px[0].toFixed(0)}, {d.centre_px[1].toFixed(0)}
                  </td>
                  <td className="small">
                    {d.issues.length ? d.issues.join(" ") : <span className="faint">—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Modal>
  );
}

/* ---------------- Step 5: calculate the mapping ---------------- */

function StepCalculate({ workspace, cameras, onError, onNext, onSay }: {
  workspace: Workspace;
  cameras: Camera[];
  onError: (m: string) => void;
  onNext: () => void;
  onSay: (m: string) => void;
}) {
  const inArea = cameras.filter((c) => c.coordinate_system_id === workspace.id);
  const [cameraId, setCameraId] = useState<number | null>(inArea[0]?.id ?? null);
  const [status, setStatus] = useState<MatchStatus | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [result, setResult] = useState<MappingResult | null>(null);
  const [map, setMap] = useState<FactoryMap | null>(null);
  const [tolerance, setTolerance] = useState("3");

  useEffect(() => {
    if (!cameraId) return;
    api.getMatches(cameraId).then(setStatus).catch(() => setStatus(null));
    setResult(null); setJob(null);
  }, [cameraId]);

  useEffect(() => { api.factoryMap(workspace.id).then(setMap).catch(() => setMap(null)); },
    [workspace.id, result]);

  const run = async () => {
    if (!cameraId || !status?.image_width) return;
    setResult(null);
    try {
      const started = await api.calculateMapping(cameraId, {
        image_width: status.image_width, image_height: status.image_height,
        tolerance_px: Number(tolerance),
      });
      setJob(started.job);
      // The work is quick, but polling keeps one honest pattern for longer jobs.
      for (let i = 0; i < 120; i++) {
        await new Promise((r) => setTimeout(r, 200));
        const current = await api.getJob(started.job.job_id);
        setJob(current);
        if (current.status === "succeeded") { setResult(current.result); onSay("Mapping calculated."); break; }
        if (current.status === "failed") break;
      }
    } catch (e) {
      onError(e instanceof ApiError ? e.message : "Could not start the calculation.");
    }
  };

  const camera = cameras.find((c) => c.id === cameraId);
  const rejected = result?.per_point.filter((p) => p.rejected) ?? [];
  const used = result?.per_point.filter((p) => p.used_in_fit) ?? [];

  return (
    <>
      <Guidance>
        This works out how the camera picture relates to the floor, using the points you matched.
        There is nothing to choose — it fits the mapping, throws out any point that disagrees with
        the rest, and refits without it.
      </Guidance>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="toolbar">
          <select value={cameraId ?? ""} aria-label="Camera"
                  onChange={(e) => setCameraId(Number(e.target.value))}>
            {inArea.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          {status && (
            <span className="small muted">
              {status.calibration_matched} matched for the mapping
            </span>
          )}
          <div className="grow" />
          <button className="btn btn-primary btn-sm" type="button"
                  disabled={!status?.ready_to_calculate || job?.status === "running"}
                  onClick={run}>
            {job?.status === "running" ? "Calculating…" : "Calculate mapping"}
          </button>
        </div>
      </div>

      {status && !status.ready_to_calculate && (
        <IssueList issues={status.issues.filter((i) => i.level === "error")} />
      )}

      {job && job.status !== "succeeded" && (
        <div className="card card-pad" style={{ marginBottom: 16 }}>
          <div className="row" style={{ marginBottom: 8 }}>
            <strong className="grow">{job.step}</strong>
            {job.status === "running" && <Spinner />}
          </div>
          <div className="progress-track">
            <div className="progress-fill" style={{ width: `${job.progress * 100}%` }} />
          </div>
          {job.status === "failed" && (
            <div style={{ marginTop: 12 }}>
              <Notice tone="danger" title="That did not work">
                {job.error}
                {job.hint && <div style={{ marginTop: 6 }}><strong>Try this:</strong> {job.hint}</div>}
              </Notice>
            </div>
          )}
        </div>
      )}

      {result && (
        <div className="match-layout">
          <div className="stack" style={{ gap: 14 }}>
            <div className="card card-pad">
              <strong>Where the camera can see</strong>
              <p className="hint" style={{ marginTop: 4 }}>
                The shaded area is the part of the floor your measured points cover. Positions
                inside it are the ones this mapping was actually built for.
              </p>
              {map && (
                <FactoryGrid
                  map={map}
                  selectedCameraId={cameraId}
                  height={380}
                  showFootprints
                  extraMarkers={result.per_point
                    .filter((p) => p.predicted)
                    .map((p) => ({
                      x: p.predicted![0], y: p.predicted![1],
                      label: `${p.code}${p.error_cm != null ? ` (${p.error_cm} cm)` : ""}`,
                      colour: p.rejected ? "#b0281f" : p.role === "validation" ? "#9a6207" : "#0f7b4f",
                    }))}
                />
              )}
            </div>
          </div>

          <div className="stack" style={{ gap: 14 }}>
            <div className="card card-pad">
              <strong>Result</strong>
              <dl className="kv small" style={{ marginTop: 10 }}>
                <dt>Points used</dt>
                <dd>{used.length} of {used.length + rejected.length}</dd>
                <dt>Typical error on those points</dt>
                <dd><ErrorValue metres={result.mean_error_m} /></dd>
                <dt>Worst</dt>
                <dd><ErrorValue metres={result.max_error_m} /></dd>
                <dt>Area covered</dt>
                <dd>{result.coverage_area_m2} m²</dd>
                <dt>Lens distortion</dt>
                <dd>{result.distortion_corrected
                  ? "Corrected using this camera's lens calibration"
                  : "Not corrected — no lens calibration available"}</dd>
              </dl>
              <p className="hint" style={{ marginTop: 8 }}>
                This is how well the mapping matches the points it was built from. It is not proof
                of accuracy — the next step checks that properly.
              </p>
            </div>

            {rejected.length > 0 && (
              <Notice tone="warn" title={`${rejected.length} point(s) left out`}>
                {rejected.map((p) => p.code).join(", ")} disagreed with the rest, so they were not
                used. That usually means a mis-click or a mistyped measurement — worth re-checking
                those two things before accepting this mapping.
              </Notice>
            )}

            {result.warnings.map((w, i) => (
              <Notice key={i} tone="warn" title="Worth knowing">{w}</Notice>
            ))}

            <Advanced title="Advanced: technical detail"
                      note="For diagnostics and for services consuming the export.">
              <dl className="kv">
                <dt>Method</dt><dd>Image-to-floor homography, RANSAC then refit on inliers</dd>
                <dt>Pixel space</dt><dd className="mono">{result.pixel_convention}</dd>
                <dt>Tolerance</dt>
                <dd className="mono">{result.ransac_threshold_px} px ({result.ransac_threshold_space})</dd>
                <dt>Condition number</dt><dd className="mono">{result.condition_number}</dd>
                <dt>Reprojection</dt>
                <dd className="mono">{result.mean_reprojection_error_px?.toFixed(3)} px</dd>
                <dt>H_image_to_floor</dt>
                <dd className="mono" style={{ fontSize: 11 }}>
                  {result.H_image_to_floor.map((row) =>
                    row.map((v) => v.toExponential(3)).join("  ")).join("\n")}
                </dd>
              </dl>
              <div className="field" style={{ marginTop: 10 }}>
                <label htmlFor="tol">Disagreement tolerance (pixels)</label>
                <input id="tol" type="number" step="0.5" value={tolerance}
                       onChange={(e) => setTolerance(e.target.value)} />
                <span className="hint">
                  How far a point may sit from the fitted mapping before it is treated as wrong.
                </span>
              </div>
            </Advanced>

            <button className="btn btn-primary btn-block" type="button" onClick={onNext}>
              Next: check the accuracy
            </button>
            <p className="hint">
              Nothing is switched on yet. {camera?.name} keeps using whatever it had until you
              activate this.
            </p>
          </div>
        </div>
      )}
    </>
  );
}

/* ---------------- Step 6: check accuracy ---------------- */

function StepAccuracy({ workspace, cameras, onError, onNext, onSay }: {
  workspace: Workspace;
  cameras: Camera[];
  onError: (m: string) => void;
  onNext: () => void;
  onSay: (m: string) => void;
}) {
  const inArea = cameras.filter((c) => c.coordinate_system_id === workspace.id);
  const [cameraId, setCameraId] = useState<number | null>(inArea[0]?.id ?? null);
  const [threshold, setThreshold] = useState(String(workspace.max_ground_error_m));
  const [check, setCheck] = useState<AccuracyResponse | null>(null);
  const [revisions, setRevisions] = useState<CalibrationRevision[]>([]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!cameraId) return;
    setRevisions(await api.listRevisions(cameraId).catch(() => []));
  }, [cameraId]);

  useEffect(() => { setCheck(null); load(); }, [cameraId, load]);

  const latest = revisions[0] ?? null;
  const active = revisions.find((r) => r.is_active) ?? null;

  const run = async () => {
    if (!cameraId) return;
    setBusy(true);
    try {
      setCheck(await api.checkAccuracy(cameraId, {
        acceptance_threshold_m: Number(threshold) }, latest?.id));
      await load();
    } catch (e) {
      onError(e instanceof ApiError ? e.message : "Could not run the check.");
    } finally { setBusy(false); }
  };

  const activate = async () => {
    if (!cameraId) return;
    setBusy(true);
    try {
      await api.activateMapping(cameraId, latest?.id);
      await load();
      onSay("Mapping is now in use.");
    } catch (e) {
      onError(e instanceof ApiError ? e.message : "Could not activate the mapping.");
    } finally { setBusy(false); }
  };

  return (
    <>
      <Guidance>
        This is the real test. The points you set aside for checking were never used to build the
        mapping, so comparing where the mapping puts them against where you measured them tells
        you how far out it is on the ground.
      </Guidance>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="toolbar">
          <select value={cameraId ?? ""} aria-label="Camera"
                  onChange={(e) => setCameraId(Number(e.target.value))}>
            {inArea.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <label className="row small" style={{ gap: 6 }}>
            Accept under
            <input type="number" step="0.05" value={threshold} style={{ width: 90 }}
                   onChange={(e) => setThreshold(e.target.value)} />
            m
          </label>
          <div className="grow" />
          <button className="btn btn-sm" type="button" disabled={!latest || busy} onClick={run}>
            {busy ? "Checking…" : "Check accuracy"}
          </button>
          <button className="btn btn-sm btn-primary" type="button"
                  disabled={!latest || busy || latest?.is_active} onClick={activate}>
            {latest?.is_active ? "In use" : "Use this mapping"}
          </button>
        </div>
      </div>

      {!latest && (
        <Notice tone="info" title="Nothing to check yet">
          Calculate a mapping for this camera first.
        </Notice>
      )}

      {/* An earlier check, shown without re-running it. Running a check records a
          new result, so it must be something the installer asks for, not a
          side effect of opening the page. */}
      {latest && !check && (
        latest.latest_validation ? (
          <div className="card card-pad">
            <div className="row" style={{ marginBottom: 10 }}>
              <strong className="grow">Last check on this mapping</strong>
              <span className={`badge ${latest.latest_validation.passed ? "badge-ok" : "badge-danger"}`}>
                <span aria-hidden="true">{latest.latest_validation.passed ? "✓" : "✕"}</span>
                {latest.latest_validation.passed ? "Passed" : "Failed"}
              </span>
            </div>
            <dl className="kv">
              <dt>Typical error</dt>
              <dd><ErrorValue metres={latest.latest_validation.holdout_ground_error_m} /></dd>
              <dt>Worst point</dt>
              <dd><ErrorValue metres={latest.latest_validation.holdout_max_error_m} /></dd>
              <dt>Points checked</dt>
              <dd>{latest.latest_validation.holdout_point_count}</dd>
              <dt>Checked</dt>
              <dd>
                {new Date(latest.latest_validation.created_at).toLocaleString(undefined,
                  { dateStyle: "medium", timeStyle: "short" })}
                {latest.latest_validation.reviewer && ` by ${latest.latest_validation.reviewer}`}
              </dd>
              <dt>In use</dt>
              <dd>{latest.is_active ? "Yes" : "Not yet — press \"Use this mapping\""}</dd>
            </dl>
            <p className="hint" style={{ marginTop: 10 }}>
              Run the check again if you have changed anything since.
            </p>
          </div>
        ) : (
          <Notice tone="info" title="Not checked yet">
            Press <strong>Check accuracy</strong> to compare this mapping against the points you
            set aside. Nothing is measured until you do.
          </Notice>
        )
      )}

      {check && (
        <div className="match-layout">
          <div className="card card-pad">
            <div className="row" style={{ marginBottom: 12 }}>
              <strong className="grow">
                {check.validation.passed ? "Within your limit" : "Outside your limit"}
              </strong>
              <span className={`badge ${check.validation.passed ? "badge-ok" : "badge-danger"}`}>
                <span aria-hidden="true">{check.validation.passed ? "✓" : "✕"}</span>
                {check.validation.passed ? "Passed" : "Failed"}
              </span>
            </div>

            <div className="stat-grid" style={{ marginBottom: 14 }}>
              <div className="stat stat-accent">
                <div className="stat-label">Typical error</div>
                <div className="stat-value">
                  <ErrorValue metres={check.validation.summary.mean_m}
                              threshold={check.validation.threshold_m} />
                </div>
                <div className="stat-note">across the checking points</div>
              </div>
              <div className="stat stat-warn">
                <div className="stat-label">Worst point</div>
                <div className="stat-value">
                  <ErrorValue metres={check.validation.summary.max_m}
                              threshold={check.validation.threshold_m * 2} />
                </div>
                <div className="stat-note">the largest single miss</div>
              </div>
              <div className="stat">
                <div className="stat-label">Points checked</div>
                <div className="stat-value">{check.validation.held_out_count}</div>
                <div className="stat-note">
                  {check.validation.distribution
                    ? `over ${check.validation.distribution.area_m2} m²`
                    : "add more for a better check"}
                </div>
              </div>
            </div>

            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr><th>Point</th><th>You measured</th><th>Mapping says</th><th>Out by</th></tr>
                </thead>
                <tbody>
                  {check.validation.held_out.map((p) => (
                    <tr key={p.reference_point_id}>
                      <td><strong>{p.code}</strong> <span className="cell-sub">{p.name}</span></td>
                      <td className="mono small">
                        {p.measured[0].toFixed(2)}, {p.measured[1].toFixed(2)}
                      </td>
                      <td className="mono small">
                        {p.predicted ? `${p.predicted[0].toFixed(2)}, ${p.predicted[1].toFixed(2)}` : "—"}
                      </td>
                      <td><ErrorValue metres={p.error_m} threshold={check.validation.threshold_m} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="stack" style={{ gap: 14 }}>
            <Notice tone={check.validation.passed ? "ok" : "warn"} title="What this means">
              <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                {check.validation.messages.map((m, i) => <li key={i}>{m}</li>)}
              </ul>
            </Notice>

            <div className="card card-pad">
              <strong>Scope of this check</strong>
              <p className="small muted" style={{ marginTop: 6 }}>
                {check.validation.interpretation}
              </p>
            </div>

            <div className="card card-pad">
              <strong>Status</strong>
              <div className="row" style={{ marginTop: 8 }}>
                <CameraStatusRow
                  connection={cameras.find((c) => c.id === cameraId)?.last_test_status ?? "untested"}
                  revision={active ?? latest} />
              </div>
              {!latest?.is_active && (
                <p className="hint" style={{ marginTop: 8 }}>
                  You can save an unchecked or failing mapping and come back to it. Nothing starts
                  using it until you press "Use this mapping".
                </p>
              )}
            </div>

            <button className="btn btn-primary btn-block" type="button" onClick={onNext}>
              Next: connect the cameras together
            </button>
          </div>
        </div>
      )}
    </>
  );
}

/* ---------------- Step 7: relationships ---------------- */

function StepRelationships({ workspace }: { workspace: Workspace }) {
  return (
    <div className="card card-pad">
      <Guidance>
        The last step records how the camera views relate to each other: which pairs see the same
        patch of floor, and which exits lead where. That lets a later tracking service follow
        movement between cameras. It records geometry only — nothing about who anyone is.
      </Guidance>
      <div className="row">
        <Link className="btn btn-primary" to={`/relationships?workspace=${workspace.id}`}>
          Open the camera connections editor
        </Link>
        <Link className="btn" to={`/factory-map?cs=${workspace.id}`}>See the area map</Link>
      </div>
    </div>
  );
}
