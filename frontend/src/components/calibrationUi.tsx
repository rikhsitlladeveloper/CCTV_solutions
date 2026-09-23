import type { ReactNode } from "react";
import type {
  CalibrationMethod, CalibrationRevision, CalibrationStatus, Conventions, ValidationDetail,
} from "../lib/calibrationTypes";
import { Notice } from "./ui";

/** Calibration status, always shown with a word and a glyph as well as colour. */
const CALIBRATION_META: Record<CalibrationStatus, { label: string; tone: string; glyph: string }> = {
  unconfigured: { label: "Unconfigured", tone: "badge-neutral", glyph: "○" },
  approximate: { label: "Approximate", tone: "badge-warn", glyph: "≈" },
  calibrated_unvalidated: { label: "Calibrated, not validated", tone: "badge-accent", glyph: "◐" },
  validated: { label: "Validated", tone: "badge-ok", glyph: "✓" },
  needs_recalibration: { label: "Needs recalibration", tone: "badge-danger", glyph: "!" },
};

export function CalibrationBadge({ status, suffix }: { status: CalibrationStatus; suffix?: string }) {
  const meta = CALIBRATION_META[status] ?? CALIBRATION_META.unconfigured;
  return (
    <span className={`badge ${meta.tone}`} title={CALIBRATION_HELP[status]}>
      <span aria-hidden="true">{meta.glyph}</span>
      {meta.label}{suffix}
    </span>
  );
}

export const CALIBRATION_HELP: Record<CalibrationStatus, string> = {
  unconfigured: "No position has been recorded for this camera.",
  approximate: "Placed by hand. Good enough to show on the map; not solved against measured points.",
  calibrated_unvalidated:
    "Solved from reference points, but no held-out points have confirmed it against ground truth.",
  validated:
    "Solved and checked against held-out reference points within the configured thresholds, over the area those points cover.",
  needs_recalibration:
    "Something it depends on changed — the coordinate system, a reference point, or the stream geometry.",
};

export const METHOD_META: Record<CalibrationMethod, { label: string; blurb: string }> = {
  manual: {
    label: "Manual placement",
    blurb: "Type or drag a position and orientation. Approximate by definition.",
  },
  homography: {
    label: "Floor-plane calibration",
    blurb: "Maps image pixels to floor X/Y. Needs no intrinsics, and recovers no camera pose.",
  },
  pnp: {
    label: "Full camera pose",
    blurb: "Recovers full 6-DoF position and orientation. Requires camera intrinsics.",
  },
};

export function ConventionsPanel({ conventions }: { conventions: Partial<Conventions> }) {
  const rows: Array<[string, string | undefined]> = [
    ["World frame", conventions.world_frame],
    ["Camera frame", conventions.camera_frame],
    ["Stored pose", conventions.authoritative_pose],
    ["Roll/pitch/yaw", conventions.rpy_convention],
    ["Zero rotation", conventions.zero_rotation_note],
    ["Map heading", conventions.map_heading],
    ["Yaw vs heading", conventions.yaw_vs_heading],
  ];
  return (
    <details className="card card-pad">
      <summary style={{ cursor: "pointer", fontWeight: 600, fontSize: 14 }}>
        Coordinate conventions
      </summary>
      <dl className="kv small" style={{ marginTop: 12 }}>
        {rows.filter(([, v]) => v).map(([label, value]) => (
          <div key={label} style={{ display: "contents" }}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

/** Fitting error and held-out error mean different things; never merge them. */
export function AccuracyPanel({ revision, validation }: {
  revision: CalibrationRevision; validation?: ValidationDetail | null;
}) {
  const metrics = revision.metrics ?? {};
  const latest = validation ?? null;
  const fitError = latest?.fit_reprojection_error_px ?? metrics.mean_reprojection_error_px ?? null;
  const groundError = latest?.holdout_ground_error_m ?? revision.latest_validation?.holdout_ground_error_m ?? null;
  const holdoutCount = latest?.holdout_point_count ?? revision.latest_validation?.holdout_point_count ?? 0;
  const inliers = (metrics.inlier_indices as number[] | undefined)?.length
    ?? revision.latest_validation?.inlier_count ?? 0;
  const outliers = (metrics.outlier_indices as number[] | undefined)?.length
    ?? revision.latest_validation?.outlier_count ?? 0;

  return (
    <div className="card card-pad">
      <h3 style={{ marginBottom: 10 }}>Accuracy</h3>
      <dl className="kv small">
        <dt>Fitting reprojection error</dt>
        <dd>
          {fitError != null ? `${fitError.toFixed(2)} px` : <span className="faint">—</span>}
          <div className="faint">
            How well the solve reproduced its own input. Not evidence of real-world accuracy.
          </div>
        </dd>
        <dt>Held-out ground error</dt>
        <dd>
          {groundError != null
            ? <strong>{groundError.toFixed(3)} m</strong>
            : <span className="faint">Not measured</span>}
          <div className="faint">
            {holdoutCount > 0
              ? `Measured against ${holdoutCount} point(s) the solver never saw.`
              : "No held-out points, so accuracy has not been independently checked."}
          </div>
        </dd>
        <dt>Points used</dt>
        <dd>
          {inliers} inlier{inliers === 1 ? "" : "s"}
          {outliers > 0 && <span className="faint"> · {outliers} rejected as outliers</span>}
        </dd>
        <dt>Validated</dt>
        <dd>
          {revision.latest_validation
            ? <>
                {new Date(revision.latest_validation.created_at).toLocaleString(undefined,
                  { dateStyle: "medium", timeStyle: "short" })}
                {revision.latest_validation.reviewer && ` by ${revision.latest_validation.reviewer}`}
              </>
            : <span className="faint">Never</span>}
        </dd>
      </dl>
    </div>
  );
}

export function CaveatList({ items, title, tone = "warn" }: {
  items: string[]; title: string; tone?: "warn" | "info" | "danger";
}): ReactNode {
  if (!items.length) return null;
  return (
    <Notice tone={tone} title={title}>
      <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
        {items.map((item, i) => <li key={i} style={{ marginBottom: 4 }}>{item}</li>)}
      </ul>
    </Notice>
  );
}
