import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError, api } from "../lib/api";
import FactoryGrid from "../components/FactoryGrid";
import ZoneEditor from "../components/ZoneEditor";
import { Guidance } from "../components/setupUi";
import { Modal, Notice, Spinner } from "../components/ui";
import type { FactoryMap } from "../lib/calibrationTypes";
import type {
  GraphNode, OverlapSuggestion, Relationship, RelationshipGraph, RelationshipKind, Workspace, Zone,
  ZoneKind,
} from "../lib/setupTypes";

const KIND_LABEL: Record<RelationshipKind, string> = {
  overlap: "See the same ground",
  transition: "People walk from one to the other",
  excluded: "No direct link",
};

const KIND_HELP: Record<RelationshipKind, string> = {
  overlap: "Both cameras can see a shared patch of floor at the same time.",
  transition: "Someone leaving one camera's view can arrive in the other's, with a plausible "
    + "walking time. Each direction is a separate record.",
  excluded: "These two views have no direct link. People can still get from one to the other by "
    + "passing through other cameras.",
};

export default function RelationshipsPage() {
  const [params, setParams] = useSearchParams();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState<number | null>(
    params.get("workspace") ? Number(params.get("workspace")) : null);
  const [graph, setGraph] = useState<RelationshipGraph | null>(null);
  const [map, setMap] = useState<FactoryMap | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [adding, setAdding] = useState<RelationshipKind | null>(null);
  const [zoneCamera, setZoneCamera] = useState<number | null>(null);
  const [selected, setSelected] = useState<Relationship | null>(null);

  const say = (m: string) => { setFlash(m); setTimeout(() => setFlash(null), 6000); };

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
      const [g, m] = await Promise.all([
        api.relationshipGraph(workspaceId),
        api.factoryMap(workspaceId).catch(() => null),
      ]);
      setGraph(g);
      setMap(m);
      setParams((p) => { p.set("workspace", String(workspaceId)); return p; }, { replace: true });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not load the camera connections.");
    } finally { setLoading(false); }
  }, [workspaceId, setParams]);

  useEffect(() => { load(); }, [load]);

  if (loading && !graph) return <main className="page"><Spinner label="Loading…" /></main>;

  if (!workspaces.length) {
    return (
      <main className="page">
        <Notice tone="info" title="Set up an area first">
          Camera connections describe how views relate across one area.{" "}
          <Link to="/setup">Start the setup</Link>.
        </Notice>
      </main>
    );
  }

  const byKind = (kind: RelationshipKind) =>
    (graph?.relationships ?? []).filter((r) => r.kind === kind);

  return (
    <main className="page page-wide">
      <div className="page-head">
        <div className="grow">
          <h1>Connect cameras</h1>
          <p className="page-sub">
            Record which cameras see the same ground, and which exits lead where.
          </p>
        </div>
        <Link className="btn" to="/setup?step=6">Back to setup</Link>
      </div>

      {error && <Notice tone="danger" title="Something went wrong">{error}</Notice>}
      {flash && <Notice tone="ok" title={flash} />}

      <Guidance>
        This describes the layout of your cameras for a future tracking service — which views
        touch, and how long it takes to walk between them. It is about geometry and doors, not
        about people: nothing here identifies anybody.
      </Guidance>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="toolbar">
          <select value={workspaceId ?? ""} aria-label="Area"
                  onChange={(e) => setWorkspaceId(Number(e.target.value))}>
            {workspaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
          <div className="grow" />
          <button className="btn btn-sm" type="button" onClick={() => setAdding("overlap")}>
            ＋ Same ground
          </button>
          <button className="btn btn-sm" type="button" onClick={() => setAdding("transition")}>
            ＋ Walking route
          </button>
          <button className="btn btn-sm" type="button" onClick={() => setAdding("excluded")}>
            ＋ No direct link
          </button>
        </div>
      </div>

      {graph && (
        <>
          <div className="stat-grid">
            <div className="stat stat-accent">
              <div className="stat-label">Cameras</div>
              <div className="stat-value">{graph.summary.cameras}</div>
              <div className="stat-note">{graph.summary.possible_pairs} possible pairs</div>
            </div>
            <div className="stat stat-ok">
              <div className="stat-label">Same ground</div>
              <div className="stat-value">{graph.summary.overlaps}</div>
              <div className="stat-note">{graph.summary.verified} confirmed on site</div>
            </div>
            <div className="stat stat-accent">
              <div className="stat-label">Walking routes</div>
              <div className="stat-value">{graph.summary.transitions}</div>
              <div className="stat-note">each direction counted separately</div>
            </div>
            <div className="stat stat-warn">
              <div className="stat-label">Not yet described</div>
              <div className="stat-value">{graph.summary.pairs_unknown}</div>
              <div className="stat-note">unknown, not impossible</div>
            </div>
          </div>

          <div className="editor-layout">
            <div className="stack" style={{ gap: 16 }}>
              {map && (
                <div className="card card-pad">
                  <strong>Where the cameras cover</strong>
                  <p className="hint" style={{ marginTop: 4 }}>
                    Shaded areas are the floor each camera's mapping covers. Overlapping shading
                    suggests a shared view, but walls and machinery are invisible to geometry.
                  </p>
                  <FactoryGrid
                    map={map}
                    selectedCameraId={null}
                    height={380}
                    extraMarkers={(selected?.overlap_polygon ?? []).map((p, i) => ({
                      x: p[0], y: p[1], label: i === 0 ? "shared area" : "", colour: "#0f7b4f" }))}
                  />
                </div>
              )}

              {graph.suggestions.length > 0 && (
                <div className="card card-pad">
                  <strong>Possible shared views ({graph.suggestions.length})</strong>
                  <p className="hint" style={{ marginTop: 4 }}>
                    Worked out from the mapped floor areas. Check each one on site before accepting
                    it — geometry cannot see racking.
                  </p>
                  <div className="chip-list" style={{ marginTop: 10 }}>
                    {graph.suggestions.map((s: OverlapSuggestion) => (
                      <div key={`${s.camera_a_id}-${s.camera_b_id}`} className="point-row">
                        <span className="grow">
                          {s.camera_a_name} ↔ {s.camera_b_name}
                        </span>
                        <span className="small faint">{s.overlap_area_m2} m² shared</span>
                        <button className="btn btn-sm" type="button"
                                onClick={async () => {
                                  try {
                                    await api.acceptSuggestion({
                                      kind: "overlap",
                                      camera_a_id: s.camera_a_id, camera_b_id: s.camera_b_id,
                                      overlap_polygon: s.overlap_polygon,
                                      verification: "unverified",
                                      notes: "Suggested from mapped floor areas." });
                                    await load();
                                    say("Added, still marked unconfirmed.");
                                  } catch (e) {
                                    setError(e instanceof ApiError ? e.message : "Could not add it.");
                                  }
                                }}>Add</button>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {(["overlap", "transition", "excluded"] as RelationshipKind[]).map((kind) => (
                <div className="card" key={kind}>
                  <div className="card-head">
                    <div className="grow">
                      <strong>{KIND_LABEL[kind]}</strong>
                      <div className="cell-sub">{KIND_HELP[kind]}</div>
                    </div>
                    <span className="badge badge-neutral">{byKind(kind).length}</span>
                  </div>
                  <div className="card-pad">
                    {byKind(kind).length === 0 ? (
                      <p className="small muted" style={{ margin: 0 }}>None recorded.</p>
                    ) : (
                      <div className="chip-list">
                        {byKind(kind).map((r) => (
                          <div key={r.id} className="point-row" style={{ cursor: "pointer" }}
                               onClick={() => setSelected(r)}>
                            <span className="grow">
                              {r.camera_a_name}
                              {kind === "transition" ? " → " : kind === "overlap" ? " ↔ " : " ⊘ "}
                              {r.camera_b_name}
                              {r.zone_a_name && (
                                <div className="cell-sub">
                                  {r.zone_a_name} → {r.zone_b_name ?? "anywhere"}
                                </div>
                              )}
                            </span>
                            {kind === "transition" && (
                              <span className="small faint">
                                {r.min_travel_seconds}–{r.max_travel_seconds} s
                              </span>
                            )}
                            {r.overlap_area_m2 != null && (
                              <span className="small faint">{r.overlap_area_m2} m²</span>
                            )}
                            <span className={`badge ${
                              r.verification === "verified" ? "badge-ok"
                                : r.verification === "needs_review" ? "badge-warn" : "badge-neutral"}`}>
                              {r.verification === "verified" ? "confirmed"
                                : r.verification === "needs_review" ? "recheck" : "unconfirmed"}
                            </span>
                            <button className="btn btn-sm btn-ghost" type="button"
                                    onClick={async (e) => {
                                      e.stopPropagation();
                                      await api.deleteRelationship(r.id);
                                      await load();
                                    }}>×</button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>

            <div className="side-panel">
              <div className="card card-pad">
                <strong>Cameras in this area</strong>
                <div className="chip-list" style={{ marginTop: 10 }}>
                  {graph.cameras.map((node: GraphNode) => (
                    <div key={node.camera_id} className="point-row" style={{ cursor: "default" }}>
                      <span className="grow">
                        <Link to={`/cameras/${node.camera_id}`}>{node.name}</Link>
                        <div className="cell-sub">
                          {node.zones.length} zone{node.zones.length === 1 ? "" : "s"}
                          {!node.has_mapping && " · no mapping yet"}
                        </div>
                      </span>
                      <button className="btn btn-sm" type="button"
                              onClick={() => setZoneCamera(node.camera_id)}>Zones</button>
                    </div>
                  ))}
                </div>
              </div>

              <div className="card card-pad">
                <strong>What "not described" means</strong>
                <p className="small muted" style={{ marginTop: 6 }}>
                  {graph.summary.interpretation}
                </p>
              </div>

              <div className="card card-pad">
                <strong>Hand this to a tracking service</strong>
                <p className="hint" style={{ marginTop: 4 }}>
                  Exports the floor mappings and this layout, with no credentials in it.
                </p>
                <button className="btn btn-sm btn-block" type="button" style={{ marginTop: 8 }}
                        onClick={async () => {
                          if (!workspaceId) return;
                          const data = await api.exportSiteGeometry(workspaceId);
                          const blob = new Blob([JSON.stringify(data, null, 2)],
                                                { type: "application/json" });
                          const url = URL.createObjectURL(blob);
                          const a = document.createElement("a");
                          a.href = url;
                          a.download = `numenor-site-geometry-${workspaceId}.json`;
                          a.click();
                          URL.revokeObjectURL(url);
                        }}>
                  Download the layout
                </button>
              </div>
            </div>
          </div>
        </>
      )}

      {adding && graph && (
        <RelationshipModal
          kind={adding}
          cameras={graph.cameras}
          onClose={() => setAdding(null)}
          onSaved={async (message) => { setAdding(null); await load(); say(message); }}
        />
      )}

      {zoneCamera && (
        <ZoneModal cameraId={zoneCamera} onClose={() => setZoneCamera(null)}
                   onChanged={async () => { await load(); }} onError={setError} />
      )}

      {selected && (
        <Modal title={KIND_LABEL[selected.kind]} onClose={() => setSelected(null)}
               footer={<>
                 {selected.verification !== "verified" && (
                   <button className="btn btn-primary" type="button"
                           onClick={async () => {
                             await api.updateRelationship(selected.id, { verification: "verified" });
                             setSelected(null); await load(); say("Marked as confirmed on site.");
                           }}>Confirm on site</button>
                 )}
                 <div className="grow" />
                 <button className="btn" type="button" onClick={() => setSelected(null)}>Close</button>
               </>}>
          <dl className="kv">
            <dt>Cameras</dt>
            <dd>{selected.camera_a_name} {selected.kind === "transition" ? "→" : "↔"} {selected.camera_b_name}</dd>
            {selected.zone_a_name && (<><dt>From zone</dt><dd>{selected.zone_a_name}</dd></>)}
            {selected.zone_b_name && (<><dt>To zone</dt><dd>{selected.zone_b_name}</dd></>)}
            {selected.min_travel_seconds != null && (
              <><dt>Walking time</dt>
                <dd>{selected.min_travel_seconds}–{selected.max_travel_seconds} seconds</dd></>
            )}
            {selected.overlap_area_m2 != null && (
              <><dt>Shared floor</dt><dd>{selected.overlap_area_m2} m²</dd></>
            )}
            <dt>Confirmed</dt>
            <dd>
              {selected.verification === "verified"
                ? `Yes${selected.verified_by ? `, by ${selected.verified_by}` : ""}`
                : selected.verification === "needs_review"
                  ? `Needs rechecking — ${selected.review_reason}`
                  : "Not yet"}
            </dd>
            {selected.suggested_by_geometry && (
              <><dt>Origin</dt>
                <dd>Suggested from mapped areas, which cannot account for walls or machinery.</dd></>
            )}
            {selected.notes && (<><dt>Notes</dt><dd>{selected.notes}</dd></>)}
          </dl>
        </Modal>
      )}
    </main>
  );
}

function RelationshipModal({ kind, cameras, onClose, onSaved }: {
  kind: RelationshipKind;
  cameras: GraphNode[];
  onClose: () => void;
  onSaved: (message: string) => void;
}) {
  const [a, setA] = useState<number | "">("");
  const [b, setB] = useState<number | "">("");
  const [zoneA, setZoneA] = useState<number | "">("");
  const [zoneB, setZoneB] = useState<number | "">("");
  const [minT, setMinT] = useState("5");
  const [maxT, setMaxT] = useState("30");
  const [notes, setNotes] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  const nodeA = cameras.find((c) => c.camera_id === a);
  const nodeB = cameras.find((c) => c.camera_id === b);

  const submit = async () => {
    setBusy(true); setLocalError(null);
    try {
      await api.createRelationship({
        kind, camera_a_id: a, camera_b_id: b,
        zone_a_id: zoneA || null, zone_b_id: zoneB || null,
        min_travel_seconds: kind === "transition" ? Number(minT) : null,
        max_travel_seconds: kind === "transition" ? Number(maxT) : null,
        verification: confirmed ? "verified" : "unverified",
        notes: notes || null,
      });
      onSaved("Saved.");
    } catch (e) {
      setLocalError(e instanceof ApiError ? e.message : "Could not save it.");
      setBusy(false);
    }
  };

  const valid = a !== "" && b !== "" && a !== b
    && (kind !== "transition" || (Number(minT) <= Number(maxT)));

  return (
    <Modal title={`Add: ${KIND_LABEL[kind]}`} wide onClose={onClose}
           footer={<>
             <button className="btn" type="button" onClick={onClose}>Cancel</button>
             <button className="btn btn-primary" type="button" disabled={!valid || busy}
                     onClick={submit}>{busy ? "Saving…" : "Save"}</button>
           </>}>
      {localError && <Notice tone="danger" title="Could not save">{localError}</Notice>}
      <Notice tone="info" title="What this records">{KIND_HELP[kind]}</Notice>

      <div className="field-row">
        <div className="field">
          <label htmlFor="rel-a">{kind === "transition" ? "People leave this camera" : "First camera"}</label>
          <select id="rel-a" value={a} onChange={(e) => { setA(Number(e.target.value)); setZoneA(""); }}>
            <option value="">Choose…</option>
            {cameras.map((c) => <option key={c.camera_id} value={c.camera_id}>{c.name}</option>)}
          </select>
        </div>
        <div className="field">
          <label htmlFor="rel-b">{kind === "transition" ? "And arrive at this one" : "Second camera"}</label>
          <select id="rel-b" value={b} onChange={(e) => { setB(Number(e.target.value)); setZoneB(""); }}>
            <option value="">Choose…</option>
            {cameras.filter((c) => c.camera_id !== a).map((c) =>
              <option key={c.camera_id} value={c.camera_id}>{c.name}</option>)}
          </select>
        </div>
      </div>

      {kind === "transition" && (
        <>
          <div className="field-row">
            <div className="field">
              <label htmlFor="rel-za">Which way out? (optional)</label>
              <select id="rel-za" value={zoneA} onChange={(e) => setZoneA(Number(e.target.value))}>
                <option value="">Anywhere in the view</option>
                {(nodeA?.zones ?? []).map((z) =>
                  <option key={z.id} value={z.id}>{z.name} ({z.kind})</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="rel-zb">Which way in? (optional)</label>
              <select id="rel-zb" value={zoneB} onChange={(e) => setZoneB(Number(e.target.value))}>
                <option value="">Anywhere in the view</option>
                {(nodeB?.zones ?? []).map((z) =>
                  <option key={z.id} value={z.id}>{z.name} ({z.kind})</option>)}
              </select>
            </div>
          </div>
          <div className="field-row">
            <div className="field">
              <label htmlFor="rel-min">Fastest anyone could walk it (seconds)</label>
              <input id="rel-min" type="number" step="1" value={minT}
                     onChange={(e) => setMinT(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="rel-max">Longest it should plausibly take (seconds)</label>
              <input id="rel-max" type="number" step="1" value={maxT}
                     onChange={(e) => setMaxT(e.target.value)} />
              <span className="hint">Walk it yourself and allow a margin either side.</span>
            </div>
          </div>
          <Notice tone="info" title="One direction at a time">
            This records the walk one way. If people also come back the other way, add a second
            record for that direction with its own times.
          </Notice>
        </>
      )}

      <div className="field">
        <label htmlFor="rel-notes">Notes</label>
        <input id="rel-notes" type="text" value={notes} onChange={(e) => setNotes(e.target.value)}
               placeholder="Checked by walking it on 12 March." />
      </div>

      <label className="row small" style={{ gap: 7 }}>
        <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
        I have checked this on site
      </label>
      <p className="hint" style={{ marginTop: 6 }}>
        Leave this unticked if you are recording it from a drawing. Only someone standing in the
        room can see whether a wall gets in the way.
      </p>
    </Modal>
  );
}

function ZoneModal({ cameraId, onClose, onChanged, onError }: {
  cameraId: number; onClose: () => void; onChanged: () => Promise<void>; onError: (m: string) => void;
}) {
  const [zones, setZones] = useState<Zone[]>([]);
  const [drawing, setDrawing] = useState(false);
  const [pendingShape, setPendingShape] = useState<
    { polygon: number[][]; size: { w: number; h: number } } | null>(null);
  const [name, setName] = useState("");
  const [kind, setKind] = useState<ZoneKind>("monitored");

  const load = useCallback(async () => {
    try { setZones(await api.listZones(cameraId)); } catch { setZones([]); }
  }, [cameraId]);
  useEffect(() => { load(); }, [load]);

  const save = async () => {
    if (!pendingShape || !name.trim()) return;
    try {
      await api.createZone(cameraId, {
        name: name.trim(), kind,
        image_polygon: pendingShape.polygon,
        image_width: pendingShape.size.w, image_height: pendingShape.size.h });
      setPendingShape(null); setName(""); setDrawing(false);
      await load(); await onChanged();
    } catch (e) {
      onError(e instanceof ApiError ? e.message : "Could not save the zone.");
    }
  };

  return (
    <Modal title="Zones in this camera" wide onClose={onClose}
           footer={<button className="btn" type="button" onClick={onClose}>Close</button>}>
      <Notice tone="info" title="Zones need no measurements">
        Mark a doorway or an aisle straight on the picture. If the camera has a floor mapping, the
        zone also gets a position on the floor; if not, it still works as a picture region.
      </Notice>

      <ZoneEditor
        cameraId={cameraId}
        zones={zones}
        drawing={drawing}
        height={380}
        onFinish={(polygon, size) => setPendingShape({ polygon, size })}
        onCancel={() => setDrawing(false)}
      />

      {!drawing && !pendingShape && (
        <button className="btn btn-sm btn-primary" style={{ marginTop: 10 }} type="button"
                onClick={() => setDrawing(true)}>
          ＋ Draw a zone
        </button>
      )}

      {pendingShape && (
        <div className="card card-pad" style={{ marginTop: 12 }}>
          <div className="field-row">
            <div className="field">
              <label htmlFor="z-name">What is this zone?</label>
              <input id="z-name" type="text" value={name} placeholder="Dispatch doorway"
                     onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="z-kind">What is it for?</label>
              <select id="z-kind" value={kind} onChange={(e) => setKind(e.target.value as ZoneKind)}>
                <option value="monitored">An area to watch</option>
                <option value="entrance">Where people come in</option>
                <option value="exit">Where people leave</option>
              </select>
            </div>
          </div>
          <div className="row">
            <button className="btn btn-sm" type="button"
                    onClick={() => { setPendingShape(null); setDrawing(false); }}>Discard</button>
            <button className="btn btn-sm btn-primary" type="button" disabled={!name.trim()}
                    onClick={save}>Save zone</button>
          </div>
        </div>
      )}

      {zones.length > 0 && (
        <>
          <div className="section-title">Existing zones</div>
          <div className="chip-list">
            {zones.map((z) => (
              <div key={z.id} className="point-row" style={{ cursor: "default" }}>
                <span className="grow"><strong>{z.name}</strong> · {z.kind}</span>
                {z.world_polygon
                  ? <span className="badge badge-ok">on the floor</span>
                  : <span className="badge badge-neutral">picture only</span>}
                {z.world_is_stale && <span className="badge badge-warn">recheck</span>}
                <button className="btn btn-sm btn-ghost" type="button"
                        onClick={async () => {
                          try {
                            await api.deleteZone(z.id);
                            await load(); await onChanged();
                          } catch (e) {
                            onError(e instanceof ApiError ? e.message : "Could not delete it.");
                          }
                        }}>×</button>
              </div>
            ))}
          </div>
        </>
      )}
    </Modal>
  );
}
