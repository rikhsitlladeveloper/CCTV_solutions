import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getToken } from "../lib/api";
import { Notice, Spinner } from "./ui";

/**
 * Draw monitored areas, counting lines and exclusions on a camera picture.
 *
 * Coordinates are kept in the frame's own native pixels, alongside the frame
 * size they were drawn against. The picture is fitted into whatever space the
 * layout gives it and letterboxed, so screen position is a function of the
 * container; storing screen pixels would silently move every region the first
 * time somebody resized a window. Recording the frame size as well means a
 * later change of stream resolution is detectable rather than a quiet
 * mis-scaling.
 */

export type RegionKind = "polygon" | "line" | "exclusion";

export interface Region {
  /** Stable within an editing session; not a database id. */
  key: string;
  kind: RegionKind;
  /** Native image pixels. Two points for a line, three or more for an area. */
  points: number[][];
  label?: string;
}

const KIND_COLOUR: Record<RegionKind, string> = {
  polygon: "#0f7b4f",
  line: "#1d4ed8",
  exclusion: "#b0281f",
};

const KIND_LABEL: Record<RegionKind, string> = {
  polygon: "Monitored area",
  line: "Counting line",
  exclusion: "Exclusion",
};

type Tool = "select" | RegionKind;

const MAX_VERTICES = 24;
const HANDLE_HIT_PX = 11;

export function regionIsComplete(region: Region): boolean {
  return region.kind === "line" ? region.points.length === 2 : region.points.length >= 3;
}

