import { useCallback, useEffect, useRef, useState } from "react";
import { getToken } from "../lib/api";
import { Notice, Spinner } from "./ui";

export type GeometryShape = "line" | "polygon";

/**
 * Draw a counting line or a zone polygon straight onto the camera picture.
 *
 * Everything is kept in the frame's own native pixels, together with the frame
 * size it was drawn against, so a shape does not move when the browser is
 * resized and a later change of stream resolution can be detected rather than
 * silently mis-scaling the shape.
 *
 * No calibration is needed. Counting and zone watching happen on the picture,
 * and an installer can mark "the doorway" before anything has been measured.
 */
export default function PictureGeometryEditor({
  cameraId, shape, points, onChange, onFrame, height = 420, disabled,
}: {
  cameraId: number;
  shape: GeometryShape;
  points: number[][];
  onChange: (points: number[][]) => void;
  /** The native size of the frame the shape is being drawn against. */
  onFrame?: (w: number, h: number) => void;
  height?: number;
  disabled?: boolean;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const objectUrl = useRef<string | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null);
  const [box, setBox] = useState({ w: 800, h: height });
  const [dragging, setDragging] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const token = getToken();
      const res = await fetch(`/api/cameras/${cameraId}/snapshot`, {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined });
      if (!res.ok) {
        let detail = `Could not fetch a picture (${res.status})`;
        try { detail = (await res.json()).detail ?? detail; } catch { /* keep default */ }
        throw new Error(detail);
      }
      const blob = await res.blob();
      if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
      const url = URL.createObjectURL(blob);
      objectUrl.current = url;
      setImageUrl(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not fetch a picture.");
    } finally { setLoading(false); }
  }, [cameraId]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => () => { if (objectUrl.current) URL.revokeObjectURL(objectUrl.current); }, []);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => setBox({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    setBox({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);

  const fit = natural ? Math.min(box.w / natural.w, box.h / natural.h) : 1;
  const drawW = natural ? natural.w * fit : 0;
  const drawH = natural ? natural.h * fit : 0;
  const offsetX = (box.w - drawW) / 2;
  const offsetY = (box.h - drawH) / 2;

  const toImage = (clientX: number, clientY: number): [number, number] | null => {
    const rect = wrapRef.current?.getBoundingClientRect();
    if (!rect || !natural) return null;
    const u = (clientX - rect.left - offsetX) / fit;
    const v = (clientY - rect.top - offsetY) / fit;
    return [Math.min(natural.w - 1, Math.max(0, u)), Math.min(natural.h - 1, Math.max(0, v))];
  };
  const toScreen = (u: number, v: number): [number, number] =>
    [offsetX + u * fit, offsetY + v * fit];

  const maxPoints = shape === "line" ? 2 : 24;

  const addPoint = (e: React.MouseEvent) => {
    if (disabled || dragging !== null) return;
    const p = toImage(e.clientX, e.clientY);
    if (!p) return;
    // A line only ever has two ends: clicking again replaces the nearer one, so
    // the shape can be nudged without having to clear and start over.
    if (shape === "line" && points.length >= 2) {
      const d = points.map(([x, y]) => Math.hypot(x - p[0], y - p[1]));
      const near = d[0] <= d[1] ? 0 : 1;
      onChange(points.map((q, i) => (i === near ? p : q)));
      return;
    }
    if (points.length >= maxPoints) return;
    onChange([...points, p]);
  };

  const moveTo = (e: React.MouseEvent) => {
    if (dragging === null || disabled) return;
    const p = toImage(e.clientX, e.clientY);
    if (p) onChange(points.map((q, i) => (i === dragging ? p : q)));
  };

  const colour = shape === "line" ? "#1d4ed8" : "#0f7b4f";
  const enough = shape === "line" ? points.length === 2 : points.length >= 3;

  return (
    <div>
      <div ref={wrapRef} className="plan-stage"
           style={{ height, background: "#101620",
                    cursor: disabled ? "default" : dragging !== null ? "grabbing" : "crosshair" }}
           onClick={addPoint}
           onMouseMove={moveTo}
           onMouseUp={() => setDragging(null)}
           onMouseLeave={() => setDragging(null)}>
        {imageUrl && (
          <img src={imageUrl} alt="Camera frame" draggable={false}
               style={{ position: "absolute", left: offsetX, top: offsetY,
                        width: drawW || undefined, height: drawH || undefined, userSelect: "none" }}
               onLoad={(e) => {
                 const img = e.currentTarget;
                 setNatural({ w: img.naturalWidth, h: img.naturalHeight });
                 onFrame?.(img.naturalWidth, img.naturalHeight);
               }} />
        )}

        {!imageUrl && (
          <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center",
                        color: "#9aa7b8", fontSize: 13 }}>
            {loading ? <Spinner label="Fetching a frame…" />
                     : "No picture yet — the shape can still be drawn once one loads."}
          </div>
        )}

        {natural && (
          <svg width={box.w} height={box.h} style={{ position: "absolute", inset: 0 }}>
            {shape === "polygon" && points.length >= 3 && (
              <polygon points={points.map(([u, v]) => toScreen(u, v).join(",")).join(" ")}
                       fill={colour} fillOpacity={0.18} stroke={colour} strokeWidth={2} />
            )}
            {shape === "polygon" && points.length === 2 && (
              <polyline fill="none" stroke={colour} strokeWidth={2} strokeDasharray="5 4"
                        points={points.map(([u, v]) => toScreen(u, v).join(",")).join(" ")} />
            )}
            {shape === "line" && points.length === 2 && (() => {
              const [a, b] = points.map(([u, v]) => toScreen(u, v));
              // A short spur marks which side counts as "A", so the direction
              // setting means something specific rather than being a guess.
              const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2;
              const len = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
              const nx = -(b[1] - a[1]) / len, ny = (b[0] - a[0]) / len;
              return (
                <>
                  <line x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} stroke={colour} strokeWidth={3} />
                  <line x1={mx} y1={my} x2={mx + nx * 26} y2={my + ny * 26}
                        stroke={colour} strokeWidth={2} strokeDasharray="4 3" />
                  <text x={mx + nx * 34} y={my + ny * 34 + 4} fontSize={11} fill="#fff"
                        stroke="#101620" strokeWidth={3} paintOrder="stroke" textAnchor="middle">B</text>
                  <text x={mx - nx * 24} y={my - ny * 24 + 4} fontSize={11} fill="#fff"
                        stroke="#101620" strokeWidth={3} paintOrder="stroke" textAnchor="middle">A</text>
                </>
              );
            })()}

            {points.map(([u, v], i) => {
              const [x, y] = toScreen(u, v);
              return (
                <g key={i} style={{ cursor: disabled ? "default" : "grab" }}
                   onMouseDown={(e) => { e.stopPropagation(); if (!disabled) setDragging(i); }}
                   onClick={(e) => e.stopPropagation()}>
                  <circle cx={x} cy={y} r={7} fill="#fff" stroke={colour} strokeWidth={2.5} />
                  <text x={x} y={y + 3.5} fontSize={9} fontWeight={700} fill={colour}
                        textAnchor="middle">{i + 1}</text>
                </g>
              );
            })}
          </svg>
        )}
      </div>

      <div className="preview-bar">
        <button className="btn btn-sm" type="button" onClick={load} disabled={loading}>
          {imageUrl ? "Refresh frame" : "Load a frame"}
        </button>
        <button className="btn btn-sm" type="button" disabled={disabled || !points.length}
                onClick={() => onChange(points.slice(0, -1))}>Undo point</button>
        <button className="btn btn-sm" type="button" disabled={disabled || !points.length}
                onClick={() => onChange([])}>Clear</button>
        <span className="small muted">
          {shape === "line"
            ? points.length < 2
              ? `Click the two ends of the line (${points.length}/2).`
              : "Drag either end to adjust. A is one side, B the other."
            : points.length < 3
              ? `Click the corners of the zone (${points.length}/3 minimum).`
              : `${points.length} corners — drag any to adjust, or keep clicking to add more.`}
        </span>
        {natural && (
          <span className="small faint">
            Drawn on {natural.w}×{natural.h} px
          </span>
        )}
      </div>

      {!enough && (
        <p className="hint" style={{ marginTop: 6 }}>
          {shape === "line" ? "A counting line needs both ends."
                            : "A zone needs at least three corners."}
        </p>
      )}
      {error && <div style={{ marginTop: 10 }}>
        <Notice tone="danger" title="Could not fetch a frame">{error}</Notice>
      </div>}
    </div>
  );
}
