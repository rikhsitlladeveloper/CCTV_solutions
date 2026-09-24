import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Circle, Group, Image as KonvaImage, Layer, Line, Rect, Stage, Text, Wedge } from "react-konva";
import type Konva from "konva";
import { mediaUrl } from "../lib/api";
import type { FactoryScene, SceneObject } from "../lib/sceneTypes";
import type { MapCamera } from "../lib/calibrationTypes";

/** World metres -> screen pixels. World +Y is drawn up the screen; this is the
 *  only place that flip happens, and the 3D view uses the same world numbers. */
export interface View { scale: number; tx: number; ty: number }

export function toScreen(v: View, x: number, y: number): [number, number] {
  return [x * v.scale + v.tx, -y * v.scale + v.ty];
}
export function toWorld(v: View, sx: number, sy: number): [number, number] {
  return [(sx - v.tx) / v.scale, -(sy - v.ty) / v.scale];
}

const STATUS_COLOUR: Record<string, string> = {
  validated: "#0f7b4f",
  calibrated_unvalidated: "#1d4ed8",
  approximate: "#9a6207",
  needs_recalibration: "#b0281f",
  unconfigured: "#5a6779",
};

export type SceneTool = "select" | "place-camera" | "aim-camera" | "draw-outline";

/** A measured floor point drawn on the scene so it can be matched to the same
 *  point in a camera picture. Colour and number are shared with the picture
 *  side, which is the whole trick: the installer matches colours, not codes. */
/** A link between two cameras, drawn where it actually is on the floor. */
export interface SceneRelationship {
  id: number;
  kind: string;
  camera_a_id: number;
  camera_b_id: number;
  label: string;
  polygon: number[][] | null;
  verified: boolean;
}

export interface SceneLandmark {
  id: number;
  x: number;
  y: number;
  index: number;
  colour: string;
  code: string;
  matched: boolean;
  holdout?: boolean;
}

interface Props {
  scene: FactoryScene;
  cameras: MapCamera[];
  selectedObjectId: number | null;
  selectedCameraId: number | null;
  tool: SceneTool;
  onSelectObject: (id: number | null) => void;
  onSelectCamera: (id: number | null) => void;
  onMoveObject?: (id: number, x: number, y: number) => void;
  onRotateObject?: (id: number, rotationDeg: number) => void;
  onWorldClick?: (x: number, y: number) => void;
  /** Vertices collected while drawing an outline. */
  draftOutline?: number[][];
  /** A camera being aimed: draw a rubber band from it to the cursor. */
  aimingFrom?: { x: number; y: number } | null;
  showFloorPlan?: boolean;
  height?: number;
  readOnly?: boolean;
  /** Measured floor points to draw and pick. */
  landmarks?: SceneLandmark[];
  selectedLandmarkId?: number | null;
  onSelectLandmark?: (id: number) => void;
  /** Camera-to-camera links to draw over the scene. */
  relationships?: SceneRelationship[];
  selectedRelationshipId?: number | null;
  onSelectRelationship?: (id: number) => void;
}

