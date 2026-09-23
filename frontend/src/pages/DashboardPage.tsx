import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError, api } from "../lib/api";
import type { Camera, CameraSummary, Site, TestResult } from "../lib/types";
import PreviewPanel from "../components/PreviewPanel";
import { LastChecked, Modal, Notice, PlacementBadge, Spinner, StatusBadge, locationSummary } from "../components/ui";

type StatusFilter = "" | "online" | "failed" | "untested" | "partial";
type PlacementFilter = "" | "placed" | "unplaced" | "needs_review";

export default function DashboardPage() {
  const navigate = useNavigate();
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [summary, setSummary] = useState<CameraSummary | null>(null);
  const [sites, setSites] = useState<Site[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [q, setQ] = useState("");
  const [siteId, setSiteId] = useState<string>("");
  const [buildingId, setBuildingId] = useState<string>("");
  const [floorId, setFloorId] = useState<string>("");
  const [status, setStatus] = useState<StatusFilter>("");
  const [placement, setPlacement] = useState<PlacementFilter>("");

  const [testing, setTesting] = useState<number | null>(null);
  const [testResult, setTestResult] = useState<{ camera: Camera; result: TestResult } | null>(null);
  const [previewCamera, setPreviewCamera] = useState<Camera | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Camera | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [list, sum] = await Promise.all([
        api.listCameras({
          q: q || undefined,
          site_id: siteId || undefined,
          building_id: buildingId || undefined,
          floor_id: floorId || undefined,
          status: status || undefined,
          placement: placement || undefined,
        }),
        api.summary(),
      ]);
      setCameras(list);
      setSummary(sum);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not load cameras.");
    } finally {
      setLoading(false);
    }
  }, [q, siteId, buildingId, floorId, status, placement]);

  useEffect(() => { api.tree().then(setSites).catch(() => setSites([])); }, []);
  useEffect(() => {
    const timer = setTimeout(load, q ? 220 : 0);
    return () => clearTimeout(timer);
  }, [load, q]);

  const buildings = useMemo(
    () => sites.filter((s) => !siteId || String(s.id) === siteId).flatMap((s) => s.buildings),
    [sites, siteId],
  );
  const floors = useMemo(
    () => buildings.filter((b) => !buildingId || String(b.id) === buildingId).flatMap((b) => b.floors),
    [buildings, buildingId],
  );

  const runTest = async (camera: Camera) => {
    setTesting(camera.id);
    try {
      const result = await api.testCamera(camera.id);
      setTestResult({ camera, result });
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The connection test could not be run.");
    } finally {
      setTesting(null);
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    try {
      await api.deleteCamera(deleteTarget.id);
      setDeleteTarget(null);
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The camera could not be deleted.");
    }
  };

  const toggleStatus = (next: StatusFilter) => setStatus((cur) => (cur === next ? "" : next));
  const togglePlacement = (next: PlacementFilter) => setPlacement((cur) => (cur === next ? "" : next));

  const hasFilters = Boolean(q || siteId || buildingId || floorId || status || placement);
  const noCamerasAtAll = !loading && summary?.total === 0;

  return (
    <main className="page">
      <div className="page-head">
        <div className="grow">
          <h1>Camera registration</h1>
          <p className="page-sub">
            Register cameras, verify they answer, and place them on a floor plan.
          </p>
        </div>
        <Link className="btn btn-primary" to="/cameras/new">＋ Register camera</Link>
      </div>

      {error && <Notice tone="danger" title="Something went wrong">{error}</Notice>}

      <div className="stat-grid">
        <button className={`stat stat-accent${status === "" && placement === "" ? " is-active" : ""}`}
                onClick={() => { setStatus(""); setPlacement(""); }} type="button">
          <div className="stat-label">Registered cameras</div>
          <div className="stat-value">{summary?.total ?? "–"}</div>
          <div className="stat-note">All cameras in the system</div>
        </button>
        <button className={`stat stat-ok${status === "online" ? " is-active" : ""}`}
                onClick={() => toggleStatus("online")} type="button">
          <div className="stat-label">Reachable</div>
          <div className="stat-value">{summary?.reachable ?? "–"}</div>
          <div className="stat-note">Verified by a real connection test</div>
        </button>
        <button className={`stat stat-danger${status === "failed" ? " is-active" : ""}`}
                onClick={() => toggleStatus("failed")} type="button">
          <div className="stat-label">Failed connections</div>
          <div className="stat-value">{summary?.failed ?? "–"}</div>
          <div className="stat-note">
            {summary?.untested ? `${summary.untested} more not tested yet` : "Last test did not succeed"}
          </div>
        </button>
        <button className={`stat stat-warn${placement === "unplaced" ? " is-active" : ""}`}
                onClick={() => togglePlacement("unplaced")} type="button">
          <div className="stat-label">Awaiting map placement</div>
          <div className="stat-value">{summary?.awaiting_placement ?? "–"}</div>
          <div className="stat-note">
            {summary?.placements_need_review
              ? `${summary.placements_need_review} placement(s) need review`
              : "Not yet positioned on a floor plan"}
          </div>
        </button>
      </div>

      <div className="card">
        <div className="toolbar">
          <input className="search" type="search" placeholder="Search name, address, manufacturer…"
                 value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search cameras" />
          <select value={siteId} aria-label="Filter by site"
                  onChange={(e) => { setSiteId(e.target.value); setBuildingId(""); setFloorId(""); }}>
            <option value="">All sites</option>
            {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
          <select value={buildingId} aria-label="Filter by building"
                  onChange={(e) => { setBuildingId(e.target.value); setFloorId(""); }}>
            <option value="">All buildings</option>
            {buildings.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
          </select>
          <select value={floorId} aria-label="Filter by floor" onChange={(e) => setFloorId(e.target.value)}>
            <option value="">All floors</option>
            {floors.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
          </select>
          <select value={status} aria-label="Filter by connection status"
                  onChange={(e) => setStatus(e.target.value as StatusFilter)}>
            <option value="">Any status</option>
            <option value="online">Online</option>
            <option value="partial">Login only</option>
            <option value="failed">Failed</option>
            <option value="untested">Not tested</option>
          </select>
          <select value={placement} aria-label="Filter by placement"
                  onChange={(e) => setPlacement(e.target.value as PlacementFilter)}>
            <option value="">Any placement</option>
            <option value="placed">Placed</option>
            <option value="unplaced">Not placed</option>
            <option value="needs_review">Needs review</option>
          </select>
          {hasFilters && (
            <button className="btn btn-ghost btn-sm" type="button"
                    onClick={() => { setQ(""); setSiteId(""); setBuildingId(""); setFloorId(""); setStatus(""); setPlacement(""); }}>
              Clear filters
            </button>
          )}
        </div>

        {loading ? (
          <div className="card-pad"><Spinner label="Loading cameras…" /></div>
        ) : noCamerasAtAll ? (
          <div className="empty">
            <div className="empty-mark" aria-hidden="true">＋</div>
            <h2>No cameras registered yet</h2>
            <p>
              Register your first camera to get started. You will enter its address and
              credentials, test the connection, record where it is installed, and optionally
              place it on a floor plan. A floor plan is not required — you can place cameras later.
            </p>
            <Link className="btn btn-primary" to="/cameras/new">Register the first camera</Link>
          </div>
        ) : cameras.length === 0 ? (
          <div className="empty">
            <div className="empty-mark" aria-hidden="true">⌕</div>
            <h2>No cameras match these filters</h2>
            <p>Try a different search term, or clear the filters to see all registered cameras.</p>
            <button className="btn" type="button"
                    onClick={() => { setQ(""); setSiteId(""); setBuildingId(""); setFloorId(""); setStatus(""); setPlacement(""); }}>
              Clear filters
            </button>
          </div>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Camera</th>
                  <th>Address</th>
                  <th>Building</th>
                  <th>Floor</th>
                  <th>Area</th>
                  <th>Connection</th>
                  <th>Placement</th>
                  <th>Last check</th>
                  <th style={{ textAlign: "right" }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {cameras.map((camera) => (
                  <tr key={camera.id}>
                    <td>
                      <Link className="cell-name" to={`/cameras/${camera.id}`}>{camera.name}</Link>
                      <div className="cell-sub">
                        {[camera.manufacturer, camera.model].filter(Boolean).join(" ") || "—"}
                      </div>
                    </td>
                    <td>
                      <span className="mono">{camera.host}</span>
                      <div className="cell-sub">
                        {camera.connection_type === "onvif"
                          ? `ONVIF :${camera.onvif_port ?? 80}`
                          : `RTSP :${camera.rtsp_port ?? 554}`}
                      </div>
                    </td>
                    <td>{camera.location.building ?? <span className="faint">—</span>}</td>
                    <td>{camera.location.floor ?? <span className="faint">—</span>}</td>
                    <td>{camera.location.area ?? <span className="faint">—</span>}</td>
                    <td><StatusBadge status={camera.last_test_status} /></td>
                    <td><PlacementBadge camera={camera} /></td>
                    <td><LastChecked camera={camera} /></td>
                    <td>
                      <div className="row-actions">
                        <button className="btn btn-sm" type="button" disabled={testing === camera.id}
                                onClick={() => runTest(camera)}>
                          {testing === camera.id ? "Testing…" : "Test"}
                        </button>
                        <button className="btn btn-sm" type="button" onClick={() => setPreviewCamera(camera)}>
                          Preview
                        </button>
                        <button className="btn btn-sm" type="button"
                                onClick={() => navigate(`/floor-plans?camera=${camera.id}`)}>
                          {camera.placement ? "Map" : "Place"}
                        </button>
                        <button className="btn btn-sm" type="button"
                                onClick={() => navigate(`/cameras/${camera.id}?edit=1`)}>
                          Edit
                        </button>
                        <button className="btn btn-sm btn-danger" type="button"
                                onClick={() => setDeleteTarget(camera)}>
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

      {testResult && (
        <Modal title={`Connection test — ${testResult.camera.name}`} onClose={() => setTestResult(null)}
               footer={<button className="btn" onClick={() => setTestResult(null)} type="button">Close</button>}>
          <TestResultBody result={testResult.result} />
        </Modal>
      )}

      {previewCamera && (
        <Modal title={`Preview — ${previewCamera.name}`} wide onClose={() => setPreviewCamera(null)}
               footer={<button className="btn" onClick={() => setPreviewCamera(null)} type="button">Close</button>}>
          <p className="small muted" style={{ marginTop: 0 }}>{locationSummary(previewCamera)}</p>
          <PreviewPanel cameraId={previewCamera.id} />
        </Modal>
      )}

      {deleteTarget && (
        <Modal title="Delete camera" onClose={() => setDeleteTarget(null)}
               footer={<>
                 <button className="btn" onClick={() => setDeleteTarget(null)} type="button">Cancel</button>
                 <button className="btn btn-danger" onClick={confirmDelete} type="button">Delete camera</button>
               </>}>
          <p style={{ marginTop: 0 }}>
            Delete <strong>{deleteTarget.name}</strong> ({deleteTarget.host})? Its stored credentials,
            map placement and installation notes are removed. This cannot be undone.
          </p>
        </Modal>
      )}
    </main>
  );
}

export function TestResultBody({ result }: { result: TestResult }) {
  return (
    <>
      <div className="row" style={{ marginBottom: 12 }}>
        <StatusBadge status={result.status} />
        <span className="small muted">
          Tested {new Date(result.tested_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}
        </span>
      </div>
      <Notice tone={result.ok ? "ok" : result.login_ok ? "warn" : "danger"} title={result.summary}>
        {result.error ?? (result.ok ? "The camera answered and delivered video." : undefined)}
      </Notice>
      <dl className="kv">
        <dt>Control channel</dt>
        <dd>{result.login_ok ? "Login succeeded" : "Not established"}</dd>
        <dt>Video stream</dt>
        <dd>{result.stream_ok ? "A live frame was decoded" : "Not verified"}</dd>
        <dt>Snapshot</dt>
        <dd>{result.snapshot_supported ? "Available" : "Not available"}</dd>
        {result.manufacturer && (<><dt>Manufacturer</dt><dd>{result.manufacturer}</dd></>)}
        {result.model && (<><dt>Model</dt><dd>{result.model}</dd></>)}
        {result.firmware && (<><dt>Firmware</dt><dd>{result.firmware}</dd></>)}
      </dl>
      {result.profiles.length > 0 && (
        <>
          <div className="section-title">Stream profiles</div>
          <ul className="stack" style={{ margin: 0, paddingLeft: 18 }}>
            {result.profiles.map((p) => (
              <li key={p.token}>
                {p.name} — {p.resolution ?? "resolution unknown"} {p.encoding ?? ""}
                {p.fps ? ` @ ${p.fps} fps` : ""}
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}
