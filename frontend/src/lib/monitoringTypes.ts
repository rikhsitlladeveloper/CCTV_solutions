export type MonitoringStateName =
  | "draft" | "connected" | "configured" | "validation_pending" | "active";

export type EventKind =
  | "restricted_entry" | "line_crossing" | "station_occupancy"
  | "dwell_time" | "ppe_violation";

export type ReviewDecision = "unreviewed" | "confirmed" | "dismissed";

export interface FactoryEvent {
  id: number;
  camera_id: number;
  camera_name: string;
  area: string | null;
  building: string | null;
  function_id: number | null;
  kind: EventKind;
  kind_label: string;
  started_at: string;
  duration_s: number | null;
  rule_summary: string;
  facts: string[];
  overlay: Array<Record<string, unknown>> | null;
  image_width: number | null;
  image_height: number | null;
  has_snapshot: boolean;
  has_clip: boolean;
  decision: ReviewDecision;
  decided_by: string | null;
  decided_at: string | null;
  acknowledged: boolean;
  acknowledged_by: string | null;
  acknowledged_at: string | null;
  notes: string | null;
  is_sample: boolean;
  created_at: string;
}

export interface EventPage {
  items: FactoryEvent[];
  total: number;
  counts: {
    awaiting_review: number;
    confirmed: number;
    dismissed: number;
    unacknowledged: number;
    sample: number;
  };
  note: string;
}

export interface CameraMonitoringState {
  camera_id: number;
  state: MonitoringStateName;
  label: string;
  can_activate: boolean;
  blockers: string[];
  analytics_configured: number;
  analytics_valid: number;
  processing_available: boolean;
  calibration_needs_revalidation: boolean;
}

export interface OverviewGroup {
  building: string;
  floor: string | null;
  area: string;
  floor_id: number | null;
  cameras: Array<{
    id: number;
    name: string;
    connection: string;
    monitoring: MonitoringStateName;
    analytics: number;
  }>;
}

export interface FactoryOverview {
  health: {
    total: number;
    online: number;
    offline: number;
    degraded: number;
    untested: number;
    monitoring_active: number;
    with_analytics: number;
    note: string;
  };
  events: {
    awaiting_review: number;
    total_in_window: number;
    window_hours: number;
    sample_rows: number;
  };
  /** `value` is null whenever the number cannot honestly be produced. */
  production: {
    available: boolean;
    value: number | null;
    unavailable_reason: string | null;
    counting_lines_configured: number;
    sample_crossings: number;
  };
  groups: OverviewGroup[];
  floor_plans: Array<{
    id: number; floor_id: number; floor: string | null;
    original_filename: string; width_px: number; height_px: number; has_scale: boolean;
  }>;
  has_floor_plan: boolean;
  recent_events: FactoryEvent[];
}

export interface SetupProgress {
  camera_id: number;
  step: string;
  draft: Record<string, unknown>;
  completed: string[];
  updated_at: string | null;
  updated_by: string | null;
}

export interface RuleSummary {
  function_id: number;
  name: string;
  kind: string;
  summary: string;
  geometry_valid: boolean;
  geometry_problem: string | null;
}

export interface DiscoveredDevice {
  address: string;
  xaddrs: string[];
  name: string | null;
  hardware: string | null;
  already_registered: boolean;
  registered_camera_id: number | null;
}

export interface DiscoveryResult {
  devices: DiscoveredDevice[];
  searched_seconds: number;
  note: string;
}
