export type GeometryProvenance = "estimated" | "measured";

export type SceneObjectKind =
  | "wall" | "door" | "column" | "machine" | "rack"
  | "workstation" | "conveyor" | "walkway" | "restricted_area";

export type SceneAssetKind = "floor_plan" | "model_3d";
export type MountType = "wall" | "ceiling" | "column" | "free";

export type FunctionKind =
  | "people_counting" | "product_counting" | "restricted_zone" | "ppe_monitoring"
  | "workstation_occupancy" | "twin_positions" | "cross_camera_tracking";

export type ConfigSpace = "image" | "world";

export interface SceneObject {
  id: number;
  kind: SceneObjectKind;
  name: string;
  x: number;
  y: number;
  z: number;
  rotation_deg: number;
  width_m: number;
  depth_m: number;
  height_m: number;
  points: number[][] | null;
  footprint: number[][];
  provenance: GeometryProvenance;
  colour: string;
  notes: string | null;
}

export interface SceneAsset {
  id: number;
  kind: SceneAssetKind;
  original_filename: string;
  content_type: string;
  size_bytes: number;
  width_px: number | null;
  height_px: number | null;
  metres_per_pixel: number | null;
  origin_x: number | null;
  origin_y: number | null;
  rotation_deg: number | null;
  model_scale: number | null;
  model_up_axis: string | null;
  model_floor_offset_m: number | null;
  model_rotation_deg: number | null;
  is_reference_only: boolean;
  notes: string | null;
  url: string;
}

export interface FactoryScene {
  id: number;
  workspace_id: number;
  workspace_name: string;
  name: string;
  building_width_m: number | null;
  building_length_m: number | null;
  geometry_provenance: GeometryProvenance;
  notes: string | null;
  draft_revision: number;
  published_revision: number | null;
  published_at: string | null;
  published_by: string | null;
  has_unpublished_changes: boolean;
  objects: SceneObject[];
  assets: SceneAsset[];
  grid: { min_x: number; max_x: number; min_y: number; max_y: number;
          spacing_m: number; floor_z: number };
  conventions: Record<string, string>;
}

export interface PaletteItem {
  kind: SceneObjectKind;
  label: string;
  default_width_m: number;
  default_depth_m: number;
  default_height_m: number;
  colour: string;
  shape: "box" | "outline" | "run";
}

export interface PlacementResult {
  camera_id: number;
  revision_id: number;
  revision_number: number;
  status: string;
  position: { x: number; y: number; z: number };
  aim: {
    mount_type: string;
    height_m: number;
    aims_at: number[];
    distance_to_target_m: number;
    tilt_below_horizontal_deg: number;
    map_heading_deg: number | null;
    note: string;
  };
  frustum_floor_polygon: number[][] | null;
  frustum_clipped: boolean;
  is_approximate: boolean;
  fov_source: "measured_intrinsics" | "illustrative" | "none";
  /** False when a solved calibration was already active and was left alone. */
  activated: boolean;
  warnings: string[];
}

export interface FunctionStatus {
  state: "configured_unavailable" | "blocked" | "disabled" | "running";
  summary: string;
  detail: string;
  processing_available: boolean;
  blockers: string[];
  space: ConfigSpace;
  requires_floor_mapping: boolean;
}

export interface CameraFunction {
  id: number;
  camera_id: number;
  kind: FunctionKind;
  label: string;
  name: string;
  enabled: boolean;
  space: ConfigSpace;
  config: Record<string, unknown>;
  image_width: number | null;
  image_height: number | null;
  zone_id: number | null;
  scene_object_id: number | null;
  notes: string | null;
  status: FunctionStatus;
  updated_at: string;
}

export interface FunctionCatalogueEntry {
  kind: FunctionKind;
  label: string;
  processing_available: boolean;
  requires_floor_mapping: boolean;
  configuration: string;
  space: ConfigSpace;
}

export interface Capability {
  state: "ready" | "partial" | "blocked" | "unavailable";
  label: string;
  detail: string;
  next_step: string | null;
  counts: Record<string, number>;
}

export interface Readiness {
  capabilities: Capability[];
  overall: "ready" | "partial" | "blocked";
  interpretation: string;
  simulation_note: string;
  workspace: { id: number; name: string };
  scene: { id: number; name: string; published: boolean;
           has_unpublished_changes: boolean } | null;
}

export interface Checkpoint {
  id: number;
  session_id: number;
  camera_id: number | null;
  camera_name: string | null;
  recorded_at: string;
  label: string;
  pixel_u: number | null;
  pixel_v: number | null;
  projected_x: number | null;
  projected_y: number | null;
  measured_x: number | null;
  measured_y: number | null;
  error_m: number | null;
  observation: string | null;
}

export interface CommissioningSession {
  id: number;
  workspace_id: number;
  name: string;
  camera_ids: number[];
  started_at: string;
  ended_at: string | null;
  operator: string | null;
  notes: string | null;
  checkpoints: Checkpoint[];
  accuracy: {
    checkpoints: number;
    with_measured_position: number;
    mean_error_m?: number;
    max_error_m?: number;
    interpretation: string;
  };
}
