export type CalibrationMethod = "manual" | "homography" | "pnp";

export type CalibrationStatus =
  | "unconfigured"
  | "approximate"
  | "calibrated_unvalidated"
  | "validated"
  | "needs_recalibration";

export type PixelConvention = "raw" | "undistorted";
export type ObservationRole = "fit" | "holdout";

export interface Conventions {
  world_frame: string;
  camera_frame: string;
  authoritative_pose: string;
  rpy_convention: string;
  zero_rotation_note: string;
  map_heading: string;
  yaw_vs_heading: string;
  floor_plane: string;
  opengl_note: string;
}

export interface CoordinateSystem {
  id: number;
  name: string;
  site_id: number | null;
  origin_description: string | null;
  notes: string | null;
  origin_photo_path: string | null;
  floor_plane_z: number;
  grid_min_x: number;
  grid_max_x: number;
  grid_min_y: number;
  grid_max_y: number;
  grid_spacing_m: number;
  max_reprojection_error_px: number;
  max_ground_error_m: number;
  min_reference_points: number;
  definition_revision: number;
  created_at: string;
  updated_at: string;
  reference_point_count: number;
  camera_count: number;
  conventions: Partial<Conventions>;
}

export interface ReferencePoint {
  id: number;
  coordinate_system_id: number;
  code: string;
  name: string;
  x: number;
  y: number;
  z: number;
  description: string | null;
  measurement_notes: string | null;
  uncertainty_m: number | null;
  photo_path: string | null;
  aruco_dictionary: string | null;
  aruco_marker_id: number | null;
  aruco_marker_size_m: number | null;
  aruco_corner_index: number | null;
  observation_count: number;
}

export interface CameraIntrinsics {
  id: number;
  camera_id: number;
  label: string;
  model: string;
  camera_matrix: number[][];
  distortion_coefficients: number[];
  width: number;
  height: number;
  image_rotation_deg: number;
  crop: number[] | null;
  geometry_key: string;
  lens_description: string | null;
  zoom_state: string | null;
  source: string;
  rms_reprojection_error_px: number | null;
  view_count: number | null;
  calibrated_at: string | null;
  notes: string | null;
  is_active: boolean;
  created_at: string;
  horizontal_fov_deg: number | null;
  vertical_fov_deg: number | null;
}

export interface Observation {
  id: number;
  camera_id: number;
  reference_point_id: number;
  pixel_u: number;
  pixel_v: number;
  image_width: number;
  image_height: number;
  role: ObservationRole;
  source: string;
  notes: string | null;
  reference_point: ReferencePoint | null;
}

export interface Vec3 { x: number; y: number; z: number }
export interface Rpy { roll: number; pitch: number; yaw: number }

export interface CalibrationMetrics {
  method?: string;
  solver?: string;
  mean_reprojection_error_px?: number;
  max_reprojection_error_px?: number;
  mean_floor_residual_m?: number;
  inlier_indices?: number[];
  outlier_indices?: number[];
  planar_points?: boolean;
  ambiguity_note?: string | null;
  point_spread?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface ValidationSummary {
  passed: boolean;
  fit_reprojection_error_px: number | null;
  holdout_ground_error_m: number | null;
  holdout_max_error_m: number | null;
  holdout_point_count: number;
  fit_point_count: number;
  inlier_count: number;
  outlier_count: number;
  reviewer: string | null;
  created_at: string;
}

export interface CalibrationRevision {
  id: number;
  camera_id: number;
  coordinate_system_id: number;
  coordinate_system_revision: number;
  revision_number: number;
  parent_revision_id: number | null;
  method: CalibrationMethod;
  status: CalibrationStatus;
  position: Vec3 | null;
  rpy_deg: Rpy | null;
  quaternion_wxyz: number[] | null;
  T_world_camera: number[][] | null;
  map_heading_deg: number | null;
  has_homography: boolean;
  plane_z: number;
  pixel_convention: PixelConvention;
  distortion_corrected: boolean;
  source_image_width: number | null;
  source_image_height: number | null;
  intrinsics_id: number | null;
  approx_hfov_deg: number | null;
  approx_vfov_deg: number | null;
  approx_range_m: number | null;
  solver: string | null;
  metrics: CalibrationMetrics | null;
  warnings: string[];
  caveats: string[];
  notes: string | null;
  is_active: boolean;
  activated_at: string | null;
  created_by: string | null;
  created_at: string;
  latest_validation: ValidationSummary | null;
}

export interface ValidationDetail {
  passed: boolean;
  fit_reprojection_error_px: number | null;
  holdout_ground_error_m: number | null;
  holdout_max_error_m: number | null;
  holdout_point_count: number;
  fit_point_count: number;
  inlier_count: number;
  outlier_count: number;
  per_point: Array<Record<string, unknown>>;
  thresholds: Record<string, number>;
  messages: string[];
  interpretation: Record<string, string>;
}

export interface MapCamera {
  camera_id: number;
  name: string;
  calibration_status: CalibrationStatus;
  method: CalibrationMethod | null;
  connection_status: string;
  position: Vec3 | null;
  rpy_deg: Rpy | null;
  map_heading_deg: number | null;
  approx_hfov_deg: number | null;
  approx_range_m: number | null;
  horizontal_fov_deg: number | null;
  vertical_fov_deg: number | null;
  floor_polygon: number[][] | null;
  floor_polygon_clipped: boolean;
  area_is_mapped_coverage: boolean;
  is_approximate: boolean;
  revision_id: number | null;
  area: string | null;
}

export interface FactoryMap {
  coordinate_system: CoordinateSystem;
  cameras: MapCamera[];
  reference_points: ReferencePoint[];
  floor_plan: {
    floor_plan_id: number;
    image_url: string;
    width_px: number;
    height_px: number;
    metres_per_pixel: number;
    origin_x: number;
    origin_y: number;
    rotation_deg: number;
    note: string;
  } | null;
  conventions: Conventions;
}

export interface ProjectionResult {
  world?: number[];
  pixel?: number[];
  in_image?: boolean;
  method: string;
  plane_z?: number;
  distance_from_camera_m: number | null;
  caveats: string[];
  calibration_status: CalibrationStatus;
  is_approximate: boolean;
  revision_id: number;
}

export interface GroundCheckResult {
  label: string;
  plane_z: number;
  cameras: Array<{
    camera_id: number;
    camera_name: string;
    world: number[] | null;
    pixel: number[];
    error?: string;
    calibration_status?: string;
    method?: string;
    is_approximate?: boolean;
    distance_from_camera_m?: number | null;
  }>;
  usable_count: number;
  pairwise: Array<{
    camera_a_id: number; camera_a_name: string;
    camera_b_id: number; camera_b_name: string;
    disagreement_m: number;
  }>;
  max_disagreement_m: number;
  mean_disagreement_m: number;
  centroid_world: number[] | null;
  max_distance_from_centroid_m: number | null;
  interpretation: string;
  warning?: string;
}
