export type PointRole = "calibration" | "validation";
export type ZoneKind = "monitored" | "entrance" | "exit";
export type RelationshipKind = "overlap" | "transition" | "excluded";
export type VerificationStatus = "unverified" | "verified" | "needs_review";

/** What an installer sees, kept separate from the internal statuses. */
export type ConnectionBadge = "not_tested" | "reachable" | "failed";
export type CalibrationBadge = "unconfigured" | "approximate" | "solved" | "stale";
export type ValidationBadge = "not_checked" | "passed" | "failed";

export interface Workspace {
  id: number;
  name: string;
  floor_id: number | null;
  floor_label: string | null;
  origin_description: string | null;
  x_axis_description: string | null;
  surface_description: string | null;
  width_m: number | null;
  length_m: number | null;
  grid_min_x: number;
  grid_max_x: number;
  grid_min_y: number;
  grid_max_y: number;
  grid_spacing_m: number;
  floor_plane_z: number;
  max_ground_error_m: number;
  min_reference_points: number;
  definition_revision: number;
  camera_count: number;
  point_count: number;
  calibration_point_count: number;
  validation_point_count: number;
  created_at: string;
  updated_at: string;
}

export interface FloorPoint {
  id: number;
  workspace_id: number;
  code: string;
  name: string;
  x: number;
  y: number;
  z: number;
  role: PointRole;
  description: string | null;
  measurement_notes: string | null;
  uncertainty_m: number | null;
  aruco_marker_id: number | null;
  aruco_marker_size_m: number | null;
  aruco_corner_index: number | null;
  observed_by_camera_ids: number[];
}

export interface Match {
  observation_id: number;
  reference_point_id: number;
  code: string;
  name: string;
  role: PointRole;
  world_x: number;
  world_y: number;
  pixel_u: number;
  pixel_v: number;
  image_width: number;
  image_height: number;
}

export interface MatchIssue {
  level: "error" | "warning";
  code: string;
  message: string;
}

export interface MatchStatus {
  camera_id: number;
  workspace_id: number | null;
  image_width: number | null;
  image_height: number | null;
  matches: Match[];
  unmatched_points: FloorPoint[];
  calibration_matched: number;
  validation_matched: number;
  issues: MatchIssue[];
  ready_to_calculate: boolean;
}

export interface MappingPointResult {
  reference_point_id: number;
  code: string;
  name: string;
  role: PointRole;
  measured: number[];
  predicted: number[] | null;
  error_m: number | null;
  error_cm: number | null;
  pixel: number[];
  used_in_fit?: boolean;
  rejected?: boolean;
}

export interface MappingResult {
  method: string;
  pixel_convention: "raw" | "undistorted";
  distortion_corrected: boolean;
  image_width: number;
  image_height: number;
  used_point_ids: number[];
  rejected_point_ids: number[];
  per_point: MappingPointResult[];
  mean_error_m: number;
  median_error_m: number;
  max_error_m: number;
  mean_reprojection_error_px: number;
  coverage_polygon: number[][];
  coverage_area_m2: number;
  condition_number: number;
  ransac_threshold_px: number;
  ransac_threshold_space: string;
  warnings: string[];
  notes: string[];
  revision_id: number;
  revision_number: number;
  camera_id: number;
  workspace_id: number;
  activated: boolean;
  next_step: string;
  H_image_to_floor: number[][];
  H_floor_to_image: number[][];
}

export interface Job {
  job_id: string;
  kind: string;
  status: "queued" | "running" | "succeeded" | "failed";
  progress: number;
  step: string;
  result: MappingResult | null;
  error: string | null;
  hint: string | null;
}

export interface AccuracyCheck {
  passed: boolean;
  threshold_m: number;
  held_out: MappingPointResult[];
  held_out_count: number;
  summary: { mean_m: number | null; median_m: number | null; max_m: number | null };
  distribution: { count: number; extent_m: number[]; area_m2: number } | null;
  messages: string[];
  interpretation: string;
}

export interface AccuracyResponse {
  revision_id: number;
  revision_number: number;
  is_active: boolean;
  calibration_status: string;
  validation: AccuracyCheck;
  next_step: string;
}

export interface Zone {
  id: number;
  camera_id: number;
  camera_name: string | null;
  name: string;
  kind: ZoneKind;
  image_polygon: number[][];
  image_width: number;
  image_height: number;
  world_polygon: number[][] | null;
  world_is_stale: boolean;
  notes: string | null;
  updated_at: string;
}

export interface Relationship {
  id: number;
  kind: RelationshipKind;
  camera_a_id: number;
  camera_a_name: string | null;
  camera_b_id: number;
  camera_b_name: string | null;
  coordinate_system_id: number | null;
  zone_a_id: number | null;
  zone_a_name: string | null;
  zone_b_id: number | null;
  zone_b_name: string | null;
  overlap_polygon: number[][] | null;
  overlap_area_m2: number | null;
  min_travel_seconds: number | null;
  max_travel_seconds: number | null;
  suggested_by_geometry: boolean;
  verification: VerificationStatus;
  verified_by: string | null;
  verified_at: string | null;
  review_reason: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
  warnings: string[];
}

export interface GraphNode {
  camera_id: number;
  name: string;
  area: string | null;
  workspace_id: number | null;
  connection_status: string;
  calibration_status: string;
  has_mapping: boolean;
  coverage_polygon: number[][] | null;
  zones: Array<{ id: number; name: string; kind: ZoneKind }>;
}

export interface OverlapSuggestion {
  camera_a_id: number;
  camera_a_name: string;
  camera_b_id: number;
  camera_b_name: string;
  coordinate_system_id: number | null;
  overlap_polygon: number[][];
  overlap_area_m2: number;
  fraction_of_a: number;
  fraction_of_b: number;
  note: string;
}

export interface RelationshipGraph {
  cameras: GraphNode[];
  relationships: Relationship[];
  summary: {
    cameras: number;
    possible_pairs: number;
    overlaps: number;
    transitions: number;
    excluded: number;
    pairs_with_a_record: number;
    pairs_unknown: number;
    verified: number;
    unverified: number;
    needs_review: number;
    interpretation: string;
  };
  suggestions: OverlapSuggestion[];
}

export interface DetectedMarker {
  marker_id: number;
  corners_px: number[][];
  centre_px: number[];
  side_px: number;
  known: boolean;
  reference_point_id: number | null;
  reference_code: string | null;
  corner_index: number | null;
  world_xy: number[] | null;
  issues: string[];
}

export interface MarkerDetection {
  dictionary: string;
  image_width: number;
  image_height: number;
  detected: DetectedMarker[];
  usable_count: number;
  warnings: string[];
  note: string;
  camera_id: number;
}

export interface ConsistencyResult {
  label: string;
  workspace: { id: number; name: string };
  cameras: Array<{
    camera_id: number;
    camera_name: string;
    pixel: number[];
    floor: number[] | null;
    within_checked_area?: boolean;
    caveats?: string[];
    error?: string;
  }>;
  usable_count: number;
  pairwise: Array<{ camera_a: string; camera_b: string; disagreement_m: number }>;
  max_disagreement_m: number;
  mean_disagreement_m: number;
  warnings: string[];
  heading: string;
  interpretation: string;
  ground_truth?: { x: number; y: number };
  per_camera_error_m?: Array<{ camera_name: string; error_m: number }>;
}
