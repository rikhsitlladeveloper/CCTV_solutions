import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { mediaUrl } from "../lib/api";
import type { FloorPlan } from "../lib/types";

/** Heading convention, used everywhere in Numenor:
 *  0° points to the top of the floor plan and increases clockwise. */
export function headingVector(headingDeg: number): { x: number; y: number } {
  const rad = (headingDeg * Math.PI) / 180;
  return { x: Math.sin(rad), y: -Math.cos(rad) };
}

export function headingFromDelta(dx: number, dy: number): number {
  const deg = (Math.atan2(dx, -dy) * 180) / Math.PI;
  return (deg + 360) % 360;
}

export interface Marker {
  id: number;
  label: string;
  norm_x: number;
  norm_y: number;
  heading_deg: number;
  fov_deg: number | null;
  view_distance_m: number | null;
  needs_review?: boolean;
}

interface Props {
  plan: FloorPlan;
  markers: Marker[];
  selectedId: number | null;
  onSelect?: (id: number | null) => void;
  onMove?: (id: number, normX: number, normY: number) => void;
  onRotate?: (id: number, heading: number) => void;
  /** Click on empty plan area while in placing mode. */
  placingMode?: boolean;
  onPlaceAt?: (normX: number, normY: number) => void;
  /** Two-point scale calibration. */
  scaleMode?: boolean;
  scalePoints?: { x: number; y: number }[];
  onScalePoint?: (normX: number, normY: number) => void;
  height?: number;
  readOnly?: boolean;
}

type Drag =
  | { kind: "pan"; startX: number; startY: number; originX: number; originY: number }
  | { kind: "move"; id: number }
  | { kind: "rotate"; id: number }
  | null;

const MIN_ZOOM = 0.08;
const MAX_ZOOM = 12;

