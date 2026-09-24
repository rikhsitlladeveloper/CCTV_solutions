import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import CameraImagePicker, { type ImageMark } from "../components/CameraImagePicker";
import SceneCanvas2D, { type SceneLandmark } from "../components/SceneCanvas2D";
import { Guidance, IssueList } from "../components/setupUi";
import { Notice, Spinner } from "../components/ui";
import { ApiError, api } from "../lib/api";
import type { Camera } from "../lib/types";
import type { FactoryMap } from "../lib/calibrationTypes";
import type { FactoryScene } from "../lib/sceneTypes";
import type { FloorPoint, MatchStatus, Workspace } from "../lib/setupTypes";

/**
 * Align live view: the camera picture beside the factory scene, so the same
 * landmark can be pointed at twice.
 *
 * The frame is deliberately frozen rather than live. A moving picture cannot be
 * clicked accurately, and a click has to be recorded against the exact frame it
 * was made on; "Refresh frame" takes a new one when the view has changed.
 *
 * Clicks are stored in the picture's own native pixels, so zooming the browser
 * or resizing the window does not move a landmark.
 */

/** Distinct hues, cycled. Matching by colour and number is faster and less
 *  error-prone than matching by reading point codes twice. */
const LANDMARK_COLOURS = [
  "#1d4ed8", "#b0281f", "#0f7b4f", "#9a6207", "#7c3aed",
  "#0e7490", "#be185d", "#4d7c0f", "#c2410c", "#4338ca",
];
const colourFor = (index: number) => LANDMARK_COLOURS[index % LANDMARK_COLOURS.length];

type Path = "floor" | "full";

interface OverlayData {
  is_approximate: boolean;
  caveats: string[];
  reference_points: Array<{
    code: string;
    role: string;
    marked_pixel: [number, number];
    projected_pixel?: [number, number];
    reprojection_error_px?: number;
    error?: string;
  }>;
  grid: { spacing_m: number; extent_m: number;
          segments: Array<{ axis: string; world_offset: number;
                            pixels: Array<[number, number] | null> }> };
}

