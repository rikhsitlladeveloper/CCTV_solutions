import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api } from "../lib/api";
import CameraImagePicker from "../components/CameraImagePicker";
import FactoryGrid from "../components/FactoryGrid";
import { CalibrationBadge } from "../components/calibrationUi";
import { Notice, Spinner } from "../components/ui";
import type {
  CoordinateSystem, FactoryMap, GroundCheckResult, MapCamera,
} from "../lib/calibrationTypes";

/** Marking one physical ground point in several camera images and comparing where
 *  each calibration places it. This checks that calibrations agree with each other;
 *  it is not a measurement of accuracy, and it has nothing to do with identity. */
export default function MultiCameraCheckPage() {
  const [systems, setSystems] = useState<CoordinateSystem[]>([]);
  const [systemId, setSystemId] = useState<number | null>(null);
  const [map, setMap] = useState<FactoryMap | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [label, setLabel] = useState("Aisle floor marker");
  const [marks, setMarks] = useState<Record<number, { u: number; v: number }>>({});
  const [result, setResult] = useState<GroundCheckResult | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.listCoordinateSystems()
      .then((list) => {
        setSystems(list);
        if (list.length) setSystemId(list[0].id);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  const load = useCallback(async () => {
    if (!systemId) return;
    try { setMap(await api.factoryMap(systemId)); }
    catch (e) { setError(e instanceof ApiError ? e.message : "Could not load the map."); }
  }, [systemId]);

  useEffect(() => { load(); }, [load]);

  const usable = (map?.cameras ?? []).filter(
    (c) => c.calibration_status !== "unconfigured" && c.revision_id);

  const compare = async () => {
    if (!systemId) return;
    setBusy(true); setError(null);
    try {
      const res = await api.checkGroundPoint({
        coordinate_system_id: systemId,
        label,
        plane_z: map?.coordinate_system.floor_plane_z ?? 0,
        marks: Object.entries(marks).map(([cameraId, p]) => ({
          camera_id: Number(cameraId), pixel_u: p.u, pixel_v: p.v,
        })),
      });
      setResult(res);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The comparison failed.");
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <main className="page"><Spinner label="Loading…" /></main>;

  if (!systems.length) {
    return (
      <main className="page">
        <Notice tone="info" title="No coordinate system yet">
          Create one on the <Link to="/factory-map">Factory map</Link> page first.
        </Notice>
      </main>
    );
  }

  return (
    <main className="page page-wide">
      <div className="page-head">
        <div className="grow">
          <h1>Multi-camera geometry check</h1>
          <p className="page-sub">
            Mark the same stationary ground point in two or more cameras and compare where each
            calibration puts it.
          </p>
        </div>
        <Link className="btn" to="/factory-map">Factory map</Link>
      </div>

      <Notice tone="warn" title="This compares calibrations, not accuracy — and not identities">
        Agreement means the cameras are geometrically consistent with each other. They can all be
        wrong in the same way, so this is not a measurement of accuracy; only a surveyed ground-truth
        coordinate gives you that. This page also does nothing with people or object identity: it
        compares one point you mark by hand in each image.
      </Notice>

      {error && <Notice tone="danger" title="Something went wrong">{error}</Notice>}

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="toolbar">
          <select value={systemId ?? ""} aria-label="Coordinate system"
                  onChange={(e) => { setSystemId(Number(e.target.value)); setMarks({}); setResult(null); }}>
            {systems.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
          <input className="search" type="text" value={label} aria-label="What is being marked"
                 placeholder="What are you marking?" onChange={(e) => setLabel(e.target.value)} />
          <div className="grow" />
          <span className="small muted">{Object.keys(marks).length} camera(s) marked</span>
          <button className="btn btn-primary btn-sm" type="button"
                  disabled={busy || Object.keys(marks).length < 2} onClick={compare}>
            {busy ? "Comparing…" : "Compare"}
          </button>
        </div>
      </div>

      {usable.length < 2 && (
        <Notice tone="info" title="At least two positioned cameras are needed">
          This frame has {usable.length} camera(s) with a calibration. Position more cameras from
          their Position &amp; Calibration tab.
        </Notice>
      )}

      <div className="editor-layout">
        <div className="stack" style={{ gap: 16 }}>
          {usable.map((camera: MapCamera) => (
            <div className="card" key={camera.camera_id}>
              <div className="card-head">
                <div className="grow">
                  <strong>{camera.name}</strong>
                  <div className="cell-sub">
                    {camera.area ?? "—"}
                    {marks[camera.camera_id] && (
                      <span className="mono">
                        {" "}· marked at {marks[camera.camera_id].u.toFixed(0)},
                        {marks[camera.camera_id].v.toFixed(0)}
                      </span>
                    )}
                  </div>
                </div>
                <CalibrationBadge status={camera.calibration_status} />
                {marks[camera.camera_id] && (
                  <button className="btn btn-sm btn-ghost" type="button"
                          onClick={() => setMarks((m) => {
                            const next = { ...m };
                            delete next[camera.camera_id];
                            return next;
                          })}>
                    Clear
                  </button>
                )}
              </div>
              <div className="card-pad">
                <CameraImagePicker
                  cameraId={camera.camera_id}
                  height={320}
                  marks={marks[camera.camera_id]
                    ? [{ id: camera.camera_id, u: marks[camera.camera_id].u,
                         v: marks[camera.camera_id].v, label }]
                    : []}
                  selectedId={null}
                  onPick={(u, v) => setMarks((m) => ({ ...m, [camera.camera_id]: { u, v } }))}
                  hint="Click the same physical spot on the floor."
                />
              </div>
            </div>
          ))}
        </div>

        <div className="side-panel">
          {map && (
            <FactoryGrid
              map={map}
              selectedCameraId={null}
              height={340}
              extraMarkers={(result?.cameras ?? [])
                .filter((c) => c.world)
                .map((c) => ({ x: c.world![0], y: c.world![1], label: c.camera_name,
                               colour: "#b0281f" }))}
            />
          )}

          {result && (
            <div className="card card-pad">
              <h3 style={{ marginBottom: 10 }}>{result.label}</h3>
              <dl className="kv small">
                <dt>Cameras compared</dt><dd>{result.usable_count}</dd>
                <dt>Worst disagreement</dt>
                <dd><strong>{result.max_disagreement_m.toFixed(3)} m</strong></dd>
                <dt>Mean disagreement</dt><dd>{result.mean_disagreement_m.toFixed(3)} m</dd>
                {result.centroid_world && (
                  <>
                    <dt>Centroid</dt>
                    <dd className="mono">
                      {result.centroid_world[0].toFixed(2)}, {result.centroid_world[1].toFixed(2)} m
                    </dd>
                  </>
                )}
              </dl>

              {result.warning && (
                <Notice tone="warn" title="Mixed approximate and solved geometry">
                  {result.warning}
                </Notice>
              )}

              <div className="section-title">Per camera</div>
              <div className="chip-list">
                {result.cameras.map((c) => (
                  <div key={c.camera_id} className="chip" style={{ cursor: "default" }}>
                    <span className="grow">{c.camera_name}</span>
                    {c.world
                      ? <span className="mono small">
                          {c.world[0].toFixed(2)}, {c.world[1].toFixed(2)}
                        </span>
                      : <span className="small faint" title={c.error}>no result</span>}
                  </div>
                ))}
              </div>

              {result.pairwise.length > 0 && (
                <>
                  <div className="section-title">Pairwise</div>
                  <div className="table-wrap">
                    <table className="data">
                      <thead><tr><th>Pair</th><th>Disagreement</th></tr></thead>
                      <tbody>
                        {result.pairwise.map((p, i) => (
                          <tr key={i}>
                            <td className="small">{p.camera_a_name} ↔ {p.camera_b_name}</td>
                            <td className="mono small">{p.disagreement_m.toFixed(3)} m</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}

              <p className="hint" style={{ marginTop: 10 }}>{result.interpretation}</p>
            </div>
          )}
        </div>
      </div>
    </main>
  );
}
