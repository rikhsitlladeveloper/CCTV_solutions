import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, api } from "../lib/api";
import CameraImagePicker, { type ImageMark } from "../components/CameraImagePicker";
import FactoryGrid from "../components/FactoryGrid";
import {
  AccuracyPanel, CalibrationBadge, CaveatList, METHOD_META,
} from "../components/calibrationUi";
import { LastChecked, Modal, Notice, Spinner, StatusBadge } from "../components/ui";
import type {
  CalibrationMethod, CalibrationRevision, CameraIntrinsics, CoordinateSystem, FactoryMap,
  Observation, ReferencePoint, ValidationDetail,
} from "../lib/calibrationTypes";
import type { Camera } from "../lib/types";

type Section = "method" | "intrinsics" | "points" | "solve" | "revisions" | "test";

const SECTIONS: Array<{ key: Section; label: string }> = [
  { key: "method", label: "1 · Method" },
  { key: "intrinsics", label: "2 · Intrinsics" },
  { key: "points", label: "3 · Reference points" },
  { key: "solve", label: "4 · Solve" },
  { key: "revisions", label: "5 · Revisions & validation" },
  { key: "test", label: "6 · Test projection" },
];

export default function CameraCalibrationPage() {
  const { id } = useParams();
  const cameraId = Number(id);

  const [camera, setCamera] = useState<Camera | null>(null);
  const [systems, setSystems] = useState<CoordinateSystem[]>([]);
  const [systemId, setSystemId] = useState<number | null>(null);
  const [points, setPoints] = useState<ReferencePoint[]>([]);
  const [observations, setObservations] = useState<Observation[]>([]);
  const [intrinsics, setIntrinsics] = useState<CameraIntrinsics[]>([]);
  const [revisions, setRevisions] = useState<CalibrationRevision[]>([]);
  const [section, setSection] = useState<Section>("method");
  const [method, setMethod] = useState<CalibrationMethod>("pnp");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [frameSize, setFrameSize] = useState<{ w: number; h: number } | null>(null);

  const activeRevision = revisions.find((r) => r.is_active) ?? null;
  const activeIntrinsics = intrinsics.find((i) => i.is_active) ?? null;

  const loadAll = useCallback(async () => {
    try {
      const [cam, sys, obs, intr, revs] = await Promise.all([
        api.getCamera(cameraId),
        api.listCoordinateSystems(),
        api.listObservations(cameraId),
        api.listIntrinsics(cameraId),
        api.listRevisions(cameraId),
      ]);
      setCamera(cam);
      setSystems(sys);
      setObservations(obs);
      setIntrinsics(intr);
      setRevisions(revs);
      const active = revs.find((r) => r.is_active);
      const chosen = active?.coordinate_system_id ?? sys[0]?.id ?? null;
      setSystemId((prev) => prev ?? chosen);
      if (active) setMethod(active.method);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not load this camera.");
    } finally {
      setLoading(false);
    }
  }, [cameraId]);

  useEffect(() => { loadAll(); }, [loadAll]);

  useEffect(() => {
    if (!systemId) return;
    api.listReferencePoints(systemId).then(setPoints).catch(() => setPoints([]));
  }, [systemId]);

  const flash = (message: string) => { setStatus(message); setTimeout(() => setStatus(null), 6000); };

  if (loading) return <main className="page"><Spinner label="Loading calibration…" /></main>;
  if (!camera) {
    return (
      <main className="page">
        <Notice tone="danger" title="Camera not found">{error}</Notice>
        <Link className="btn" to="/">Back to dashboard</Link>
      </main>
    );
  }

  const calibrationStatus = activeRevision?.status ?? "unconfigured";

  return (
    <main className="page page-wide">
      <div className="page-head">
        <div className="grow">
          <div className="row" style={{ marginBottom: 4 }}>
            <Link to={`/cameras/${cameraId}`} className="small">← {camera.name}</Link>
          </div>
          <h1>Position &amp; calibration</h1>
          <p className="page-sub">
            Place this camera in a shared metric factory frame, and verify the geometry.
          </p>
        </div>
      </div>

      {/* Connection and calibration are independent facts about a camera. */}
      <div className="card card-pad" style={{ marginBottom: 16 }}>
        <div className="row">
          <div>
            <div className="stat-label">Connection</div>
            <div className="row" style={{ marginTop: 4 }}>
              <StatusBadge status={camera.last_test_status} />
              <LastChecked camera={camera} />
            </div>
          </div>
          <div style={{ width: 28 }} />
          <div>
            <div className="stat-label">Calibration</div>
            <div className="row" style={{ marginTop: 4 }}>
              <CalibrationBadge status={calibrationStatus} />
              {activeRevision && (
                <span className="small muted">
                  {METHOD_META[activeRevision.method].label} · revision {activeRevision.revision_number}
                </span>
              )}
            </div>
          </div>
          <div className="grow" />
          <select value={systemId ?? ""} aria-label="Coordinate system"
                  onChange={(e) => setSystemId(Number(e.target.value))}>
            <option value="">Select coordinate frame…</option>
            {systems.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </div>
        <p className="hint" style={{ marginTop: 10, marginBottom: 0 }}>
          A camera can be online and uncalibrated, or calibrated and unreachable. These two
          statuses never substitute for each other.
        </p>
      </div>

      {error && <Notice tone="danger" title="Something went wrong">{error}</Notice>}
      {status && <Notice tone="ok" title={status} />}

      {!systems.length && (
        <Notice tone="warn" title="No coordinate system exists yet">
          Create one on the <Link to="/factory-map">Factory map</Link> page first. Every camera
          position belongs to an explicitly identified frame.
        </Notice>
      )}

      <div className="stepper">
        {SECTIONS.map((s) => (
          <button key={s.key} type="button"
                  className={`step${section === s.key ? " is-current" : ""}`}
                  onClick={() => setSection(s.key)}>
            {s.label}
          </button>
        ))}
      </div>

      {section === "method" && (
        <MethodSection method={method} onChange={setMethod}
                       hasIntrinsics={!!activeIntrinsics} onGoTo={setSection} />
      )}

      {section === "intrinsics" && (
        <IntrinsicsSection cameraId={cameraId} intrinsics={intrinsics}
                           onChanged={async () => { setIntrinsics(await api.listIntrinsics(cameraId)); }}
                           onFlash={flash} onError={setError} />
      )}

      {section === "points" && systemId && (
        <ObservationsSection
          cameraId={cameraId}
          points={points}
          observations={observations}
          activeRevision={activeRevision}
          frameSize={frameSize}
          onFrameSize={setFrameSize}
          onChanged={async () => { setObservations(await api.listObservations(cameraId)); }}
          onFlash={flash}
          onError={setError}
        />
      )}

      {section === "solve" && systemId && (
        <SolveSection
          cameraId={cameraId}
          systemId={systemId}
          method={method}
          observations={observations}
          activeIntrinsics={activeIntrinsics}
          frameSize={frameSize}
          onSolved={async (message) => {
            setRevisions(await api.listRevisions(cameraId));
            setCamera(await api.getCamera(cameraId));
            flash(message);
            setSection("revisions");
          }}
          onError={setError}
        />
      )}

      {section === "revisions" && (
        <RevisionsSection
          cameraId={cameraId}
          revisions={revisions}
          onChanged={async () => setRevisions(await api.listRevisions(cameraId))}
          onFlash={flash}
          onError={setError}
        />
      )}

      {section === "test" && (
        <TestProjectionSection cameraId={cameraId} systemId={systemId}
                               activeRevision={activeRevision} onError={setError} />
      )}
    </main>
  );
}

/* ---------------- 1. Method ---------------- */

function MethodSection({ method, onChange, hasIntrinsics, onGoTo }: {
  method: CalibrationMethod;
  onChange: (m: CalibrationMethod) => void;
  hasIntrinsics: boolean;
  onGoTo: (s: Section) => void;
}) {
  return (
    <div className="card card-pad">
      <div className="section-title">Choose a positioning method</div>
      <div className="stack" style={{ gap: 10 }}>
        {(["manual", "homography", "pnp"] as CalibrationMethod[]).map((key) => (
          <label key={key} className={`radio-card${method === key ? " is-selected" : ""}`}
                 style={{ minWidth: 0 }}>
            <input type="radio" name="method" checked={method === key}
                   onChange={() => onChange(key)} />
            <span>
              <strong>{METHOD_META[key].label}</strong>
              <span>{METHOD_META[key].blurb}</span>
              {key === "manual" && (
                <span style={{ display: "block", marginTop: 6 }}>
                  Result is saved as <em>Approximate</em>. Typed angles will not give accurate
                  object coordinates — use this to get cameras onto the map, not to measure.
                </span>
              )}
              {key === "homography" && (
                <span style={{ display: "block", marginTop: 6 }}>
                  Four or more non-collinear floor points; six or more spread across the working
                  area is much better. Good for floor tracking. It produces no XYZ camera position,
                  and none is fabricated from it.
                </span>
              )}
              {key === "pnp" && (
                <span style={{ display: "block", marginTop: 6 }}>
                  Needs intrinsics plus measured world points, ideally at varied heights.
                  {hasIntrinsics
                    ? " Active intrinsics are present."
                    : " No intrinsics yet — import or calibrate them first."}
                </span>
              )}
            </span>
          </label>
        ))}
      </div>
      <div className="row" style={{ marginTop: 16 }}>
        {method === "pnp" && !hasIntrinsics ? (
          <button className="btn btn-primary" type="button" onClick={() => onGoTo("intrinsics")}>
            Set up intrinsics
          </button>
        ) : method === "manual" ? (
          <button className="btn btn-primary" type="button" onClick={() => onGoTo("solve")}>
            Enter position
          </button>
        ) : (
          <button className="btn btn-primary" type="button" onClick={() => onGoTo("points")}>
            Mark reference points
          </button>
        )}
      </div>
    </div>
  );
}

/* ---------------- 2. Intrinsics ---------------- */

function IntrinsicsSection({ cameraId, intrinsics, onChanged, onFlash, onError }: {
  cameraId: number; intrinsics: CameraIntrinsics[];
  onChanged: () => Promise<void>; onFlash: (m: string) => void; onError: (m: string) => void;
}) {
  const [importing, setImporting] = useState(false);
  const [board, setBoard] = useState({ inner_cols: "9", inner_rows: "6", square_size_m: "0.025" });
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);

  const runCheckerboard = async () => {
    setBusy(true);
    try {
      const res = await api.checkerboardCalibrate(cameraId, {
        inner_cols: Number(board.inner_cols),
        inner_rows: Number(board.inner_rows),
        square_size_m: Number(board.square_size_m),
        activate: false,
      }, files);
      setResult(res);
      await onChanged();
      onFlash("Checkerboard calibration finished. Review the errors before activating it.");
    } catch (e) {
      onError(e instanceof ApiError ? e.message : "Checkerboard calibration failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack" style={{ gap: 16 }}>
      <Notice tone="info" title="Intrinsics belong to one exact image geometry">
        A calibration is tied to the resolution, crop, rotation and lens/zoom state it was measured
        at. Numenor refuses to reuse it on a different geometry rather than quietly rescaling, so
        a 1080p main-stream calibration will not be applied to a 720p substream.
      </Notice>

      <div className="card">
        <div className="card-head">
          <h2 className="grow">Stored intrinsics</h2>
          <button className="btn btn-sm" type="button" onClick={() => setImporting(true)}>
            Import JSON
          </button>
        </div>
        <div className="card-pad">
          {intrinsics.length === 0 ? (
            <p className="small muted">
              None yet. Import a validated calibration, or run the checkerboard workflow below.
              Floor-plane calibration works without intrinsics; full pose recovery does not.
            </p>
          ) : (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Label</th><th>Geometry</th><th>FOV</th><th>Source</th>
                    <th>RMS error</th><th>Active</th><th />
                  </tr>
                </thead>
                <tbody>
                  {intrinsics.map((i) => (
                    <tr key={i.id}>
                      <td>
                        <div className="cell-name">{i.label}</div>
                        <div className="cell-sub">{i.lens_description ?? "lens not recorded"}</div>
                      </td>
                      <td className="mono small">{i.geometry_key}</td>
                      <td className="small">
                        {i.horizontal_fov_deg ? `${i.horizontal_fov_deg.toFixed(1)}° × ${i.vertical_fov_deg?.toFixed(1)}°` : "—"}
                      </td>
                      <td className="small">{i.source}</td>
                      <td className="small">
                        {i.rms_reprojection_error_px != null
                          ? `${i.rms_reprojection_error_px.toFixed(2)} px`
                          : <span className="faint">—</span>}
                      </td>
                      <td>
                        {i.is_active
                          ? <span className="badge badge-ok"><span aria-hidden="true">✓</span>Active</span>
                          : <span className="faint small">—</span>}
                      </td>
                      <td>
                        <div className="row-actions">
                          {!i.is_active && (
                            <button className="btn btn-sm" type="button"
                                    onClick={async () => {
                                      await api.activateIntrinsics(cameraId, i.id);
                                      await onChanged();
                                      onFlash("Intrinsics activated.");
                                    }}>
                              Activate
                            </button>
                          )}
                          <button className="btn btn-sm btn-danger" type="button"
                                  onClick={async () => {
                                    try {
                                      await api.deleteIntrinsics(cameraId, i.id);
                                      await onChanged();
                                    } catch (e) {
                                      onError(e instanceof ApiError ? e.message : "Could not delete.");
                                    }
                                  }}>
                            Delete
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      <div className="card">
        <div className="card-head"><h2 className="grow">Guided checkerboard calibration</h2></div>
        <div className="card-pad">
          <p className="hint" style={{ marginTop: 0 }}>
            Print a checkerboard, hold it flat, and capture at least ten views at varied angles
            and distances covering the whole frame. Only the OpenCV pinhole model is supported —
            fisheye lenses are rejected rather than approximated.
          </p>
          <div className="field-row">
            <div className="field">
              <label htmlFor="cb-cols">Inner corners across</label>
              <input id="cb-cols" type="number" min={3} max={40} value={board.inner_cols}
                     onChange={(e) => setBoard((b) => ({ ...b, inner_cols: e.target.value }))} />
            </div>
            <div className="field">
              <label htmlFor="cb-rows">Inner corners down</label>
              <input id="cb-rows" type="number" min={3} max={40} value={board.inner_rows}
                     onChange={(e) => setBoard((b) => ({ ...b, inner_rows: e.target.value }))} />
              <span className="hint">Must differ from the across count, or the board is ambiguous.</span>
            </div>
            <div className="field">
              <label htmlFor="cb-square">Square size (m)</label>
              <input id="cb-square" type="number" step="0.001" value={board.square_size_m}
                     onChange={(e) => setBoard((b) => ({ ...b, square_size_m: e.target.value }))} />
            </div>
          </div>
          <div className="field">
            <label htmlFor="cb-files">Calibration images</label>
            <input id="cb-files" type="file" accept="image/*" multiple
                   onChange={(e) => setFiles(Array.from(e.target.files ?? []))} />
            {files.length > 0 && <span className="hint">{files.length} image(s) selected.</span>}
          </div>
          <button className="btn btn-primary" type="button" disabled={busy || files.length < 3}
                  onClick={runCheckerboard}>
            {busy ? "Detecting and solving…" : "Run calibration"}
          </button>
        </div>
      </div>

      {result && <CheckerboardResult result={result} onClose={() => setResult(null)} />}

      {importing && (
        <ImportIntrinsicsModal cameraId={cameraId} onClose={() => setImporting(false)}
                               onImported={async () => {
                                 setImporting(false);
                                 await onChanged();
                                 onFlash("Intrinsics imported.");
                               }} />
      )}
    </div>
  );
}

function CheckerboardResult({ result, onClose }: {
  result: Record<string, unknown>; onClose: () => void;
}) {
  const diversity = result.pose_diversity as Record<string, unknown> | undefined;
  const warnings = (result.warnings as string[]) ?? [];
  const perImage = (result.per_image as Array<Record<string, unknown>>) ?? [];
  const failed = perImage.filter((i) => !i.detected);

  return (
    <div className="card card-pad">
      <div className="row" style={{ marginBottom: 10 }}>
        <h3 className="grow">Calibration result</h3>
        <button className="btn btn-sm btn-ghost" type="button" onClick={onClose}>Dismiss</button>
      </div>
      <dl className="kv small">
        <dt>RMS reprojection error</dt>
        <dd>
          {String(result.rms_reprojection_error_px)} px
          <div className="faint">{String(result.note)}</div>
        </dd>
        <dt>Views used</dt>
        <dd>{perImage.filter((i) => i.detected).length} of {perImage.length}</dd>
        {diversity && (
          <>
            <dt>Pose diversity</dt>
            <dd>
              tilt range {String(diversity.tilt_range_deg)}°, distance range{" "}
              {String(diversity.distance_range_m)} m
              {diversity.sufficient === false && (
                <div className="faint">Not varied enough for a confident distortion estimate.</div>
              )}
            </dd>
          </>
        )}
      </dl>
      <CaveatList items={warnings} title="Warnings" tone="warn" />
      {failed.length > 0 && (
        <details style={{ marginTop: 10 }}>
          <summary className="small">{failed.length} image(s) had no detection</summary>
          <ul className="small muted" style={{ paddingLeft: 18 }}>
            {failed.map((f, i) => <li key={i}>{String(f.name)}: {String(f.reason)}</li>)}
          </ul>
        </details>
      )}
    </div>
  );
}

function ImportIntrinsicsModal({ cameraId, onClose, onImported }: {
  cameraId: number; onClose: () => void; onImported: () => void;
}) {
  const [text, setText] = useState("");
  const [label, setLabel] = useState("Imported calibration");
  const [busy, setBusy] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true); setLocalError(null);
    try {
      const parsed = JSON.parse(text);
      const body = {
        label,
        model: parsed.model ?? parsed.distortion_model ?? "pinhole",
        camera_matrix: parsed.camera_matrix ?? parsed.K ?? parsed.camera_matrix_k,
        distortion_coefficients:
          parsed.distortion_coefficients ?? parsed.dist ?? parsed.distortion ?? [],
        image_width: parsed.image_width ?? parsed.width,
        image_height: parsed.image_height ?? parsed.height,
        image_rotation_deg: parsed.image_rotation_deg ?? 0,
        crop: parsed.crop ?? null,
        lens_description: parsed.lens ?? parsed.lens_description ?? null,
        zoom_state: parsed.zoom_state ?? null,
        rms_reprojection_error_px: parsed.rms_reprojection_error_px ?? null,
        calibrated_at: parsed.calibrated_at ?? null,
        notes: parsed.notes ?? null,
        activate: true,
      };
      await api.importIntrinsics(cameraId, body);
      onImported();
    } catch (e) {
      const message = e instanceof ApiError ? e.message
        : e instanceof SyntaxError ? "That is not valid JSON."
        : "Import failed.";
      setLocalError(message);
      setBusy(false);
    }
  };

  return (
    <Modal title="Import camera intrinsics" wide onClose={onClose}
           footer={<>
             <button className="btn" type="button" onClick={onClose}>Cancel</button>
             <button className="btn btn-primary" type="button" onClick={submit} disabled={busy || !text.trim()}>
               {busy ? "Importing…" : "Import"}
             </button>
           </>}>
      {localError && <Notice tone="danger" title="Could not import">{localError}</Notice>}
      <div className="field">
        <label htmlFor="intr-label">Label</label>
        <input id="intr-label" type="text" value={label} onChange={(e) => setLabel(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="intr-json">Calibration JSON</label>
        <textarea id="intr-json" value={text} rows={12} className="mono"
                  placeholder={`{
  "model": "pinhole",
  "camera_matrix": [[1380,0,958],[0,1378,543],[0,0,1]],
  "distortion_coefficients": [-0.284, 0.083, 0.0006, -0.0004, -0.011],
  "image_width": 1920,
  "image_height": 1080,
  "lens": "4 mm fixed",
  "rms_reprojection_error_px": 0.21
}`}
                  onChange={(e) => setText(e.target.value)} />
        <span className="hint">
          Must state the resolution it was calibrated at. Fisheye models are rejected.
        </span>
      </div>
      <div className="row">
        <input type="file" accept="application/json,.json"
               onChange={async (e) => {
                 const file = e.target.files?.[0];
                 if (file) setText(await file.text());
               }} />
      </div>
    </Modal>
  );
}

/* ---------------- 3. Reference points ---------------- */

function ObservationsSection({
  cameraId, points, observations, activeRevision, frameSize, onFrameSize, onChanged, onFlash, onError,
}: {
  cameraId: number;
  points: ReferencePoint[];
  observations: Observation[];
  activeRevision: CalibrationRevision | null;
  frameSize: { w: number; h: number } | null;
  onFrameSize: (s: { w: number; h: number }) => void;
  onChanged: () => Promise<void>;
  onFlash: (m: string) => void;
  onError: (m: string) => void;
}) {
  const [pendingPointId, setPendingPointId] = useState<number | null>(null);
  const [selected, setSelected] = useState<number | string | null>(null);
  const [draft, setDraft] = useState<Record<number, { u: number; v: number }>>({});
  const [busy, setBusy] = useState(false);

  const observedIds = new Set(observations.map((o) => o.reference_point_id));
  const unobserved = points.filter((p) => !observedIds.has(p.id) && !(p.id in draft));

  const marks: ImageMark[] = useMemo(() => {
    const list: ImageMark[] = observations.map((o) => ({
      id: o.id,
      u: o.pixel_u,
      v: o.pixel_v,
      label: o.reference_point?.code ?? `#${o.reference_point_id}`,
      role: o.role,
    }));
    Object.entries(draft).forEach(([pointId, pos]) => {
      const point = points.find((p) => p.id === Number(pointId));
      list.push({ id: `draft-${pointId}`, u: pos.u, v: pos.v,
                  label: `${point?.code ?? pointId} (unsaved)` });
    });
    return list;
  }, [observations, draft, points]);

  const save = async () => {
    if (!frameSize) { onError("Load a camera frame first so pixel coordinates have a geometry."); return; }
    setBusy(true);
    try {
      const payload = [
        ...observations.map((o) => ({
          reference_point_id: o.reference_point_id,
          pixel_u: o.pixel_u, pixel_v: o.pixel_v,
          image_width: o.image_width, image_height: o.image_height,
          role: o.role,
        })),
        ...Object.entries(draft).map(([pointId, pos]) => ({
          reference_point_id: Number(pointId),
          pixel_u: pos.u, pixel_v: pos.v,
          image_width: frameSize.w, image_height: frameSize.h,
          role: "fit" as const,
        })),
      ];
      await api.saveObservations(cameraId, { observations: payload });
      setDraft({});
      await onChanged();
      onFlash("Observations saved.");
    } catch (e) {
      onError(e instanceof ApiError ? e.message : "Could not save observations.");
    } finally {
      setBusy(false);
    }
  };

  const toggleRole = async (obs: Observation) => {
    setBusy(true);
    try {
      await api.saveObservations(cameraId, {
        observations: [{
          reference_point_id: obs.reference_point_id,
          pixel_u: obs.pixel_u, pixel_v: obs.pixel_v,
          image_width: obs.image_width, image_height: obs.image_height,
          role: obs.role === "fit" ? "holdout" : "fit",
        }],
      });
      await onChanged();
    } catch (e) {
      onError(e instanceof ApiError ? e.message : "Could not change the role.");
    } finally {
      setBusy(false);
    }
  };

  const fitCount = observations.filter((o) => o.role === "fit").length;
  const holdoutCount = observations.filter((o) => o.role === "holdout").length;

  return (
    <div className="editor-layout">
      <div className="stack" style={{ gap: 12 }}>
        <CameraImagePicker
          cameraId={cameraId}
          marks={marks}
          selectedId={selected}
          onSelect={setSelected}
          onImageLoaded={(w, h) => onFrameSize({ w, h })}
          onPick={pendingPointId
            ? (u, v) => { setDraft((d) => ({ ...d, [pendingPointId]: { u, v } })); setPendingPointId(null); }
            : undefined}
          hint={pendingPointId
            ? "Click the exact spot in the image where that point is."
            : "Pick a reference point on the right, then click it in the image."}
        />
        {activeRevision && frameSize
          && activeRevision.source_image_width
          && (activeRevision.source_image_width !== frameSize.w
            || activeRevision.source_image_height !== frameSize.h) && (
          <Notice tone="danger" title="This frame does not match the active calibration">
            The active calibration was solved at {activeRevision.source_image_width}×
            {activeRevision.source_image_height}, but this frame is {frameSize.w}×{frameSize.h}.
            Pixel coordinates are not comparable across image geometries — recalibrate at this size.
          </Notice>
        )}
      </div>

      <div className="side-panel">
        <div className="card card-pad">
          <h3 style={{ marginBottom: 8 }}>Points to mark</h3>
          {points.length === 0 ? (
            <p className="small muted">
              No reference points in this frame yet. Add them on the{" "}
              <Link to="/factory-map">Factory map</Link>.
            </p>
          ) : unobserved.length === 0 ? (
            <p className="small muted">Every reference point in this frame has been marked.</p>
          ) : (
            <div className="chip-list">
              {unobserved.map((p) => (
                <button key={p.id} type="button"
                        className={`chip${pendingPointId === p.id ? " is-selected" : ""}`}
                        onClick={() => setPendingPointId(p.id)}>
                  <span className="grow"><strong>{p.code}</strong> {p.name}</span>
                  <span className="mono small faint">
                    {p.x.toFixed(1)}, {p.y.toFixed(1)}, {p.z.toFixed(1)}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="card card-pad">
          <div className="row" style={{ marginBottom: 8 }}>
            <h3 className="grow">Marked ({observations.length})</h3>
            {Object.keys(draft).length > 0 && (
              <button className="btn btn-sm btn-primary" type="button" onClick={save} disabled={busy}>
                Save {Object.keys(draft).length} new
              </button>
            )}
          </div>
          <p className="hint" style={{ marginTop: 0 }}>
            {fitCount} for fitting, {holdoutCount} held out. Held-out points are never used to
            solve, so they are the only independent check of accuracy. Reserve at least two.
          </p>
          {observations.length === 0 ? (
            <p className="small muted">Nothing marked yet.</p>
          ) : (
            <div className="chip-list">
              {observations.map((o) => (
                <div key={o.id} className={`chip${selected === o.id ? " is-selected" : ""}`}
                     style={{ cursor: "pointer" }} onClick={() => setSelected(o.id)}>
                  <span className="grow">
                    <strong>{o.reference_point?.code}</strong>{" "}
                    <span className="faint mono">
                      {o.pixel_u.toFixed(0)},{o.pixel_v.toFixed(0)}
                    </span>
                  </span>
                  <button className={`badge ${o.role === "holdout" ? "badge-warn" : "badge-accent"}`}
                          type="button" style={{ border: "none", cursor: "pointer" }}
                          onClick={(e) => { e.stopPropagation(); toggleRole(o); }}
                          title="Switch between fitting and held-out">
                    {o.role === "holdout" ? "held out" : "fit"}
                  </button>
                  <button className="btn btn-sm btn-ghost" type="button"
                          onClick={async (e) => {
                            e.stopPropagation();
                            await api.deleteObservation(cameraId, o.id);
                            await onChanged();
                          }}>×</button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ---------------- 4. Solve ---------------- */

function SolveSection({
  cameraId, systemId, method, observations, activeIntrinsics, frameSize, onSolved, onError,
}: {
  cameraId: number; systemId: number; method: CalibrationMethod;
  observations: Observation[]; activeIntrinsics: CameraIntrinsics | null;
  frameSize: { w: number; h: number } | null;
  onSolved: (message: string) => Promise<void>; onError: (m: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [manual, setManual] = useState({
    x: "0", y: "0", z: "4", roll_deg: "-90", pitch_deg: "0", yaw_deg: "0",
    approx_hfov_deg: "78", approx_range_m: "12",
  });

  const geometry = frameSize
    ?? (activeIntrinsics ? { w: activeIntrinsics.width, h: activeIntrinsics.height } : null)
    ?? (observations[0] ? { w: observations[0].image_width, h: observations[0].image_height } : null);

  const solve = async () => {
    setBusy(true);
    try {
      if (method === "manual") {
        await api.saveManualPose(cameraId, {
          coordinate_system_id: systemId,
          x: Number(manual.x), y: Number(manual.y), z: Number(manual.z),
          roll_deg: Number(manual.roll_deg), pitch_deg: Number(manual.pitch_deg),
          yaw_deg: Number(manual.yaw_deg),
          approx_hfov_deg: manual.approx_hfov_deg ? Number(manual.approx_hfov_deg) : null,
          approx_range_m: manual.approx_range_m ? Number(manual.approx_range_m) : null,
          source_image_width: geometry?.w ?? null,
          source_image_height: geometry?.h ?? null,
          activate: true,
        });
        await onSolved("Approximate placement saved and activated.");
      } else if (!geometry) {
        onError("Load a camera frame first so the solve knows the image geometry.");
      } else if (method === "homography") {
        await api.solveHomography(cameraId, {
          coordinate_system_id: systemId,
          image_width: geometry.w, image_height: geometry.h,
          plane_z: 0, use_intrinsics: true, activate: false,
        });
        await onSolved("Floor homography solved. Review it, then activate it.");
      } else {
        await api.solvePose(cameraId, {
          coordinate_system_id: systemId,
          image_width: geometry.w, image_height: geometry.h, activate: false,
        });
        await onSolved("Camera pose solved. Review it, then activate it.");
      }
    } catch (e) {
      onError(e instanceof ApiError ? e.message : "The solve failed.");
    } finally {
      setBusy(false);
    }
  };

  const fitCount = observations.filter((o) => o.role === "fit").length;

  return (
    <div className="card card-pad">
      <div className="section-title">{METHOD_META[method].label}</div>

      {method === "manual" ? (
        <>
          <Notice tone="warn" title="Saved as an approximate placement">
            Hand-entered angles put a camera on the map. They do not produce accurate object
            coordinates, and this revision will never be marked Validated.
          </Notice>
          <div className="field-row">
            {(["x", "y", "z"] as const).map((axis) => (
              <div className="field" key={axis}>
                <label htmlFor={`m-${axis}`}>{axis.toUpperCase()} (m)</label>
                <input id={`m-${axis}`} type="number" step="0.1" value={manual[axis]}
                       onChange={(e) => setManual((m) => ({ ...m, [axis]: e.target.value }))} />
              </div>
            ))}
          </div>
          <div className="field-row">
            {(["roll_deg", "pitch_deg", "yaw_deg"] as const).map((angle) => (
              <div className="field" key={angle}>
                <label htmlFor={`m-${angle}`}>{angle.replace("_deg", "")} (°)</label>
                <input id={`m-${angle}`} type="number" step="1" value={manual[angle]}
                       onChange={(e) => setManual((m) => ({ ...m, [angle]: e.target.value }))} />
              </div>
            ))}
          </div>
          <Notice tone="info" title="What these angles mean">
            R_world_camera = Rz(yaw) · Ry(pitch) · Rx(roll), applied to the optical frame
            (X right, Y down, Z forward). Zero rotation points the camera straight up, not along
            the floor — a wall-mounted camera looking horizontally along world +Y is roll −90°,
            pitch 0°, yaw 0°. Yaw turns counter-clockwise seen from above, while map heading runs
            clockwise, so heading = −yaw.
          </Notice>
          <div className="field-row">
            <div className="field">
              <label htmlFor="m-hfov">Approximate horizontal FOV (°)</label>
              <input id="m-hfov" type="number" step="1" value={manual.approx_hfov_deg}
                     onChange={(e) => setManual((m) => ({ ...m, approx_hfov_deg: e.target.value }))} />
              <span className="hint">A nominal figure for drawing only — not measured intrinsics.</span>
            </div>
            <div className="field">
              <label htmlFor="m-range">Approximate viewing distance (m)</label>
              <input id="m-range" type="number" step="1" value={manual.approx_range_m}
                     onChange={(e) => setManual((m) => ({ ...m, approx_range_m: e.target.value }))} />
            </div>
          </div>
        </>
      ) : (
        <>
          <dl className="kv small" style={{ marginBottom: 14 }}>
            <dt>Points for fitting</dt>
            <dd>
              {fitCount}
              {fitCount < 4 && <span className="faint"> — at least 4 are required</span>}
              {fitCount >= 4 && fitCount < 6 && <span className="faint"> — 6 or more is better</span>}
            </dd>
            <dt>Held out</dt>
            <dd>{observations.filter((o) => o.role === "holdout").length}</dd>
            <dt>Image geometry</dt>
            <dd className="mono">
              {geometry ? `${geometry.w}×${geometry.h}` : <span className="faint">Load a frame first</span>}
            </dd>
            <dt>Intrinsics</dt>
            <dd>
              {activeIntrinsics
                ? `${activeIntrinsics.label} (${activeIntrinsics.geometry_key})`
                : method === "pnp"
                  ? <span className="badge badge-danger"><span aria-hidden="true">!</span>Required</span>
                  : <span className="faint">None — the fit will run on raw pixels</span>}
            </dd>
          </dl>

          {method === "homography" && !activeIntrinsics && (
            <Notice tone="warn" title="No distortion model available">
              The homography will be fitted on raw pixels. Lens distortion will not be corrected,
              so accuracy falls off toward the image edges. This is an explicitly approximate floor
              mapping, valid near the measured points.
            </Notice>
          )}
          {method === "homography" && (
            <Notice tone="info" title="A homography is not a camera pose">
              This maps image pixels onto one floor plane. It cannot tell you where the camera is,
              and Numenor will not invent an XYZ position from it.
            </Notice>
          )}
        </>
      )}

      <button className="btn btn-primary" type="button" onClick={solve}
              disabled={busy || (method !== "manual" && fitCount < 4)}>
        {busy ? "Solving…" : method === "manual" ? "Save placement" : "Solve"}
      </button>
      {method !== "manual" && (
        <p className="hint" style={{ marginTop: 8 }}>
          Solving creates a new revision but does not activate it. Your current calibration keeps
          working until you explicitly switch over.
        </p>
      )}
    </div>
  );
}

/* ---------------- 5. Revisions ---------------- */

function RevisionsSection({ cameraId, revisions, onChanged, onFlash, onError }: {
  cameraId: number; revisions: CalibrationRevision[];
  onChanged: () => Promise<void>; onFlash: (m: string) => void; onError: (m: string) => void;
}) {
  void onError;
  const [validation, setValidation] = useState<{ revision: number; detail: ValidationDetail } | null>(null);
  const [busy, setBusy] = useState(false);
  const [exported, setExported] = useState<string | null>(null);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    try { await fn(); } catch (e) {
      onError(e instanceof ApiError ? e.message : "Action failed.");
    } finally { setBusy(false); }
  };

  if (!revisions.length) {
    return (
      <div className="card empty">
        <div className="empty-mark" aria-hidden="true">◎</div>
        <h2>No calibration revisions yet</h2>
        <p>Choose a method and solve; each attempt is kept as its own revision.</p>
      </div>
    );
  }

  return (
    <div className="stack" style={{ gap: 16 }}>
      {revisions.map((revision) => (
        <div className="card" key={revision.id}>
          <div className="card-head">
            <div className="grow">
              <div className="row">
                <strong>Revision {revision.revision_number}</strong>
                <CalibrationBadge status={revision.status} />
                {revision.is_active && (
                  <span className="badge badge-ok"><span aria-hidden="true">✓</span>Active</span>
                )}
              </div>
              <div className="cell-sub">
                {METHOD_META[revision.method].label} · {revision.solver ?? "—"} ·{" "}
                {new Date(revision.created_at).toLocaleString(undefined,
                  { dateStyle: "medium", timeStyle: "short" })}
                {revision.created_by && ` · ${revision.created_by}`}
              </div>
            </div>
            {!revision.is_active && (
              <button className="btn btn-sm" type="button" disabled={busy}
                      onClick={() => run(async () => {
                        await api.activateRevision(cameraId, revision.id);
                        await onChanged();
                        onFlash(`Revision ${revision.revision_number} is now active.`);
                      })}>
                Activate
              </button>
            )}
            <button className="btn btn-sm" type="button" disabled={busy}
                    onClick={() => run(async () => {
                      const res = await api.validateRevision(cameraId, revision.id);
                      setValidation({ revision: revision.revision_number, detail: res.validation });
                      await onChanged();
                    })}>
              Validate
            </button>
            <button className="btn btn-sm" type="button" disabled={busy}
                    onClick={() => run(async () => {
                      const data = await api.exportCalibration(cameraId, revision.id);
                      setExported(JSON.stringify(data, null, 2));
                    })}>
              Export
            </button>
          </div>
          <div className="card-pad">
            <div className="detail-grid">
              <div>
                <dl className="kv small">
                  {revision.position ? (
                    <>
                      <dt>Position</dt>
                      <dd className="mono">
                        {revision.position.x.toFixed(3)}, {revision.position.y.toFixed(3)},{" "}
                        {revision.position.z.toFixed(3)} m
                      </dd>
                      <dt>Roll / pitch / yaw</dt>
                      <dd className="mono">
                        {revision.rpy_deg!.roll.toFixed(2)}°, {revision.rpy_deg!.pitch.toFixed(2)}°,{" "}
                        {revision.rpy_deg!.yaw.toFixed(2)}°
                      </dd>
                      <dt>Map heading</dt>
                      <dd>{revision.map_heading_deg != null
                        ? `${revision.map_heading_deg.toFixed(1)}°`
                        : "Not horizontal"}</dd>
                    </>
                  ) : (
                    <>
                      <dt>Pose</dt>
                      <dd>
                        <span className="faint">None</span>
                        <div className="faint">
                          A floor homography does not determine where the camera is.
                        </div>
                      </dd>
                    </>
                  )}
                  <dt>Pixels</dt>
                  <dd>
                    {revision.pixel_convention}
                    {revision.distortion_corrected
                      ? <span className="faint"> · distortion modelled</span>
                      : <span className="faint"> · distortion NOT corrected</span>}
                  </dd>
                  <dt>Solved at</dt>
                  <dd className="mono">
                    {revision.source_image_width}×{revision.source_image_height}
                  </dd>
                </dl>
              </div>
              <AccuracyPanel revision={revision} />
            </div>
            <CaveatList items={revision.warnings} title="Solver warnings" tone="warn" />
            <CaveatList items={revision.caveats} title="How far to trust this" tone="info" />
          </div>
        </div>
      ))}

      {validation && (
        <Modal title={`Validation — revision ${validation.revision}`} wide
               onClose={() => setValidation(null)}
               footer={<button className="btn" type="button" onClick={() => setValidation(null)}>Close</button>}>
          <Notice tone={validation.detail.passed ? "ok" : "warn"}
                  title={validation.detail.passed
                    ? "Within the configured thresholds"
                    : "Not validated"}>
            <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
              {validation.detail.messages.map((m, i) => <li key={i}>{m}</li>)}
            </ul>
          </Notice>
          <dl className="kv">
            <dt>Fitting reprojection error</dt>
            <dd>
              {validation.detail.fit_reprojection_error_px != null
                ? `${validation.detail.fit_reprojection_error_px.toFixed(3)} px` : "—"}
              <div className="faint small">
                {validation.detail.interpretation.fit_reprojection_error_px}
              </div>
            </dd>
            <dt>Held-out ground error</dt>
            <dd>
              {validation.detail.holdout_ground_error_m != null
                ? `${validation.detail.holdout_ground_error_m.toFixed(3)} m mean, ${validation.detail.holdout_max_error_m?.toFixed(3)} m worst`
                : "Not measured"}
              <div className="faint small">
                {validation.detail.interpretation.holdout_ground_error_m}
              </div>
            </dd>
            <dt>Thresholds</dt>
            <dd className="mono small">{JSON.stringify(validation.detail.thresholds)}</dd>
          </dl>
          {validation.detail.per_point.length > 0 && (
            <>
              <div className="section-title">Per held-out point</div>
              <div className="table-wrap">
                <table className="data">
                  <thead><tr><th>Code</th><th>Expected</th><th>Projected</th><th>Error</th></tr></thead>
                  <tbody>
                    {validation.detail.per_point.map((p, i) => (
                      <tr key={i}>
                        <td>{String(p.code)}</td>
                        <td className="mono small">
                          {(p.expected_world as number[])?.map((v) => v.toFixed(2)).join(", ")}
                        </td>
                        <td className="mono small">
                          {(p.projected_world as number[])?.map((v) => v.toFixed(2)).join(", ") ?? "—"}
                        </td>
                        <td>
                          {p.ground_error_m != null
                            ? `${Number(p.ground_error_m).toFixed(3)} m`
                            : <span className="small faint">{String(p.error ?? "—")}</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </Modal>
      )}

      {exported && (
        <Modal title="Calibration export" wide onClose={() => setExported(null)}
               footer={<>
                 <button className="btn" type="button"
                         onClick={() => navigator.clipboard?.writeText(exported)}>Copy</button>
                 <button className="btn" type="button" onClick={() => setExported(null)}>Close</button>
               </>}>
          <p className="small muted" style={{ marginTop: 0 }}>
            Self-describing geometry for downstream services. Contains no credentials, host
            addresses or stream URLs.
          </p>
          <textarea readOnly value={exported} rows={20} className="mono" />
        </Modal>
      )}
    </div>
  );
}

/* ---------------- 6. Test projection ---------------- */

function TestProjectionSection({ cameraId, systemId, activeRevision, onError }: {
  cameraId: number; systemId: number | null;
  activeRevision: CalibrationRevision | null; onError: (m: string) => void;
}) {
  const [map, setMap] = useState<FactoryMap | null>(null);
  const [worldResult, setWorldResult] = useState<{ u: number; v: number; world: number[] } | null>(null);
  const [imageResult, setImageResult] = useState<{ x: number; y: number; pixel: number[]; inImage: boolean } | null>(null);
  const [worldInput, setWorldInput] = useState({ x: "5", y: "5", z: "0" });
  const [overlay, setOverlay] = useState<Record<string, unknown> | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (systemId) api.factoryMap(systemId).then(setMap).catch(() => setMap(null));
  }, [systemId]);

  if (!activeRevision) {
    return (
      <Notice tone="warn" title="No active calibration">
        Solve and activate a calibration before testing projections.
      </Notice>
    );
  }

  const pickImagePoint = async (u: number, v: number) => {
    setMessage(null);
    try {
      const res = await api.imageToWorld(cameraId, { pixel_u: u, pixel_v: v });
      setWorldResult({ u, v, world: res.world! });
    } catch (e) {
      setWorldResult(null);
      setMessage(e instanceof ApiError ? e.message : "Projection failed.");
    }
  };

  const projectWorld = async () => {
    setMessage(null);
    try {
      const res = await api.worldToImage(cameraId, {
        x: Number(worldInput.x), y: Number(worldInput.y), z: Number(worldInput.z),
      });
      setImageResult({ x: Number(worldInput.x), y: Number(worldInput.y),
                       pixel: res.pixel!, inImage: !!res.in_image });
    } catch (e) {
      setImageResult(null);
      setMessage(e instanceof ApiError ? e.message : "Projection failed.");
    }
  };

  const overlaySegments = ((overlay?.grid as Record<string, unknown> | undefined)?.segments as
    Array<{ pixels: Array<[number, number] | null> }> | undefined)?.map((s) => s.pixels) ?? [];

  const overlayPoints = (overlay?.reference_points as Array<Record<string, unknown>> | undefined) ?? [];

  const marks: ImageMark[] = [
    ...overlayPoints.map((p, i) => ({
      id: `ov-${i}`,
      u: (p.marked_pixel as number[])[0],
      v: (p.marked_pixel as number[])[1],
      label: String(p.code),
      role: (p.role as "fit" | "holdout") ?? "fit",
      projected: (p.projected_pixel as [number, number]) ?? null,
      errorPx: (p.reprojection_error_px as number) ?? null,
    })),
    ...(imageResult ? [{
      id: "world-probe",
      u: imageResult.pixel[0], v: imageResult.pixel[1],
      label: `world ${imageResult.x}, ${imageResult.y}`,
    }] : []),
    ...(worldResult ? [{ id: "image-probe", u: worldResult.u, v: worldResult.v, label: "probe" }] : []),
  ];

  return (
    <div className="editor-layout">
      <div className="stack" style={{ gap: 12 }}>
        <CameraImagePicker
          cameraId={cameraId}
          marks={marks}
          selectedId={null}
          onPick={pickImagePoint}
          overlaySegments={overlaySegments}
          hint="Click anywhere on the floor to project it into world coordinates."
        />
        {map && (
          <FactoryGrid
            map={map}
            selectedCameraId={cameraId}
            height={340}
            extraMarkers={[
              ...(worldResult ? [{ x: worldResult.world[0], y: worldResult.world[1],
                                   label: "projected", colour: "#b0281f" }] : []),
              ...(imageResult ? [{ x: imageResult.x, y: imageResult.y,
                                   label: "world probe", colour: "#0f7b4f" }] : []),
            ]}
          />
        )}
      </div>

      <div className="side-panel">
        {message && <Notice tone="warn" title="Projection refused">{message}</Notice>}

        <CaveatList items={activeRevision.caveats} title="How far to trust these numbers" tone="info" />

        <div className="card card-pad">
          <h3 style={{ marginBottom: 8 }}>Image → world</h3>
          {worldResult ? (
            <dl className="kv small">
              <dt>Clicked pixel</dt>
              <dd className="mono">{worldResult.u.toFixed(0)}, {worldResult.v.toFixed(0)}</dd>
              <dt>Floor position</dt>
              <dd className="mono">
                <strong>
                  {worldResult.world[0].toFixed(3)}, {worldResult.world[1].toFixed(3)} m
                </strong>
                <div className="faint">at Z = {worldResult.world[2].toFixed(2)} m</div>
              </dd>
            </dl>
          ) : (
            <p className="small muted">Click a point on the camera image.</p>
          )}
        </div>

        <div className="card card-pad">
          <h3 style={{ marginBottom: 8 }}>World → image</h3>
          <div className="field-row">
            {(["x", "y", "z"] as const).map((axis) => (
              <div className="field" key={axis}>
                <label htmlFor={`w-${axis}`}>{axis.toUpperCase()} (m)</label>
                <input id={`w-${axis}`} type="number" step="0.1" value={worldInput[axis]}
                       onChange={(e) => setWorldInput((w) => ({ ...w, [axis]: e.target.value }))} />
              </div>
            ))}
          </div>
          <button className="btn btn-sm btn-block" type="button" onClick={projectWorld}>
            Project into the image
          </button>
          {imageResult && (
            <dl className="kv small" style={{ marginTop: 10 }}>
              <dt>Pixel</dt>
              <dd className="mono">
                {imageResult.pixel[0].toFixed(0)}, {imageResult.pixel[1].toFixed(0)}
              </dd>
              <dt>In frame</dt>
              <dd>{imageResult.inImage ? "Yes" : "Outside the image"}</dd>
            </dl>
          )}
        </div>

        <div className="card card-pad">
          <h3 style={{ marginBottom: 8 }}>Overlays</h3>
          <button className="btn btn-sm btn-block" type="button"
                  onClick={async () => {
                    try { setOverlay(await api.projectionOverlay(cameraId)); }
                    catch (e) { onError(e instanceof ApiError ? e.message : "Overlay failed."); }
                  }}>
            Show reference points and floor grid
          </button>
          {overlay && (
            <p className="hint" style={{ marginTop: 8 }}>
              Circles are where you marked each point; rings are where this calibration projects
              them. The gap between them is the reprojection error.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