export default function SceneCanvas2D({
  scene, cameras, selectedObjectId, selectedCameraId, tool,
  onSelectObject, onSelectCamera, onMoveObject, onRotateObject, onWorldClick,
  draftOutline = [], aimingFrom = null, showFloorPlan = true, height = 640, readOnly,
  landmarks = [], selectedLandmarkId = null, onSelectLandmark,
  relationships = [], selectedRelationshipId = null, onSelectRelationship,
}: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 900, height });
  const [view, setView] = useState<View>({ scale: 24, tx: 80, ty: 560 });
  const [planImage, setPlanImage] = useState<HTMLImageElement | null>(null);
  const [cursor, setCursor] = useState<[number, number] | null>(null);

  const grid = scene.grid;

  const fit = useCallback(() => {
    const el = wrapRef.current;
    if (!el) return;
    const w = el.clientWidth || 900;
    const h = el.clientHeight || height;
    const pad = 44;
    const spanX = Math.max(1, grid.max_x - grid.min_x);
    const spanY = Math.max(1, grid.max_y - grid.min_y);
    const scale = Math.min((w - pad * 2) / spanX, (h - pad * 2) / spanY);
    setSize({ width: w, height: h });
    setView({ scale, tx: pad - grid.min_x * scale, ty: h - pad + grid.min_y * scale });
  }, [grid.min_x, grid.max_x, grid.min_y, grid.max_y, height]);

  useEffect(() => { fit(); }, [fit]);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => setSize((p) => {
      const w = el.clientWidth, h = el.clientHeight;
      return p.width === w && p.height === h ? p : { width: w, height: h };
    }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const plan = scene.assets.find((a) => a.kind === "floor_plan" && a.metres_per_pixel);
  useEffect(() => {
    if (!showFloorPlan || !plan) { setPlanImage(null); return; }
    const img = new window.Image();
    img.crossOrigin = "anonymous";
    img.src = mediaUrl(plan.url);
    img.onload = () => setPlanImage(img);
    return () => { img.onload = null; };
  }, [plan, showFloorPlan]);

  const zoomBy = (factor: number, cx?: number, cy?: number) => {
    setView((v) => {
      const next = Math.min(300, Math.max(2, v.scale * factor));
      const ratio = next / v.scale;
      const ax = cx ?? size.width / 2;
      const ay = cy ?? size.height / 2;
      return { scale: next, tx: ax - (ax - v.tx) * ratio, ty: ay - (ay - v.ty) * ratio };
    });
  };

  const fitCameras = () => {
    // A camera counts as "somewhere" if it either hangs at a known point or
    // covers a known patch of floor; framing only the former hides the rest.
    const xs: number[] = [];
    const ys: number[] = [];
    cameras.forEach((c) => {
      if (c.position) { xs.push(c.position.x); ys.push(c.position.y); }
      c.floor_polygon?.forEach(([x, y]) => { xs.push(x); ys.push(y); });
    });
    if (!xs.length) { fit(); return; }
    const pad = 44;
    const minX = Math.min(...xs) - 5, maxX = Math.max(...xs) + 5;
    const minY = Math.min(...ys) - 5, maxY = Math.max(...ys) + 5;
    const scale = Math.min((size.width - pad * 2) / Math.max(1, maxX - minX),
                           (size.height - pad * 2) / Math.max(1, maxY - minY));
    setView({ scale, tx: pad - minX * scale, ty: size.height - pad + minY * scale });
  };

  const gridLines = useMemo(() => {
    const spacing = grid.spacing_m;
    const px = spacing * view.scale;
    const step = px < 14 ? Math.ceil(14 / px) * spacing : spacing;
    const out: Array<{ pts: number[]; major: boolean; label?: string; lx?: number; ly?: number }> = [];
    for (let x = Math.ceil(grid.min_x / step) * step; x <= grid.max_x; x += step) {
      const [x1, y1] = toScreen(view, x, grid.min_y);
      const [x2, y2] = toScreen(view, x, grid.max_y);
      const major = Math.abs(x % (step * 5)) < 1e-6;
      out.push({ pts: [x1, y1, x2, y2], major, label: major ? `${x}` : undefined,
                 lx: x1 + 3, ly: y1 - 14 });
    }
    for (let y = Math.ceil(grid.min_y / step) * step; y <= grid.max_y; y += step) {
      const [x1, y1] = toScreen(view, grid.min_x, y);
      const [x2, y2] = toScreen(view, grid.max_x, y);
      const major = Math.abs(y % (step * 5)) < 1e-6;
      out.push({ pts: [x1, y1, x2, y2], major, label: major ? `${y}` : undefined,
                 lx: x1 + 4, ly: y1 - 13 });
    }
    return out;
  }, [grid, view]);

  const handleClick = (e: Konva.KonvaEventObject<MouseEvent>) => {
    const stage = e.target.getStage();
    const pointer = stage?.getPointerPosition();
    if (!pointer) return;
    const hitBackground = e.target === stage || e.target.name() === "backdrop";
    if (tool !== "select") {
      const [wx, wy] = toWorld(view, pointer.x, pointer.y);
      onWorldClick?.(wx, wy);
      return;
    }
    if (hitBackground) { onSelectObject(null); onSelectCamera(null); }
  };

  const [ox, oy] = toScreen(view, 0, 0);
  const axis = Math.min(60, 2.5 * view.scale);

  // With a tool armed the whole canvas is the target. Shapes stop listening, or
  // their own click handlers swallow the click and placing a camera on top of a
  // machine — exactly where cameras usually point — silently does nothing.
  const picking = tool !== "select";

  return (
    <div ref={wrapRef} className="scene-stage" style={{ height }}>
      <div className="plan-toolbar">
        <button className="btn btn-sm" type="button" onClick={() => zoomBy(1.25)} title="Zoom in">+</button>
        <button className="btn btn-sm" type="button" onClick={() => zoomBy(1 / 1.25)} title="Zoom out">−</button>
        <button className="btn btn-sm" type="button" onClick={fit}>Fit scene</button>
        <button className="btn btn-sm" type="button" onClick={fitCameras}>Fit cameras</button>
        <span className="slider-val" style={{ padding: "0 6px", alignSelf: "center" }}>
          {view.scale.toFixed(0)} px/m
        </span>
      </div>

      <Stage
        width={size.width} height={size.height}
        draggable={tool === "select"}
        onClick={handleClick}
        onMouseMove={(e) => {
          const p = e.target.getStage()?.getPointerPosition();
          if (p) setCursor(toWorld(view, p.x, p.y));
        }}
        onWheel={(e) => {
          e.evt.preventDefault();
          const p = e.target.getStage()?.getPointerPosition();
          zoomBy(e.evt.deltaY < 0 ? 1.12 : 1 / 1.12, p?.x, p?.y);
        }}
        onDragEnd={(e) => {
          setView((v) => ({ ...v, tx: v.tx + e.target.x(), ty: v.ty + e.target.y() }));
          e.target.position({ x: 0, y: 0 });
        }}
        style={{ cursor: tool === "select" ? "default" : "crosshair" }}
      >
        <Layer listening={false}>
          <Rect name="backdrop" x={0} y={0} width={size.width} height={size.height} fill="#eef1f6" />
          {planImage && plan?.metres_per_pixel && (() => {
            const [px, py] = toScreen(view, plan.origin_x ?? 0, plan.origin_y ?? 0);
            const w = (plan.width_px ?? 0) * plan.metres_per_pixel * view.scale;
            const h = (plan.height_px ?? 0) * plan.metres_per_pixel * view.scale;
            return <KonvaImage image={planImage} x={px} y={py - h} width={w} height={h}
                               rotation={-(plan.rotation_deg ?? 0)} opacity={0.4} />;
          })()}
          {gridLines.map((l, i) => (
            <Line key={i} points={l.pts} stroke={l.major ? "#c3cbd8" : "#dfe4ec"}
                  strokeWidth={l.major ? 1.1 : 0.6} />
          ))}
          {gridLines.filter((l) => l.label).map((l, i) => (
            <Text key={`t${i}`} x={l.lx} y={l.ly} text={`${l.label} m`} fontSize={10} fill="#8593a6" />
          ))}
          <Line points={[ox, oy, ox + axis, oy]} stroke="#b0281f" strokeWidth={2.5} />
          <Line points={[ox, oy, ox, oy - axis]} stroke="#0f7b4f" strokeWidth={2.5} />
          <Circle x={ox} y={oy} radius={3} fill="#16202f" />
        </Layer>

        {/* Scene objects */}
        <Layer listening={!picking}>
          {scene.objects.map((obj) => (
            <SceneObjectShape
              key={obj.id} obj={obj} view={view}
              selected={obj.id === selectedObjectId}
              draggable={!readOnly && tool === "select" && !!onMoveObject}
              onSelect={() => { onSelectObject(obj.id); onSelectCamera(null); }}
              onMove={(x, y) => onMoveObject?.(obj.id, x, y)}
              onRotate={(deg) => onRotateObject?.(obj.id, deg)}
            />
          ))}

          {draftOutline.length > 0 && (() => {
            const pts = draftOutline.flatMap(([x, y]) => toScreen(view, x, y));
            return (
              <Group listening={false}>
                <Line points={pts} stroke="#1d4ed8" strokeWidth={2} dash={[6, 4]}
                      closed={draftOutline.length > 2} fill="rgba(29,78,216,0.12)" />
                {draftOutline.map(([x, y], i) => {
                  const [sx, sy] = toScreen(view, x, y);
                  return <Circle key={i} x={sx} y={sy} radius={4} fill="#1d4ed8"
                                 stroke="#fff" strokeWidth={1.5} />;
                })}
              </Group>
            );
          })()}
        </Layer>

        {/* Cameras, landmarks and camera links */}
        <Layer listening={!picking}>
          {cameras.map((camera) => {
            const selected = camera.camera_id === selectedCameraId;
            const colour = STATUS_COLOUR[camera.calibration_status] ?? "#5a6779";
            const select = (e: Konva.KonvaEventObject<MouseEvent>) => {
              e.cancelBubble = true;
              onSelectCamera(camera.camera_id);
              onSelectObject(null);
            };

            // A camera mapped by floor homography knows which patch of floor its
            // measured points cover, but not where it hangs. Draw the patch where
            // it is: dropping the camera from the scene would make a working
            // mapping look like nothing had been set up at all.
            if (!camera.position) {
              const poly = camera.floor_polygon;
              if (!poly || poly.length < 3) return null;
              const cxw = poly.reduce((a, [x]) => a + x, 0) / poly.length;
              const cyw = poly.reduce((a, [, y]) => a + y, 0) / poly.length;
              const [lx, ly] = toScreen(view, cxw, cyw);
              return (
                <Group key={camera.camera_id} onClick={select}>
                  <Line points={poly.flatMap(([x, y]) => toScreen(view, x, y))} closed
                        fill={selected ? "rgba(13,148,136,0.22)" : "rgba(13,148,136,0.10)"}
                        stroke="#0d9488" strokeWidth={selected ? 2 : 1.2} dash={[7, 5]} />
                  <Text x={lx - 70} y={ly - 15} width={140} align="center" text={camera.name}
                        fontSize={11} fontStyle="bold" fill="#0f766e" />
                  <Text x={lx - 70} y={ly + 1} width={140} align="center"
                        text="mapped floor area · not placed" fontSize={9.5} fill="#0f766e" />
                </Group>
              );
            }

            const [sx, sy] = toScreen(view, camera.position.x, camera.position.y);
            const fov = camera.horizontal_fov_deg ?? camera.approx_hfov_deg;
            const range = (camera.approx_range_m ?? 10) * view.scale;
            const heading = camera.map_heading_deg;

            return (
              <Group key={camera.camera_id} onClick={select}>
                {camera.floor_polygon ? (
                  <Line points={camera.floor_polygon.flatMap(([x, y]) => toScreen(view, x, y))}
                        closed fill={selected ? "rgba(29,78,216,0.20)" : "rgba(29,78,216,0.09)"}
                        stroke="rgba(29,78,216,0.4)" strokeWidth={1}
                        dash={camera.area_is_mapped_coverage ? [7, 5] : undefined} />
                ) : heading !== null && fov ? (
                  <Wedge x={sx} y={sy} radius={range} angle={fov}
                         rotation={heading - 90 - fov / 2}
                         fill={selected ? "rgba(29,78,216,0.20)" : "rgba(29,78,216,0.10)"}
                         stroke="rgba(29,78,216,0.4)" strokeWidth={1} />
                ) : null}

                {heading !== null && (() => {
                  const rad = (heading * Math.PI) / 180;
                  const len = Math.max(24, Math.min(range * 0.45, 64));
                  return <Line points={[sx, sy, sx + Math.sin(rad) * len, sy - Math.cos(rad) * len]}
                               stroke={colour} strokeWidth={2.5} />;
                })()}

                <Circle x={sx} y={sy} radius={selected ? 9 : 7} fill={colour}
                        stroke="#fff" strokeWidth={2.5}
                        shadowBlur={selected ? 8 : 0} shadowColor="#1d4ed8" />
                {camera.is_approximate && (
                  <Circle x={sx} y={sy} radius={13} stroke={colour} strokeWidth={1} dash={[3, 3]} />
                )}
                <Text x={sx + 12} y={sy - 16} text={camera.name} fontSize={11}
                      fontStyle="bold" fill="#16202f" />
                {camera.is_approximate && (
                  <Text x={sx + 12} y={sy - 3} text="approximate position"
                        fontSize={9.5} fill="#9a6207" />
                )}
              </Group>
            );
          })}

          {relationships.map((rel) => {
            const a = anchorOf(cameras.find((c) => c.camera_id === rel.camera_a_id));
            const b = anchorOf(cameras.find((c) => c.camera_id === rel.camera_b_id));
            const selected = rel.id === selectedRelationshipId;
            // Verified links are stated in green; anything a person has not
            // confirmed stays amber, so a guess never reads as a fact.
            const colour = rel.verified ? "#0f7b4f" : "#9a6207";
            const pick = (e: Konva.KonvaEventObject<MouseEvent>) => {
              e.cancelBubble = true; onSelectRelationship?.(rel.id);
            };
            return (
              <Group key={`rel-${rel.id}`} onClick={pick}>
                {rel.polygon && rel.polygon.length >= 3 && (
                  <Line points={rel.polygon.flatMap(([x, y]) => toScreen(view, x, y))} closed
                        fill={selected ? `${colour}44` : `${colour}22`}
                        stroke={colour} strokeWidth={selected ? 2.5 : 1.5} dash={[6, 4]} />
                )}
                {a && b && (() => {
                  const [ax, ay] = toScreen(view, a[0], a[1]);
                  const [bx, by] = toScreen(view, b[0], b[1]);
                  return (
                    <>
                      <Line points={[ax, ay, bx, by]} stroke={colour}
                            strokeWidth={selected ? 3 : 1.6}
                            dash={rel.verified ? undefined : [8, 5]} opacity={0.85} />
                      <Text x={(ax + bx) / 2 - 70} y={(ay + by) / 2 - 7} width={140}
                            align="center" text={rel.label} fontSize={10}
                            fontStyle={selected ? "bold" : "normal"} fill={colour}
                            listening={false} />
                    </>
                  );
                })()}
              </Group>
            );
          })}

          {landmarks.map((lm) => {
            const [sx, sy] = toScreen(view, lm.x, lm.y);
            const selected = lm.id === selectedLandmarkId;
            const r = selected ? 11 : 8;
            return (
              <Group key={`lm-${lm.id}`}
                     onClick={(e) => { e.cancelBubble = true; onSelectLandmark?.(lm.id); }}>
                {selected && <Circle x={sx} y={sy} radius={r + 5} stroke={lm.colour}
                                     strokeWidth={2} dash={[3, 3]} />}
                <Circle x={sx} y={sy} radius={r}
                        fill={lm.matched ? lm.colour : "#ffffff"}
                        stroke={lm.colour} strokeWidth={2.5} />
                <Text x={sx - r} y={sy - 5} width={r * 2} align="center"
                      text={String(lm.index)} fontSize={11} fontStyle="bold"
                      fill={lm.matched ? "#ffffff" : lm.colour} listening={false} />
                <Text x={sx + r + 3} y={sy - 5} text={lm.code + (lm.holdout ? " ·held out" : "")}
                      fontSize={10} fill="#3b475a" listening={false} />
              </Group>
            );
          })}

          {aimingFrom && cursor && (() => {
            const [ax, ay] = toScreen(view, aimingFrom.x, aimingFrom.y);
            const [cx, cy] = toScreen(view, cursor[0], cursor[1]);
            return (
              <Group listening={false}>
                <Line points={[ax, ay, cx, cy]} stroke="#1d4ed8" strokeWidth={2} dash={[6, 4]} />
                <Circle x={cx} y={cy} radius={5} stroke="#1d4ed8" strokeWidth={2} />
              </Group>
            );
          })()}
        </Layer>
      </Stage>

      {cursor && (
        <div className="scene-cursor mono">
          {cursor[0].toFixed(2)}, {cursor[1].toFixed(2)} m
        </div>
      )}
    </div>
  );
}

