import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, getToken, mediaUrl } from "../lib/api";
import { Notice, Spinner } from "./ui";

/** Preview is always server-produced. RTSP is never handed to the browser:
 *  snapshots are JPEG, live view is MJPEG transcoded by the backend. */
type Mode = "idle" | "snapshot" | "live";

interface Props {
  /** Saved camera: enables live preview. */
  cameraId?: number;
  /** Unsaved wizard draft: snapshot only, taken from the entered details. */
  draftBody?: Record<string, unknown>;
  compact?: boolean;
}

export default function PreviewPanel({ cameraId, draftBody, compact }: Props) {
  const [mode, setMode] = useState<Mode>("idle");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [snapshotUrl, setSnapshotUrl] = useState<string | null>(null);
  const [liveUrl, setLiveUrl] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const sessionRef = useRef<string | null>(null);
  const objectUrlRef = useRef<string | null>(null);

  const releaseSnapshot = useCallback(() => {
    if (objectUrlRef.current) {
      URL.revokeObjectURL(objectUrlRef.current);
      objectUrlRef.current = null;
    }
  }, []);

  const stopLive = useCallback(async () => {
    const id = sessionRef.current;
    sessionRef.current = null;
    setLiveUrl(null);
    if (id) {
      // Tell the server to tear down ffmpeg rather than waiting for the reaper.
      try { await api.stopPreview(id); } catch { /* already reaped */ }
    }
  }, []);

  useEffect(() => () => {
    releaseSnapshot();
    const id = sessionRef.current;
    if (id) {
      const token = getToken();
      // The component may be unmounting on navigation; keepalive lets the
      // stop request finish so no ffmpeg process is orphaned.
      fetch(`/api/preview/${id}/stop`, {
        method: "POST", keepalive: true,
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      }).catch(() => undefined);
    }
  }, [releaseSnapshot]);

  const takeSnapshot = async () => {
    setBusy(true); setError(null); setNote(null);
    await stopLive();
    try {
      const token = getToken();
      const res = cameraId
        ? await fetch(`/api/cameras/${cameraId}/snapshot`, {
            headers: token ? { Authorization: `Bearer ${token}` } : undefined,
          })
        : await fetch("/api/cameras/snapshot-preview", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              ...(token ? { Authorization: `Bearer ${token}` } : {}),
            },
            body: JSON.stringify(draftBody ?? {}),
          });
      if (!res.ok) {
        let detail = `Snapshot failed (${res.status})`;
        try { detail = (await res.json()).detail ?? detail; } catch { /* keep default */ }
        throw new Error(detail);
      }
      const blob = await res.blob();
      releaseSnapshot();
      const url = URL.createObjectURL(blob);
      objectUrlRef.current = url;
      setSnapshotUrl(url);
      setMode("snapshot");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Snapshot failed.");
      setMode("idle");
    } finally {
      setBusy(false);
    }
  };

  const startLive = async () => {
    if (!cameraId) return;
    setBusy(true); setError(null);
    try {
      const session = await api.startPreview(cameraId);
      sessionRef.current = session.session_id;
      setLiveUrl(mediaUrl(session.stream_url));
      setNote(session.note);
      setMode("live");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Live preview could not be started.");
      setMode("idle");
    } finally {
      setBusy(false);
    }
  };

  const stop = async () => {
    await stopLive();
    setMode(snapshotUrl ? "snapshot" : "idle");
    setNote(null);
  };

  return (
    <div>
      <div className="preview-frame" style={compact ? { minHeight: 150 } : undefined}>
        {mode === "live" && liveUrl ? (
          <img src={liveUrl} alt="Live camera preview" />
        ) : mode === "snapshot" && snapshotUrl ? (
          <img src={snapshotUrl} alt="Camera snapshot" />
        ) : (
          <div className="preview-placeholder">
            {busy ? <Spinner label="Contacting the camera…" /> : "No preview loaded."}
          </div>
        )}
      </div>

      <div className="preview-bar">
        <button className="btn btn-sm" onClick={takeSnapshot} disabled={busy} type="button">
          {mode === "snapshot" ? "Refresh snapshot" : "Take snapshot"}
        </button>
        {cameraId && mode !== "live" && (
          <button className="btn btn-sm" onClick={startLive} disabled={busy} type="button">
            Start live preview
          </button>
        )}
        {mode === "live" && (
          <button className="btn btn-sm btn-danger" onClick={stop} type="button">Stop preview</button>
        )}
        {busy && <Spinner />}
        {!cameraId && <span className="faint small">Live preview becomes available once the camera is saved.</span>}
      </div>

      {note && <div className="small faint" style={{ marginTop: 8 }}>{note}</div>}
      {error && <div style={{ marginTop: 10 }}><Notice tone="danger" title="Preview failed">{error}</Notice></div>}
    </div>
  );
}
