import { useCallback, useEffect, useRef, useState } from "react";
import { getToken } from "../lib/api";
import { Notice, Spinner } from "./ui";

export interface ImageMark {
  id: number | string;
  u: number;
  v: number;
  label: string;
  role?: "fit" | "holdout";
  /** Where the current calibration says this point should appear. */
  projected?: [number, number] | null;
  errorPx?: number | null;
}

interface Props {
  cameraId: number;
  marks: ImageMark[];
  selectedId: number | string | null;
  onSelect?: (id: number | string | null) => void;
  /** Click on the image, in native image pixels. */
  onPick?: (u: number, v: number) => void;
  /** Extra line overlays in image pixels, e.g. a projected floor grid. */
  overlaySegments?: Array<Array<[number, number] | null>>;
  height?: number;
  hint?: string;
  onImageLoaded?: (width: number, height: number) => void;
}

/** Shows a live snapshot and maps clicks back to native image pixels, so marked
 *  coordinates are always in the stream's own geometry rather than screen space. */
export default function CameraImagePicker({
  cameraId, marks, selectedId, onSelect, onPick, overlaySegments = [],
  height = 460, hint, onImageLoaded,
}: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null);
  const [box, setBox] = useState({ w: 800, h: height });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const objectUrl = useRef<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const token = getToken();
      const res = await fetch(`/api/cameras/${cameraId}/snapshot`, {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      });
      if (!res.ok) {
        let detail = `Snapshot failed (${res.status})`;
        try { detail = (await res.json()).detail ?? detail; } catch { /* keep default */ }
        throw new Error(detail);
      }
      const blob = await res.blob();
      if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
      const url = URL.createObjectURL(blob);
      objectUrl.current = url;
      setImageUrl(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Snapshot failed.");
    } finally {
      setLoading(false);
    }
  }, [cameraId]);

  useEffect(() => () => { if (objectUrl.current) URL.revokeObjectURL(objectUrl.current); }, []);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => setBox({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    setBox({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);

  /** Fit the image inside the box, preserving aspect ratio. */
  const fit = natural
    ? Math.min(box.w / natural.w, box.h / natural.h)
    : 1;
  const drawW = natural ? natural.w * fit : 0;
  const drawH = natural ? natural.h * fit : 0;
  const offsetX = (box.w - drawW) / 2;
  const offsetY = (box.h - drawH) / 2;

  const toImagePixels = (clientX: number, clientY: number): [number, number] | null => {
    const rect = wrapRef.current?.getBoundingClientRect();
    if (!rect || !natural) return null;
    const u = (clientX - rect.left - offsetX) / fit;
    const v = (clientY - rect.top - offsetY) / fit;
    if (u < 0 || v < 0 || u > natural.w - 1 || v > natural.h - 1) return null;
    return [u, v];
  };

  const toScreen = (u: number, v: number): [number, number] =>
    [offsetX + u * fit, offsetY + v * fit];

  return (
    <div>
      <div
        ref={wrapRef}
        className="plan-stage"
        style={{ height, cursor: onPick && natural ? "crosshair" : "default", background: "#101620" }}
        onClick={(e) => {
          if (!onPick) return;
          const point = toImagePixels(e.clientX, e.clientY);
          if (point) onPick(point[0], point[1]);
        }}
      >
        {imageUrl && (
          <img
            src={imageUrl}
            alt="Camera frame"
            draggable={false}
            style={{
              position: "absolute", left: offsetX, top: offsetY,
              width: drawW || undefined, height: drawH || undefined, userSelect: "none",
            }}
            onLoad={(e) => {
              const img = e.currentTarget;
              setNatural({ w: img.naturalWidth, h: img.naturalHeight });
              onImageLoaded?.(img.naturalWidth, img.naturalHeight);
            }}
          />
        )}

        {!imageUrl && (
          <div className="preview-placeholder" style={{ position: "absolute", inset: 0,
               display: "grid", placeItems: "center" }}>
            {loading ? <Spinner label="Fetching a frame…" />
                     : "Load a camera frame to mark reference points."}
          </div>
        )}

        {natural && (
          <svg width={box.w} height={box.h} style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
            {overlaySegments.map((segment, i) => {
              const pts = segment
                .map((p) => (p ? toScreen(p[0], p[1]) : null))
                .filter((p): p is [number, number] => p !== null);
              if (pts.length < 2) return null;
              return (
                <polyline key={`seg-${i}`} fill="none" stroke="rgba(29,78,216,0.5)" strokeWidth={1}
                          points={pts.map(([x, y]) => `${x},${y}`).join(" ")} />
              );
            })}

            {marks.map((mark) => {
              const [sx, sy] = toScreen(mark.u, mark.v);
              const selected = mark.id === selectedId;
              const colour = mark.role === "holdout" ? "#9a6207" : "#1d4ed8";
              return (
                <g key={String(mark.id)} style={{ pointerEvents: "all", cursor: "pointer" }}
                   onClick={(e) => { e.stopPropagation(); onSelect?.(mark.id); }}>
                  {mark.projected && (() => {
                    const [px, py] = toScreen(mark.projected[0], mark.projected[1]);
                    return (
                      <>
                        <line x1={sx} y1={sy} x2={px} y2={py} stroke="#b0281f" strokeWidth={1.5} />
                        <circle cx={px} cy={py} r={4} fill="none" stroke="#b0281f" strokeWidth={1.5} />
                      </>
                    );
                  })()}
                  <circle cx={sx} cy={sy} r={selected ? 8 : 6} fill="none"
                          stroke={colour} strokeWidth={selected ? 3 : 2} />
                  <circle cx={sx} cy={sy} r={1.6} fill={colour} />
                  <text x={sx + 10} y={sy - 8} fontSize={11} fill="#fff"
                        stroke="#101620" strokeWidth={3} paintOrder="stroke">
                    {mark.label}{mark.role === "holdout" ? " (held out)" : ""}
                  </text>
                  {mark.errorPx != null && (
                    <text x={sx + 10} y={sy + 6} fontSize={10} fill="#ffb3ad"
                          stroke="#101620" strokeWidth={3} paintOrder="stroke">
                      {mark.errorPx.toFixed(1)} px
                    </text>
                  )}
                </g>
              );
            })}
          </svg>
        )}
      </div>

      <div className="preview-bar">
        <button className="btn btn-sm" type="button" onClick={load} disabled={loading}>
          {imageUrl ? "Refresh frame" : "Load camera frame"}
        </button>
        {natural && (
          <span className="small faint">
            Frame {natural.w}×{natural.h} px · clicks are recorded in these native pixels
          </span>
        )}
        {hint && <span className="small muted">{hint}</span>}
      </div>

      {error && <div style={{ marginTop: 10 }}>
        <Notice tone="danger" title="Could not fetch a frame">{error}</Notice>
      </div>}
    </div>
  );
}