function SceneObjectShape({ obj, view, selected, draggable, onSelect, onMove, onRotate }: {
  obj: SceneObject;
  view: View;
  selected: boolean;
  draggable: boolean;
  onSelect: () => void;
  onMove: (x: number, y: number) => void;
  onRotate: (deg: number) => void;
}) {
  const [sx, sy] = toScreen(view, obj.x, obj.y);
  const outline = obj.footprint.flatMap(([x, y]) => toScreen(view, x, y));
  const isOutline = obj.kind === "walkway" || obj.kind === "restricted_area";
  const isRun = (obj.kind === "wall" || obj.kind === "conveyor") && obj.points;
  const estimated = obj.provenance === "estimated";

  return (
    <Group draggable={draggable}
           onClick={(e) => { e.cancelBubble = true; onSelect(); }}
           onTap={(e) => { e.cancelBubble = true; onSelect(); }}
           onDragEnd={(e) => {
             const node = e.target;
             const [wx, wy] = toWorld(view, sx + node.x(), sy + node.y());
             node.position({ x: 0, y: 0 });
             onMove(wx, wy);
           }}>
      {isRun ? (
        <Line points={obj.points!.flatMap(([x, y]) => toScreen(view, x, y))}
              stroke={obj.colour} strokeWidth={selected ? 7 : 5} lineCap="round"
              dash={estimated ? [10, 5] : undefined} />
      ) : (
        <Line points={outline} closed
              fill={`${obj.colour}${isOutline ? "22" : "33"}`}
              stroke={obj.colour} strokeWidth={selected ? 2.5 : 1.5}
              dash={estimated ? [7, 4] : undefined} />
      )}

      {view.scale > 6 && (
        <Text x={sx - 45} y={sy - 7} width={90} align="center" text={obj.name}
              fontSize={10.5} fill="#16202f"
              shadowColor="#fff" shadowBlur={3} shadowOpacity={1} />
      )}

      {selected && !isRun && (() => {
        // Rotation handle, one metre clear of the footprint.
        const reach = Math.max(obj.width_m, obj.depth_m) / 2 + 1.0;
        const rad = (obj.rotation_deg * Math.PI) / 180;
        const [hx, hy] = toScreen(view, obj.x + Math.cos(rad) * reach,
                                  obj.y + Math.sin(rad) * reach);
        return (
          <Group>
            <Line points={[sx, sy, hx, hy]} stroke="#1d4ed8" strokeWidth={1.2} dash={[4, 3]} />
            <Circle x={hx} y={hy} radius={6} fill="#fff" stroke="#1d4ed8" strokeWidth={2.5}
                    draggable
                    onDragMove={(e) => {
                      const node = e.target;
                      const [wx, wy] = toWorld(view, node.x(), node.y());
                      const deg = (Math.atan2(wy - obj.y, wx - obj.x) * 180) / Math.PI;
                      onRotate(Math.round(deg));
                    }}
                    onDragEnd={(e) => { e.cancelBubble = true; }} />
          </Group>
        );
      })()}
    </Group>
  );
}


/** Where to hang a camera-to-camera link: the mount point if it is known, and
 *  otherwise the middle of the floor the camera actually covers. */
function anchorOf(camera: MapCamera | undefined): [number, number] | null {
  if (!camera) return null;
  if (camera.position) return [camera.position.x, camera.position.y];
  const poly = camera.floor_polygon;
  if (!poly || poly.length < 3) return null;
  return [poly.reduce((a, [x]) => a + x, 0) / poly.length,
          poly.reduce((a, [, y]) => a + y, 0) / poly.length];
}
