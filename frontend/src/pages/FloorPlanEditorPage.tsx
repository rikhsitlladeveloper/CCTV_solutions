import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError, api } from "../lib/api";
import FloorPlanCanvas, { type Marker } from "../components/FloorPlanCanvas";
import { Modal, Notice, Spinner, StatusBadge } from "../components/ui";
import type { Camera, FloorPlan, Site } from "../lib/types";

export default function FloorPlanEditorPage() {
  const [params] = useSearchParams();
  const focusCameraId = params.get("camera") ? Number(params.get("camera")) : null;

  const [sites, setSites] = useState<Site[]>([]);
  const [buildingId, setBuildingId] = useState<number | null>(null);
  const [floorId, setFloorId] = useState<number | null>(null);
  const [plan, setPlan] = useState<FloorPlan | null>(null);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(focusCameraId);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [dirty, setDirty] = useState<Record<number, Marker>>({});
  const [placingId, setPlacingId] = useState<number | null>(null);
  const [scaleMode, setScaleMode] = useState(false);
  const [scalePoints, setScalePoints] = useState<{ x: number; y: number }[]>([]);
  const [scaleDistance, setScaleDistance] = useState("");
  const [replaceWarning, setReplaceWarning] = useState<File | null>(null);

  const buildings = useMemo(() => sites.flatMap((s) =>
    s.buildings.map((b) => ({ ...b, siteName: s.name }))), [sites]);
  const floors = useMemo(
    () => buildings.find((b) => b.id === buildingId)?.floors ?? [],
    [buildings, buildingId],
  );

  /* Initial load: focus the floor of the camera we were sent to place. */
  useEffect(() => {
    (async () => {
      try {
        const [tree, allCameras] = await Promise.all([api.tree(), api.listCameras()]);
        setSites(tree);
        setCameras(allCameras);

        const focus = focusCameraId ? allCameras.find((c) => c.id === focusCameraId) : undefined;
        const targetFloorId = focus?.location.floor_id
          ?? tree.flatMap((s) => s.buildings).flatMap((b) => b.floors).find((f) => f.has_floor_plan)?.id
          ?? null;
        const owningBuilding = tree.flatMap((s) => s.buildings)
          .find((b) => b.floors.some((f) => f.id === targetFloorId));
        setBuildingId(owningBuilding?.id ?? tree[0]?.buildings[0]?.id ?? null);
        setFloorId(targetFloorId);
      } catch (e) {
        setError(e instanceof ApiError ? e.message : "Could not load floor plans.");
      } finally {
        setLoading(false);
      }
    })();
  }, [focusCameraId]);

  const loadPlan = useCallback(async () => {
    if (!floorId) { setPlan(null); return; }
    try {
      const plans = await api.listFloorPlans(floorId);
      setPlan(plans[0] ?? null);
    } catch {
      setPlan(null);
    }
  }, [floorId]);

  useEffect(() => { loadPlan(); }, [loadPlan]);

  const refreshCameras = useCallback(async () => {
    setCameras(await api.listCameras());
  }, []);

  /* Cameras belonging to the selected floor, split by placement state. */
  const floorCameras = useMemo(
    () => cameras.filter((c) => c.location.floor_id === floorId),
    [cameras, floorId],
  );
  const placedHere = useMemo(
    () => cameras.filter((c) => c.placement && plan && c.placement.floor_plan_id === plan.id),
    [cameras, plan],
  );
  const unplaced = useMemo(
    () => floorCameras.filter((c) => !c.placement),
    [floorCameras],
  );

  const markers: Marker[] = useMemo(() => placedHere.map((c) => {
    const local = dirty[c.id];
    return local ?? {
      id: c.id,
      label: c.name,
      norm_x: c.placement!.norm_x,
      norm_y: c.placement!.norm_y,
      heading_deg: c.placement!.heading_deg,
      fov_deg: c.placement!.fov_deg,
      view_distance_m: c.placement!.view_distance_m,
      needs_review: c.placement!.review_status === "needs_review",
    };
  }), [placedHere, dirty]);

  const selected = cameras.find((c) => c.id === selectedId) ?? null;
  const selectedMarker = markers.find((m) => m.id === selectedId) ?? null;
  const hasUnsaved = Object.keys(dirty).length > 0;

  const patchMarker = (id: number, patch: Partial<Marker>) => {
    setDirty((d) => {
      const base = d[id] ?? markers.find((m) => m.id === id);
      if (!base) return d;
      return { ...d, [id]: { ...base, ...patch } };
    });
  };

  const saveMarker = async (id: number) => {
    const marker = dirty[id];
    if (!marker || !plan) return;
    setError(null);
    try {
      await api.savePlacement(id, {
        floor_plan_id: plan.id,
        norm_x: marker.norm_x,
        norm_y: marker.norm_y,
        heading_deg: marker.heading_deg,
        fov_deg: marker.fov_deg,
        view_distance_m: marker.view_distance_m,
        review_status: "confirmed",
      });
      setDirty((d) => { const next = { ...d }; delete next[id]; return next; });
      await refreshCameras();
      setStatus("Placement saved.");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The placement could not be saved.");
    }
  };

  const saveAll = async () => {
    for (const id of Object.keys(dirty).map(Number)) await saveMarker(id);
  };

  const placeCamera = async (cameraId: number, x: number, y: number) => {
    if (!plan) return;
    try {
      await api.savePlacement(cameraId, {
        floor_plan_id: plan.id, norm_x: x, norm_y: y, heading_deg: 0,
        fov_deg: 90, view_distance_m: null, review_status: "confirmed",
      });
      setPlacingId(null);
      setSelectedId(cameraId);
      await refreshCameras();
      setStatus("Camera placed. Drag the marker to adjust, and use the handle to set direction.");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The camera could not be placed.");
    }
  };

  const removePlacement = async (cameraId: number) => {
    try {
      await api.removePlacement(cameraId);
      setDirty((d) => { const next = { ...d }; delete next[cameraId]; return next; });
      await refreshCameras();
      setStatus("Placement removed.");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The placement could not be removed.");
    }
  };

  const doUpload = async (file: File) => {
    if (!floorId) return;
    setError(null);
    try {
      const uploaded = await api.uploadFloorPlan(floorId, file);
      setPlan(uploaded);
      setReplaceWarning(null);
      await refreshCameras();
      setStatus(uploaded.version > 1
        ? "Floor plan replaced. Existing placements are flagged for review — confirm each marker."
        : "Floor plan uploaded.");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The floor plan could not be uploaded.");
    }
  };

  const onPickPlan = (file: File) => {
    if (plan && plan.placement_count > 0) setReplaceWarning(file);
    else doUpload(file);
  };

  const applyScale = async () => {
    if (!plan || scalePoints.length !== 2 || !scaleDistance) return;
    try {
      const updated = await api.setScale(plan.id, {
        point_a_x: scalePoints[0].x, point_a_y: scalePoints[0].y,
        point_b_x: scalePoints[1].x, point_b_y: scalePoints[1].y,
        distance_m: Number(scaleDistance),
      });
      setPlan(updated);
      setScaleMode(false); setScalePoints([]); setScaleDistance("");
      setStatus("Map scale set. Viewing distances are now drawn to scale.");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The scale could not be set.");
    }
  };

  if (loading) return <main className="page"><Spinner label="Loading floor plans…" /></main>;

  return (
    <main className="page page-wide">
      <div className="page-head">
        <div className="grow">
          <h1>Floor plans</h1>
          <p className="page-sub">
            Position cameras and set their viewing direction. Each building floor has its own plan.
          </p>
        </div>
        {hasUnsaved && (
          <button className="btn btn-primary" type="button" onClick={saveAll}>
            Save {Object.keys(dirty).length} change{Object.keys(dirty).length > 1 ? "s" : ""}
          </button>
        )}
      </div>

      {error && <Notice tone="danger" title="Something went wrong">{error}</Notice>}
      {status && <Notice tone="ok" title={status} />}

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="toolbar">
          <select value={buildingId ?? ""} aria-label="Building"
                  onChange={(e) => {
                    const id = e.target.value ? Number(e.target.value) : null;
                    setBuildingId(id);
                    const first = buildings.find((b) => b.id === id)?.floors[0];
                    setFloorId(first?.id ?? null);
                    setDirty({}); setSelectedId(null);
                  }}>
            <option value="">Select building…</option>
            {buildings.map((b) => (
              <option key={b.id} value={b.id}>{b.siteName} · {b.name}</option>
            ))}
          </select>
          <select value={floorId ?? ""} aria-label="Floor"
                  onChange={(e) => {
                    setFloorId(e.target.value ? Number(e.target.value) : null);
                    setDirty({}); setSelectedId(null); setScaleMode(false); setScalePoints([]);
                  }}>
            <option value="">Select floor…</option>
            {floors.map((f) => (
              <option key={f.id} value={f.id}>{f.name}{f.has_floor_plan ? "" : " (no plan)"}</option>
            ))}
          </select>
          <div className="grow" />
          {plan && (
            <>
              <span className="small muted">
                {plan.scale_px_per_metre
                  ? `Scale: ${plan.scale_px_per_metre.toFixed(1)} px/m`
                  : "No map scale set"}
              </span>
              <button className="btn btn-sm" type="button"
                      onClick={() => { setScaleMode((v) => !v); setScalePoints([]); }}>
                {scaleMode ? "Cancel scale" : plan.scale_px_per_metre ? "Reset scale" : "Set map scale"}
              </button>
            </>
          )}
          <label className="btn btn-sm" style={{ cursor: floorId ? "pointer" : "not-allowed" }}>
            {plan ? "Replace plan" : "Upload plan"}
            <input type="file" accept="image/png,image/jpeg" hidden disabled={!floorId}
                   onChange={(e) => { const f = e.target.files?.[0]; if (f) onPickPlan(f); e.target.value = ""; }} />
          </label>
        </div>
      </div>

      {!floorId ? (
        <div className="card empty">
          <div className="empty-mark" aria-hidden="true">▦</div>
          <h2>Select a building and floor</h2>
          <p>Floor plans are stored per building floor. Choose one above to view or upload its plan.</p>
        </div>
      ) : !plan ? (
        <div className="card empty">
          <div className="empty-mark" aria-hidden="true">▦</div>
          <h2>No floor plan for this floor</h2>
          <p>
            Upload a PNG or JPEG plan to place cameras on it. Marker positions are stored relative
            to the image, so resizing the viewer never moves a camera.
          </p>
          <label className="btn btn-primary">
            Upload floor plan
            <input type="file" accept="image/png,image/jpeg" hidden
                   onChange={(e) => { const f = e.target.files?.[0]; if (f) onPickPlan(f); }} />
          </label>
        </div>
      ) : (
        <>
          {plan.needs_review_count > 0 && (
            <Notice tone="warn" title={`${plan.needs_review_count} placement(s) need review`}>
              This floor-plan image was replaced after those cameras were placed. Check each marker
              against the new image and save it to confirm. They are not treated as verified until you do.
            </Notice>
          )}
          {scaleMode && (
            <Notice tone="info" title="Set map scale">
              Click two points on the plan whose real distance you know
              ({scalePoints.length}/2 selected), then enter the distance between them.
              <div className="row" style={{ marginTop: 8 }}>
                <input type="number" min="0.1" step="0.1" placeholder="Distance in metres"
                       style={{ maxWidth: 190 }} value={scaleDistance}
                       onChange={(e) => setScaleDistance(e.target.value)} />
                <button className="btn btn-sm btn-primary" type="button"
                        disabled={scalePoints.length !== 2 || !scaleDistance} onClick={applyScale}>
                  Apply scale
                </button>
                <button className="btn btn-sm" type="button" onClick={() => setScalePoints([])}>
                  Clear points
                </button>
                {plan.scale_px_per_metre && (
                  <button className="btn btn-sm btn-danger" type="button"
                          onClick={async () => { setPlan(await api.clearScale(plan.id)); setScaleMode(false); }}>
                    Remove existing scale
                  </button>
                )}
              </div>
              <div className="small" style={{ marginTop: 6 }}>
                Setting a scale lets Numenor draw estimated viewing distances in metres. It is a map
                measurement only — it does not calibrate the camera.
              </div>
            </Notice>
          )}
          {placingId && (
            <Notice tone="info" title="Click the plan to place this camera">
              Placing <strong>{cameras.find((c) => c.id === placingId)?.name}</strong>.
              <button className="btn btn-sm" type="button" style={{ marginLeft: 10 }}
                      onClick={() => setPlacingId(null)}>Cancel</button>
            </Notice>
          )}

          <div className="editor-layout">
            <FloorPlanCanvas
              plan={plan}
              markers={markers}
              selectedId={selectedId}
              onSelect={setSelectedId}
              onMove={(id, x, y) => patchMarker(id, { norm_x: x, norm_y: y })}
              onRotate={(id, h) => patchMarker(id, { heading_deg: h })}
              placingMode={placingId !== null}
              onPlaceAt={(x, y) => placingId && placeCamera(placingId, x, y)}
              scaleMode={scaleMode}
              scalePoints={scalePoints}
              onScalePoint={(x, y) => setScalePoints((p) => (p.length >= 2 ? [{ x, y }] : [...p, { x, y }]))}
            />

            <div className="side-panel">
              <div className="card card-pad">
                <h3 style={{ marginBottom: 10 }}>Selected camera</h3>
                {!selected || !selectedMarker ? (
                  <p className="small muted">
                    Select a marker on the plan, or choose a camera below to place it.
                  </p>
                ) : (
                  <>
                    <div className="row" style={{ marginBottom: 10 }}>
                      <Link to={`/cameras/${selected.id}`} className="cell-name">{selected.name}</Link>
                      <div className="grow" />
                      <StatusBadge status={selected.last_test_status} />
                    </div>
                    <dl className="kv small" style={{ marginBottom: 12 }}>
                      <dt>Address</dt><dd className="mono">{selected.host}</dd>
                      <dt>Area</dt><dd>{selected.location.area ?? "—"}</dd>
                      <dt>Position</dt>
                      <dd className="mono">
                        {selectedMarker.norm_x.toFixed(3)}, {selectedMarker.norm_y.toFixed(3)}
                        {plan.scale_px_per_metre && (
                          <div className="faint">
                            ≈ {(selectedMarker.norm_x * plan.width_px / plan.scale_px_per_metre).toFixed(1)} m,
                            {" "}{(selectedMarker.norm_y * plan.height_px / plan.scale_px_per_metre).toFixed(1)} m
                            from the top-left of the plan
                          </div>
                        )}
                      </dd>
                    </dl>

                    <div className="field">
                      <span className="field-label">Heading</span>
                      <div className="slider-row">
                        <input type="range" min={0} max={359} value={Math.round(selectedMarker.heading_deg)}
                               aria-label="Heading in degrees"
                               onChange={(e) => patchMarker(selected.id, { heading_deg: Number(e.target.value) })} />
                        <span className="slider-val">{Math.round(selectedMarker.heading_deg)}°</span>
                      </div>
                      <span className="hint">0° = top of the plan, increasing clockwise.</span>
                    </div>

                    <div className="field">
                      <span className="field-label">Field of view</span>
                      <div className="slider-row">
                        <input type="range" min={10} max={360} step={5} value={selectedMarker.fov_deg ?? 90}
                               aria-label="Field of view in degrees"
                               onChange={(e) => patchMarker(selected.id, { fov_deg: Number(e.target.value) })} />
                        <span className="slider-val">{selectedMarker.fov_deg ?? 90}°</span>
                      </div>
                    </div>

                    <div className="field">
                      <label htmlFor="sel-dist">Estimated viewing distance (m)</label>
                      <input id="sel-dist" type="number" min={0} step={0.5}
                             value={selectedMarker.view_distance_m ?? ""}
                             onChange={(e) => patchMarker(selected.id, {
                               view_distance_m: e.target.value ? Number(e.target.value) : null,
                             })} />
                      {!plan.scale_px_per_metre && (
                        <span className="hint">Set a map scale to draw this distance to scale.</span>
                      )}
                    </div>

                    <div className="row">
                      <button className="btn btn-sm btn-primary" type="button"
                              disabled={!dirty[selected.id]} onClick={() => saveMarker(selected.id)}>
                        {dirty[selected.id] ? "Save placement" : "Saved"}
                      </button>
                      <button className="btn btn-sm btn-danger" type="button"
                              onClick={() => removePlacement(selected.id)}>
                        Remove
                      </button>
                    </div>
                    {selected.placement?.review_status === "needs_review" && (
                      <p className="hint" style={{ marginTop: 8 }}>
                        Flagged for review after the plan image changed. Saving confirms this position.
                      </p>
                    )}
                  </>
                )}
              </div>

              <div className="card card-pad">
                <h3 style={{ marginBottom: 4 }}>Not yet placed</h3>
                <p className="hint" style={{ marginBottom: 10 }}>
                  Cameras assigned to this floor with no marker.
                </p>
                {unplaced.length === 0 ? (
                  <p className="small muted">Every camera on this floor has been placed.</p>
                ) : (
                  <div className="chip-list">
                    {unplaced.map((c) => (
                      <button key={c.id} type="button"
                              className={`chip${placingId === c.id ? " is-selected" : ""}`}
                              onClick={() => setPlacingId(c.id)}>
                        <span className="grow">{c.name}</span>
                        <span className="badge badge-accent">Place</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>

              <div className="card card-pad">
                <h3 style={{ marginBottom: 10 }}>Placed on this plan ({placedHere.length})</h3>
                {placedHere.length === 0 ? (
                  <p className="small muted">No cameras placed yet.</p>
                ) : (
                  <div className="chip-list">
                    {placedHere.map((c) => (
                      <button key={c.id} type="button"
                              className={`chip${selectedId === c.id ? " is-selected" : ""}`}
                              onClick={() => setSelectedId(c.id)}>
                        <span className="grow">{c.name}</span>
                        {dirty[c.id] && <span className="badge badge-warn">Unsaved</span>}
                        {c.placement?.review_status === "needs_review" && (
                          <span className="badge badge-warn">Review</span>
                        )}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        </>
      )}

      {replaceWarning && (
        <Modal title="Replace this floor plan?" onClose={() => setReplaceWarning(null)}
               footer={<>
                 <button className="btn" type="button" onClick={() => setReplaceWarning(null)}>Cancel</button>
                 <button className="btn btn-primary" type="button" onClick={() => doUpload(replaceWarning)}>
                   Replace and flag for review
                 </button>
               </>}>
          <Notice tone="warn" title="Existing placements will need review">
            {plan?.placement_count} camera marker(s) are positioned on the current image. Numenor keeps
            their coordinates, but a new image may not line up with the old one, so every placement on
            this floor will be marked <strong>needs review</strong> until you confirm it.
            {plan?.scale_px_per_metre && " The map scale was measured against the old image and will be cleared."}
          </Notice>
        </Modal>
      )}
    </main>
  );
}
