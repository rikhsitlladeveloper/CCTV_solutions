import type { ReactNode } from "react";
import { useEffect } from "react";
import type { Camera, TestStatus } from "../lib/types";

/* Status presentation. Every badge carries a word and a glyph, so the meaning
   survives without colour. */

const STATUS_META: Record<TestStatus, { label: string; tone: string; glyph: string }> = {
  online: { label: "Online", tone: "badge-ok", glyph: "●" },
  partial: { label: "Login only", tone: "badge-warn", glyph: "◐" },
  auth_failed: { label: "Auth failed", tone: "badge-danger", glyph: "✕" },
  unreachable: { label: "Unreachable", tone: "badge-danger", glyph: "✕" },
  timeout: { label: "Timed out", tone: "badge-danger", glyph: "✕" },
  error: { label: "Failed", tone: "badge-danger", glyph: "✕" },
  untested: { label: "Not tested", tone: "badge-neutral", glyph: "○" },
};

export function StatusBadge({ status }: { status: TestStatus }) {
  const meta = STATUS_META[status] ?? STATUS_META.untested;
  return (
    <span className={`badge ${meta.tone}`}>
      <span aria-hidden="true">{meta.glyph}</span>
      {meta.label}
    </span>
  );
}

export function PlacementBadge({ camera }: { camera: Camera }) {
  if (!camera.placement) {
    return <span className="badge badge-neutral"><span aria-hidden="true">○</span>Not placed</span>;
  }
  if (camera.placement.review_status === "needs_review") {
    return <span className="badge badge-warn"><span aria-hidden="true">!</span>Review placement</span>;
  }
  return <span className="badge badge-accent"><span aria-hidden="true">◎</span>Placed</span>;
}

export function statusLabel(status: TestStatus): string {
  return (STATUS_META[status] ?? STATUS_META.untested).label;
}

/** "Last verified at ..." wording differs from "never tested" on purpose. */
export function LastChecked({ camera }: { camera: Camera }) {
  if (!camera.last_test_at) {
    return <span className="faint small">Never tested</span>;
  }
  const when = new Date(camera.last_test_at);
  const prefix = camera.last_test_status === "online" ? "Last verified" : "Last checked";
  return (
    <span className="small muted" title={when.toString()}>
      {prefix} {when.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}
    </span>
  );
}

export function Notice({ tone = "info", title, children }: {
  tone?: "info" | "warn" | "danger" | "ok" | "plain";
  title?: string;
  children?: ReactNode;
}) {
  const glyph = { info: "i", warn: "!", danger: "✕", ok: "✓", plain: "·" }[tone];
  return (
    <div className={`notice ${tone === "plain" ? "" : `notice-${tone}`}`} role={tone === "danger" ? "alert" : undefined}>
      <span className="notice-icon" aria-hidden="true">{glyph}</span>
      <div>
        {title && <strong>{title}</strong>}
        {children}
      </div>
    </div>
  );
}

export function Modal({ title, children, footer, onClose, wide }: {
  title: string; children: ReactNode; footer?: ReactNode; onClose: () => void; wide?: boolean;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal" style={wide ? { width: "min(760px, 100%)" } : undefined}
           role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-head"><h2>{title}</h2></div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="row" style={{ gap: 7 }}>
      <span className="spinner" aria-hidden="true" />
      {label && <span className="small muted">{label}</span>}
    </span>
  );
}

export function locationSummary(camera: Camera): string {
  const l = camera.location;
  const parts = [l.site, l.building, l.floor, l.area].filter(Boolean);
  return parts.length ? parts.join(" · ") : "No location set";
}
