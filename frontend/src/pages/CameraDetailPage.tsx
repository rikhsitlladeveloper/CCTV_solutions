import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ApiError, api, mediaUrl } from "../lib/api";
import FloorPlanCanvas from "../components/FloorPlanCanvas";
import PreviewPanel from "../components/PreviewPanel";
import { LastChecked, Modal, Notice, PlacementBadge, Spinner, StatusBadge, locationSummary } from "../components/ui";
import { CalibrationBadge, METHOD_META } from "../components/calibrationUi";
import { TestResultBody } from "./DashboardPage";
import type { Camera, FloorPlan, TestResult } from "../lib/types";
import type { CalibrationRevision } from "../lib/calibrationTypes";

export default function CameraDetailPage() {
  const { id } = useParams();
  const cameraId = Number(id);
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();

  const [camera, setCamera] = useState<Camera | null>(null);
  const [plan, setPlan] = useState<FloorPlan | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  const [editing, setEditing] = useState(params.get("edit") === "1");
  const [calibration, setCalibration] = useState<CalibrationRevision | null>(null);

  const load = useCallback(async () => {
    try {
      const cam = await api.getCamera(cameraId);
      setCamera(cam);
      if (cam.placement) {
        setPlan(await api.getFloorPlan(cam.placement.floor_plan_id).catch(() => null));
      } else {
        setPlan(null);
      }
      const revisions = await api.listRevisions(cameraId).catch(() => []);
      setCalibration(revisions.find((r) => r.is_active) ?? null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not load this camera.");
    } finally {
      setLoading(false);
    }
  }, [cameraId]);

  useEffect(() => { load(); }, [load]);

  const retest = async () => {
    setTesting(true);
    try {
      setTestResult(await api.testCamera(cameraId));
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The connection test could not be run.");
    } finally {
      setTesting(false);
    }
  };

  if (loading) return <main className="page"><Spinner label="Loading camera…" /></main>;
  if (!camera) {
    return (
      <main className="page">
        <Notice tone="danger" title="Camera not found">{error ?? "This camera no longer exists."}</Notice>
        <Link className="btn" to="/">Back to dashboard</Link>
      </main>
    );
  }

  return (
    <main className="page">
      <div className="page-head">
        <div className="grow">
          <div className="row" style={{ marginBottom: 4 }}>
            <Link to="/" className="small">← All cameras</Link>
          </div>
          <h1>{camera.name}</h1>
          <p className="page-sub">{locationSummary(camera)}</p>
        </div>
        <button className="btn" type="button" disabled={testing} onClick={retest}>
          {testing ? "Testing…" : "Retest connection"}
        </button>
        <button className="btn btn-primary" type="button" onClick={() => { setEditing(true); setParams({ edit: "1" }); }}>
          Edit
        </button>
      </div>

      {error && <Notice tone="danger" title="Something went wrong">{error}</Notice>}

      <div className="card card-pad" style={{ marginBottom: 16 }}>
        <div className="row">
          <div>
            <div className="stat-label">Connection</div>
            <div className="row" style={{ marginTop: 4 }}>
              <StatusBadge status={camera.last_test_status} />
              <LastChecked camera={camera} />
            </div>
          </div>
          <div style={{ width: 24 }} />
          <div>
            <div className="stat-label">Calibration</div>
            <div className="row" style={{ marginTop: 4 }}>
              <CalibrationBadge status={calibration?.status ?? "unconfigured"} />
              {calibration && (
                <span className="small muted">
                  {METHOD_META[calibration.method].label} · revision {calibration.revision_number}
                </span>
              )}
            </div>
          </div>
          <div style={{ width: 24 }} />
          <div>
            <div className="stat-label">Floor-plan placement</div>
            <div className="row" style={{ marginTop: 4 }}>
              <PlacementBadge camera={camera} />
            </div>
          </div>
          <div className="grow" />
          <Link className="btn btn-sm" to={`/cameras/${camera.id}/calibration`}>
            Position &amp; calibration
          </Link>
        </div>
        <p className="hint" style={{ marginTop: 10, marginBottom: 0 }}>
          Reaching a camera, positioning it in the factory frame, and pinning it on a floor plan
          are three separate things. None of them implies another.
        </p>
      </div>

      {camera.last_test_status === "partial" && (
        <Notice tone="warn" title="Login works, video does not">
          {camera.last_test_error ?? "The control channel responded but no video frame was received."}
        </Notice>
      )}
      {["auth_failed", "unreachable", "timeout", "error"].includes(camera.last_test_status) && camera.last_test_error && (
        <Notice tone="danger" title="Last connection test failed">{camera.last_test_error}</Notice>
      )}
      {camera.placement?.review_status === "needs_review" && (
        <Notice tone="warn" title="Map placement needs review">
          The floor-plan image for this floor was replaced after this camera was placed. Open the
          floor-plan editor to confirm the marker is still in the right position.
        </Notice>
      )}

      <div className="detail-grid">
        <div className="stack" style={{ gap: 16 }}>
          <div className="card">
            <div className="card-head"><h2 className="grow">Preview</h2></div>
            <div className="card-pad"><PreviewPanel cameraId={camera.id} /></div>
          </div>

          <div className="card">
            <div className="card-head"><h2 className="grow">Map placement</h2></div>
            <div className="card-pad">
              {plan && camera.placement ? (
                <>
                  <FloorPlanCanvas
                    plan={plan}
                    height={380}
                    readOnly
                    selectedId={camera.id}
                    markers={[{
                      id: camera.id,
                      label: camera.name,
                      norm_x: camera.placement.norm_x,
                      norm_y: camera.placement.norm_y,
                      heading_deg: camera.placement.heading_deg,
                      fov_deg: camera.placement.fov_deg,
                      view_distance_m: camera.placement.view_distance_m,
                      needs_review: camera.placement.review_status === "needs_review",
                    }]}
                  />
                  <div className="row" style={{ marginTop: 10 }}>
                    <span className="small muted">
                      Heading {Math.round(camera.placement.heading_deg)}° · 0° is the top of the plan, clockwise
                      {camera.placement.fov_deg ? ` · ${camera.placement.fov_deg}° field of view` : ""}
                      {camera.placement.view_distance_m ? ` · ~${camera.placement.view_distance_m} m estimated range` : ""}
                    </span>
                    <div className="grow" />
                    <Link className="btn btn-sm" to={`/floor-plans?camera=${camera.id}`}>Open in editor</Link>
                  </div>
                </>
              ) : (
                <div className="stack">
                  <Notice tone="info" title="Not placed on a floor plan">
                    This camera is registered but has no position on a map yet.
                  </Notice>
                  <Link className="btn" to={`/floor-plans?camera=${camera.id}`}>Place on a floor plan</Link>
                </div>
              )}
            </div>
          </div>
        </div>

        <div className="stack" style={{ gap: 16 }}>
          <div className="card">
            <div className="card-head"><h2 className="grow">Connection</h2></div>
            <div className="card-pad">
              <dl className="kv">
                <dt>Address</dt><dd className="mono">{camera.host}</dd>
                <dt>Method</dt>
                <dd>
                  {camera.connection_type === "onvif"
                    ? `ONVIF · port ${camera.onvif_port}`
                    : `Manual RTSP · port ${camera.rtsp_port}`}
                </dd>
                {camera.connection_type === "onvif" ? (
                  <><dt>ONVIF path</dt><dd className="mono">{camera.onvif_path}</dd></>
                ) : (
                  <><dt>Stream path</dt><dd className="mono">{camera.stream_path || "—"}</dd></>
                )}
                <dt>Username</dt><dd>{camera.username || <span className="faint">None</span>}</dd>
                <dt>Password</dt>
                <dd>
                  {camera.has_password
                    ? <span className="badge badge-neutral"><span aria-hidden="true">🔒</span>Stored, encrypted</span>
                    : <span className="faint">None stored</span>}
                </dd>
                <dt>Stream profile</dt>
                <dd>
                  {camera.selected_profile_name
                    ? `${camera.selected_profile_name}${camera.profile_resolution ? ` — ${camera.profile_resolution}` : ""}${camera.profile_encoding ? ` ${camera.profile_encoding}` : ""}`
                    : <span className="faint">Not selected</span>}
                </dd>
                <dt>Snapshot</dt>
                <dd>{camera.snapshot_supported ? "Supported" : "Not confirmed"}</dd>
                <dt>Last result</dt>
                <dd>{camera.last_test_detail ?? <span className="faint">Never tested</span>}</dd>
              </dl>
            </div>
          </div>

          <div className="card">
            <div className="card-head">
              <h2 className="grow">Factory position</h2>
              <Link className="btn btn-sm" to={`/cameras/${camera.id}/calibration`}>Edit</Link>
            </div>
            <div className="card-pad">
              {!calibration ? (
                <p className="small muted" style={{ margin: 0 }}>
                  Not positioned in a metric coordinate system yet. A floor-plan marker records
                  roughly where a camera is on a drawing; a calibration records where it is in
                  metres and where it points.
                </p>
              ) : (
                <dl className="kv">
                  <dt>Method</dt><dd>{METHOD_META[calibration.method].label}</dd>
                  <dt>Position</dt>
                  <dd className="mono">
                    {calibration.position
                      ? `${calibration.position.x.toFixed(2)}, ${calibration.position.y.toFixed(2)}, ${calibration.position.z.toFixed(2)} m`
                      : "No pose (floor-plane calibration only)"}
                  </dd>
                  {calibration.rpy_deg && (
                    <>
                      <dt>Orientation</dt>
                      <dd className="mono">
                        roll {calibration.rpy_deg.roll.toFixed(1)}°,
                        pitch {calibration.rpy_deg.pitch.toFixed(1)}°,
                        yaw {calibration.rpy_deg.yaw.toFixed(1)}°
                      </dd>
                    </>
                  )}
                  <dt>Accuracy</dt>
                  <dd>
                    {calibration.latest_validation?.holdout_ground_error_m != null
                      ? <>
                          {calibration.latest_validation.holdout_ground_error_m.toFixed(3)} m
                          <div className="faint small">
                            against {calibration.latest_validation.holdout_point_count} held-out point(s)
                          </div>
                        </>
                      : <span className="faint">Not independently validated</span>}
                  </dd>
                </dl>
              )}
            </div>
          </div>

          <div className="card">
            <div className="card-head"><h2 className="grow">Installation</h2></div>
            <div className="card-pad">
              <dl className="kv">
                <dt>Site</dt><dd>{camera.location.site ?? "—"}</dd>
                <dt>Building</dt><dd>{camera.location.building ?? "—"}</dd>
                <dt>Floor</dt><dd>{camera.location.floor ?? "—"}</dd>
                <dt>Area</dt><dd>{camera.location.area ?? "—"}</dd>
                <dt>Description</dt>
                <dd>{camera.installation_description || <span className="faint">Not described</span>}</dd>
                <dt>Mounting height</dt>
                <dd>{camera.mounting_height_m ? `${camera.mounting_height_m} m` : <span className="faint">Not set</span>}</dd>
                <dt>Manufacturer</dt><dd>{camera.manufacturer || <span className="faint">Unknown</span>}</dd>
                <dt>Model</dt><dd>{camera.model || <span className="faint">Unknown</span>}</dd>
                <dt>Notes</dt><dd>{camera.notes || <span className="faint">None</span>}</dd>
              </dl>
              {camera.installation_photo_path && (
                <img src={mediaUrl(`/api/cameras/${camera.id}/photo`)} alt="Installation photo"
                     style={{ width: "100%", marginTop: 12, borderRadius: 4, border: "1px solid var(--border)" }} />
              )}
            </div>
          </div>
        </div>
      </div>

      {testResult && (
        <Modal title="Connection test" onClose={() => setTestResult(null)}
               footer={<button className="btn" onClick={() => setTestResult(null)} type="button">Close</button>}>
          <TestResultBody result={testResult} />
        </Modal>
      )}

      {editing && (
        <EditCameraModal
          camera={camera}
          onClose={() => { setEditing(false); setParams({}); }}
          onSaved={async () => { setEditing(false); setParams({}); await load(); }}
          onDeleted={() => navigate("/")}
        />
      )}
    </main>
  );
}

function EditCameraModal({ camera, onClose, onSaved, onDeleted }: {
  camera: Camera; onClose: () => void; onSaved: () => void; onDeleted: () => void;
}) {
  const [form, setForm] = useState({
    name: camera.name,
    host: camera.host,
    connection_type: camera.connection_type,
    onvif_port: camera.onvif_port ?? 80,
    onvif_path: camera.onvif_path ?? "/onvif/device_service",
    rtsp_port: camera.rtsp_port ?? 554,
    stream_path: camera.stream_path ?? "",
    username: camera.username ?? "",
    password: "",
    manufacturer: camera.manufacturer ?? "",
    model: camera.model ?? "",
    notes: camera.notes ?? "",
    installation_description: camera.installation_description ?? "",
    mounting_height_m: camera.mounting_height_m?.toString() ?? "",
  });
  const [showPassword, setShowPassword] = useState(false);
  const [clearPassword, setClearPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const change = (key: keyof typeof form, value: string | number) =>
    setForm((f) => ({ ...f, [key]: value }));

  const submit = async () => {
    setBusy(true); setError(null);
    try {
      await api.updateCamera(camera.id, {
        name: form.name,
        host: form.host,
        connection_type: form.connection_type,
        onvif_port: Number(form.onvif_port),
        onvif_path: form.onvif_path,
        rtsp_port: Number(form.rtsp_port),
        stream_path: form.stream_path || null,
        username: form.username || null,
        // An empty password field keeps the stored one; clearing is explicit.
        ...(form.password ? { password: form.password } : {}),
        ...(clearPassword ? { clear_password: true } : {}),
        manufacturer: form.manufacturer || null,
        model: form.model || null,
        notes: form.notes || null,
        installation_description: form.installation_description || null,
        mounting_height_m: form.mounting_height_m ? Number(form.mounting_height_m) : null,
      });
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The camera could not be saved.");
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    try {
      await api.deleteCamera(camera.id);
      onDeleted();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The camera could not be deleted.");
      setBusy(false);
    }
  };

  return (
    <Modal title={`Edit ${camera.name}`} wide onClose={onClose}
           footer={<>
             <button className="btn btn-danger" type="button" disabled={busy}
                     onClick={() => setConfirmDelete(true)}>Delete</button>
             <div className="grow" />
             <button className="btn" type="button" onClick={onClose} disabled={busy}>Cancel</button>
             <button className="btn btn-primary" type="button" onClick={submit} disabled={busy}>
               {busy ? "Saving…" : "Save changes"}
             </button>
           </>}>
      {error && <Notice tone="danger" title="Save failed">{error}</Notice>}
      {confirmDelete && (
        <Notice tone="danger" title="Delete this camera?">
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn btn-sm btn-danger" type="button" onClick={remove}>Yes, delete</button>
            <button className="btn btn-sm" type="button" onClick={() => setConfirmDelete(false)}>Cancel</button>
          </div>
        </Notice>
      )}

      <div className="field-row">
        <div className="field">
          <label htmlFor="e-name">Camera name</label>
          <input id="e-name" type="text" value={form.name} onChange={(e) => change("name", e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="e-host">IP address or hostname</label>
          <input id="e-host" type="text" className="mono" value={form.host}
                 onChange={(e) => change("host", e.target.value)} />
        </div>
      </div>

      <div className="field">
        <span className="field-label">Connection method</span>
        <div className="radio-row">
          <label className={`radio-card${form.connection_type === "onvif" ? " is-selected" : ""}`}>
            <input type="radio" name="e-conn" checked={form.connection_type === "onvif"}
                   onChange={() => change("connection_type", "onvif")} />
            <span><strong>ONVIF</strong></span>
          </label>
          <label className={`radio-card${form.connection_type === "rtsp" ? " is-selected" : ""}`}>
            <input type="radio" name="e-conn" checked={form.connection_type === "rtsp"}
                   onChange={() => change("connection_type", "rtsp")} />
            <span><strong>Manual RTSP</strong></span>
          </label>
        </div>
      </div>

      {form.connection_type === "onvif" ? (
        <div className="field-row">
          <div className="field">
            <label htmlFor="e-oport">ONVIF port</label>
            <input id="e-oport" type="number" value={form.onvif_port}
                   onChange={(e) => change("onvif_port", e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="e-opath">ONVIF path</label>
            <input id="e-opath" type="text" className="mono" value={form.onvif_path}
                   onChange={(e) => change("onvif_path", e.target.value)} />
          </div>
        </div>
      ) : (
        <div className="field-row">
          <div className="field">
            <label htmlFor="e-rport">RTSP port</label>
            <input id="e-rport" type="number" value={form.rtsp_port}
                   onChange={(e) => change("rtsp_port", e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="e-rpath">Stream path</label>
            <input id="e-rpath" type="text" className="mono" value={form.stream_path}
                   onChange={(e) => change("stream_path", e.target.value)} />
          </div>
        </div>
      )}

      <div className="field-row">
        <div className="field">
          <label htmlFor="e-user">Username</label>
          <input id="e-user" type="text" value={form.username} autoComplete="off"
                 onChange={(e) => change("username", e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="e-pass">Password</label>
          <div className="input-group">
            <input id="e-pass" type={showPassword ? "text" : "password"} value={form.password}
                   autoComplete="new-password" disabled={clearPassword}
                   placeholder={camera.has_password ? "Leave blank to keep the stored password" : "No password stored"}
                   onChange={(e) => change("password", e.target.value)} />
            <button className="btn" type="button" onClick={() => setShowPassword((v) => !v)}
                    aria-pressed={showPassword}>{showPassword ? "Hide" : "Show"}</button>
          </div>
          {camera.has_password && (
            <label className="hint row" style={{ gap: 6, marginTop: 4 }}>
              <input type="checkbox" checked={clearPassword}
                     onChange={(e) => { setClearPassword(e.target.checked); if (e.target.checked) change("password", ""); }} />
              Remove the stored password entirely
            </label>
          )}
          <span className="hint">
            The saved password is never sent back to the browser. Blank means “keep it as it is”.
          </span>
        </div>
      </div>

      <div className="field-row">
        <div className="field">
          <label htmlFor="e-make">Manufacturer</label>
          <input id="e-make" type="text" value={form.manufacturer}
                 onChange={(e) => change("manufacturer", e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="e-model">Model</label>
          <input id="e-model" type="text" value={form.model}
                 onChange={(e) => change("model", e.target.value)} />
        </div>
      </div>

      <div className="field">
        <label htmlFor="e-install">Installation description</label>
        <input id="e-install" type="text" value={form.installation_description}
               onChange={(e) => change("installation_description", e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="e-height">Mounting height (m)</label>
        <input id="e-height" type="number" step="0.1" value={form.mounting_height_m}
               onChange={(e) => change("mounting_height_m", e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="e-notes">Notes</label>
        <textarea id="e-notes" value={form.notes} onChange={(e) => change("notes", e.target.value)} />
      </div>

      <Notice tone="info" title="Changing connection details resets verification">
        If you change the address, port, path, username or password, the camera returns to
        “Not tested” until a new connection test succeeds.
      </Notice>
    </Modal>
  );
}