export default function RegionEditor({
  cameraId, regions, onChange, onFrame, height = 460, disabled, singleKind, initialTool,
}: {
  cameraId: number;
  regions: Region[];
  onChange: (regions: Region[]) => void;
  onFrame?: (w: number, h: number) => void;
  height?: number;
  disabled?: boolean;
  /** Restrict the toolbar when a function only supports one shape. */
  singleKind?: RegionKind;
  /** Which tool is armed on open. A step that exists to draw one shape should
   *  start on that shape, not on Select — otherwise the first clicks do
   *  nothing and it looks broken. */
  initialTool?: Tool;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const objectUrl = useRef<string | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null);
  const [box, setBox] = useState({ w: 800, h: height });
  const [tool, setTool] = useState<Tool>(initialTool ?? singleKind ?? "select");
  const [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState<number[][] | null>(null);
  const [drag, setDrag] = useState<
    | { kind: "vertex"; key: string; index: number }
    | { kind: "shape"; key: string; from: [number, number] }
    | null>(null);
  const [history, setHistory] = useState<Region[][]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /* ---------------- the frame ---------------- */

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const token = getToken();
      const res = await fetch(`/api/cameras/${cameraId}/snapshot`, {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined });
      if (!res.ok) {
        let detail = `Could not fetch a picture (${res.status})`;
        try { detail = (await res.json()).detail ?? detail; } catch { /* keep default */ }
        throw new Error(typeof detail === "string" ? detail : "Could not fetch a picture.");
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

  /* ---------------- image <-> screen ---------------- */

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

  /* ---------------- edits ---------------- */

  const commit = (next: Region[]) => {
    setHistory((h) => [...h.slice(-40), regions]);
    onChange(next);
  };

  const undo = () => {
    setHistory((h) => {
      if (draft) { setDraft(null); return h; }
      if (!h.length) return h;
      onChange(h[h.length - 1]);
      return h.slice(0, -1);
    });
  };

  const deleteSelected = () => {
    if (!selected) return;
    commit(regions.filter((r) => r.key !== selected));
    setSelected(null);
  };

  const finishDraft = useCallback((points: number[][], kind: RegionKind) => {
    const key = `r${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
    commit([...regions, { key, kind, points }]);
    setSelected(key);
    setDraft(null);
    if (!singleKind) setTool("select");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [regions, singleKind]);

  const onStageClick = (e: React.MouseEvent) => {
    if (disabled || drag) return;
    const point = toImage(e.clientX, e.clientY);
    if (!point) return;

    if (tool === "select") {
      setSelected(hitRegion(point) ?? null);
      return;
    }
    if (tool === "line") {
      const next = [...(draft ?? []), point];
      if (next.length === 2) finishDraft(next, "line"); else setDraft(next);
      return;
    }
    // polygon or exclusion: keep collecting corners until it is closed
    const next = [...(draft ?? []), point];
    if (next.length >= MAX_VERTICES) finishDraft(next, tool as RegionKind);
    else setDraft(next);
  };

  const closeDraft = () => {
    if (!draft || tool === "select") return;
    if (tool === "line" ? draft.length === 2 : draft.length >= 3) {
      finishDraft(draft, tool as RegionKind);
    }
  };

  /** Which region is under a point: a vertex first, then an enclosing shape. */
  const hitRegion = (point: [number, number]): string | null => {
    const tol = HANDLE_HIT_PX / Math.max(fit, 0.0001);
    for (const region of regions) {
      for (const [x, y] of region.points) {
        if (Math.hypot(x - point[0], y - point[1]) <= tol) return region.key;
      }
    }
    for (const region of regions) {
      if (region.kind === "line") {
        if (region.points.length === 2
            && distanceToSegment(point, region.points[0], region.points[1]) <= tol) {
          return region.key;
        }
      } else if (pointInPolygon(point, region.points)) {
        return region.key;
      }
    }
    return null;
  };

  const onMouseDown = (e: React.MouseEvent) => {
    if (disabled || tool !== "select") return;
    const point = toImage(e.clientX, e.clientY);
    if (!point) return;
    const tol = HANDLE_HIT_PX / Math.max(fit, 0.0001);
    for (const region of regions) {
      for (let i = 0; i < region.points.length; i += 1) {
        const [x, y] = region.points[i];
        if (Math.hypot(x - point[0], y - point[1]) <= tol) {
          setHistory((h) => [...h.slice(-40), regions]);
          setSelected(region.key);
          setDrag({ kind: "vertex", key: region.key, index: i });
          return;
        }
      }
    }
    const key = hitRegion(point);
    if (key) {
      setHistory((h) => [...h.slice(-40), regions]);
      setSelected(key);
      setDrag({ kind: "shape", key, from: point });
    }
  };

  const onMouseMove = (e: React.MouseEvent) => {
    if (!drag || disabled) return;
    const point = toImage(e.clientX, e.clientY);
    if (!point) return;
    if (drag.kind === "vertex") {
      onChange(regions.map((r) => r.key !== drag.key ? r : {
        ...r, points: r.points.map((p, i) => (i === drag.index ? point : p)),
      }));
    } else {
      const dx = point[0] - drag.from[0];
      const dy = point[1] - drag.from[1];
      onChange(regions.map((r) => r.key !== drag.key ? r : {
        ...r,
        points: r.points.map(([x, y]) => [
          Math.min(natural!.w - 1, Math.max(0, x + dx)),
          Math.min(natural!.h - 1, Math.max(0, y + dy)),
        ]),
      }));
      setDrag({ ...drag, from: point });
    }
  };

  /* ---------------- keyboard ---------------- */

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (disabled) return;
    if (e.key === "Escape") { setDraft(null); setSelected(null); }
    else if (e.key === "Enter") closeDraft();
    else if ((e.key === "Delete" || e.key === "Backspace") && selected) {
      e.preventDefault(); deleteSelected();
    } else if ((e.key === "z" || e.key === "Z") && (e.ctrlKey || e.metaKey)) {
      e.preventDefault(); undo();
    }
  };

  const tools: Tool[] = singleKind ? ["select", singleKind]
                                   : ["select", "polygon", "line", "exclusion"];
  const selectedRegion = regions.find((r) => r.key === selected) ?? null;
  const incomplete = useMemo(() => regions.filter((r) => !regionIsComplete(r)), [regions]);

  return (
    <div>
      <div className="region-toolbar" role="toolbar" aria-label="Drawing tools">
        {tools.map((t) => (
          <button key={t} type="button" disabled={disabled}
                  className={`btn btn-sm${tool === t ? " btn-primary" : ""}`}
                  aria-pressed={tool === t}
                  onClick={() => { setTool(t); setDraft(null); }}>
            {t === "select" ? "Select" : KIND_LABEL[t as RegionKind]}
          </button>
        ))}
        <span className="divider" />
        <button className="btn btn-sm" type="button" onClick={undo}
                disabled={disabled || (!history.length && !draft)}>Undo</button>
        <button className="btn btn-sm" type="button" onClick={deleteSelected}
                disabled={disabled || !selected}>Delete</button>
        {draft && draft.length >= (tool === "line" ? 2 : 3) && (
          <button className="btn btn-sm btn-primary" type="button" onClick={closeDraft}>
            Finish shape
          </button>
        )}
        <div className="grow" />
        <button className="btn btn-sm" type="button" onClick={load} disabled={loading}>
          {imageUrl ? "Refresh frame" : "Load a frame"}
        </button>
      </div>

      <div ref={wrapRef} className="plan-stage" tabIndex={0} onKeyDown={onKeyDown}
           style={{ height, background: "#101620",
                    cursor: disabled ? "default"
                      : drag ? "grabbing" : tool === "select" ? "default" : "crosshair" }}
           onClick={onStageClick}
           onMouseDown={onMouseDown}
           onMouseMove={onMouseMove}
           onMouseUp={() => setDrag(null)}
           onMouseLeave={() => setDrag(null)}>
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
                        color: "#9aa7b8", fontSize: 13, textAlign: "center", padding: 20 }}>
            {loading ? <Spinner label="Fetching a frame…" />
                     : error ? "No picture available — see the message below."
                             : "No picture yet."}
          </div>
        )}

        {natural && (
          <svg width={box.w} height={box.h} style={{ position: "absolute", inset: 0 }}>
            {regions.map((region) => (
              <RegionShape key={region.key} region={region} toScreen={toScreen}
                           selected={region.key === selected} />
            ))}
            {draft && draft.length > 0 && (
              <DraftShape points={draft} kind={tool === "select" ? "polygon" : tool}
                          toScreen={toScreen} />
            )}
          </svg>
        )}
      </div>

      <div className="preview-bar">
        <span className="small muted">
          {tool === "select"
            ? selectedRegion
              ? `${KIND_LABEL[selectedRegion.kind]} selected — drag it, drag a corner, or press Delete.`
              : "Click a shape to select it, or pick a drawing tool."
            : tool === "line"
              ? `Click the two ends of the counting line (${draft?.length ?? 0}/2).`
              : `Click each corner, then Finish shape (${draft?.length ?? 0} so far, 3 minimum).`}
        </span>
        <div className="grow" />
        {natural && (
          <span className="small faint">
            Drawn on {natural.w}×{natural.h} px · stored in these pixels, so resizing moves nothing
          </span>
        )}
      </div>

      {incomplete.length > 0 && (
        <p className="hint" style={{ marginTop: 6 }}>
          {incomplete.length} shape{incomplete.length === 1 ? " is" : "s are"} incomplete and
          will not be saved.
        </p>
      )}
      {error && <div style={{ marginTop: 10 }}>
        <Notice tone="warn" title="No picture from this camera">
          {error} You can still continue; the regions need a picture to be drawn against, so
          come back once the camera is reachable.
        </Notice>
      </div>}
    </div>
  );
}

/* ---------------- shapes ---------------- */

function RegionShape({ region, toScreen, selected }: {
  region: Region;
  toScreen: (u: number, v: number) => [number, number];
  selected: boolean;
}) {
  const colour = KIND_COLOUR[region.kind];
  const pts = region.points.map(([u, v]) => toScreen(u, v));

  return (
    <g>
      {region.kind === "line" ? (
        pts.length === 2 && <LineWithArrow a={pts[0]} b={pts[1]} colour={colour} bold={selected} />
      ) : pts.length >= 3 ? (
        <polygon points={pts.map((p) => p.join(",")).join(" ")}
                 fill={colour} fillOpacity={selected ? 0.3 : 0.18}
                 stroke={colour} strokeWidth={selected ? 3 : 2}
                 strokeDasharray={region.kind === "exclusion" ? "7 4" : undefined} />
      ) : (
        <polyline points={pts.map((p) => p.join(",")).join(" ")} fill="none"
                  stroke={colour} strokeWidth={2} strokeDasharray="5 4" />
      )}
      {pts.map(([x, y], i) => (
        <circle key={i} cx={x} cy={y} r={selected ? 6.5 : 5}
                fill="#fff" stroke={colour} strokeWidth={2} />
      ))}
    </g>
  );
}

/** A counting line is directional, so it is drawn with the direction on it. */
function LineWithArrow({ a, b, colour, bold }: {
  a: [number, number]; b: [number, number]; colour: string; bold: boolean;
}) {
  const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2;
  const len = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
  const nx = -(b[1] - a[1]) / len, ny = (b[0] - a[0]) / len;
  const tipX = mx + nx * 30, tipY = my + ny * 30;
  const wingX = -ny * 7, wingY = nx * 7;
  return (
    <>
      <line x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} stroke={colour} strokeWidth={bold ? 4 : 3} />
      <line x1={mx} y1={my} x2={tipX} y2={tipY} stroke={colour} strokeWidth={2} />
      <polygon points={`${tipX},${tipY} ${tipX - nx * 9 + wingX},${tipY - ny * 9 + wingY} ${tipX - nx * 9 - wingX},${tipY - ny * 9 - wingY}`}
               fill={colour} />
      <text x={mx - nx * 16} y={my - ny * 16 + 4} fontSize={11} fill="#fff"
            stroke="#101620" strokeWidth={3} paintOrder="stroke" textAnchor="middle">A</text>
      <text x={tipX + nx * 12} y={tipY + ny * 12 + 4} fontSize={11} fill="#fff"
            stroke="#101620" strokeWidth={3} paintOrder="stroke" textAnchor="middle">B</text>
    </>
  );
}

function DraftShape({ points, kind, toScreen }: {
  points: number[][]; kind: RegionKind;
  toScreen: (u: number, v: number) => [number, number];
}) {
  const colour = KIND_COLOUR[kind];
  const pts = points.map(([u, v]) => toScreen(u, v));
  return (
    <g>
      <polyline points={pts.map((p) => p.join(",")).join(" ")} fill="none"
                stroke={colour} strokeWidth={2} strokeDasharray="6 4" />
      {pts.map(([x, y], i) => (
        <circle key={i} cx={x} cy={y} r={5} fill="#fff" stroke={colour} strokeWidth={2} />
      ))}
    </g>
  );
}

/* ---------------- geometry helpers ---------------- */

function distanceToSegment(p: number[], a: number[], b: number[]): number {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const lengthSq = dx * dx + dy * dy;
  if (lengthSq === 0) return Math.hypot(p[0] - a[0], p[1] - a[1]);
  let t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / lengthSq;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy));
}

function pointInPolygon(p: number[], polygon: number[][]): boolean {
  if (polygon.length < 3) return false;
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [xi, yi] = polygon[i];
    const [xj, yj] = polygon[j];
    if ((yi > p[1]) !== (yj > p[1])
        && p[0] < ((xj - xi) * (p[1] - yi)) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}