export default function FloorPlanCanvas({
  plan, markers, selectedId, onSelect, onMove, onRotate,
  placingMode, onPlaceAt, scaleMode, scalePoints = [], onScalePoint,
  height, readOnly,
}: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [box, setBox] = useState({ w: 900, h: height ?? 620 });
  const [view, setView] = useState({ scale: 1, tx: 0, ty: 0 });
  const [drag, setDrag] = useState<Drag>(null);
  const dragRef = useRef<Drag>(null);
  dragRef.current = drag;

  const fit = useCallback(() => {
    const el = wrapRef.current;
    if (!el) return;
    const w = el.clientWidth || 900;
    const h = el.clientHeight || height || 620;
    const pad = 24;
    const scale = Math.min((w - pad * 2) / plan.width_px, (h - pad * 2) / plan.height_px);
    setBox({ w, h });
    setView({
      scale,
      tx: (w - plan.width_px * scale) / 2,
      ty: (h - plan.height_px * scale) / 2,
    });
  }, [plan.width_px, plan.height_px, height]);

  useLayoutEffect(() => { fit(); }, [fit, plan.id, plan.version]);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    // Markers are stored normalized, so a resize re-fits the view without
    // altering a single placement.
    const ro = new ResizeObserver(() => {
      const w = el.clientWidth, h = el.clientHeight;
      setBox((prev) => (prev.w === w && prev.h === h ? prev : { w, h }));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  /** Screen point -> normalized image coordinates. */
  const toNorm = useCallback((clientX: number, clientY: number) => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return { x: 0, y: 0 };
    const px = (clientX - rect.left - view.tx) / view.scale;
    const py = (clientY - rect.top - view.ty) / view.scale;
    return {
      x: Math.min(1, Math.max(0, px / plan.width_px)),
      y: Math.min(1, Math.max(0, py / plan.height_px)),
    };
  }, [view, plan.width_px, plan.height_px]);

  const zoomBy = useCallback((factor: number, cx?: number, cy?: number) => {
    setView((v) => {
      const next = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, v.scale * factor));
      const ratio = next / v.scale;
      const ax = cx ?? box.w / 2;
      const ay = cy ?? box.h / 2;
      return { scale: next, tx: ax - (ax - v.tx) * ratio, ty: ay - (ay - v.ty) * ratio };
    });
  }, [box.w, box.h]);

  const onWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault();
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return;
    zoomBy(e.deltaY < 0 ? 1.12 : 1 / 1.12, e.clientX - rect.left, e.clientY - rect.top);
  }, [zoomBy]);

  // Wheel must be a non-passive native listener to be preventable.
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const handler = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      zoomBy(e.deltaY < 0 ? 1.12 : 1 / 1.12, e.clientX - rect.left, e.clientY - rect.top);
    };
    el.addEventListener("wheel", handler, { passive: false });
    return () => el.removeEventListener("wheel", handler);
  }, [zoomBy]);

  const onPointerDown = (e: React.PointerEvent) => {
    const target = e.target as Element;
    const markerId = target.closest("[data-marker-id]")?.getAttribute("data-marker-id");
    const isHandle = !!target.closest("[data-rotate-handle]");
    (e.currentTarget as Element).setPointerCapture(e.pointerId);

    if (scaleMode) {
      const p = toNorm(e.clientX, e.clientY);
      onScalePoint?.(p.x, p.y);
      return;
    }
    if (markerId && !readOnly) {
      const id = Number(markerId);
      onSelect?.(id);
      setDrag(isHandle ? { kind: "rotate", id } : { kind: "move", id });
      return;
    }
    if (markerId) { onSelect?.(Number(markerId)); return; }
    if (placingMode && onPlaceAt) {
      const p = toNorm(e.clientX, e.clientY);
      onPlaceAt(p.x, p.y);
      return;
    }
    setDrag({ kind: "pan", startX: e.clientX, startY: e.clientY, originX: view.tx, originY: view.ty });
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const d = dragRef.current;
    if (!d) return;
    if (d.kind === "pan") {
      setView((v) => ({ ...v, tx: d.originX + (e.clientX - d.startX), ty: d.originY + (e.clientY - d.startY) }));
      return;
    }
    const p = toNorm(e.clientX, e.clientY);
    if (d.kind === "move") { onMove?.(d.id, p.x, p.y); return; }
    const marker = markers.find((m) => m.id === d.id);
    if (!marker) return;
    const dx = (p.x - marker.norm_x) * plan.width_px;
    const dy = (p.y - marker.norm_y) * plan.height_px;
    if (Math.hypot(dx, dy) < 2) return;
    onRotate?.(d.id, Math.round(headingFromDelta(dx, dy)));
  };

  const endDrag = (e: React.PointerEvent) => {
    if (dragRef.current) setDrag(null);
    try { (e.currentTarget as Element).releasePointerCapture(e.pointerId); } catch { /* already released */ }
  };

  const pxPerMetre = plan.scale_px_per_metre;
  /** Fallback radius when no scale is configured: indicative only, no metres. */
  const indicativeRadius = Math.min(plan.width_px, plan.height_px) * 0.12;

  const imageUrl = useMemo(() => mediaUrl(plan.image_url), [plan.image_url]);

  return (
    <div
      ref={wrapRef}
      className={`plan-stage${drag?.kind === "pan" ? " is-panning" : ""}${placingMode || scaleMode ? " is-placing" : ""}`}
      style={height ? { height } : undefined}
    >
      <div className="plan-toolbar">
        <button className="btn btn-sm" onClick={() => zoomBy(1.25)} title="Zoom in" type="button">+</button>
        <button className="btn btn-sm" onClick={() => zoomBy(1 / 1.25)} title="Zoom out" type="button">−</button>
        <button className="btn btn-sm" onClick={fit} title="Fit to screen" type="button">Fit</button>
        <button className="btn btn-sm" onClick={fit} title="Reset view" type="button">Reset</button>
        <span className="slider-val" style={{ padding: "0 6px", alignSelf: "center" }}>
          {Math.round(view.scale * 100)}%
        </span>
      </div>

      <svg
        ref={svgRef}
        width={box.w}
        height={box.h}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onWheel={onWheel}
        style={{ display: "block", userSelect: "none" }}
        role="application"
        aria-label={`Floor plan for ${plan.location.building ?? ""} ${plan.location.floor ?? ""}`}
      >
        <g transform={`translate(${view.tx} ${view.ty}) scale(${view.scale})`}>
          <image href={imageUrl} x={0} y={0} width={plan.width_px} height={plan.height_px} />
          <rect x={0} y={0} width={plan.width_px} height={plan.height_px}
                fill="none" stroke="#b9c3d2" strokeWidth={1 / view.scale} />

          {/* scale calibration points */}
          {scalePoints.map((p, i) => (
            <g key={i} transform={`translate(${p.x * plan.width_px} ${p.y * plan.height_px})`}>
              <circle r={7 / view.scale} fill="#1d4ed8" stroke="#fff" strokeWidth={2 / view.scale} />
              <text y={-12 / view.scale} textAnchor="middle" className="marker-label"
                    style={{ fontSize: 12 / view.scale }}>{i === 0 ? "A" : "B"}</text>
            </g>
          ))}
          {scalePoints.length === 2 && (
            <line
              x1={scalePoints[0].x * plan.width_px} y1={scalePoints[0].y * plan.height_px}
              x2={scalePoints[1].x * plan.width_px} y2={scalePoints[1].y * plan.height_px}
              stroke="#1d4ed8" strokeWidth={2 / view.scale} strokeDasharray={`${6 / view.scale} ${4 / view.scale}`}
            />
          )}

          {markers.map((m) => {
            const cx = m.norm_x * plan.width_px;
            const cy = m.norm_y * plan.height_px;
            const selected = m.id === selectedId;
            const radius = pxPerMetre && m.view_distance_m
              ? m.view_distance_m * pxPerMetre
              : indicativeRadius;
            const fov = m.fov_deg ?? 0;
            const dir = headingVector(m.heading_deg);
            const handleDist = 46 / view.scale + 14;

            return (
              <g key={m.id} data-marker-id={m.id} style={{ cursor: readOnly ? "pointer" : "grab" }}>
                {fov > 0 && (
                  <path
                    d={sectorPath(cx, cy, radius, m.heading_deg, fov)}
                    fill={selected ? "rgba(29,78,216,0.22)" : "rgba(29,78,216,0.13)"}
                    stroke="rgba(29,78,216,0.5)"
                    strokeWidth={1 / view.scale}
                  />
                )}
                {/* direction arrow */}
                <line
                  x1={cx} y1={cy}
                  x2={cx + dir.x * (radius * 0.62)} y2={cy + dir.y * (radius * 0.62)}
                  stroke={selected ? "#1d4ed8" : "#33415a"} strokeWidth={2.5 / view.scale}
                  markerEnd="url(#numenor-arrow)"
                />
                <circle
                  cx={cx} cy={cy} r={9 / view.scale}
                  fill={m.needs_review ? "#9a6207" : selected ? "#1d4ed8" : "#33415a"}
                  stroke="#fff" strokeWidth={2.5 / view.scale}
                />
                <text x={cx} y={cy - 15 / view.scale} textAnchor="middle" className="marker-label"
                      style={{ fontSize: 12 / view.scale, strokeWidth: 3 / view.scale }}>
                  {m.label}
                </text>
                {selected && !readOnly && (
                  <g data-rotate-handle="1" style={{ cursor: "crosshair" }}>
                    <line x1={cx} y1={cy} x2={cx + dir.x * handleDist} y2={cy + dir.y * handleDist}
                          stroke="#1d4ed8" strokeWidth={1.5 / view.scale}
                          strokeDasharray={`${4 / view.scale} ${3 / view.scale}`} />
                    <circle cx={cx + dir.x * handleDist} cy={cy + dir.y * handleDist} r={7 / view.scale}
                            fill="#fff" stroke="#1d4ed8" strokeWidth={2.5 / view.scale} />
                  </g>
                )}
              </g>
            );
          })}
        </g>
        <defs>
          <marker id="numenor-arrow" viewBox="0 0 10 10" refX="8" refY="5"
                  markerWidth="5" markerHeight="5" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#1d4ed8" />
          </marker>
        </defs>
      </svg>

      <div className="plan-legend">
        <strong>Approximate viewing area.</strong> The shaded sector is an indication of where a
        camera points, drawn from the heading and field-of-view you enter. It is not calibrated
        coverage and does not measure visible floor space.
        {!pxPerMetre && " No map scale is set, so the sector length is indicative only."}
      </div>
    </div>
  );
}

/** SVG path for a viewing sector centred on `heading` (0° = up, clockwise). */
function sectorPath(cx: number, cy: number, radius: number, heading: number, fov: number): string {
  const half = Math.min(fov, 359.9) / 2;
  const a1 = headingVector(heading - half);
  const a2 = headingVector(heading + half);
  const x1 = cx + a1.x * radius, y1 = cy + a1.y * radius;
  const x2 = cx + a2.x * radius, y2 = cy + a2.y * radius;
  const largeArc = fov > 180 ? 1 : 0;
  return `M ${cx} ${cy} L ${x1} ${y1} A ${radius} ${radius} 0 ${largeArc} 1 ${x2} ${y2} Z`;
}
