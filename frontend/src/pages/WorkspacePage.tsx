import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError, api, getToken } from "../lib/api";
import SceneCanvas2D, { type SceneTool, type SceneRelationship } from "../components/SceneCanvas2D";
import PictureGeometryEditor, { type GeometryShape } from "../components/PictureGeometryEditor";
import PreviewPanel from "../components/PreviewPanel";
import { Advanced, CameraStatusRow, Guidance } from "../components/setupUi";
import { Modal, Notice, Spinner } from "../components/ui";
import type { Camera, Site } from "../lib/types";
import type { CalibrationRevision, FactoryMap, MapCamera } from "../lib/calibrationTypes";
import type { Relationship, RelationshipGraph, Workspace } from "../lib/setupTypes";
import type {
  CameraFunction, FactoryScene, FunctionCatalogueEntry, MountType, PaletteItem,
  PlacementResult, SceneObject, SceneObjectKind,
} from "../lib/sceneTypes";

// Three.js is large and most sessions stay in 2D.
const SceneView3D = lazy(() => import("../components/SceneView3D"));

type Mode = "setup" | "monitor";

export default function WorkspacePage() {
  const [params, setParams] = useSearchParams();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState<number | null>(
    params.get("workspace") ? Number(params.get("workspace")) : null);
  const [scene, setScene] = useState<FactoryScene | null>(null);
  const [map, setMap] = useState<FactoryMap | null>(null);
  const [graph, setGraph] = useState<RelationshipGraph | null>(null);
  const [showLinks, setShowLinks] = useState(false);
  const [selectedRelationshipId, setSelectedRelationshipId] = useState<number | null>(null);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [palette, setPalette] = useState<PaletteItem[]>([]);
  const [sites, setSites] = useState<Site[]>([]);

  const [mode, setMode] = useState<Mode>((params.get("mode") as Mode) ?? "setup");
  const [view, setView] = useState<"2d" | "3d">("2d");
  const [tool, setTool] = useState<SceneTool>("select");
  const [armedKind, setArmedKind] = useState<SceneObjectKind | null>(null);
  const [draftOutline, setDraftOutline] = useState<number[][]>([]);
  const [placingCameraId, setPlacingCameraId] = useState<number | null>(null);
  const [pendingDrop, setPendingDrop] = useState<{ cameraId: number; x: number; y: number } | null>(null);
  const [aimingCameraId, setAimingCameraId] = useState<number | null>(null);

  const [selectedObjectId, setSelectedObjectId] = useState<number | null>(null);
  const [selectedCameraId, setSelectedCameraId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [creatingScene, setCreatingScene] = useState(false);

  const say = (m: string) => { setFlash(m); setTimeout(() => setFlash(null), 6000); };

  /* ---- loading ---- */

  useEffect(() => {
    Promise.all([api.listWorkspaces(), api.scenePalette(), api.tree()])
      .then(([list, pal, tree]) => {
        setWorkspaces(list);
        setPalette(pal.items);
        setSites(tree);
        if (!workspaceId && list.length) setWorkspaceId(list[0].id);
        if (!list.length) setLoading(false);
      })
      .catch((e) => { setError(e instanceof ApiError ? e.message : "Could not load."); setLoading(false); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const load = useCallback(async () => {
    if (!workspaceId) return;
    setLoading(true);
    try {
      const [scenes, factoryMap, cams, links] = await Promise.all([
        api.listScenes(workspaceId),
        api.factoryMap(workspaceId).catch(() => null),
        api.listCameras(),
        api.relationshipGraph(workspaceId).catch(() => null),
      ]);
      setScene(scenes[0] ?? null);
      setMap(factoryMap);
      setCameras(cams);
      setGraph(links);
      setParams((p) => { p.set("workspace", String(workspaceId)); return p; }, { replace: true });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not load the workspace.");
    } finally { setLoading(false); }
  }, [workspaceId, setParams]);

  useEffect(() => { load(); }, [load]);

  const refreshScene = useCallback(async () => {
    if (!scene) return;
    const [fresh, factoryMap] = await Promise.all([
      api.getScene(scene.id),
      workspaceId ? api.factoryMap(workspaceId).catch(() => null) : Promise.resolve(null),
    ]);
    setScene(fresh);
    if (factoryMap) setMap(factoryMap);
  }, [scene, workspaceId]);

  const refreshAll = useCallback(async () => {
    await Promise.all([refreshScene(), api.listCameras().then(setCameras)]);
  }, [refreshScene]);

  /* ---- derived ---- */

  const mapCameras: MapCamera[] = useMemo(() => map?.cameras ?? [], [map]);
  const inWorkspace = useMemo(
    () => cameras.filter((c) => c.coordinate_system_id === workspaceId), [cameras, workspaceId]);
  // Three states, not two. A camera solved by floor homography has no pose but
  // does have a working mapping and a known patch of floor; calling it
  // "unplaced" alongside a camera nobody has touched throws that away.
  const mappedOnly = useMemo(
    () => mapCameras.filter((m) => !m.position && (m.floor_polygon?.length ?? 0) >= 3),
    [mapCameras]);
  const unplaced = useMemo(
    () => cameras.filter((c) => !mapCameras.some(
      (m) => m.camera_id === c.id && (m.position || (m.floor_polygon?.length ?? 0) >= 3))),
    [cameras, mapCameras]);
  const selectedCamera = cameras.find((c) => c.id === selectedCameraId) ?? null;
  const selectedMapCamera = mapCameras.find((m) => m.camera_id === selectedCameraId) ?? null;
  const selectedObject = scene?.objects.find((o) => o.id === selectedObjectId) ?? null;

  const sceneLinks: SceneRelationship[] = useMemo(() => {
    if (!showLinks) return [];
    return (graph?.relationships ?? []).map((r) => ({
      id: r.id, kind: r.kind,
      camera_a_id: r.camera_a_id, camera_b_id: r.camera_b_id,
      label: r.kind === "overlap"
        ? (r.overlap_area_m2 ? `overlap \u00b7 ${r.overlap_area_m2.toFixed(0)} m\u00b2` : "overlap")
        : r.kind.replace("_", " "),
      polygon: r.overlap_polygon,
      verified: r.verification === "verified",
    }));
  }, [graph, showLinks]);

  const selectedRelationship = graph?.relationships.find(
    (r) => r.id === selectedRelationshipId) ?? null;

  /* ---- scene interactions ---- */

  const onWorldClick = async (x: number, y: number) => {
    if (!scene) return;

    if (tool === "place-camera" && placingCameraId) {
      setPendingDrop({ cameraId: placingCameraId, x, y });
      setTool("select");
      setPlacingCameraId(null);
      return;
    }

    if (tool === "aim-camera" && aimingCameraId) {
      const camera = mapCameras.find((m) => m.camera_id === aimingCameraId);
      if (!camera?.position || !workspaceId) return;
      try {
        await api.placeCamera(aimingCameraId, {
          workspace_id: workspaceId,
          x: camera.position.x, y: camera.position.y, height_m: camera.position.z,
          target_x: x, target_y: y,
          mount_type: "wall",
          illustrative_hfov_deg: camera.horizontal_fov_deg ? null : (camera.approx_hfov_deg ?? 78),
          illustrative_range_m: camera.approx_range_m ?? 15,
        });
        await refreshAll();
        say("Aim updated.");
      } catch (e) {
        setError(e instanceof ApiError ? e.message : "Could not aim the camera.");
      }
      setTool("select");
      setAimingCameraId(null);
      return;
    }

    if (tool === "draw-outline" && armedKind) {
      setDraftOutline((pts) => [...pts, [Number(x.toFixed(2)), Number(y.toFixed(2))]]);
      return;
    }

    if (armedKind) {
      const item = palette.find((p) => p.kind === armedKind);
      try {
        await api.addSceneObject(scene.id, {
          kind: armedKind,
          name: `${item?.label ?? armedKind} ${scene.objects.length + 1}`,
          x: Number(x.toFixed(2)), y: Number(y.toFixed(2)),
          width_m: item?.default_width_m ?? 1,
          depth_m: item?.default_depth_m ?? 1,
          height_m: item?.default_height_m ?? 1,
        });
        await refreshScene();
        setArmedKind(null);
        setTool("select");
      } catch (e) {
        setError(e instanceof ApiError ? e.message : "Could not add that object.");
      }
    }
  };

  const finishOutline = async () => {
    if (!scene || !armedKind || draftOutline.length < 3) return;
    const item = palette.find((p) => p.kind === armedKind);
    try {
      await api.addSceneObject(scene.id, {
        kind: armedKind,
        name: `${item?.label ?? armedKind} ${scene.objects.length + 1}`,
        points: draftOutline,
      });
      await refreshScene();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not add that shape.");
    }
    setDraftOutline([]); setArmedKind(null); setTool("select");
  };

  const armPalette = (item: PaletteItem) => {
    setArmedKind(item.kind);
    setDraftOutline([]);
    setTool(item.shape === "box" ? "select" : "draw-outline");
    if (item.shape === "box") setTool("select");
  };

  const moveObject = async (id: number, x: number, y: number) => {
    if (!scene) return;
    setScene((s) => s && { ...s, objects: s.objects.map((o) => o.id === id ? { ...o, x, y } : o) });
    try {
      await api.updateSceneObject(scene.id, id,
        { x: Number(x.toFixed(3)), y: Number(y.toFixed(3)) });
      await refreshScene();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not move that object.");
      await refreshScene();
    }
  };

  const rotateObject = async (id: number, deg: number) => {
    if (!scene) return;
    setScene((s) => s && {
      ...s, objects: s.objects.map((o) => o.id === id ? { ...o, rotation_deg: deg } : o) });
    try {
      await api.updateSceneObject(scene.id, id, { rotation_deg: deg });
      await refreshScene();
    } catch { await refreshScene(); }
  };

  if (loading && !scene && !workspaces.length) {
    return <main className="page"><Spinner label="Loading the workspace…" /></main>;
  }

  if (!workspaces.length) {
    return (
      <main className="page">
        <div className="card empty">
          <div className="empty-mark" aria-hidden="true">▦</div>
          <h2>No area set up yet</h2>
          <p>
            The workspace needs an area to build a scene in — the patch of floor your cameras
            watch, with a corner you measure from.
          </p>
          <Link className="btn btn-primary" to="/setup?step=1">Create an area</Link>
        </div>
      </main>
    );
  }

  return (
    <main className="page page-wide">
      <div className="page-head">
        <div className="grow">
          <h1>Commissioning workspace</h1>
          <p className="page-sub">
            Build the factory scene, drop cameras in, and aim them at what matters.
          </p>
        </div>
        <div className="mode-switch" role="group" aria-label="Mode">
          <button type="button" className={mode === "setup" ? "is-active" : ""}
                  onClick={() => { setMode("setup"); setParams((p) => { p.set("mode", "setup"); return p; }, { replace: true }); }}>
            Setup
          </button>
          <button type="button" className={mode === "monitor" ? "is-active" : ""}
                  onClick={() => { setMode("monitor"); setTool("select"); setArmedKind(null);
                                   setParams((p) => { p.set("mode", "monitor"); return p; }, { replace: true }); }}>
            Monitor
          </button>
        </div>
        <select value={workspaceId ?? ""} aria-label="Area"
                onChange={(e) => { setWorkspaceId(Number(e.target.value));
                                   setSelectedCameraId(null); setSelectedObjectId(null); }}>
          {workspaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
        </select>
      </div>

      {error && <Notice tone="danger" title="Something went wrong">{error}</Notice>}
      {flash && <Notice tone="ok" title={flash} />}

      {!scene ? (
        <div className="card empty">
          <div className="empty-mark" aria-hidden="true">▤</div>
          <h2>No factory scene yet</h2>
          <p>
            A scene is a simple model of the floor: walls, machines, racks and walkways, in metres.
            You can draw it from scratch — no floor plan or CAD file is needed.
          </p>
          <button className="btn btn-primary" type="button" onClick={() => setCreatingScene(true)}>
            Create the scene
          </button>
        </div>
      ) : (
        <>
          {mode === "monitor" && (
            <Guidance>
              Monitor shows the published scene and whatever live data is available. Nothing is
              being analysed — no detection service is connected to this deployment — so this view
              shows camera pictures and geometry, and invents nothing.
            </Guidance>
          )}

          {mode === "setup" && scene.has_unpublished_changes && (
            <Notice tone="warn" title="Unpublished changes">
              The scene has been edited since it was last published. Monitor mode and any export
              still show the published version, so nothing downstream has moved.
              <div className="row" style={{ marginTop: 8 }}>
                <button className="btn btn-sm btn-primary" type="button"
                        onClick={async () => {
                          try {
                            await api.publishScene(scene.id);
                            await refreshScene();
                            say("Scene published.");
                          } catch (e) {
                            setError(e instanceof ApiError ? e.message : "Could not publish.");
                          }
                        }}>
                  Publish the scene
                </button>
              </div>
            </Notice>
          )}

          <div className="workspace">
            <LeftPanel
              scene={scene} sites={sites} cameras={cameras} mapCameras={mapCameras}
              unplaced={unplaced} mappedOnly={mappedOnly} inWorkspace={inWorkspace} palette={palette}
              mode={mode} armedKind={armedKind}
              selectedObjectId={selectedObjectId} selectedCameraId={selectedCameraId}
              onArm={armPalette}
              onSelectObject={(id) => { setSelectedObjectId(id); setSelectedCameraId(null); }}
              onSelectCamera={(id) => { setSelectedCameraId(id); setSelectedObjectId(null); }}
              onPlaceCamera={(id) => { setPlacingCameraId(id); setTool("place-camera");
                                       setSelectedCameraId(id); }}
            />

            <div>
              <div className="card" style={{ marginBottom: 12 }}>
                <div className="toolbar">
                  <div className="mode-switch" role="group" aria-label="View">
                    <button type="button" className={view === "2d" ? "is-active" : ""}
                            onClick={() => setView("2d")}>2D</button>
                    <button type="button" className={view === "3d" ? "is-active" : ""}
                            onClick={() => setView("3d")}>3D</button>
                  </div>
                  {mode === "setup" && armedKind && (
                    <span className="badge badge-accent">
                      {tool === "draw-outline"
                        ? `Click corners for the ${armedKind.replace("_", " ")}`
                        : `Click to drop the ${armedKind.replace("_", " ")}`}
                    </span>
                  )}
                  {tool === "draw-outline" && draftOutline.length >= 3 && (
                    <button className="btn btn-sm btn-primary" type="button" onClick={finishOutline}>
                      Finish shape
                    </button>
                  )}
                  {(armedKind || tool !== "select") && (
                    <button className="btn btn-sm" type="button"
                            onClick={() => { setArmedKind(null); setTool("select");
                                             setDraftOutline([]); setPlacingCameraId(null);
                                             setAimingCameraId(null); }}>
                      Cancel
                    </button>
                  )}
                  {placingCameraId && (
                    <span className="badge badge-accent">Click where the camera is mounted</span>
                  )}
                  {aimingCameraId && (
                    <span className="badge badge-accent">Click what the camera should look at</span>
                  )}
                  <div className="grow" />
                  {(graph?.relationships.length ?? 0) > 0 && (
                    <label className="small check">
                      <input type="checkbox" checked={showLinks}
                             onChange={(e) => {
                               setShowLinks(e.target.checked);
                               if (!e.target.checked) setSelectedRelationshipId(null);
                             }} />
                      Camera links ({graph!.relationships.length})
                    </label>
                  )}
                  <span className={`badge ${scene.geometry_provenance === "measured"
                    ? "badge-ok" : "badge-warn"}`}>
                    <span aria-hidden="true">{scene.geometry_provenance === "measured" ? "✓" : "≈"}</span>
                    {scene.geometry_provenance === "measured" ? "Measured" : "Estimated"} geometry
                  </span>
                </div>
              </div>

              {view === "2d" ? (
                <SceneCanvas2D
                  scene={scene} cameras={mapCameras}
                  selectedObjectId={selectedObjectId} selectedCameraId={selectedCameraId}
                  tool={mode === "setup" ? tool : "select"}
                  onSelectObject={(id) => { setSelectedObjectId(id); setSelectedCameraId(null); }}
                  onSelectCamera={(id) => { setSelectedCameraId(id); setSelectedObjectId(null); }}
                  onMoveObject={mode === "setup" ? moveObject : undefined}
                  onRotateObject={mode === "setup" ? rotateObject : undefined}
                  onWorldClick={onWorldClick}
                  draftOutline={draftOutline}
                  aimingFrom={aimingCameraId
                    ? mapCameras.find((m) => m.camera_id === aimingCameraId)?.position ?? null
                    : null}
                  relationships={sceneLinks}
                  selectedRelationshipId={selectedRelationshipId}
                  onSelectRelationship={(id) => {
                    setSelectedRelationshipId(id);
                    setSelectedCameraId(null); setSelectedObjectId(null);
                  }}
                  readOnly={mode === "monitor"}
                />
              ) : (
                <Suspense fallback={
                  <div className="scene-stage" style={{ height: 640, display: "grid",
                                                        placeItems: "center" }}>
                    <Spinner label="Loading the 3D view…" />
                  </div>
                }>
                  <SceneView3D
                    scene={scene} cameras={mapCameras}
                    selectedObjectId={selectedObjectId} selectedCameraId={selectedCameraId}
                    onSelectObject={(id) => { setSelectedObjectId(id); setSelectedCameraId(null); }}
                    onSelectCamera={(id) => { setSelectedCameraId(id); setSelectedObjectId(null); }}
                  />
                </Suspense>
              )}
            </div>

            {selectedRelationship ? (
              <RelationshipPanel
                relationship={selectedRelationship} cameras={cameras}
                onClose={() => setSelectedRelationshipId(null)}
                onSelectCamera={(id) => {
                  setSelectedRelationshipId(null); setSelectedCameraId(id);
                }}
              />
            ) : (
            <RightPanel
              mode={mode}
              scene={scene}
              camera={selectedCamera}
              mapCamera={selectedMapCamera}
              object={selectedObject}
              workspaceId={workspaceId!}
              onChanged={refreshAll}
              onSceneChanged={refreshScene}
              onAim={(id) => { setAimingCameraId(id); setTool("aim-camera"); }}
              onPlace={(id) => { setPlacingCameraId(id); setTool("place-camera"); }}
              onError={setError}
              onSay={say}
            />
            )}
          </div>
        </>
      )}

      {pendingDrop && workspaceId && (
        <PlaceCameraModal
          camera={cameras.find((c) => c.id === pendingDrop.cameraId)!}
          workspaceId={workspaceId}
          scene={scene!}
          at={pendingDrop}
          onClose={() => setPendingDrop(null)}
          onPlaced={async (message) => { setPendingDrop(null); await refreshAll(); say(message); }}
          onError={setError}
        />
      )}

      {creatingScene && workspaceId && (
        <CreateSceneModal
          workspace={workspaces.find((w) => w.id === workspaceId)!}
          onClose={() => setCreatingScene(false)}
          onCreated={async () => { setCreatingScene(false); await load(); say("Scene created."); }}
          onError={setError}
        />
      )}
    </main>
  );
}

/* ---------------- left panel: scene tree, palette, camera tray ---------------- */

function LeftPanel({
  scene, sites, cameras, mapCameras, unplaced, mappedOnly, inWorkspace, palette, mode, armedKind,
  selectedObjectId, selectedCameraId, onArm, onSelectObject, onSelectCamera, onPlaceCamera,
}: {
  scene: FactoryScene;
  sites: Site[];
  cameras: Camera[];
  mapCameras: MapCamera[];
  unplaced: Camera[];
  mappedOnly: MapCamera[];
  inWorkspace: Camera[];
  palette: PaletteItem[];
  mode: Mode;
  armedKind: SceneObjectKind | null;
  selectedObjectId: number | null;
  selectedCameraId: number | null;
  onArm: (item: PaletteItem) => void;
  onSelectObject: (id: number) => void;
  onSelectCamera: (id: number) => void;
  onPlaceCamera: (id: number) => void;
}) {
  const [tab, setTab] = useState<"scene" | "cameras">("scene");
  void sites;

  const grouped = useMemo(() => {
    const out: Record<string, SceneObject[]> = {};
    scene.objects.forEach((o) => { (out[o.kind] ??= []).push(o); });
    return out;
  }, [scene.objects]);

  return (
    <aside className="workspace-panel">
      <header>
        <button className={`btn btn-sm ${tab === "scene" ? "btn-primary" : "btn-ghost"}`}
                type="button" onClick={() => setTab("scene")}>Scene</button>
        <button className={`btn btn-sm ${tab === "cameras" ? "btn-primary" : "btn-ghost"}`}
                type="button" onClick={() => setTab("cameras")}>
          Cameras {unplaced.length > 0 && <span className="badge badge-warn">{unplaced.length}</span>}
        </button>
      </header>

      <div className="body">
        {tab === "scene" ? (
          <>
            <div className="section">
              <h4>{scene.workspace_name}</h4>
              <div className="small muted">
                {scene.building_width_m ?? "?"} × {scene.building_length_m ?? "?"} m ·
                {" "}{scene.objects.length} object{scene.objects.length === 1 ? "" : "s"}
              </div>
            </div>

            {mode === "setup" && (
              <div className="section">
                <h4>Drop into the scene</h4>
                <div className="palette-grid">
                  {palette.map((item) => (
                    <button key={item.kind} type="button"
                            className={`palette-item${armedKind === item.kind ? " is-armed" : ""}`}
                            onClick={() => onArm(item)}
                            title={item.shape === "box"
                              ? `Click the scene to drop a ${item.label.toLowerCase()}`
                              : `Click corners to draw a ${item.label.toLowerCase()}`}>
                      <span className="palette-swatch" style={{ background: item.colour }} />
                      <span>{item.label}</span>
                      <span className="faint" style={{ fontSize: 10 }}>
                        {item.shape === "box"
                          ? `${item.default_width_m}×${item.default_depth_m} m`
                          : "draw"}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            <div className="section">
              <h4>In the scene</h4>
              {scene.objects.length === 0 ? (
                <p className="small muted">Nothing yet. Pick something above and click the scene.</p>
              ) : (
                Object.entries(grouped).map(([kind, objects]) => (
                  <div key={kind} style={{ marginBottom: 8 }}>
                    <div className="faint" style={{ fontSize: 11, marginBottom: 3 }}>
                      {kind.replace("_", " ")} ({objects.length})
                    </div>
                    {objects.map((o) => (
                      <button key={o.id} type="button"
                              className={`tree-row tree-indent${o.id === selectedObjectId ? " is-selected" : ""}`}
                              onClick={() => onSelectObject(o.id)}>
                        <span className="grow">{o.name}</span>
                        {o.provenance === "estimated"
                          ? <span className="faint" title="Estimated dimensions">≈</span>
                          : <span style={{ color: "var(--ok)" }} title="Measured">✓</span>}
                      </button>
                    ))}
                  </div>
                ))
              )}
            </div>
          </>
        ) : (
          <>
            {unplaced.length > 0 && (
              <div className="section">
                <h4>Unplaced cameras ({unplaced.length})</h4>
                <p className="small muted" style={{ marginTop: -4, marginBottom: 8 }}>
                  Pick one, then click the scene where it is mounted.
                </p>
                {unplaced.map((camera) => (
                  <CameraTrayCard
                    key={camera.id} camera={camera}
                    selected={camera.id === selectedCameraId}
                    onSelect={() => onSelectCamera(camera.id)}
                    onPlace={mode === "setup" ? () => onPlaceCamera(camera.id) : undefined}
                  />
                ))}
              </div>
            )}

            {mappedOnly.length > 0 && (
              <div className="section">
                <h4>Mapped, not placed ({mappedOnly.length})</h4>
                <p className="small muted" style={{ marginTop: -4, marginBottom: 8 }}>
                  These have a working floor mapping, so picture positions already
                  turn into real places. Placing them adds where the camera hangs,
                  which is what the 3D view and camera relationships need.
                </p>
                {mappedOnly.map((m) => (
                  <button key={m.camera_id} type="button"
                          className={`tree-row${m.camera_id === selectedCameraId ? " is-selected" : ""}`}
                          onClick={() => onSelectCamera(m.camera_id)}>
                    <span className="grow">{m.name}</span>
                    <span className="faint" style={{ fontSize: 10 }}>mapped</span>
                    {mode === "setup" && (
                      <span className="link-like" style={{ fontSize: 11 }}
                            onClick={(e) => { e.stopPropagation(); onPlaceCamera(m.camera_id); }}>
                        Place
                      </span>
                    )}
                  </button>
                ))}
              </div>
            )}

            <div className="section">
              <h4>Placed in this area ({mapCameras.filter((m) => m.position).length})</h4>
              {mapCameras.filter((m) => m.position).length === 0 ? (
                <p className="small muted">None placed yet.</p>
              ) : (
                mapCameras.filter((m) => m.position).map((m) => {
                  const camera = cameras.find((c) => c.id === m.camera_id);
                  return (
                    <button key={m.camera_id} type="button"
                            className={`tree-row${m.camera_id === selectedCameraId ? " is-selected" : ""}`}
                            onClick={() => onSelectCamera(m.camera_id)}>
                      <span className="grow">{m.name}</span>
                      {m.is_approximate && <span className="faint" title="Approximate position">≈</span>}
                      <span className="faint" style={{ fontSize: 10 }}>
                        {camera?.last_test_status === "online" ? "●" : "○"}
                      </span>
                    </button>
                  );
                })
              )}
            </div>

            <div className="section">
              <h4>All registered ({cameras.length})</h4>
              <p className="small muted" style={{ marginTop: -4 }}>
                {inWorkspace.length} assigned to this area.
              </p>
              <Link className="btn btn-sm btn-block" to="/cameras/new" style={{ marginTop: 8 }}>
                ＋ Register a camera
              </Link>
            </div>
          </>
        )}
      </div>
    </aside>
  );
}

function CameraTrayCard({ camera, selected, onSelect, onPlace }: {
  camera: Camera; selected: boolean; onSelect: () => void; onPlace?: () => void;
}) {
  return (
    <div className={`tray-card${selected ? " is-selected" : ""}`} onClick={onSelect}>
      <CameraThumb cameraId={camera.id} online={camera.last_test_status === "online"} />
      <div className="tray-meta">
        <strong>{camera.name}</strong>
        <div className="faint mono" style={{ fontSize: 10.5 }}>{camera.host}</div>
        <div className="row" style={{ gap: 4, marginTop: 4 }}>
          <span className={`badge ${camera.last_test_status === "online" ? "badge-ok"
            : camera.last_test_status === "untested" ? "badge-neutral" : "badge-danger"}`}>
            <span aria-hidden="true">
              {camera.last_test_status === "online" ? "●"
                : camera.last_test_status === "untested" ? "○" : "✕"}
            </span>
            {camera.last_test_status === "online" ? "Connected"
              : camera.last_test_status === "untested" ? "Not tested" : "Failed"}
          </span>
        </div>
        {camera.location.floor && (
          <div className="faint" style={{ fontSize: 10.5, marginTop: 3 }}>
            {camera.location.building} · {camera.location.floor}
          </div>
        )}
        {onPlace && (
          <button className="btn btn-sm btn-block" type="button" style={{ marginTop: 6 }}
                  onClick={(e) => { e.stopPropagation(); onPlace(); }}>
            Place in scene
          </button>
        )}
      </div>
    </div>
  );
}

/** A small live thumbnail, fetched through the authenticated snapshot endpoint. */
function CameraThumb({ cameraId, online }: { cameraId: number; online: boolean }) {
  const [url, setUrl] = useState<string | null>(null);
  const objectUrl = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    if (!online) return;
    (async () => {
      try {
        const token = getToken();
        const res = await fetch(`/api/cameras/${cameraId}/snapshot`, {
          headers: token ? { Authorization: `Bearer ${token}` } : undefined });
        if (!res.ok || cancelled) return;
        const blob = await res.blob();
        if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
        const next = URL.createObjectURL(blob);
        objectUrl.current = next;
        if (!cancelled) setUrl(next);
      } catch { /* a thumbnail is a nicety, not a requirement */ }
    })();
    return () => {
      cancelled = true;
      if (objectUrl.current) { URL.revokeObjectURL(objectUrl.current); objectUrl.current = null; }
    };
  }, [cameraId, online]);

  if (url) return <img className="tray-thumb" src={url} alt="" />;
  return <div className="tray-thumb">{online ? "…" : "offline"}</div>;
}

/* ---------------- right panel: selection details and actions ---------------- */

function RightPanel({
  mode, scene, camera, mapCamera, object, workspaceId,
  onChanged, onSceneChanged, onAim, onPlace, onError, onSay,
}: {
  mode: Mode;
  scene: FactoryScene;
  camera: Camera | null;
  mapCamera: MapCamera | null;
  object: SceneObject | null;
  workspaceId: number;
  onChanged: () => Promise<void>;
  onSceneChanged: () => Promise<void>;
  onAim: (cameraId: number) => void;
  onPlace: (cameraId: number) => void;
  onError: (m: string) => void;
  onSay: (m: string) => void;
}) {
  const [revision, setRevision] = useState<CalibrationRevision | null>(null);
  const [functions, setFunctions] = useState<CameraFunction[]>([]);
  const [catalogue, setCatalogue] = useState<FunctionCatalogueEntry[]>([]);
  const [addingFunction, setAddingFunction] = useState(false);
  const [editingShape, setEditingShape] = useState<CameraFunction | null>(null);

  useEffect(() => {
    api.functionCatalogue().then((c) => setCatalogue(c.functions)).catch(() => setCatalogue([]));
  }, []);

  useEffect(() => {
    if (!camera) { setRevision(null); setFunctions([]); return; }
    api.listRevisions(camera.id)
      .then((rs) => setRevision(rs.find((r) => r.is_active) ?? null)).catch(() => setRevision(null));
    api.listFunctions(camera.id).then(setFunctions).catch(() => setFunctions([]));
  }, [camera]);

  if (object) {
    return <ObjectPanel scene={scene} object={object} readOnly={mode === "monitor"}
                        onChanged={onSceneChanged} onError={onError} />;
  }

  if (!camera) {
    return (
      <aside className="workspace-panel">
        <header>Details</header>
        <div className="body">
          <p className="small muted">
            Select a camera or an object — in the tree, the tray or the scene — and its details
            appear here.
          </p>
          <div className="section">
            <h4>This scene</h4>
            <dl className="kv small">
              <dt>Area</dt><dd>{scene.workspace_name}</dd>
              <dt>Objects</dt><dd>{scene.objects.length}</dd>
              <dt>Geometry</dt>
              <dd>
                {scene.geometry_provenance === "measured" ? "All measured" : "Estimated"}
                <div className="faint">
                  {scene.geometry_provenance === "measured"
                    ? "Every object has measured dimensions."
                    : "Drawn rather than surveyed. Fine for organising cameras; not survey data."}
                </div>
              </dd>
              <dt>Published</dt>
              <dd>{scene.published_at
                ? new Date(scene.published_at).toLocaleString(undefined,
                    { dateStyle: "medium", timeStyle: "short" })
                : "Never"}</dd>
            </dl>
          </div>
          <Link className="btn btn-sm btn-block" to={`/readiness?workspace=${workspaceId}`}>
            See what is ready
          </Link>
        </div>
      </aside>
    );
  }

  const placed = !!mapCamera?.position;
  const coveragePoly = mapCamera?.floor_polygon ?? null;
  const mappedCoverage = !placed && (coveragePoly?.length ?? 0) >= 3;
  const coverageArea = polygonArea(coveragePoly);

  return (
    <aside className="workspace-panel">
      <header>{camera.name}</header>
      <div className="body">
        <div className="section">
          <CameraStatusRow connection={camera.last_test_status} revision={revision} compact />
        </div>

        <div className="section">
          <h4>Live view</h4>
          <PreviewPanel cameraId={camera.id} compact />
        </div>

        {mode === "setup" && (
          <div className="section">
            <h4>Placement</h4>
            {placed ? (
              <>
                <dl className="kv small">
                  <dt>Position</dt>
                  <dd className="mono">
                    {mapCamera!.position!.x.toFixed(2)}, {mapCamera!.position!.y.toFixed(2)} m
                  </dd>
                  <dt>Height</dt>
                  <dd className="mono">{mapCamera!.position!.z.toFixed(2)} m</dd>
                  <dt>Facing</dt>
                  <dd>{mapCamera!.map_heading_deg != null
                    ? `${mapCamera!.map_heading_deg.toFixed(0)}° on the map`
                    : "straight down"}</dd>
                  <dt>Field of view</dt>
                  <dd>
                    {mapCamera!.horizontal_fov_deg
                      ? <>{mapCamera!.horizontal_fov_deg.toFixed(0)}°
                          <div className="faint">from measured lens calibration</div></>
                      : mapCamera!.approx_hfov_deg
                        ? <>≈{mapCamera!.approx_hfov_deg}°
                            <div className="faint">
                              illustration only — does not touch the camera's zoom
                            </div></>
                        : <span className="faint">not set</span>}
                  </dd>
                </dl>
                {mapCamera!.is_approximate && (
                  <p className="hint">
                    Placed by hand, so the position is approximate. Align the live view to get
                    real geometry.
                  </p>
                )}
                <div className="row" style={{ marginTop: 8 }}>
                  <button className="btn btn-sm" type="button" onClick={() => onAim(camera.id)}>
                    Aim here…
                  </button>
                  <Link className="btn btn-sm btn-primary"
                        to={`/align?camera=${camera.id}&workspace=${workspaceId}`}>
                    Align live view
                  </Link>
                </div>
              </>
            ) : mappedCoverage ? (
              <>
                <p className="small muted">
                  Mapped, but not placed. A floor mapping already turns a position in this
                  camera's picture into a real place on the floor, and it covers about{" "}
                  {coverageArea.toFixed(0)} m² of floor.
                </p>
                <p className="hint">
                  Where the camera itself hangs is still unknown, so the 3D view and camera
                  relationships have nothing to draw. Place it to add that — the mapping is
                  kept and is not overwritten.
                </p>
                <div className="row" style={{ marginTop: 8 }}>
                  <button className="btn btn-sm btn-primary" type="button"
                          onClick={() => onPlace(camera.id)}>
                    Place in scene…
                  </button>
                  <Link className="btn btn-sm"
                        to={`/align?camera=${camera.id}&workspace=${workspaceId}`}>
                    Align live view
                  </Link>
                </div>
              </>
            ) : (
              <>
                <p className="small muted">Not placed in the scene yet.</p>
                <p className="hint">
                  Open the Cameras tab on the left and choose “Place in scene”, then click where
                  it is mounted.
                </p>
              </>
            )}
          </div>
        )}

        <div className="section">
          <div className="row" style={{ marginBottom: 6 }}>
            <h4 className="grow" style={{ margin: 0 }}>What should this camera do?</h4>
            {mode === "setup" && (
              <button className="btn btn-sm" type="button" onClick={() => setAddingFunction(true)}>＋</button>
            )}
          </div>
          {functions.length === 0 ? (
            <p className="small muted">
              Nothing configured. Counting and zone watching work on the picture and need no
              calibration at all.
            </p>
          ) : (
            functions.map((f) => (
              <div key={f.id} className="tray-card" style={{ cursor: "default" }}>
                <div className="tray-meta">
                  <strong>{f.name}</strong>
                  <div className="faint" style={{ fontSize: 10.5 }}>{f.label}</div>
                  <div className="row" style={{ gap: 4, marginTop: 5 }}>
                    <span className={`badge ${
                      f.status.state === "running" ? "badge-ok"
                        : f.status.state === "blocked" ? "badge-danger" : "badge-neutral"}`}>
                      {f.status.summary}
                    </span>
                    <span className="badge badge-neutral">{f.space === "image" ? "picture" : "floor"}</span>
                  </div>
                  <div className="faint" style={{ fontSize: 10.5, marginTop: 4 }}>
                    {f.status.detail}
                  </div>
                  {mode === "setup" && (
                    <div className="row" style={{ gap: 4, marginTop: 4 }}>
                      {shapeFor(f.kind) && (
                        <button className="btn btn-sm btn-ghost" type="button"
                                onClick={() => setEditingShape(f)}>Edit shape</button>
                      )}
                      <button className="btn btn-sm btn-ghost" type="button"
                              onClick={async () => {
                                await api.deleteFunction(f.id);
                                setFunctions(await api.listFunctions(camera.id));
                              }}>Remove</button>
                    </div>
                  )}
                </div>
              </div>
            ))
          )}
        </div>

        <Advanced title="Advanced: pose and conventions"
                  note="The numbers behind the placement. You never need to type these.">
          {revision ? (
            <dl className="kv">
              <dt>Method</dt><dd>{revision.method}</dd>
              <dt>Status</dt><dd>{revision.status}</dd>
              <dt>Position</dt>
              <dd className="mono">
                {revision.position
                  ? `${revision.position.x.toFixed(3)}, ${revision.position.y.toFixed(3)}, ${revision.position.z.toFixed(3)}`
                  : "no pose (floor mapping only)"}
              </dd>
              {revision.rpy_deg && (
                <>
                  <dt>Roll / pitch / yaw</dt>
                  <dd className="mono">
                    {revision.rpy_deg.roll.toFixed(1)}, {revision.rpy_deg.pitch.toFixed(1)},
                    {" "}{revision.rpy_deg.yaw.toFixed(1)}
                  </dd>
                </>
              )}
              <dt>Revision</dt><dd>{revision.revision_number}</dd>
            </dl>
          ) : <p className="small muted">No calibration revision yet.</p>}
          <Link className="btn btn-sm btn-block" to={`/cameras/${camera.id}/calibration`}
                style={{ marginTop: 8 }}>
            Open full calibration
          </Link>
        </Advanced>
      </div>

      {addingFunction && (
        <FunctionModal
          camera={camera} catalogue={catalogue} revision={revision}
          onClose={() => setAddingFunction(false)}
          onSaved={async () => {
            setAddingFunction(false);
            setFunctions(await api.listFunctions(camera.id));
            await onChanged();
            onSay("Function configured.");
          }}
        />
      )}

      {editingShape && (
        <ShapeModal
          camera={camera} fn={editingShape}
          onClose={() => setEditingShape(null)}
          onSaved={async () => {
            setEditingShape(null);
            setFunctions(await api.listFunctions(camera.id));
            onSay("Shape saved.");
          }}
        />
      )}
    </aside>
  );
}

function ObjectPanel({ scene, object, readOnly, onChanged, onError }: {
  scene: FactoryScene; object: SceneObject; readOnly: boolean;
  onChanged: () => Promise<void>; onError: (m: string) => void;
}) {
  const [form, setForm] = useState({
    name: object.name,
    width_m: String(object.width_m), depth_m: String(object.depth_m),
    height_m: String(object.height_m),
    x: String(object.x), y: String(object.y), rotation_deg: String(object.rotation_deg),
    provenance: object.provenance,
  });
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setForm({
      name: object.name,
      width_m: String(object.width_m), depth_m: String(object.depth_m),
      height_m: String(object.height_m),
      x: String(object.x), y: String(object.y), rotation_deg: String(object.rotation_deg),
      provenance: object.provenance,
    });
  }, [object]);

  const set = (k: keyof typeof form, v: string) => setForm((f) => ({ ...f, [k]: v }));

  const save = async () => {
    setBusy(true);
    try {
      await api.updateSceneObject(scene.id, object.id, {
        name: form.name,
        width_m: Number(form.width_m), depth_m: Number(form.depth_m),
        height_m: Number(form.height_m),
        x: Number(form.x), y: Number(form.y), rotation_deg: Number(form.rotation_deg),
        provenance: form.provenance,
      });
      await onChanged();
    } catch (e) {
      onError(e instanceof ApiError ? e.message : "Could not save that object.");
    } finally { setBusy(false); }
  };

  return (
    <aside className="workspace-panel">
      <header>{object.kind.replace("_", " ")}</header>
      <div className="body">
        <div className="field">
          <label htmlFor="obj-name">Name</label>
          <input id="obj-name" type="text" value={form.name} disabled={readOnly}
                 onChange={(e) => set("name", e.target.value)} />
        </div>

        {!object.points && (
          <>
            <div className="field-row">
              <div className="field">
                <label htmlFor="obj-w">Width (m)</label>
                <input id="obj-w" type="number" step="0.1" value={form.width_m} disabled={readOnly}
                       onChange={(e) => set("width_m", e.target.value)} />
              </div>
              <div className="field">
                <label htmlFor="obj-d">Depth (m)</label>
                <input id="obj-d" type="number" step="0.1" value={form.depth_m} disabled={readOnly}
                       onChange={(e) => set("depth_m", e.target.value)} />
              </div>
            </div>
            <div className="field-row">
              <div className="field">
                <label htmlFor="obj-h">Height (m)</label>
                <input id="obj-h" type="number" step="0.1" value={form.height_m} disabled={readOnly}
                       onChange={(e) => set("height_m", e.target.value)} />
              </div>
              <div className="field">
                <label htmlFor="obj-r">Rotation (°)</label>
                <input id="obj-r" type="number" step="5" value={form.rotation_deg} disabled={readOnly}
                       onChange={(e) => set("rotation_deg", e.target.value)} />
              </div>
            </div>
            <div className="field-row">
              <div className="field">
                <label htmlFor="obj-x">X (m)</label>
                <input id="obj-x" type="number" step="0.1" value={form.x} disabled={readOnly}
                       onChange={(e) => set("x", e.target.value)} />
              </div>
              <div className="field">
                <label htmlFor="obj-y">Y (m)</label>
                <input id="obj-y" type="number" step="0.1" value={form.y} disabled={readOnly}
                       onChange={(e) => set("y", e.target.value)} />
              </div>
            </div>
          </>
        )}

        <div className="field">
          <span className="field-label">Where did these dimensions come from?</span>
          <div className="radio-row" style={{ flexDirection: "column" }}>
            <label className={`radio-card${form.provenance === "estimated" ? " is-selected" : ""}`}>
              <input type="radio" name="prov" checked={form.provenance === "estimated"}
                     disabled={readOnly} onChange={() => set("provenance", "estimated")} />
              <span><strong>Estimated</strong>
                <span>Drawn or paced out. Fine for organising cameras.</span></span>
            </label>
            <label className={`radio-card${form.provenance === "measured" ? " is-selected" : ""}`}>
              <input type="radio" name="prov" checked={form.provenance === "measured"}
                     disabled={readOnly} onChange={() => set("provenance", "measured")} />
              <span><strong>Measured</strong>
                <span>Taken with a tape or laser. Only these may be used as calibration landmarks.</span></span>
            </label>
          </div>
        </div>

        {!readOnly && (
          <div className="row">
            <button className="btn btn-sm btn-primary" type="button" disabled={busy} onClick={save}>
              {busy ? "Saving…" : "Save"}
            </button>
            <button className="btn btn-sm btn-danger" type="button"
                    onClick={async () => {
                      await api.deleteSceneObject(scene.id, object.id);
                      await onChanged();
                    }}>Delete</button>
          </div>
        )}
      </div>
    </aside>
  );
}

/* ---------------- modals ---------------- */

function PlaceCameraModal({ camera, workspaceId, scene, at, onClose, onPlaced, onError }: {
  camera: Camera;
  workspaceId: number;
  scene: FactoryScene;
  at: { x: number; y: number };
  onClose: () => void;
  onPlaced: (message: string) => void;
  onError: (m: string) => void;
}) {
  const [mount, setMount] = useState<MountType>("wall");
  const [height, setHeight] = useState("3.5");
  const [hfov, setHfov] = useState("78");
  const [range, setRange] = useState("15");
  const [busy, setBusy] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [result, setResult] = useState<PlacementResult | null>(null);

  const submit = async () => {
    setBusy(true);
    try {
      // Aim into the room to begin with; the installer refines it with
      // "Aim here…" straight afterwards. A fixed diagonal offset would point a
      // camera on the east or north wall straight out of the building, so aim
      // at the middle of the area instead, and only fall back to the diagonal
      // when the camera was dropped on that very spot.
      const [target_x, target_y] = defaultAim(scene.grid, at);
      const placed = await api.placeCamera(camera.id, {
        workspace_id: workspaceId,
        x: at.x, y: at.y, height_m: Number(height),
        target_x, target_y,
        mount_type: mount,
        illustrative_hfov_deg: Number(hfov),
        illustrative_range_m: Number(range),
      });
      setResult(placed);
    } catch (e) {
      onError(e instanceof ApiError ? e.message : "Could not place the camera.");
      setBusy(false);
    }
  };

  if (result) {
    return (
      <Modal title={`${camera.name} placed`} onClose={() => onPlaced("Camera placed.")}
             footer={<button className="btn btn-primary" type="button"
                             onClick={() => onPlaced("Camera placed. Now aim it.")}>
                       {result.activated ? "Done — now aim it" : "Keep the solved geometry"}
                     </button>}>
        <Notice tone="warn" title="Approximate position">
          {result.aim.note}
        </Notice>
        <dl className="kv">
          <dt>Mounted at</dt>
          <dd className="mono">
            {result.position.x.toFixed(2)}, {result.position.y.toFixed(2)} m,
            {" "}{result.position.z.toFixed(2)} m up
          </dd>
          <dt>Tilt</dt>
          <dd>{result.aim.tilt_below_horizontal_deg}° below horizontal</dd>
          <dt>Field of view</dt>
          <dd>
            {result.fov_source === "measured_intrinsics"
              ? "From this camera's measured lens calibration."
              : result.fov_source === "illustrative"
                ? "Illustration only — it draws the cone and does not touch the camera's zoom."
                : "Not set."}
          </dd>
        </dl>
        {result.warnings.map((w, i) => <Notice key={i} tone="warn" title="Note">{w}</Notice>)}

        {!result.activated && (
          <div className="card card-pad" style={{ marginTop: 12 }}>
            <strong>Which geometry should this camera use?</strong>
            <p className="small muted" style={{ marginTop: 5 }}>
              The solved mapping is measured and stays in use. This placement is a hand
              estimate, and until it is the active one the camera will keep showing as
              mapped rather than placed, and aiming it will change nothing.
            </p>
            <p className="hint">
              Switching is safe and reversible: nothing is deleted, and every revision stays
              on the camera's calibration page.
            </p>
            <button className="btn btn-sm" type="button" disabled={switching}
                    onClick={async () => {
                      setSwitching(true);
                      try {
                        await api.activateRevision(camera.id, result.revision_id);
                        onPlaced("Now using the hand placement. The solved mapping is kept.");
                      } catch (e) {
                        onError(e instanceof ApiError
                          ? e.message : "Could not switch to this placement.");
                        setSwitching(false);
                      }
                    }}>
              {switching ? "Switching…" : "Use this placement instead"}
            </button>
          </div>
        )}
      </Modal>
    );
  }

  return (
    <Modal title={`Place ${camera.name}`} onClose={onClose}
           footer={<>
             <button className="btn" type="button" onClick={onClose}>Cancel</button>
             <button className="btn btn-primary" type="button" disabled={busy} onClick={submit}>
               {busy ? "Placing…" : "Place camera"}
             </button>
           </>}>
      <Notice tone="info" title="Where is it mounted?">
        You clicked {at.x.toFixed(2)}, {at.y.toFixed(2)} m. Tell us how high it is and what it is
        fixed to — no angles needed; you will aim it by clicking in the scene.
      </Notice>

      <div className="field">
        <span className="field-label">Fixed to</span>
        <div className="radio-row">
          {(["wall", "ceiling", "column", "free"] as MountType[]).map((m) => (
            <label key={m} className={`radio-card${mount === m ? " is-selected" : ""}`}
                   style={{ minWidth: 100 }}>
              <input type="radio" name="mount" checked={mount === m}
                     onChange={() => setMount(m)} />
              <span><strong>{m === "free" ? "Free-standing" : m[0].toUpperCase() + m.slice(1)}</strong></span>
            </label>
          ))}
        </div>
      </div>

      <div className="field-row">
        <div className="field">
          <label htmlFor="place-h">How high is it? (metres)</label>
          <input id="place-h" type="number" step="0.1" value={height}
                 onChange={(e) => setHeight(e.target.value)} />
          <span className="hint">Floor to the camera body.</span>
        </div>
        <div className="field">
          <label htmlFor="place-range">How far does it usefully see? (metres)</label>
          <input id="place-range" type="number" step="1" value={range}
                 onChange={(e) => setRange(e.target.value)} />
        </div>
      </div>

      <Advanced title="Advanced: illustrative field of view">
        <div className="field">
          <label htmlFor="place-fov">Horizontal field of view (°)</label>
          <input id="place-fov" type="number" step="1" value={hfov}
                 onChange={(e) => setHfov(e.target.value)} />
          <span className="hint">
            Used only to draw the cone on the scene. It is not measured lens data, and changing it
            does not operate the camera's own zoom. If the camera has a real lens calibration,
            that is used instead.
          </span>
        </div>
      </Advanced>
    </Modal>
  );
}

function CreateSceneModal({ workspace, onClose, onCreated, onError }: {
  workspace: Workspace; onClose: () => void; onCreated: () => void; onError: (m: string) => void;
}) {
  const [route, setRoute] = useState<"draw" | "plan" | "model">("draw");
  const [name, setName] = useState(`${workspace.name} scene`);
  const [width, setWidth] = useState(String(workspace.width_m ?? 30));
  const [length, setLength] = useState(String(workspace.length_m ?? 20));
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    try {
      const scene = await api.createScene({
        workspace_id: workspace.id, name: name.trim(),
        building_width_m: Number(width), building_length_m: Number(length),
      });
      // Starting walls make the scene legible immediately; they are estimated
      // until someone measures them.
      if (route === "draw") {
        const w = Number(width), l = Number(length);
        await api.replaceSceneObjects(scene.id, [
          { kind: "wall", name: "South wall", points: [[0, 0], [w, 0]] },
          { kind: "wall", name: "East wall", points: [[w, 0], [w, l]] },
          { kind: "wall", name: "North wall", points: [[w, l], [0, l]] },
          { kind: "wall", name: "West wall", points: [[0, l], [0, 0]] },
        ]);
      }
      onCreated();
    } catch (e) {
      onError(e instanceof ApiError ? e.message : "Could not create the scene.");
      setBusy(false);
    }
  };

  return (
    <Modal title="Create the factory scene" wide onClose={onClose}
           footer={<>
             <button className="btn" type="button" onClick={onClose}>Cancel</button>
             <button className="btn btn-primary" type="button" disabled={busy || !name.trim()}
                     onClick={submit}>{busy ? "Creating…" : "Create scene"}</button>
           </>}>
      <div className="radio-row" style={{ flexDirection: "column" }}>
        <label className={`radio-card${route === "draw" ? " is-selected" : ""}`}>
          <input type="radio" name="route" checked={route === "draw"}
                 onChange={() => setRoute("draw")} />
          <span>
            <strong>Draw a simple factory</strong>
            <span>
              Start with four walls at the size you give, then drag machines, racks and
              walkways in. Nothing to upload.
            </span>
          </span>
        </label>
        <label className={`radio-card${route === "plan" ? " is-selected" : ""}`}>
          <input type="radio" name="route" checked={route === "plan"}
                 onChange={() => setRoute("plan")} />
          <span>
            <strong>Start from a floor plan</strong>
            <span>
              Creates an empty scene; upload a PNG or JPEG afterwards, set its scale from two
              measured points, and draw objects over it. The image is a backdrop — nothing is
              read out of it automatically.
            </span>
          </span>
        </label>
        <label className={`radio-card${route === "model" ? " is-selected" : ""}`}>
          <input type="radio" name="route" checked={route === "model"}
                 onChange={() => setRoute("model")} />
          <span>
            <strong>Import a 3D model</strong>
            <span>
              Creates an empty scene; upload a GLB or glTF afterwards and confirm its units and
              orientation. It is shown as reference geometry only — no editable objects are
              extracted from a mesh, and other CAD formats are not supported.
            </span>
          </span>
        </label>
      </div>

      <div className="field" style={{ marginTop: 14 }}>
        <label htmlFor="scene-name">Scene name</label>
        <input id="scene-name" type="text" value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="field-row">
        <div className="field">
          <label htmlFor="scene-w">Building width (m)</label>
          <input id="scene-w" type="number" step="0.5" value={width}
                 onChange={(e) => setWidth(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="scene-l">Building length (m)</label>
          <input id="scene-l" type="number" step="0.5" value={length}
                 onChange={(e) => setLength(e.target.value)} />
        </div>
      </div>
      <p className="hint">
        These start as estimates. Mark individual objects as measured once you have actually
        measured them — only measured geometry counts as survey data.
      </p>
    </Modal>
  );
}

function FunctionModal({ camera, catalogue, revision, onClose, onSaved }: {
  camera: Camera;
  catalogue: FunctionCatalogueEntry[];
  revision: CalibrationRevision | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [kind, setKind] = useState<FunctionCatalogueEntry | null>(null);
  const [name, setName] = useState("");
  const [direction, setDirection] = useState("a_to_b");
  const [geometry, setGeometry] = useState<number[][]>([]);
  const [frame, setFrame] = useState<{ w: number; h: number } | null>(null);
  const [ppe, setPpe] = useState<string[]>(["hi_vis"]);
  const [workstation, setWorkstation] = useState("");
  const [busy, setBusy] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  const hasMapping = !!(revision?.has_homography
    && revision.status !== "needs_recalibration");

  const submit = async () => {
    if (!kind) return;
    setBusy(true); setLocalError(null);
    try {
      // The shape is whatever was drawn on this camera's own picture. Where no
      // frame could be fetched, fall back to a starting shape on the frame size
      // the camera last reported, so the function can still be created and
      // adjusted later rather than being blocked by an offline camera.
      const config: Record<string, unknown> = {};
      const w = frame?.w ?? 1920;
      const h = frame?.h ?? 1080;
      const drawn = geometry.length ? geometry : null;
      if (kind.kind === "people_counting" || kind.kind === "product_counting") {
        config.line = drawn ?? [[w * 0.2, h * 0.65], [w * 0.8, h * 0.65]];
        config.direction = direction;
      }
      if (["restricted_zone", "ppe_monitoring", "workstation_occupancy"].includes(kind.kind)) {
        config.polygon = drawn ?? [[w * 0.3, h * 0.4], [w * 0.7, h * 0.4],
                                   [w * 0.7, h * 0.8], [w * 0.3, h * 0.8]];
      }
      if (kind.kind === "ppe_monitoring") config.required_ppe = ppe;
      if (kind.kind === "workstation_occupancy") config.workstation_name = workstation || "Workstation";

      await api.addFunction(camera.id, {
        kind: kind.kind,
        name: name.trim() || kind.label,
        space: kind.space,
        image_width: kind.space === "image" ? w : null,
        image_height: kind.space === "image" ? h : null,
        config,
      });
      onSaved();
    } catch (e) {
      setLocalError(e instanceof ApiError ? e.message : "Could not save that.");
      setBusy(false);
    }
  };

  return (
    <Modal title="What should this camera do?" wide onClose={onClose}
           footer={<>
             <button className="btn" type="button" onClick={onClose}>Cancel</button>
             <button className="btn btn-primary" type="button" disabled={!kind || busy}
                     onClick={submit}>{busy ? "Saving…" : "Add function"}</button>
           </>}>
      {localError && <Notice tone="danger" title="Could not save">{localError}</Notice>}

      <Notice tone="warn" title="No detection service is connected">
        Settings are stored and exported so a processing service can pick them up later. Nothing
        in this deployment is analysing video, and the system will not pretend otherwise.
      </Notice>

      {kind ? (
        // Once a kind is chosen the drawing surface is the point of this modal,
        // so the full catalogue collapses out of the way rather than pushing it
        // off the bottom of the screen.
        <div className="row chosen-function">
          <span className="grow">
            <strong>{kind.label}</strong>
            <span className="faint" style={{ display: "block", fontSize: 11 }}>
              {kind.configuration}
            </span>
          </span>
          <button className="btn btn-sm" type="button" onClick={() => setKind(null)}>
            Choose a different one
          </button>
        </div>
      ) : (
      <div className="stack" style={{ gap: 8 }}>
        {catalogue.map((entry) => {
          const blocked = entry.requires_floor_mapping && !hasMapping;
          return (
            <label key={entry.kind} className="radio-card"
                   style={{ minWidth: 0, opacity: blocked ? 0.75 : 1 }}>
              <input type="radio" name="fnkind" checked={false}
                     onChange={() => { setKind(entry); setName(entry.label); setGeometry([]); }} />
              <span>
                <strong>{entry.label}</strong>
                <span>{entry.configuration}</span>
                <span style={{ display: "block", marginTop: 5 }}>
                  <span className="badge badge-neutral">
                    {entry.space === "image" ? "works on the picture" : "needs floor geometry"}
                  </span>
                  {blocked && (
                    <span className="badge badge-warn" style={{ marginLeft: 5 }}>
                      needs a floor mapping first
                    </span>
                  )}
                </span>
              </span>
            </label>
          );
        })}
      </div>
      )}

      {kind && (
        <>
          <div className="field" style={{ marginTop: 14 }}>
            <label htmlFor="fn-name">Name this</label>
            <input id="fn-name" type="text" value={name} onChange={(e) => setName(e.target.value)} />
          </div>

          {shapeFor(kind.kind) && (
            <div className="field">
              <span className="field-label">
                {shapeFor(kind.kind) === "line"
                  ? "Where should the counting line go?"
                  : "Which part of the picture is the zone?"}
              </span>
              <PictureGeometryEditor
                cameraId={camera.id}
                shape={shapeFor(kind.kind)!}
                points={geometry}
                onChange={setGeometry}
                onFrame={(w, h) => setFrame({ w, h })}
                height={360}
              />
              {geometry.length === 0 && (
                <span className="hint">
                  Nothing drawn yet. Saving now places a starting shape across the middle of the
                  picture, which you can move afterwards.
                </span>
              )}
            </div>
          )}

          {(kind.kind === "people_counting" || kind.kind === "product_counting") && (
            <div className="field">
              <label htmlFor="fn-dir">Which way should it count?</label>
              <select id="fn-dir" value={direction} onChange={(e) => setDirection(e.target.value)}>
                <option value="a_to_b">One way across the line</option>
                <option value="b_to_a">The other way</option>
                <option value="both">Both directions</option>
              </select>
              <span className="hint">
                A is the side the short dashed spur points away from, B the side it points
                towards, so “one way” means A to B.
              </span>
            </div>
          )}

          {kind.kind === "ppe_monitoring" && (
            <div className="field">
              <span className="field-label">Required PPE</span>
              <div className="row">
                {["hi_vis", "helmet", "gloves", "eye_protection", "ear_protection"].map((item) => (
                  <label key={item} className="row small" style={{ gap: 5 }}>
                    <input type="checkbox" checked={ppe.includes(item)}
                           onChange={(e) => setPpe((p) =>
                             e.target.checked ? [...p, item] : p.filter((x) => x !== item))} />
                    {item.replace("_", " ")}
                  </label>
                ))}
              </div>
            </div>
          )}

          {kind.kind === "workstation_occupancy" && (
            <div className="field">
              <label htmlFor="fn-ws">Which workstation?</label>
              <input id="fn-ws" type="text" value={workstation} placeholder="Assembly bench 2"
                     onChange={(e) => setWorkstation(e.target.value)} />
            </div>
          )}

          {kind.requires_floor_mapping && !hasMapping && (
            <Notice tone="warn" title="This one needs floor geometry">
              It can be configured now, but it stays blocked until this camera has a validated
              floor mapping — without one there is no way to turn a picture position into a place
              on the factory floor.
            </Notice>
          )}
        </>
      )}
    </Modal>
  );
}


/** Shoelace area of a closed floor polygon, in square metres. */
function polygonArea(poly: number[][] | null): number {
  if (!poly || poly.length < 3) return 0;
  let sum = 0;
  for (let i = 0; i < poly.length; i += 1) {
    const [x1, y1] = poly[i];
    const [x2, y2] = poly[(i + 1) % poly.length];
    sum += x1 * y2 - x2 * y1;
  }
  return Math.abs(sum) / 2;
}


/** What a function's geometry looks like on the picture, if it has any. */
function shapeFor(kind: string): GeometryShape | null {
  if (kind === "people_counting" || kind === "product_counting") return "line";
  if (kind === "restricted_zone" || kind === "ppe_monitoring"
      || kind === "workstation_occupancy") return "polygon";
  return null;
}


/** Redraw an existing function's line or zone on the camera picture. */
function ShapeModal({ camera, fn, onClose, onSaved }: {
  camera: Camera; fn: CameraFunction; onClose: () => void; onSaved: () => void;
}) {
  const shape = shapeFor(fn.kind)!;
  const initial = (shape === "line" ? fn.config.line : fn.config.polygon) as number[][] | undefined;
  const [points, setPoints] = useState<number[][]>(initial ?? []);
  const [frame, setFrame] = useState<{ w: number; h: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  const enough = shape === "line" ? points.length === 2 : points.length >= 3;
  // A shape drawn against a different frame size than the one on screen would
  // be silently mis-scaled, so say so rather than saving something wrong.
  const sizeChanged = !!(frame && fn.image_width && fn.image_height
    && (frame.w !== fn.image_width || frame.h !== fn.image_height));

  const save = async () => {
    setBusy(true); setLocalError(null);
    try {
      const config = { ...fn.config, [shape === "line" ? "line" : "polygon"]: points };
      await api.updateFunction(fn.id, {
        config,
        image_width: frame?.w ?? fn.image_width,
        image_height: frame?.h ?? fn.image_height,
      });
      onSaved();
    } catch (e) {
      setLocalError(e instanceof ApiError ? e.message : "Could not save that shape.");
      setBusy(false);
    }
  };

  return (
    <Modal title={`${fn.name} — draw it on the picture`} wide onClose={onClose}
           footer={<>
             <button className="btn" type="button" onClick={onClose}>Cancel</button>
             <button className="btn btn-primary" type="button" disabled={!enough || busy}
                     onClick={save}>{busy ? "Saving…" : "Save shape"}</button>
           </>}>
      {localError && <Notice tone="danger" title="Could not save">{localError}</Notice>}
      {sizeChanged && (
        <Notice tone="warn" title="The camera frame size has changed">
          This shape was drawn on {fn.image_width}×{fn.image_height} px and the camera is now
          sending {frame!.w}×{frame!.h}. Redraw it, or it will not sit where you expect.
        </Notice>
      )}
      <PictureGeometryEditor
        cameraId={camera.id} shape={shape} points={points}
        onChange={setPoints} onFrame={(w, h) => setFrame({ w, h })} height={430}
      />
    </Modal>
  );
}


/**
 * Two cameras that share a piece of floor, side by side.
 *
 * The point is to see the same place in both pictures at once: if a link says
 * they overlap, the same forklift should be visible in both, and if it is not,
 * the link is wrong however good the arithmetic looked.
 */
function RelationshipPanel({ relationship, cameras, onClose, onSelectCamera }: {
  relationship: Relationship;
  cameras: Camera[];
  onClose: () => void;
  onSelectCamera: (id: number) => void;
}) {
  const a = cameras.find((c) => c.id === relationship.camera_a_id) ?? null;
  const b = cameras.find((c) => c.id === relationship.camera_b_id) ?? null;
  const verified = relationship.verification === "verified";

  return (
    <aside className="workspace-panel">
      <header>
        <span className="grow">Camera link</span>
        <button className="btn btn-sm btn-ghost" type="button" onClick={onClose}>×</button>
      </header>
      <div className="body">
        <div className="section">
          <div className="row" style={{ gap: 5, flexWrap: "wrap" }}>
            <span className={`badge ${verified ? "badge-ok" : "badge-warn"}`}>
              {verified ? "✓ Verified by a person" : "Not verified"}
            </span>
            <span className="badge badge-neutral">{relationship.kind.replace("_", " ")}</span>
            {relationship.suggested_by_geometry && (
              <span className="badge badge-neutral">suggested by geometry</span>
            )}
          </div>
          {!verified && (
            <p className="hint" style={{ marginTop: 8 }}>
              Geometry proposed this link; nobody has confirmed it. Watch both pictures while
              someone walks across the shared area, then record what you saw on the Connections
              page.
            </p>
          )}
        </div>

        <div className="section">
          <dl className="kv small">
            <dt>Cameras</dt>
            <dd>
              <button className="link-like" type="button"
                      onClick={() => onSelectCamera(relationship.camera_a_id)}>
                {relationship.camera_a_name ?? `#${relationship.camera_a_id}`}
              </button>
              {" ↔ "}
              <button className="link-like" type="button"
                      onClick={() => onSelectCamera(relationship.camera_b_id)}>
                {relationship.camera_b_name ?? `#${relationship.camera_b_id}`}
              </button>
            </dd>
            {relationship.overlap_area_m2 != null && (
              <>
                <dt>Shared floor</dt>
                <dd className="mono">{relationship.overlap_area_m2.toFixed(1)} m²</dd>
              </>
            )}
            {relationship.min_travel_seconds != null && (
              <>
                <dt>Walking time</dt>
                <dd className="mono">
                  {relationship.min_travel_seconds}–{relationship.max_travel_seconds ?? "?"} s
                </dd>
              </>
            )}
          </dl>
          {relationship.warnings.length > 0 && (
            <ul className="small" style={{ color: "var(--warn)", paddingLeft: 18, marginTop: 6 }}>
              {relationship.warnings.map((w) => <li key={w}>{w}</li>)}
            </ul>
          )}
        </div>

        <div className="section">
          <h4>Both pictures at once</h4>
          <p className="small muted" style={{ marginTop: -4, marginBottom: 8 }}>
            Walk the shared area and watch for the same person in both.
          </p>
          {a && (
            <div style={{ marginBottom: 10 }}>
              <div className="faint" style={{ fontSize: 11, marginBottom: 3 }}>{a.name}</div>
              <PreviewPanel cameraId={a.id} compact />
            </div>
          )}
          {b && (
            <div>
              <div className="faint" style={{ fontSize: 11, marginBottom: 3 }}>{b.name}</div>
              <PreviewPanel cameraId={b.id} compact />
            </div>
          )}
        </div>

        <div className="section">
          <Link className="btn btn-sm btn-block" to="/relationships">
            Record what you saw
          </Link>
        </div>
      </div>
    </aside>
  );
}


/** Where a freshly dropped camera should look: the middle of the area, unless
 *  it was dropped there, in which case anywhere is as good as anywhere else. */
function defaultAim(grid: FactoryScene["grid"], at: { x: number; y: number }): [number, number] {
  const cx = (grid.min_x + grid.max_x) / 2;
  const cy = (grid.min_y + grid.max_y) / 2;
  return Math.hypot(cx - at.x, cy - at.y) < 1 ? [at.x + 6, at.y + 6] : [cx, cy];
}
