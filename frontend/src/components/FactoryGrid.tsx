import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Circle, Group, Image as KonvaImage, Layer, Line, Rect, Stage, Text, Wedge } from "react-konva";
import type Konva from "konva";
import { mediaUrl } from "../lib/api";
import type { FactoryMap, MapCamera, ReferencePoint } from "../lib/calibrationTypes";

/** World -> screen. World +Y is drawn up the screen, so screen Y is negated.
 *  This is the only place that flip happens. */
export interface View { scale: number; tx: number; ty: number }

export function worldToScreen(view: View, x: number, y: number): [number, number] {
  return [x * view.scale + view.tx, -y * view.scale + view.ty];
}
export function screenToWorld(view: View, sx: number, sy: number): [number, number] {
  return [(sx - view.tx) / view.scale, -(sy - view.ty) / view.scale];
}

/** Heading (0 = world +Y, clockwise) -> unit vector in world XY. */
export function headingToWorldVector(headingDeg: number): [number, number] {
  const rad = (headingDeg * Math.PI) / 180;
  return [Math.sin(rad), Math.cos(rad)];
}

const STATUS_COLOUR: Record<string, string> = {
  validated: "#0f7b4f",
  calibrated_unvalidated: "#1d4ed8",
  approximate: "#9a6207",
  needs_recalibration: "#b0281f",
  unconfigured: "#5a6779",
};

interface Props {
  map: FactoryMap;
  selectedCameraId: number | null;
  onSelectCamera?: (id: number | null) => void;
  onMoveCamera?: (id: number, x: number, y: number) => void;
  /** Click anywhere on the grid (world metres). */
  onWorldClick?: (x: number, y: number) => void;
  showFootprints?: boolean;
  showReferencePoints?: boolean;
  showFloorPlan?: boolean;
  extraMarkers?: Array<{ x: number; y: number; label: string; colour?: string }>;
  height?: number;
  draggable?: boolean;
}

