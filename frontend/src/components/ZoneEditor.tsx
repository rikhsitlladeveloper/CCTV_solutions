import { useCallback, useEffect, useRef, useState } from "react";
import { getToken } from "../lib/api";
import { Notice, Spinner } from "./ui";
import type { Zone, ZoneKind } from "../lib/setupTypes";

const KIND_COLOUR: Record<ZoneKind, string> = {
  monitored: "#1d4ed8",
  entrance: "#0f7b4f",
  exit: "#9a6207",
};

/** Draw polygons directly on a camera picture. Works with no calibration at all —
 *  an installer can mark "the door" before anything has been measured. */
export default function ZoneEditor({ cameraId, zones, drawing, onFinish, onCancel, height = 460 }: {
  cameraId: number;
  zones: Zone[];
  drawing: boolean;
  onFinish: (polygon: number[][], size: { w: number; h: number }) => void;
  onCancel: () => void;
  height?: number;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const objectUrl = useRef<string | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null);
  const [box, setBox] = useState({ w: 800, h: height });
  const [pending, setPending] = useState<number[][]>([]);
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
        try { detail = (await res.json()).detail ?? detail; } catch { /* keep */ }
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

  useEffect(() => { if (!drawing) setPending([]); }, [drawing]);

  const fit = natural ? Math.min(box.w / natural.w, box.h / natural.h) : 1;
  const drawW = natural ? natural.w * fit : 0;
  const drawH = natural ? natural.h * fit : 0;
  const offsetX = (box.w - drawW) / 2;
  const offsetY = (box.h - drawH) / 2;

  const toImage = (clientX: number, clientY: number): number[] | null => {
    const rect = wrapRef.current?.getBoundingClientRect();
    if (!rect || !natural) return null;
    const u = (clientX - rect.left - offsetX) / fit;
    const v = (clientY - rect.top - offsetY) / fit;
    if (u < 0 || v < 0 || u > natural.w - 1 || v > natural.h - 1) return null;
    return [u, v];
  };

  const toScreen = (u: number, v: number) => [offsetX + u * fit, offsetY + v * fit];

  return (
    <div>
      <div ref={wrapRef} className="plan-stage"
           style={{ height, background: "#101620", cursor: drawing ? "crosshair" : "default" }}
           onClick={(e) => {
             if (!drawing) return;
             const point = toImage(e.clientX, e.clientY);
             if (point) setPending((p) => [...p, point]);
           }}>
        {imageUrl && (
          <img src={imageUrl} alt="Camera view" draggable={false}
               style={{ position: "absolute", left: offsetX, top: offsetY,
                        width: drawW || undefined, height: drawH || undefined }}
               onLoad={(e) => setNatural({ w: e.currentTarget.naturalWidth,
                                           h: e.currentTarget.naturalHeight })} />
        )}
        {!imageUrl && (
          <div className="preview-placeholder" style={{ position: "absolute", inset: 0,
               display: "grid", placeItems: "center" }}>
            {loading ? <Spinner label="Fetching a picture…" /> : "No picture loaded."}
          </div>
        )}

        {natural && (
          <svg width={box.w} height={box.h}
               style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
            {zones.map((zone) => {
              const pts = zone.image_polygon.map(([u, v]) => toScreen(u, v));
              const colour = KIND_COLOUR[zone.kind];
              return (
                <g key={zone.id}>
                  <polygon points={pts.map(([x, y]) => `${x},${y}`).join(" ")}
                           fill={`${colour}22`} stroke={colour} strokeWidth={2} />
                  <text x={pts[0][0] + 6} y={pts[0][1] - 6} fontSize={12} fill="#fff"
                        stroke="#101620" strokeWidth={3} paintOrder="stroke">
                    {zone.name} · {zone.kind}
                  </text>
                </g>
              );
            })}

            {pending.length > 0 && (() => {
              const pts = pending.map(([u, v]) => toScreen(u, v));
              return (
                <g>
                  <polyline points={pts.map(([x, y]) => `${x},${y}`).join(" ")}
                            fill="rgba(29,78,216,0.15)" stroke="#1d4ed8" strokeWidth={2}
                            strokeDasharray="5 4" />
                  {pts.map(([x, y], i) => (
                    <circle key={i} cx={x} cy={y} r={4} fill="#1d4ed8" stroke="#fff" strokeWidth={1.5} />
                  ))}
                </g>
              );
            })()}
          </svg>
        )}
      </div>

      <div className="preview-bar">
        <button className="btn btn-sm" type="button" onClick={load} disabled={loading}>
          {imageUrl ? "Refresh picture" : "Load picture"}
        </button>
        {natural && <span className="small faint">{natural.w}×{natural.h} px</span>}
        {drawing && (
          <>
            <span className="small muted">
              {pending.length < 3
                ? `Click the corners of the area — ${3 - pending.length} more needed.`
                : `${pending.length} corners. Finish when the shape looks right.`}
            </span>
            <div className="grow" />
            <button className="btn btn-sm" type="button" disabled={!pending.length}
                    onClick={() => setPending((p) => p.slice(0, -1))}>Undo</button>
            <button className="btn btn-sm" type="button" onClick={() => { setPending([]); onCancel(); }}>
              Cancel
            </button>
            <button className="btn btn-sm btn-primary" type="button"
                    disabled={pending.length < 3 || !natural}
                    onClick={() => { onFinish(pending, natural!); setPending([]); }}>
              Finish shape
            </button>
          </>
        )}
      </div>

      {error && <div style={{ marginTop: 10 }}>
        <Notice tone="danger" title="No picture">{error}</Notice></div>}
    </div>
  );
}
