import { useState, type ReactNode } from "react";
import type {
  CalibrationBadge, ConnectionBadge, ValidationBadge,
} from "../lib/setupTypes";
import type { CalibrationRevision } from "../lib/calibrationTypes";
import type { TestStatus } from "../lib/types";

/* Three independent facts about a camera, shown as three badges.
   Colour is never the only signal: each carries a word and a glyph. */

const CONNECTION: Record<ConnectionBadge, { label: string; tone: string; glyph: string; help: string }> = {
  not_tested: { label: "Not tested", tone: "badge-neutral", glyph: "○",
                help: "Nobody has tried to reach this camera yet." },
  reachable: { label: "Reachable", tone: "badge-ok", glyph: "●",
               help: "The camera answered and delivered a picture." },
  failed: { label: "Failed", tone: "badge-danger", glyph: "✕",
            help: "The last attempt to reach this camera did not work." },
};

const CALIBRATION: Record<CalibrationBadge, { label: string; tone: string; glyph: string; help: string }> = {
  unconfigured: { label: "Not set up", tone: "badge-neutral", glyph: "○",
                  help: "This camera has no floor mapping yet." },
  approximate: { label: "Approximate", tone: "badge-warn", glyph: "≈",
                 help: "Positioned by hand. Good enough to show on the map, not for measuring." },
  solved: { label: "Mapping ready", tone: "badge-accent", glyph: "◈",
            help: "A floor mapping has been calculated from your measured points." },
  stale: { label: "Needs redoing", tone: "badge-danger", glyph: "!",
           help: "Something the mapping relies on changed, so it can no longer be trusted." },
};

const VALIDATION: Record<ValidationBadge, { label: string; tone: string; glyph: string; help: string }> = {
  not_checked: { label: "Not checked", tone: "badge-neutral", glyph: "○",
                 help: "Accuracy has not been measured against separately surveyed points." },
  passed: { label: "Accuracy checked", tone: "badge-ok", glyph: "✓",
            help: "Checked against points that were not used to build the mapping, and within your limit." },
  failed: { label: "Check failed", tone: "badge-danger", glyph: "✕",
            help: "The check against separately surveyed points was outside your limit." },
};

export function connectionBadgeOf(status: TestStatus | string): ConnectionBadge {
  if (status === "online") return "reachable";
  if (status === "untested") return "not_tested";
  return "failed";
}

export function calibrationBadgeOf(revision: CalibrationRevision | null | undefined): CalibrationBadge {
  if (!revision) return "unconfigured";
  switch (revision.status) {
    case "needs_recalibration": return "stale";
    case "approximate": return "approximate";
    case "validated":
    case "calibrated_unvalidated": return "solved";
    default: return "unconfigured";
  }
}

export function validationBadgeOf(revision: CalibrationRevision | null | undefined): ValidationBadge {
  if (!revision?.latest_validation) return "not_checked";
  return revision.latest_validation.passed ? "passed" : "failed";
}

export function ConnectionBadgeView({ status }: { status: TestStatus | string }) {
  const meta = CONNECTION[connectionBadgeOf(status)];
  return <span className={`badge ${meta.tone}`} title={meta.help}>
    <span aria-hidden="true">{meta.glyph}</span>{meta.label}</span>;
}

export function CalibrationBadgeView({ state }: { state: CalibrationBadge }) {
  const meta = CALIBRATION[state];
  return <span className={`badge ${meta.tone}`} title={meta.help}>
    <span aria-hidden="true">{meta.glyph}</span>{meta.label}</span>;
}

export function ValidationBadgeView({ state }: { state: ValidationBadge }) {
  const meta = VALIDATION[state];
  return <span className={`badge ${meta.tone}`} title={meta.help}>
    <span aria-hidden="true">{meta.glyph}</span>{meta.label}</span>;
}

/** All three, in the order an installer works through them. */
export function CameraStatusRow({ connection, revision, compact }: {
  connection: TestStatus | string;
  revision: CalibrationRevision | null | undefined;
  compact?: boolean;
}) {
  return (
    <div className="row" style={{ gap: compact ? 5 : 8 }}>
      <ConnectionBadgeView status={connection} />
      <CalibrationBadgeView state={calibrationBadgeOf(revision)} />
      <ValidationBadgeView state={validationBadgeOf(revision)} />
    </div>
  );
}

/** Technical detail, collapsed by default and clearly marked. */
export function Advanced({ title = "Advanced details", children, note }: {
  title?: string; children: ReactNode; note?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="advanced">
      <button type="button" className="advanced-toggle" onClick={() => setOpen((v) => !v)}
              aria-expanded={open}>
        <span aria-hidden="true">{open ? "▾" : "▸"}</span> {title}
      </button>
      {open && (
        <div className="advanced-body">
          {note && <p className="hint" style={{ marginTop: 0 }}>{note}</p>}
          {children}
        </div>
      )}
    </div>
  );
}

/** A short instruction attached to the current step. */
export function Guidance({ children, tone = "info" }: {
  children: ReactNode; tone?: "info" | "warn";
}) {
  return (
    <div className={`guidance guidance-${tone}`}>
      <span className="guidance-mark" aria-hidden="true">{tone === "warn" ? "!" : "i"}</span>
      <div>{children}</div>
    </div>
  );
}

export function StepProgress({ steps, current, furthest, onJump }: {
  steps: string[]; current: number; furthest: number; onJump: (i: number) => void;
}) {
  return (
    <ol className="wizard-steps">
      {/* Keyed by position, not label: step 1 and step 7 are both "Connect
          cameras" - first connecting to them, then connecting them together. */}
      {steps.map((label, i) => {
        const state = i === current ? "is-current" : i < furthest ? "is-done" : "";
        return (
          <li key={i}>
            <button type="button" className={`wizard-step ${state}`}
                    disabled={i > furthest} onClick={() => onJump(i)}>
              <span className="wizard-step-num">{i < furthest ? "✓" : i + 1}</span>
              <span>{label}</span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

/** Errors and warnings from the pre-flight checks, in plain words. */
export function IssueList({ issues }: { issues: Array<{ level: string; message: string }> }) {
  if (!issues.length) return null;
  const errors = issues.filter((i) => i.level === "error");
  const warnings = issues.filter((i) => i.level !== "error");
  return (
    <>
      {errors.length > 0 && (
        <div className="notice notice-danger" role="alert">
          <span className="notice-icon" aria-hidden="true">✕</span>
          <div>
            <strong>{errors.length === 1 ? "This needs fixing" : "These need fixing"}</strong>
            <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
              {errors.map((i, k) => <li key={k}>{i.message}</li>)}
            </ul>
          </div>
        </div>
      )}
      {warnings.length > 0 && (
        <div className="notice notice-warn">
          <span className="notice-icon" aria-hidden="true">!</span>
          <div>
            <strong>Worth checking</strong>
            <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
              {warnings.map((i, k) => <li key={k}>{i.message}</li>)}
            </ul>
          </div>
        </div>
      )}
    </>
  );
}

/** Error in centimetres, which is what an installer actually thinks in. */
export function ErrorValue({ metres, threshold }: { metres: number | null; threshold?: number }) {
  if (metres === null || metres === undefined || Number.isNaN(metres)) {
    return <span className="faint">—</span>;
  }
  const cm = metres * 100;
  const over = threshold !== undefined && metres > threshold;
  return (
    <strong style={{ color: over ? "var(--danger)" : undefined }}>
      {cm < 10 ? cm.toFixed(1) : cm.toFixed(0)} cm
    </strong>
  );
}