export default function FactoryGrid({
  map, selectedCameraId, onSelectCamera, onMoveCamera, onWorldClick,
  showFootprints = true, showReferencePoints = true, showFloorPlan = true,
  extraMarkers = [], height = 620, draggable = false,
}: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 900, height });
  const [view, setView] = useState<View>({ scale: 20, tx: 100, ty: 500 });
  const [planImage, setPlanImage] = useState<HTMLImageElement | null>(null);
  const cs = map.coordinate_system;

  /* Fit the configured grid extent into the viewport. */
  const fitGrid = useCallback(() => {
    const el = wrapRef.current;
    if (!el) return;
    const width = el.clientWidth || 900;
    const h = el.clientHeight || height;
    const pad = 48;
    const spanX = Math.max(1, cs.grid_max_x - cs.grid_min_x);
    const spanY = Math.max(1, cs.grid_max_y - cs.grid_min_y);
    const scale = Math.min((width - pad * 2) / spanX, (h - pad * 2) / spanY);
    setSize({ width, height: h });
    setView({
      scale,
      tx: pad - cs.grid_min_x * scale,
      ty: h - pad + cs.grid_min_y * scale,
    });
  }, [cs.grid_min_x, cs.grid_max_x, cs.grid_min_y, cs.grid_max_y, height]);

  const fitCameras = useCallback(() => {
    const el = wrapRef.current;
    const placed = map.cameras.filter((c) => c.position);
    if (!el || placed.length === 0) { fitGrid(); return; }
    const xs = placed.map((c) => c.position!.x);
    const ys = placed.map((c) => c.position!.y);
    const minX = Math.min(...xs) - 4, maxX = Math.max(...xs) + 4;
    const minY = Math.min(...ys) - 4, maxY = Math.max(...ys) + 4;
    const width = el.clientWidth || 900;
    const h = el.clientHeight || height;
    const pad = 48;
    const scale = Math.min((width - pad * 2) / Math.max(1, maxX - minX),
                           (h - pad * 2) / Math.max(1, maxY - minY));
    setView({ scale, tx: pad - minX * scale, ty: h - pad + minY * scale });
  }, [map.cameras, fitGrid, height]);

  useEffect(() => { fitGrid(); }, [fitGrid]);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      setSize((prev) => {
        const width = el.clientWidth, h = el.clientHeight;
        return prev.width === width && prev.height === h ? prev : { width, height: h };
      });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  /* Floor-plan backdrop, if one has been aligned into this world frame. */
  useEffect(() => {
    if (!showFloorPlan || !map.floor_plan) { setPlanImage(null); return; }
    const img = new window.Image();
    img.crossOrigin = "anonymous";
    img.src = mediaUrl(map.floor_plan.image_url);
    img.onload = () => setPlanImage(img);
    return () => { img.onload = null; };
  }, [map.floor_plan, showFloorPlan]);

  const zoomBy = (factor: number, cx?: number, cy?: number) => {
    setView((v) => {
      const next = Math.min(400, Math.max(1.5, v.scale * factor));
      const ratio = next / v.scale;
      const ax = cx ?? size.width / 2;
      const ay = cy ?? size.height / 2;
      return { scale: next, tx: ax - (ax - v.tx) * ratio, ty: ay - (ay - v.ty) * ratio };
    });
  };

  const onWheel = (e: Konva.KonvaEventObject<WheelEvent>) => {
    e.evt.preventDefault();
    const stage = e.target.getStage();
    const pointer = stage?.getPointerPosition();
    zoomBy(e.evt.deltaY < 0 ? 1.12 : 1 / 1.12, pointer?.x, pointer?.y);
  };

  /* Grid lines, thinned out so labels stay readable when zoomed out. */
  const gridLines = useMemo(() => {
    const spacing = cs.grid_spacing_m;
    const pixelsPerCell = spacing * view.scale;
    const step = pixelsPerCell < 14 ? Math.ceil(14 / pixelsPerCell) * spacing : spacing;
    const lines: Array<{ points: number[]; major: boolean; label?: string; lx?: number; ly?: number }> = [];

    for (let x = Math.ceil(cs.grid_min_x / step) * step; x <= cs.grid_max_x; x += step) {
      const [sx1, sy1] = worldToScreen(view, x, cs.grid_min_y);
      const [sx2, sy2] = worldToScreen(view, x, cs.grid_max_y);
      const major = Math.abs(x % (step * 5)) < 1e-6;
      lines.push({ points: [sx1, sy1, sx2, sy2], major,
                   label: major ? `${x}` : undefined, lx: sx1 + 3, ly: sy1 - 15 });
    }
    for (let y = Math.ceil(cs.grid_min_y / step) * step; y <= cs.grid_max_y; y += step) {
      const [sx1, sy1] = worldToScreen(view, cs.grid_min_x, y);
      const [sx2, sy2] = worldToScreen(view, cs.grid_max_x, y);
      const major = Math.abs(y % (step * 5)) < 1e-6;
      lines.push({ points: [sx1, sy1, sx2, sy2], major,
                   label: major ? `${y}` : undefined, lx: sx1 + 4, ly: sy1 - 14 });
    }
    return lines;
  }, [cs, view]);

  const [originX, originY] = worldToScreen(view, 0, 0);
  const axisLength = Math.min(70, 3 * view.scale);

  const handleStageClick = (e: Konva.KonvaEventObject<MouseEvent>) => {
    if (e.target !== e.target.getStage() && e.target.name() !== "backdrop") return;
    const pointer = e.target.getStage()?.getPointerPosition();
    if (!pointer) return;
    const [wx, wy] = screenToWorld(view, pointer.x, pointer.y);
    if (onWorldClick) onWorldClick(wx, wy);
    else onSelectCamera?.(null);
  };

  return (
    <div ref={wrapRef} className="plan-stage" style={{ height }}>
      <div className="plan-toolbar">
        <button className="btn btn-sm" type="button" onClick={() => zoomBy(1.25)} title="Zoom in">+</button>
        <button className="btn btn-sm" type="button" onClick={() => zoomBy(1 / 1.25)} title="Zoom out">−</button>
        <button className="btn btn-sm" type="button" onClick={fitGrid}>Fit grid</button>
        <button className="btn btn-sm" type="button" onClick={fitCameras}>Fit cameras</button>
        <span className="slider-val" style={{ padding: "0 6px", alignSelf: "center" }}>
          {view.scale.toFixed(0)} px/m
        </span>
      </div>

      <Stage width={size.width} height={size.height} onWheel={onWheel} onClick={handleStageClick}
             draggable
             onDragEnd={(e) => {
               setView((v) => ({ ...v, tx: v.tx + e.target.x(), ty: v.ty + e.target.y() }));
               e.target.position({ x: 0, y: 0 });
             }}>
        <Layer listening={false}>
          <Rect name="backdrop" x={0} y={0} width={size.width} height={size.height} fill="#eef1f6" />
          {planImage && map.floor_plan && (() => {
            const fp = map.floor_plan;
            const [ox, oy] = worldToScreen(view, fp.origin_x, fp.origin_y);
            return (
              <KonvaImage
                image={planImage}
                x={ox}
                y={oy - fp.height_px * fp.metres_per_pixel * view.scale}
                width={fp.width_px * fp.metres_per_pixel * view.scale}
                height={fp.height_px * fp.metres_per_pixel * view.scale}
                rotation={-(fp.rotation_deg || 0)}
                opacity={0.45}
              />
            );
          })()}
          {gridLines.map((line, i) => (
            <Line key={i} points={line.points} stroke={line.major ? "#c3cbd8" : "#dfe4ec"}
                  strokeWidth={line.major ? 1.2 : 0.7} />
          ))}
          {gridLines.filter((l) => l.label).map((line, i) => (
            <Text key={`t${i}`} x={line.lx} y={line.ly} text={`${line.label} m`}
                  fontSize={10} fill="#8593a6" />
          ))}
          {/* Origin axes: X red, Y green, matching the 3D view. */}
          <Line points={[originX, originY, originX + axisLength, originY]} stroke="#b0281f" strokeWidth={2.5} />
          <Line points={[originX, originY, originX, originY - axisLength]} stroke="#0f7b4f" strokeWidth={2.5} />
          <Text x={originX + axisLength + 4} y={originY - 6} text="X" fontSize={12} fill="#b0281f" fontStyle="bold" />
          <Text x={originX - 4} y={originY - axisLength - 16} text="Y" fontSize={12} fill="#0f7b4f" fontStyle="bold" />
          <Circle x={originX} y={originY} radius={3.5} fill="#16202f" />
          <Text x={originX + 7} y={originY + 6} text="origin (0, 0)" fontSize={10} fill="#5a6779" />
        </Layer>

        {showFootprints && (
          <Layer listening={false}>
            {map.cameras.filter((c) => c.floor_polygon).map((camera) => {
              const polygon = camera.floor_polygon!;
              const flat = polygon.flatMap(([x, y]) => worldToScreen(view, x, y));
              const selected = camera.camera_id === selectedCameraId;
              const mapped = camera.area_is_mapped_coverage;
              // A mapped-coverage area is where the floor mapping works. It says
              // nothing about where the camera physically hangs, so it is drawn
              // differently and labelled at its centre rather than at a marker.
              const cx = polygon.reduce((a, p) => a + p[0], 0) / polygon.length;
              const cy = polygon.reduce((a, p) => a + p[1], 0) / polygon.length;
              const [lx, ly] = worldToScreen(view, cx, cy);
              return (
                <Group key={`fp-${camera.camera_id}`}>
                  <Line points={flat} closed
                        fill={selected ? "rgba(29,78,216,0.20)" : "rgba(29,78,216,0.10)"}
                        stroke="rgba(29,78,216,0.45)" strokeWidth={1}
                        dash={mapped ? [7, 5] : undefined} />
                  {mapped && !camera.position && (
                    <>
                      <Text x={lx - 70} y={ly - 14} width={140} align="center"
                            text={camera.name} fontSize={11} fontStyle="bold" fill="#16202f" />
                      <Text x={lx - 70} y={ly} width={140} align="center"
                            text="mapped floor area" fontSize={10} fill="#5a6779" />
                    </>
                  )}
                </Group>
              );
            })}
          </Layer>
        )}

        {showReferencePoints && (
          <Layer listening={false}>
            {map.reference_points.map((point: ReferencePoint) => {
              const [sx, sy] = worldToScreen(view, point.x, point.y);
              return (
                <Group key={`rp-${point.id}`}>
                  <Line points={[sx - 5, sy, sx + 5, sy]} stroke="#9a6207" strokeWidth={1.6} />
                  <Line points={[sx, sy - 5, sx, sy + 5]} stroke="#9a6207" strokeWidth={1.6} />
                  {view.scale > 8 && (
                    <Text x={sx + 7} y={sy - 5} text={point.code} fontSize={10} fill="#9a6207" />
                  )}
                </Group>
              );
            })}
          </Layer>
        )}

        <Layer>
          {extraMarkers.map((marker, i) => {
            const [sx, sy] = worldToScreen(view, marker.x, marker.y);
            return (
              <Group key={`extra-${i}`} listening={false}>
                <Circle x={sx} y={sy} radius={7} fill={marker.colour ?? "#b0281f"}
                        stroke="#fff" strokeWidth={2} />
                <Text x={sx + 10} y={sy - 6} text={marker.label} fontSize={11}
                      fill={marker.colour ?? "#b0281f"} fontStyle="bold" />
              </Group>
            );
          })}

          {map.cameras.map((camera: MapCamera) => {
            if (!camera.position) return null;
            const [sx, sy] = worldToScreen(view, camera.position.x, camera.position.y);
            const selected = camera.camera_id === selectedCameraId;
            const colour = STATUS_COLOUR[camera.calibration_status] ?? "#5a6779";
            const heading = camera.map_heading_deg;
            const fov = camera.horizontal_fov_deg ?? camera.approx_hfov_deg;
            const range = (camera.approx_range_m ?? 8) * view.scale;

            return (
              <Group
                key={camera.camera_id}
                draggable={draggable && !!onMoveCamera}
                onClick={(e) => { e.cancelBubble = true; onSelectCamera?.(camera.camera_id); }}
                onTap={(e) => { e.cancelBubble = true; onSelectCamera?.(camera.camera_id); }}
                onDragEnd={(e) => {
                  const node = e.target;
                  const [wx, wy] = screenToWorld(view, sx + node.x(), sy + node.y());
                  node.position({ x: 0, y: 0 });
                  onMoveCamera?.(camera.camera_id, wx, wy);
                }}
              >
                {/* Viewing wedge. Konva angles run clockwise from +X; our heading
                    is clockwise from +Y, hence the 90 degree offset. */}
                {heading !== null && fov && !camera.floor_polygon && (
                  <Wedge
                    x={sx} y={sy} radius={range} angle={fov}
                    rotation={heading - 90 - fov / 2}
                    fill={selected ? "rgba(29,78,216,0.20)" : "rgba(29,78,216,0.11)"}
                    stroke="rgba(29,78,216,0.4)" strokeWidth={1}
                  />
                )}
                {heading !== null && (() => {
                  const [dx, dy] = headingToWorldVector(heading);
                  const len = Math.max(26, Math.min(range * 0.5, 70));
                  return (
                    <Line points={[sx, sy, sx + dx * len, sy - dy * len]}
                          stroke={colour} strokeWidth={2.5}
                          pointerLength={7} pointerWidth={6} />
                  );
                })()}
                <Circle x={sx} y={sy} radius={selected ? 9 : 7} fill={colour}
                        stroke="#fff" strokeWidth={2.5}
                        shadowBlur={selected ? 8 : 0} shadowColor="#1d4ed8" />
                {camera.is_approximate && (
                  <Circle x={sx} y={sy} radius={13} stroke={colour} strokeWidth={1}
                          dash={[3, 3]} />
                )}
                <Text x={sx + 12} y={sy - 18} text={camera.name} fontSize={11}
                      fontStyle="bold" fill="#16202f" />
                <Text x={sx + 12} y={sy - 5}
                      text={camera.is_approximate
                        ? "Approximate camera position"
                        : `${camera.position.x.toFixed(1)}, ${camera.position.y.toFixed(1)}, ${camera.position.z.toFixed(1)} m`}
                      fontSize={10} fill="#5a6779" />
              </Group>
            );
          })}
        </Layer>
      </Stage>

      <div className="plan-legend plan-legend-right">
        <strong>Metric floor grid.</strong> Positions in metres from the area's origin.
        A dashed outline is the floor area a camera's mapping covers — that is where its
        measurements work, not where the camera hangs. A dashed ring round a marker means the
        position was typed in by hand and is approximate. Shaded areas are geometry only: they
        ignore machinery, racking and walls.
      </div>
    </div>
  );
}