export default function AlignPage() {
  const [params, setParams] = useSearchParams();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState<number | null>(
    params.get("workspace") ? Number(params.get("workspace")) : null);
  const [cameraId, setCameraId] = useState<number | null>(
    params.get("camera") ? Number(params.get("camera")) : null);
  const [path, setPath] = useState<Path>("floor");

  const [cameras, setCameras] = useState<Camera[]>([]);
  const [points, setPoints] = useState<FloorPoint[]>([]);
  const [scene, setScene] = useState<FactoryScene | null>(null);
  const [map, setMap] = useState<FactoryMap | null>(null);
  const [status, setStatus] = useState<MatchStatus | null>(null);

  const [frame, setFrame] = useState<{ w: number; h: number } | null>(null);
  const [draft, setDraft] = useState<Record<number, { u: number; v: number }>>({});
  const [undoStack, setUndoStack] = useState<Array<Record<number, { u: number; v: number }>>>([]);
  const [targetId, setTargetId] = useState<number | null>(null);
  const [overlay, setOverlay] = useState<OverlayData | null>(null);
  const [showGrid, setShowGrid] = useState(true);
  const [showResiduals, setShowResiduals] = useState(true);
  const [overlayNote, setOverlayNote] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [said, setSaid] = useState<string | null>(null);
  const [matchesVersion, setMatchesVersion] = useState(0);

  /* ---------- load ---------- */

  useEffect(() => {
    Promise.all([api.listWorkspaces(), api.listCameras()])
      .then(([ws, cams]) => {
        setWorkspaces(ws);
        setCameras(cams);
        setWorkspaceId((current) => current ?? ws[0]?.id ?? null);
        if (!ws.length) setLoading(false);
      })
      .catch((e) => {
        setError(e instanceof ApiError ? e.message : "Could not load.");
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    if (!workspaceId) return;
    setLoading(true);
    Promise.all([
      api.listFloorPoints(workspaceId),
      api.listScenes(workspaceId),
      api.factoryMap(workspaceId).catch(() => null),
    ])
      .then(([pts, scenes, factoryMap]) => {
        setPoints(pts);
        setScene(scenes[0] ?? null);
        setMap(factoryMap);
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : "Could not load the area."))
      .finally(() => setLoading(false));
  }, [workspaceId]);

  const loadMatches = useCallback(async () => {
    if (!cameraId) { setStatus(null); return; }
    try { setStatus(await api.getMatches(cameraId)); }
    catch { setStatus(null); }
  }, [cameraId]);

  useEffect(() => { loadMatches(); }, [loadMatches]);

  // Only a camera with an active mapping has anything to project. A camera
  // still being aligned has none, which is not an error worth shouting about.
  const loadOverlay = useCallback(async () => {
    if (!cameraId) { setOverlay(null); setOverlayNote(null); return; }
    try {
      setOverlay(await api.projectionOverlay(cameraId, 1, 14) as unknown as OverlayData);
      setOverlayNote(null);
    } catch (e) {
      setOverlay(null);
      setOverlayNote(e instanceof ApiError && e.status === 409
        ? "No mapping is active for this camera yet, so there is nothing to draw over the picture."
        : null);
    }
  }, [cameraId]);

  useEffect(() => { loadOverlay(); }, [loadOverlay, matchesVersion]);

  useEffect(() => {
    setParams((p) => {
      if (workspaceId) p.set("workspace", String(workspaceId));
      if (cameraId) p.set("camera", String(cameraId)); else p.delete("camera");
      return p;
    }, { replace: true });
  }, [workspaceId, cameraId, setParams]);

  /* ---------- derived ---------- */

  const camera = cameras.find((c) => c.id === cameraId) ?? null;
  const workspace = workspaces.find((w) => w.id === workspaceId) ?? null;

  // Cameras already in this area, on its floor, or not yet in any area. The
  // last case matters on an upgraded install where cameras predate areas.
  const choosable = useMemo(() => cameras.filter((c) =>
    c.coordinate_system_id === workspaceId
    || (workspace?.floor_id != null && c.location.floor_id === workspace.floor_id)
    || c.coordinate_system_id === null), [cameras, workspaceId, workspace]);

  const matches = useMemo(() => status?.matches ?? [], [status]);

  /** Where the active mapping says each marked landmark ought to appear. The
   *  gap between that and where it was marked is the residual, and it is the
   *  only honest way to see whether the mapping actually fits the picture. */
  const residualByCode = useMemo(() => {
    const out = new Map<string, { projected: [number, number]; errorPx: number }>();
    if (!showResiduals) return out;
    overlay?.reference_points?.forEach((rp) => {
      if (rp.projected_pixel && rp.reprojection_error_px != null) {
        out.set(rp.code, { projected: rp.projected_pixel, errorPx: rp.reprojection_error_px });
      }
    });
    return out;
  }, [overlay, showResiduals]);

  const overlaySegments = useMemo(() => {
    if (!showGrid) return [];
    return (overlay?.grid?.segments ?? []).map((seg) => seg.pixels);
  }, [overlay, showGrid]);

  const worstResidual = useMemo(() => {
    let worst = 0;
    residualByCode.forEach((r) => { worst = Math.max(worst, r.errorPx); });
    return worst;
  }, [residualByCode]);

  const matchedByPoint = useMemo(
    () => new Map(matches.map((m) => [m.reference_point_id, m])), [matches]);

  /** One stable number and colour per point, so both panes agree. */
  const ordered = useMemo(
    () => [...points].sort((a, b) => a.code.localeCompare(b.code, undefined, { numeric: true })),
    [points]);
  const indexOf = useMemo(() => {
    const out = new Map<number, number>();
    ordered.forEach((p, i) => out.set(p.id, i + 1));
    return out;
  }, [ordered]);

  const landmarks: SceneLandmark[] = useMemo(() => ordered.map((p, i) => ({
    id: p.id, x: p.x, y: p.y,
    index: i + 1,
    colour: colourFor(i),
    code: p.code,
    matched: matchedByPoint.has(p.id) || p.id in draft,
    holdout: p.role === "validation",
  })), [ordered, matchedByPoint, draft]);

  const marks: ImageMark[] = useMemo(() => {
    const out: ImageMark[] = [];
    matches.forEach((m) => {
      const moved = draft[m.reference_point_id];
      const i = (indexOf.get(m.reference_point_id) ?? 1) - 1;
      const residual = moved ? undefined : residualByCode.get(m.code);
      out.push({
        id: m.reference_point_id,
        u: moved?.u ?? m.pixel_u, v: moved?.v ?? m.pixel_v,
        label: `${m.code}${moved ? " (moved)" : ""}`,
        index: i + 1, colour: colourFor(i),
        role: m.role === "validation" ? "holdout" : "fit",
        projected: residual?.projected ?? null,
        errorPx: residual?.errorPx ?? null,
      });
    });
    Object.entries(draft).forEach(([id, pos]) => {
      const pid = Number(id);
      if (matchedByPoint.has(pid)) return;
      const point = ordered.find((p) => p.id === pid);
      const i = (indexOf.get(pid) ?? 1) - 1;
      out.push({
        id: pid, u: pos.u, v: pos.v,
        label: `${point?.code ?? pid} (unsaved)`,
        index: i + 1, colour: colourFor(i),
        role: point?.role === "validation" ? "holdout" : "fit",
      });
    });
    return out;
  }, [matches, draft, matchedByPoint, indexOf, ordered, residualByCode]);

  const pending = Object.keys(draft).length;
  const targetPoint = ordered.find((p) => p.id === targetId) ?? null;

  /* ---------- actions ---------- */

  const pickInPicture = (u: number, v: number) => {
    if (targetId == null) return;
    setUndoStack((st) => [...st, draft]);
    setDraft((d) => ({ ...d, [targetId]: { u, v } }));
    setTargetId(null);
  };

  const save = async () => {
    if (!cameraId || !frame) {
      setError("Load a frame from the camera first, so the clicks have a known image size.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (camera && camera.coordinate_system_id !== workspaceId && workspaceId) {
        await api.assignCameraToWorkspace(workspaceId, cameraId);
        setCameras(await api.listCameras());
      }
      // A draft entry for an already-matched point is a move, so it wins.
      const merged = new Map<number, { pixel_u: number; pixel_v: number }>();
      matches.forEach((m) => merged.set(m.reference_point_id,
                                        { pixel_u: m.pixel_u, pixel_v: m.pixel_v }));
      Object.entries(draft).forEach(([id, p]) =>
        merged.set(Number(id), { pixel_u: p.u, pixel_v: p.v }));
      setStatus(await api.saveMatches(cameraId, {
        image_width: frame.w, image_height: frame.h, replace_existing: true,
        matches: [...merged.entries()].map(([reference_point_id, pixel]) =>
          ({ reference_point_id, ...pixel })),
      }));
      setDraft({});
      setUndoStack([]);
      setMatchesVersion((v) => v + 1);
      setSaid("Landmarks saved.");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not save the landmarks.");
    } finally { setBusy(false); }
  };

  const removeLandmark = async (pointId: number) => {
    const existing = matchedByPoint.get(pointId);
    setUndoStack((st) => [...st, draft]);
    setDraft((d) => { const next = { ...d }; delete next[pointId]; return next; });
    if (!existing || !cameraId) return;
    try {
      await api.deleteMatch(cameraId, existing.observation_id);
      await loadMatches();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not remove that landmark.");
    }
  };

  const undo = () => {
    setUndoStack((st) => {
      if (!st.length) return st;
      setDraft(st[st.length - 1]);
      return st.slice(0, -1);
    });
  };

  /* ---------- render ---------- */

  if (loading && !scene && !points.length) return <Spinner label="Loading the area…" />;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Align live view</h1>
          <p className="sub">
            Point at the same landmark twice — once in the picture, once in the scene.
          </p>
        </div>
        <select value={workspaceId ?? ""} aria-label="Area"
                onChange={(e) => {
                  setWorkspaceId(Number(e.target.value));
                  setCameraId(null); setDraft({}); setUndoStack([]);
                }}>
          {workspaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
        </select>
        <select value={cameraId ?? ""} aria-label="Camera"
                onChange={(e) => {
                  setCameraId(e.target.value ? Number(e.target.value) : null);
                  setDraft({}); setUndoStack([]); setTargetId(null); setFrame(null);
                }}>
          <option value="">Choose a camera…</option>
          {choosable.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
      </div>

      {error && <Notice tone="danger" title="Something went wrong">{error}</Notice>}
      {said && <Notice tone="ok" title={said} />}

      <PathChooser path={path} onChange={setPath} cameraId={cameraId} />

      {path === "full" ? (
        <FullAlignmentPanel camera={camera} matchCount={matches.length} />
      ) : !camera ? (
        <Notice tone="info" title="Choose a camera">
          {choosable.length === 0
            ? "No camera is in this area or waiting to be assigned. Register one first."
            : "Pick a camera above to start aligning it."}
        </Notice>
      ) : points.length === 0 ? (
        <Notice tone="warn" title="No measured floor points in this area">
          Aligning needs points whose real position on the floor you have measured.{" "}
          <Link to={`/setup?step=2&workspace=${workspaceId}`}>Add measured floor points</Link>,
          then come back.
        </Notice>
      ) : (
        <>
          <Guidance>
            Pick a numbered landmark on the right, then click exactly where it appears in the
            picture on the left. The number and colour are the same on both sides. Four is the
            minimum; six or more, spread across the whole frame rather than clustered in one
            corner, give a far better mapping.
          </Guidance>

          <div className="card" style={{ marginBottom: 14 }}>
            <div className="toolbar">
              <span className="small muted">
                {matches.length} saved · {pending} unsaved
                {status && ` · ${status.calibration_matched} for the mapping,`}
                {status && ` ${status.validation_matched} held back for checking`}
              </span>
              <div className="grow" />
              {undoStack.length > 0 && (
                <button className="btn btn-sm" type="button" onClick={undo}>Undo</button>
              )}
              {pending > 0 && (
                <button className="btn btn-sm" type="button"
                        onClick={() => { setDraft({}); setUndoStack([]); setTargetId(null); }}>
                  Discard unsaved
                </button>
              )}
              <button className="btn btn-sm btn-primary" type="button"
                      disabled={busy || pending === 0} onClick={save}>
                {pending > 0 ? `Save ${pending} change${pending > 1 ? "s" : ""}` : "Saved"}
              </button>
            </div>
          </div>

          <div className="align-split">
            <section>
              <div className="row" style={{ alignItems: "baseline", marginBottom: 8 }}>
                <h3 className="split-title grow" style={{ margin: 0 }}>What the camera sees</h3>
                {overlay && (
                  <>
                    <label className="small check">
                      <input type="checkbox" checked={showGrid}
                             onChange={(e) => setShowGrid(e.target.checked)} />
                      Floor grid
                    </label>
                    <label className="small check">
                      <input type="checkbox" checked={showResiduals}
                             onChange={(e) => setShowResiduals(e.target.checked)} />
                      Residuals
                    </label>
                  </>
                )}
              </div>
              <CameraImagePicker
                cameraId={camera.id}
                marks={marks}
                selectedId={targetId}
                height={520}
                overlaySegments={overlaySegments}
                onImageLoaded={(w, h) => setFrame({ w, h })}
                onPick={targetId != null ? pickInPicture : undefined}
                hint={targetPoint
                  ? `Click landmark ${indexOf.get(targetPoint.id)} — ${targetPoint.code} — in the picture.`
                  : "Pick a landmark in the scene first."}
              />
              {overlay ? (
                <p className="small muted" style={{ marginTop: 8 }}>
                  The thin grid is one metre of real floor projected through the mapping that is
                  live now. Where it lies along the floor's own lines, the mapping fits; where it
                  drifts, it does not. Red spurs show each landmark's residual
                  {worstResidual > 0 && <> — worst {worstResidual.toFixed(1)} px</>}.
                  {overlay.is_approximate && " This mapping is approximate, not surveyed."}
                </p>
              ) : overlayNote ? (
                <p className="small muted" style={{ marginTop: 8 }}>{overlayNote}</p>
              ) : null}
              {status && <IssueList issues={status.issues} />}
            </section>

            <section>
              <h3 className="split-title">Where it is in the factory</h3>
              {scene ? (
                <div className="card" style={{ padding: 0, overflow: "hidden" }}>
                  <SceneCanvas2D
                    scene={scene}
                    // Only the camera being aligned: other cameras' coverage
                    // shading and labels sit right on top of the landmarks.
                    cameras={(map?.cameras ?? []).filter((c) => c.camera_id === cameraId)}
                    selectedObjectId={null}
                    selectedCameraId={cameraId}
                    tool="select"
                    onSelectObject={() => undefined}
                    onSelectCamera={() => undefined}
                    landmarks={landmarks}
                    selectedLandmarkId={targetId}
                    onSelectLandmark={setTargetId}
                    height={520}
                    readOnly
                  />
                </div>
              ) : (
                <Notice tone="info" title="No scene drawn for this area yet">
                  The list below still works — <Link to={`/workspace?workspace=${workspaceId}`}>
                  draw the area</Link> to pick landmarks off a plan instead.
                </Notice>
              )}

              <LandmarkList
                points={ordered} indexOf={indexOf} draft={draft}
                matchedByPoint={matchedByPoint} targetId={targetId}
                onTarget={setTargetId} onRemove={removeLandmark}
              />
            </section>
          </div>

          <NextStep status={status} cameraId={camera.id} workspaceId={workspaceId} />
        </>
      )}
    </>
  );
}

/* ---------------- the two paths ---------------- */

function PathChooser({ path, onChange, cameraId }: {
  path: Path; onChange: (p: Path) => void; cameraId: number | null;
}) {
  void cameraId;
  return (
    <div className="path-cards">
      <button type="button" className={`path-card${path === "floor" ? " is-active" : ""}`}
              onClick={() => onChange("floor")}>
        <strong>Floor mapping</strong>
        <span className="small muted">
          Turns a position in the picture into a place on the floor. Needs four or more
          landmarks that all lie flat on the same floor. This is what counting, zones and
          twin positions use.
        </span>
      </button>
      <button type="button" className={`path-card${path === "full" ? " is-active" : ""}`}
              onClick={() => onChange("full")}>
        <strong>Full camera alignment</strong>
        <span className="small muted">
          Works out where the camera hangs and how it is angled, in three dimensions. Needs a
          lens calibration as well, and landmarks at more than one height.
        </span>
      </button>
    </div>
  );
}

function FullAlignmentPanel({ camera, matchCount }: {
  camera: Camera | null; matchCount: number;
}) {
  if (!camera) {
    return <Notice tone="info" title="Choose a camera">
      Pick a camera above to see what its full alignment needs.
    </Notice>;
  }
  return (
    <div className="card card-pad">
      <h3 style={{ marginTop: 0 }}>Full camera alignment for {camera.name}</h3>
      <p className="small">
        Full alignment recovers the camera's own position and angles, which is what the 3D view,
        camera relationships and cross-camera tracking need. Floor mapping alone cannot give
        that: many different camera positions produce the very same flat mapping.
      </p>
      <ul className="small" style={{ lineHeight: 1.7 }}>
        <li>
          <strong>A lens calibration</strong> — the focal length and distortion of this camera.
          Without it there is nothing to solve the angles against.
        </li>
        <li>
          <strong>Landmarks at more than one height</strong> — points that all lie flat on the
          floor leave the solution ambiguous. Mark tops of racks, door frames or column
          brackets as well.
        </li>
        <li>
          <strong>Six or more landmarks</strong> — you currently have {matchCount} marked for
          this camera. The same landmarks serve both paths, so nothing is marked twice.
        </li>
      </ul>
      <p className="hint">
        The full solver, its conventions and the pose it produces live on the camera's own
        calibration page, alongside the lens calibration it depends on.
      </p>
      <Link className="btn btn-primary" to={`/cameras/${camera.id}/calibration`}>
        Open full alignment for {camera.name}
      </Link>
    </div>
  );
}

/* ---------------- landmark list ---------------- */

function LandmarkList({ points, indexOf, draft, matchedByPoint, targetId, onTarget, onRemove }: {
  points: FloorPoint[];
  indexOf: Map<number, number>;
  draft: Record<number, { u: number; v: number }>;
  matchedByPoint: Map<number, { observation_id: number; pixel_u: number; pixel_v: number }>;
  targetId: number | null;
  onTarget: (id: number) => void;
  onRemove: (id: number) => void;
}) {
  return (
    <div className="card card-pad" style={{ marginTop: 12 }}>
      <strong>Landmarks ({points.length})</strong>
      <p className="hint" style={{ marginTop: 4 }}>
        A camera only needs the ones it can actually see.
      </p>
      <div className="chip-list" style={{ maxHeight: 260, marginTop: 8 }}>
        {points.map((p) => {
          const i = (indexOf.get(p.id) ?? 1) - 1;
          const saved = matchedByPoint.get(p.id);
          const moved = draft[p.id];
          const done = !!saved || !!moved;
          return (
            <div key={p.id}
                 className={`point-row${done ? " is-matched" : ""}${targetId === p.id ? " is-target" : ""}${p.role === "validation" ? " is-validation" : ""}`}>
              <span className="landmark-dot"
                    style={{ background: done ? colourFor(i) : "#fff",
                             borderColor: colourFor(i),
                             color: done ? "#fff" : colourFor(i) }}>
                {i + 1}
              </span>
              <button type="button" className="grow landmark-pick"
                      onClick={() => onTarget(p.id)}>
                <strong>{p.code}</strong> {p.name}
              </button>
              <span className="mono small faint">
                {moved ? "unsaved" : saved ? "marked" : `${p.x.toFixed(1)}, ${p.y.toFixed(1)}`}
              </span>
              {done && (
                <button className="btn btn-sm btn-ghost" type="button" title="Remove this landmark"
                        onClick={() => onRemove(p.id)}>×</button>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ---------------- what to do next ---------------- */

function NextStep({ status, cameraId, workspaceId }: {
  status: MatchStatus | null; cameraId: number; workspaceId: number | null;
}) {
  void cameraId;
  if (!status) return null;
  if (!status.ready_to_calculate) {
    return (
      <Notice tone="info" title="Not enough to calculate a mapping yet">
        Four landmarks lying flat on the floor is the minimum, and any error above has to be
        cleared first.
      </Notice>
    );
  }
  return (
    <Notice tone="ok" title="Ready to calculate the mapping">
      <p style={{ margin: "4px 0 10px" }}>
        {status.calibration_matched} landmark{status.calibration_matched === 1 ? "" : "s"} will be
        fitted{status.validation_matched > 0
          ? `, and ${status.validation_matched} held back to check the result honestly`
          : ". Marking a point as a checking point holds it back from the fit, which is the only way to know whether the mapping really works"}.
      </p>
      <Link className="btn btn-primary btn-sm" to={`/setup?step=4&workspace=${workspaceId}`}>
        Calculate the mapping
      </Link>
    </Notice>
  );
}
