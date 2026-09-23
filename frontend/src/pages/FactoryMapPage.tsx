import { Suspense, lazy, useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError, api } from "../lib/api";
import FactoryGrid from "../components/FactoryGrid";
// Three.js is a large dependency used only by the 3D view, so it loads on demand.
const Scene3D = lazy(() => import("../components/Scene3D"));
import { CalibrationBadge, ConventionsPanel } from "../components/calibrationUi";
import { Modal, Notice, Spinner, StatusBadge } from "../components/ui";
import type { CoordinateSystem, FactoryMap, ReferencePoint } from "../lib/calibrationTypes";

export default function FactoryMapPage() {
  const [params, setParams] = useSearchParams();
  const [systems, setSystems] = useState<CoordinateSystem[]>([]);
  const [systemId, setSystemId] = useState<number | null>(
    params.get("cs") ? Number(params.get("cs")) : null);
  const [map, setMap] = useState<FactoryMap | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedCamera, setSelectedCamera] = useState<number | null>(null);
  const [view, setView] = useState<"2d" | "3d">("2d");
  const [showFootprints, setShowFootprints] = useState(true);
  const [showPoints, setShowPoints] = useState(true);
  const [creating, setCreating] = useState(false);
  const [addingPoint, setAddingPoint] = useState(false);
  const [newPointAt, setNewPointAt] = useState<{ x: number; y: number } | null>(null);
  const [coverage, setCoverage] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    api.listCoordinateSystems()
      .then((list) => {
        setSystems(list);
        if (!systemId && list.length) setSystemId(list[0].id);
        if (!list.length) setLoading(false);
      })
      .catch((e) => { setError(e instanceof ApiError ? e.message : "Could not load frames."); setLoading(false); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const load = useCallback(async () => {
    if (!systemId) return;
    setLoading(true);
    try {
      setMap(await api.factoryMap(systemId));
      setParams({ cs: String(systemId) }, { replace: true });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not load the factory map.");
    } finally {
      setLoading(false);
    }
  }, [systemId, setParams]);

  useEffect(() => { load(); }, [load]);

  const selected = useMemo(
    () => map?.cameras.find((c) => c.camera_id === selectedCamera) ?? null,
    [map, selectedCamera]);

  const statusCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    map?.cameras.forEach((c) => { counts[c.calibration_status] = (counts[c.calibration_status] ?? 0) + 1; });
    return counts;
  }, [map]);

  if (loading && !map) return <main className="page"><Spinner label="Loading factory map…" /></main>;

  if (!systems.length) {
    return (
      <main className="page">
        <div className="page-head">
          <div className="grow">
            <h1>Factory map</h1>
            <p className="page-sub">Position cameras in a shared metric coordinate system.</p>
          </div>
        </div>
        <div className="card empty">
          <div className="empty-mark" aria-hidden="true">▦</div>
          <h2>No coordinate system yet</h2>
          <p>
            A coordinate system is the shared metric frame every camera and reference point is
            measured in: X and Y on the factory floor, Z up, in metres. No floor plan is needed —
            cameras are placed on a blank grid.
          </p>
          <button className="btn btn-primary" type="button" onClick={() => setCreating(true)}>
            Create a coordinate system
          </button>
        </div>
        {creating && (
          <CreateSystemModal onClose={() => setCreating(false)}
                             onCreated={(cs) => { setSystems([cs]); setSystemId(cs.id); setCreating(false); }} />
        )}
      </main>
    );
  }

  return (
    <main className="page page-wide">
      <div className="page-head">
        <div className="grow">
          <h1>Factory map</h1>
          <p className="page-sub">
            All cameras in one metric frame. X/Y on the floor, Z up, metres.
          </p>
        </div>
        <Link className="btn" to="/multi-camera-check">Multi-camera check</Link>
        <button className="btn" type="button" onClick={() => setCreating(true)}>New frame</button>
      </div>

      {error && <Notice tone="danger" title="Something went wrong">{error}</Notice>}

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="toolbar">
          <select value={systemId ?? ""} aria-label="Coordinate system"
                  onChange={(e) => { setSystemId(Number(e.target.value)); setSelectedCamera(null); }}>
            {systems.map((s) => (
              <option key={s.id} value={s.id}>{s.name}</option>
            ))}
          </select>
          <div className="row" role="group" aria-label="View mode">
            <button className={`btn btn-sm${view === "2d" ? " btn-primary" : ""}`} type="button"
                    onClick={() => setView("2d")}>2D grid</button>
            <button className={`btn btn-sm${view === "3d" ? " btn-primary" : ""}`} type="button"
                    onClick={() => setView("3d")}>3D view</button>
          </div>
          <label className="row small" style={{ gap: 6 }}>
            <input type="checkbox" checked={showFootprints}
                   onChange={(e) => setShowFootprints(e.target.checked)} />
            Floor coverage
          </label>
          <label className="row small" style={{ gap: 6 }}>
            <input type="checkbox" checked={showPoints}
                   onChange={(e) => setShowPoints(e.target.checked)} />
            Reference points
          </label>
          <div className="grow" />
          <button className={`btn btn-sm${addingPoint ? " btn-primary" : ""}`} type="button"
                  onClick={() => setAddingPoint((v) => !v)}>
            {addingPoint ? "Cancel add point" : "＋ Add reference point"}
          </button>
          <button className="btn btn-sm" type="button"
                  onClick={async () => setCoverage(await api.coverage(systemId!))}>
            Coverage report
          </button>
        </div>
      </div>

      {addingPoint && (
        <Notice tone="info" title="Click the grid to place a reference point">
          The click gives X and Y in metres; you then enter the measured Z and a description.
          Reference points must be physically surveyed — clicking the map records where you say a
          point is, it does not measure anything.
        </Notice>
      )}

      {map && (
        <div className="editor-layout">
          <div>
            {view === "2d" ? (
              <FactoryGrid
                map={map}
                selectedCameraId={selectedCamera}
                onSelectCamera={setSelectedCamera}
                onWorldClick={addingPoint ? (x, y) => { setNewPointAt({ x, y }); setAddingPoint(false); } : undefined}
                showFootprints={showFootprints}
                showReferencePoints={showPoints}
              />
            ) : (
              <Suspense fallback={
                <div className="plan-stage" style={{ display: "grid", placeItems: "center" }}>
                  <Spinner label="Loading the 3D view…" />
                </div>
              }>
                <Scene3D map={map} selectedCameraId={selectedCamera}
                         onSelectCamera={setSelectedCamera} />
              </Suspense>
            )}
          </div>

          <div className="side-panel">
            <div className="card card-pad">
              <h3 style={{ marginBottom: 10 }}>{map.coordinate_system.name}</h3>
              <dl className="kv small">
                <dt>Origin</dt>
                <dd>{map.coordinate_system.origin_description ?? <span className="faint">Not described</span>}</dd>
                <dt>Floor plane</dt><dd>Z = {map.coordinate_system.floor_plane_z} m</dd>
                <dt>Grid</dt>
                <dd>
                  X {map.coordinate_system.grid_min_x}…{map.coordinate_system.grid_max_x} m,
                  Y {map.coordinate_system.grid_min_y}…{map.coordinate_system.grid_max_y} m
                  · {map.coordinate_system.grid_spacing_m} m cells
                </dd>
                <dt>Definition</dt><dd>revision {map.coordinate_system.definition_revision}</dd>
                <dt>Thresholds</dt>
                <dd>
                  ≤ {map.coordinate_system.max_reprojection_error_px} px reprojection,
                  ≤ {map.coordinate_system.max_ground_error_m} m ground error,
                  ≥ {map.coordinate_system.min_reference_points} points
                </dd>
              </dl>
            </div>

            <div className="card card-pad">
              <h3 style={{ marginBottom: 8 }}>Cameras ({map.cameras.length})</h3>
              <div className="row small" style={{ gap: 6, marginBottom: 10 }}>
                {Object.entries(statusCounts).map(([status, count]) => (
                  <CalibrationBadge key={status} status={status as never} suffix={` ${count}`} />
                ))}
              </div>
              {map.cameras.length === 0 ? (
                <p className="small muted">
                  No cameras are assigned to this frame yet. Open a camera and use its
                  Position &amp; Calibration tab.
                </p>
              ) : (
                <div className="chip-list">
                  {map.cameras.map((camera) => (
                    <button key={camera.camera_id} type="button"
                            className={`chip${selectedCamera === camera.camera_id ? " is-selected" : ""}`}
                            onClick={() => setSelectedCamera(camera.camera_id)}>
                      <span className="grow">{camera.name}</span>
                      <CalibrationBadge status={camera.calibration_status} />
                    </button>
                  ))}
                </div>
              )}
            </div>

            {selected && (
              <div className="card card-pad">
                <div className="row" style={{ marginBottom: 8 }}>
                  <Link to={`/cameras/${selected.camera_id}`} className="cell-name grow">
                    {selected.name}
                  </Link>
                </div>
                <div className="row small" style={{ gap: 6, marginBottom: 10 }}>
                  <CalibrationBadge status={selected.calibration_status} />
                  <StatusBadge status={selected.connection_status as never} />
                </div>
                <dl className="kv small">
                  <dt>Method</dt>
                  <dd>{selected.method ? METHOD_LABEL[selected.method] : "—"}</dd>
                  <dt>Position</dt>
                  <dd className="mono">
                    {selected.position
                      ? `${selected.position.x.toFixed(2)}, ${selected.position.y.toFixed(2)}, ${selected.position.z.toFixed(2)} m`
                      : "No pose (floor-plane calibration only)"}
                  </dd>
                  {selected.rpy_deg && (
                    <>
                      <dt>Roll / pitch / yaw</dt>
                      <dd className="mono">
                        {selected.rpy_deg.roll.toFixed(1)}°, {selected.rpy_deg.pitch.toFixed(1)}°,{" "}
                        {selected.rpy_deg.yaw.toFixed(1)}°
                      </dd>
                    </>
                  )}
                  <dt>Map heading</dt>
                  <dd>{selected.map_heading_deg != null
                    ? `${selected.map_heading_deg.toFixed(1)}°`
                    : "Not horizontal"}</dd>
                  {selected.horizontal_fov_deg && (
                    <>
                      <dt>Field of view</dt>
                      <dd>
                        {selected.horizontal_fov_deg.toFixed(1)}° × {selected.vertical_fov_deg?.toFixed(1)}°
                        <div className="faint">from measured intrinsics</div>
                      </dd>
                    </>
                  )}
                  {!selected.horizontal_fov_deg && selected.approx_hfov_deg && (
                    <>
                      <dt>Field of view</dt>
                      <dd>
                        ≈{selected.approx_hfov_deg}°
                        <div className="faint">nominal figure, not measured</div>
                      </dd>
                    </>
                  )}
                </dl>
                {selected.floor_polygon_clipped && (
                  <p className="hint" style={{ marginTop: 8 }}>
                    Part of this camera's view points at or above the horizon, so the drawn
                    footprint is only the portion that reaches the floor.
                  </p>
                )}
                <Link className="btn btn-sm btn-block" style={{ marginTop: 10 }}
                      to={`/cameras/${selected.camera_id}/calibration`}>
                  Open Position &amp; Calibration
                </Link>
              </div>
            )}

            <div className="card card-pad">
              <h3 style={{ marginBottom: 8 }}>Reference points ({map.reference_points.length})</h3>
              {map.reference_points.length === 0 ? (
                <p className="small muted">
                  None yet. Reference points are surveyed positions reused across cameras.
                </p>
              ) : (
                <div className="chip-list">
                  {map.reference_points.map((p) => (
                    <div key={p.id} className="chip" style={{ cursor: "default" }}>
                      <span className="grow">
                        <strong>{p.code}</strong> {p.name}
                      </span>
                      <span className="mono small faint">
                        {p.x.toFixed(1)}, {p.y.toFixed(1)}, {p.z.toFixed(1)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <ConventionsPanel conventions={map.conventions} />
          </div>
        </div>
      )}

      {newPointAt && systemId && (
        <NewReferencePointModal
          systemId={systemId}
          at={newPointAt}
          onClose={() => setNewPointAt(null)}
          onCreated={() => { setNewPointAt(null); load(); }}
        />
      )}

      {creating && (
        <CreateSystemModal
          onClose={() => setCreating(false)}
          onCreated={(cs) => {
            setSystems((prev) => [...prev, cs]);
            setSystemId(cs.id);
            setCreating(false);
          }}
        />
      )}

      {coverage && (
        <Modal title="Geometric coverage report" wide onClose={() => setCoverage(null)}
               footer={<button className="btn" type="button" onClick={() => setCoverage(null)}>Close</button>}>
          <Notice tone="warn" title="Geometric coverage, not visibility">
            {String(coverage.interpretation)}
          </Notice>
          <dl className="kv">
            <dt>Cameras with a footprint</dt><dd>{String(coverage.cameras_with_footprint)}</dd>
            <dt>Cameras without one</dt>
            <dd>
              {String(coverage.cameras_without_footprint)}
              <div className="faint small">
                No pose, or no lens model, so no footprint can be drawn.
              </div>
            </dd>
            <dt>Sum of footprint areas</dt>
            <dd>
              {String(coverage.sum_of_footprint_areas_m2)} m²
              <div className="faint small">Overlaps counted once per camera; not covered floor area.</div>
            </dd>
          </dl>
        </Modal>
      )}
    </main>
  );
}

export const METHOD_LABEL: Record<string, string> = {
  manual: "Manual placement",
  homography: "Floor-plane calibration",
  pnp: "Full camera pose",
};

function CreateSystemModal({ onClose, onCreated }: {
  onClose: () => void; onCreated: (cs: CoordinateSystem) => void;
}) {
  const [form, setForm] = useState({
    name: "", origin_description: "",
    grid_min_x: "-5", grid_max_x: "50", grid_min_y: "-5", grid_max_y: "50",
    grid_spacing_m: "1", max_reprojection_error_px: "3", max_ground_error_m: "0.25",
    min_reference_points: "6",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true); setError(null);
    try {
      const created = await api.createCoordinateSystem({
        name: form.name.trim(),
        origin_description: form.origin_description || null,
        grid_min_x: Number(form.grid_min_x), grid_max_x: Number(form.grid_max_x),
        grid_min_y: Number(form.grid_min_y), grid_max_y: Number(form.grid_max_y),
        grid_spacing_m: Number(form.grid_spacing_m),
        max_reprojection_error_px: Number(form.max_reprojection_error_px),
        max_ground_error_m: Number(form.max_ground_error_m),
        min_reference_points: Number(form.min_reference_points),
      });
      onCreated(created);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not create the coordinate system.");
      setBusy(false);
    }
  };

  const set = (k: keyof typeof form, v: string) => setForm((f) => ({ ...f, [k]: v }));

  return (
    <Modal title="New factory coordinate system" wide onClose={onClose}
           footer={<>
             <button className="btn" type="button" onClick={onClose}>Cancel</button>
             <button className="btn btn-primary" type="button" onClick={submit}
                     disabled={busy || !form.name.trim()}>
               {busy ? "Creating…" : "Create"}
             </button>
           </>}>
      {error && <Notice tone="danger" title="Could not create">{error}</Notice>}
      <Notice tone="info" title="Right-handed, metres, Z up">
        X and Y lie on the factory floor and Z points up. Pick a physical origin you can find
        again — a column base, a door threshold — and describe it precisely. Every camera and
        reference point in this frame is measured from it.
      </Notice>
      <div className="field">
        <label htmlFor="cs-name">Name</label>
        <input id="cs-name" type="text" value={form.name} placeholder="Plant 1 floor frame"
               onChange={(e) => set("name", e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="cs-origin">Origin description</label>
        <textarea id="cs-origin" value={form.origin_description}
                  placeholder="Inside corner of column A1 at floor level. X runs east along the packaging aisle, Y runs north."
                  onChange={(e) => set("origin_description", e.target.value)} />
      </div>
      <div className="section-title">Grid extent</div>
      <div className="field-row">
        {(["grid_min_x", "grid_max_x", "grid_min_y", "grid_max_y", "grid_spacing_m"] as const).map((key) => (
          <div className="field" key={key}>
            <label htmlFor={`cs-${key}`}>{key.replace(/_/g, " ").replace("grid ", "")} (m)</label>
            <input id={`cs-${key}`} type="number" step="0.5" value={form[key]}
                   onChange={(e) => set(key, e.target.value)} />
          </div>
        ))}
      </div>
      <div className="section-title">Acceptance thresholds</div>
      <p className="hint" style={{ marginTop: -4, marginBottom: 10 }}>
        A calibration is only marked Validated when it meets these limits against held-out points.
      </p>
      <div className="field-row">
        <div className="field">
          <label htmlFor="cs-reproj">Max reprojection error (px)</label>
          <input id="cs-reproj" type="number" step="0.5" value={form.max_reprojection_error_px}
                 onChange={(e) => set("max_reprojection_error_px", e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="cs-ground">Max ground error (m)</label>
          <input id="cs-ground" type="number" step="0.05" value={form.max_ground_error_m}
                 onChange={(e) => set("max_ground_error_m", e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="cs-min">Minimum reference points</label>
          <input id="cs-min" type="number" step="1" value={form.min_reference_points}
                 onChange={(e) => set("min_reference_points", e.target.value)} />
        </div>
      </div>
    </Modal>
  );
}

function NewReferencePointModal({ systemId, at, onClose, onCreated }: {
  systemId: number; at: { x: number; y: number }; onClose: () => void; onCreated: (p: ReferencePoint) => void;
}) {
  const [form, setForm] = useState({
    code: "", name: "", x: at.x.toFixed(3), y: at.y.toFixed(3), z: "0",
    description: "", measurement_notes: "", uncertainty_m: "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (k: keyof typeof form, v: string) => setForm((f) => ({ ...f, [k]: v }));

  const submit = async () => {
    setBusy(true); setError(null);
    try {
      onCreated(await api.createReferencePoint({
        coordinate_system_id: systemId,
        code: form.code.trim(), name: form.name.trim(),
        x: Number(form.x), y: Number(form.y), z: Number(form.z),
        description: form.description || null,
        measurement_notes: form.measurement_notes || null,
        uncertainty_m: form.uncertainty_m ? Number(form.uncertainty_m) : null,
      }));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not create the reference point.");
      setBusy(false);
    }
  };

  return (
    <Modal title="New reference point" onClose={onClose}
           footer={<>
             <button className="btn" type="button" onClick={onClose}>Cancel</button>
             <button className="btn btn-primary" type="button" onClick={submit}
                     disabled={busy || !form.code.trim() || !form.name.trim()}>
               {busy ? "Saving…" : "Save point"}
             </button>
           </>}>
      {error && <Notice tone="danger" title="Could not save">{error}</Notice>}
      <Notice tone="warn" title="These coordinates must be measured, not guessed">
        Calibration is only as good as these numbers. Clicking the grid gives a starting X and Y;
        replace them with surveyed values before calibrating against this point.
      </Notice>
      <div className="field-row">
        <div className="field">
          <label htmlFor="rp-code">Code</label>
          <input id="rp-code" type="text" value={form.code} placeholder="RP-01"
                 onChange={(e) => set("code", e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="rp-name">Name</label>
          <input id="rp-name" type="text" value={form.name} placeholder="Column B3 base"
                 onChange={(e) => set("name", e.target.value)} />
        </div>
      </div>
      <div className="field-row">
        {(["x", "y", "z"] as const).map((axis) => (
          <div className="field" key={axis}>
            <label htmlFor={`rp-${axis}`}>{axis.toUpperCase()} (m)</label>
            <input id={`rp-${axis}`} type="number" step="0.001" value={form[axis]}
                   onChange={(e) => set(axis, e.target.value)} />
          </div>
        ))}
      </div>
      <div className="field">
        <label htmlFor="rp-unc">Measurement uncertainty (m)</label>
        <input id="rp-unc" type="number" step="0.001" value={form.uncertainty_m} placeholder="0.01"
               onChange={(e) => set("uncertainty_m", e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="rp-notes">How it was measured</label>
        <textarea id="rp-notes" value={form.measurement_notes}
                  placeholder="Tape from column A1, laser height."
                  onChange={(e) => set("measurement_notes", e.target.value)} />
      </div>
    </Modal>
  );
}
