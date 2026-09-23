export type ConnectionType = "onvif" | "rtsp";

export type TestStatus =
  | "untested" | "online" | "partial" | "auth_failed" | "unreachable" | "timeout" | "error";

export type ReviewStatus = "confirmed" | "needs_review";

export interface LocationPath {
  site_id?: number | null; site?: string | null;
  building_id?: number | null; building?: string | null;
  floor_id?: number | null; floor?: string | null;
  area_id?: number | null; area?: string | null;
}

export interface Placement {
  id: number;
  camera_id: number;
  floor_plan_id: number;
  norm_x: number;
  norm_y: number;
  heading_deg: number;
  mounting_height_m: number | null;
  fov_deg: number | null;
  view_distance_m: number | null;
  review_status: ReviewStatus;
  updated_at: string;
}

export interface Camera {
  id: number;
  name: string;
  host: string;
  connection_type: ConnectionType;
  onvif_port: number | null;
  onvif_path: string | null;
  rtsp_port: number | null;
  stream_path: string | null;
  username: string | null;
  has_password: boolean;
  manufacturer: string | null;
  model: string | null;
  notes: string | null;
  installation_description: string | null;
  mounting_height_m: number | null;
  installation_photo_path: string | null;
  selected_profile_token: string | null;
  selected_profile_name: string | null;
  profile_resolution: string | null;
  profile_encoding: string | null;
  snapshot_supported: boolean;
  last_test_status: TestStatus;
  last_test_at: string | null;
  last_test_error: string | null;
  last_test_detail: string | null;
  created_at: string;
  updated_at: string;
  location: LocationPath;
  placement: Placement | null;
}

export interface CameraSummary {
  total: number;
  reachable: number;
  failed: number;
  untested: number;
  awaiting_placement: number;
  placements_need_review: number;
}

export interface StreamProfile {
  token: string;
  name: string;
  encoding: string | null;
  resolution: string | null;
  fps: number | null;
}

export interface TestResult {
  status: TestStatus;
  ok: boolean;
  login_ok: boolean;
  stream_ok: boolean;
  snapshot_supported: boolean;
  summary: string;
  error: string | null;
  tested_at: string;
  profiles: StreamProfile[];
  selected_profile_token: string | null;
  manufacturer: string | null;
  model: string | null;
  firmware: string | null;
}

export interface Area { id: number; name: string; floor_id: number; camera_count: number }
export interface Floor {
  id: number; name: string; building_id: number; areas: Area[];
  has_floor_plan: boolean; floor_plan_id: number | null;
}
export interface Building { id: number; name: string; site_id: number; floors: Floor[] }
export interface Site { id: number; name: string; buildings: Building[] }

export interface FloorPlan {
  id: number;
  floor_id: number;
  original_filename: string;
  content_type: string;
  width_px: number;
  height_px: number;
  scale_px_per_metre: number | null;
  scale_point_a_x: number | null;
  scale_point_a_y: number | null;
  scale_point_b_x: number | null;
  scale_point_b_y: number | null;
  scale_distance_m: number | null;
  version: number;
  updated_at: string;
  image_url: string;
  location: LocationPath;
  placement_count: number;
  needs_review_count: number;
}

export interface PreviewSession {
  session_id: string;
  camera_id: number;
  stream_url: string;
  expires_in_s: number;
  note: string;
}

/** Draft camera held by the registration wizard. The password lives here only
 *  until the camera is saved, then it is dropped from frontend state. */
export interface CameraDraft {
  name: string;
  host: string;
  connection_type: ConnectionType;
  onvif_port: number;
  onvif_path: string;
  rtsp_port: number;
  stream_path: string;
  username: string;
  password: string;
  manufacturer: string;
  model: string;
  notes: string;
  selected_profile_token: string | null;
  selected_profile_name: string | null;
  profile_resolution: string | null;
  profile_encoding: string | null;
  site: string; site_id: number | null;
  building: string; building_id: number | null;
  floor: string; floor_id: number | null;
  area: string; area_id: number | null;
  installation_description: string;
  mounting_height_m: string;
  photo: File | null;
  placement: {
    floor_plan_id: number | null;
    norm_x: number;
    norm_y: number;
    heading_deg: number;
    fov_deg: number | null;
    view_distance_m: number | null;
  } | null;
}
