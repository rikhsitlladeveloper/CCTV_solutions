import type {
  Area, Camera, CameraSummary, FloorPlan, PreviewSession, Site, TestResult,
} from "./types";
import type {
  CalibrationRevision, CameraIntrinsics, Conventions, CoordinateSystem, FactoryMap,
  GroundCheckResult, Observation, ProjectionResult, ReferencePoint, ValidationDetail,
} from "./calibrationTypes";

const TOKEN_KEY = "numenor.token";

export function getToken(): string | null {
  try { return localStorage.getItem(TOKEN_KEY); } catch { return null; }
}
export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch { /* storage unavailable; the session simply won't persist */ }
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function parseError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) {
      return body.detail.map((d: { loc?: string[]; msg: string }) =>
        `${d.loc?.slice(1).join(".") ?? ""}: ${d.msg}`.replace(/^: /, "")).join("; ");
    }
  } catch { /* fall through to status text */ }
  return res.statusText || `Request failed (${res.status})`;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  const res = await fetch(path, { ...init, headers });
  if (res.status === 401) {
    setToken(null);
    if (!path.includes("/auth/login")) window.dispatchEvent(new Event("numenor:signed-out"));
    throw new ApiError(401, await parseError(res));
  }
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

/** URL for an authenticated <img>/<video> source. Tags cannot send headers,
 *  so the session token travels as a query parameter. */
export function mediaUrl(path: string): string {
  const token = getToken();
  const sep = path.includes("?") ? "&" : "?";
  return token ? `${path}${sep}token=${encodeURIComponent(token)}` : path;
}

export const api = {
  login: (username: string, password: string) =>
    request<{ access_token: string; username: string; expires_at: number }>(
      "/api/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  me: () => request<{ username: string }>("/api/auth/me"),

  // cameras
  listCameras: (params: Record<string, string | number | undefined> = {}) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== "" && v !== null) q.set(k, String(v));
    });
    const qs = q.toString();
    return request<Camera[]>(`/api/cameras${qs ? `?${qs}` : ""}`);
  },
  summary: () => request<CameraSummary>("/api/cameras/summary"),
  getCamera: (id: number) => request<Camera>(`/api/cameras/${id}`),
  createCamera: (body: Record<string, unknown>) =>
    request<Camera>("/api/cameras", { method: "POST", body: JSON.stringify(body) }),
  updateCamera: (id: number, body: Record<string, unknown>) =>
    request<Camera>(`/api/cameras/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteCamera: (id: number) => request<void>(`/api/cameras/${id}`, { method: "DELETE" }),

  testUnsaved: (body: Record<string, unknown>) =>
    request<TestResult>("/api/cameras/test-connection", { method: "POST", body: JSON.stringify(body) }),
  discoverProfiles: (body: Record<string, unknown>) =>
    request<TestResult>("/api/cameras/profiles", { method: "POST", body: JSON.stringify(body) }),
  testCamera: (id: number, body: Record<string, unknown> = {}) =>
    request<TestResult>(`/api/cameras/${id}/test`, { method: "POST", body: JSON.stringify(body) }),
  cameraProfiles: (id: number, body: Record<string, unknown> = {}) =>
    request<TestResult>(`/api/cameras/${id}/profiles`, { method: "POST", body: JSON.stringify(body) }),

  uploadPhoto: (id: number, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<Camera>(`/api/cameras/${id}/photo`, { method: "POST", body: fd });
  },

  savePlacement: (id: number, body: Record<string, unknown>) =>
    request<unknown>(`/api/cameras/${id}/placement`, { method: "PUT", body: JSON.stringify(body) }),
  removePlacement: (id: number) =>
    request<void>(`/api/cameras/${id}/placement`, { method: "DELETE" }),

  // locations
  tree: () => request<Site[]>("/api/locations/tree"),
  resolveLocation: (body: Record<string, unknown>) =>
    request<Area>("/api/locations/resolve", { method: "POST", body: JSON.stringify(body) }),
  createSite: (name: string) =>
    request<Site>("/api/locations/sites", { method: "POST", body: JSON.stringify({ name }) }),
  createBuilding: (site_id: number, name: string) =>
    request<unknown>("/api/locations/buildings", { method: "POST", body: JSON.stringify({ site_id, name }) }),
  createFloor: (building_id: number, name: string) =>
    request<unknown>("/api/locations/floors", { method: "POST", body: JSON.stringify({ building_id, name }) }),
  createArea: (floor_id: number, name: string) =>
    request<Area>("/api/locations/areas", { method: "POST", body: JSON.stringify({ floor_id, name }) }),

  // floor plans
  listFloorPlans: (floor_id?: number) =>
    request<FloorPlan[]>(`/api/floor-plans${floor_id ? `?floor_id=${floor_id}` : ""}`),
  getFloorPlan: (id: number) => request<FloorPlan>(`/api/floor-plans/${id}`),
  planCameras: (id: number) => request<Camera[]>(`/api/floor-plans/${id}/cameras`),
  uploadFloorPlan: (floor_id: number, file: File) => {
    const fd = new FormData();
    fd.append("floor_id", String(floor_id));
    fd.append("file", file);
    return request<FloorPlan>("/api/floor-plans", { method: "POST", body: fd });
  },
  setScale: (id: number, body: Record<string, number>) =>
    request<FloorPlan>(`/api/floor-plans/${id}/scale`, { method: "PUT", body: JSON.stringify(body) }),
  clearScale: (id: number) => request<FloorPlan>(`/api/floor-plans/${id}/scale`, { method: "DELETE" }),

  // coordinate systems
  conventions: () =>
    request<{ conventions: Conventions }>("/api/coordinate-systems/conventions"),
  listCoordinateSystems: () => request<CoordinateSystem[]>("/api/coordinate-systems"),
  getCoordinateSystem: (id: number) => request<CoordinateSystem>(`/api/coordinate-systems/${id}`),
  createCoordinateSystem: (body: Record<string, unknown>) =>
    request<CoordinateSystem>("/api/coordinate-systems", { method: "POST", body: JSON.stringify(body) }),
  updateCoordinateSystem: (id: number, body: Record<string, unknown>) =>
    request<CoordinateSystem>(`/api/coordinate-systems/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteCoordinateSystem: (id: number) =>
    request<void>(`/api/coordinate-systems/${id}`, { method: "DELETE" }),

  // reference points
  listReferencePoints: (coordinateSystemId?: number) =>
    request<ReferencePoint[]>(
      `/api/reference-points${coordinateSystemId ? `?coordinate_system_id=${coordinateSystemId}` : ""}`),
  createReferencePoint: (body: Record<string, unknown>) =>
    request<ReferencePoint>("/api/reference-points", { method: "POST", body: JSON.stringify(body) }),
  updateReferencePoint: (id: number, body: Record<string, unknown>) =>
    request<ReferencePoint>(`/api/reference-points/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteReferencePoint: (id: number) =>
    request<void>(`/api/reference-points/${id}`, { method: "DELETE" }),

  // intrinsics
  listIntrinsics: (cameraId: number) =>
    request<CameraIntrinsics[]>(`/api/cameras/${cameraId}/intrinsics`),
  importIntrinsics: (cameraId: number, body: Record<string, unknown>) =>
    request<CameraIntrinsics>(`/api/cameras/${cameraId}/intrinsics`,
      { method: "POST", body: JSON.stringify(body) }),
  activateIntrinsics: (cameraId: number, intrinsicsId: number) =>
    request<CameraIntrinsics>(`/api/cameras/${cameraId}/intrinsics/${intrinsicsId}/activate`,
      { method: "POST" }),
  deleteIntrinsics: (cameraId: number, intrinsicsId: number) =>
    request<void>(`/api/cameras/${cameraId}/intrinsics/${intrinsicsId}`, { method: "DELETE" }),
  checkerboardCalibrate: (cameraId: number, params: Record<string, string | number | boolean>,
                          files: File[]) => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => q.set(k, String(v)));
    return request<Record<string, unknown>>(
      `/api/cameras/${cameraId}/intrinsics/checkerboard?${q}`, { method: "POST", body: fd });
  },

  // observations
  listObservations: (cameraId: number) =>
    request<Observation[]>(`/api/cameras/${cameraId}/observations`),
  saveObservations: (cameraId: number, body: Record<string, unknown>) =>
    request<Observation[]>(`/api/cameras/${cameraId}/observations`,
      { method: "PUT", body: JSON.stringify(body) }),
  deleteObservation: (cameraId: number, observationId: number) =>
    request<void>(`/api/cameras/${cameraId}/observations/${observationId}`, { method: "DELETE" }),

  // calibration
  listRevisions: (cameraId: number) =>
    request<CalibrationRevision[]>(`/api/cameras/${cameraId}/calibration/revisions`),
  saveManualPose: (cameraId: number, body: Record<string, unknown>) =>
    request<CalibrationRevision>(`/api/cameras/${cameraId}/calibration/manual`,
      { method: "POST", body: JSON.stringify(body) }),
  solveHomography: (cameraId: number, body: Record<string, unknown>) =>
    request<CalibrationRevision>(`/api/cameras/${cameraId}/calibration/homography`,
      { method: "POST", body: JSON.stringify(body) }),
  solvePose: (cameraId: number, body: Record<string, unknown>) =>
    request<CalibrationRevision>(`/api/cameras/${cameraId}/calibration/pose`,
      { method: "POST", body: JSON.stringify(body) }),
  adjustPose: (cameraId: number, revisionId: number, body: Record<string, unknown>) =>
    request<CalibrationRevision>(
      `/api/cameras/${cameraId}/calibration/revisions/${revisionId}/adjust`,
      { method: "POST", body: JSON.stringify(body) }),
  activateRevision: (cameraId: number, revisionId: number) =>
    request<CalibrationRevision>(
      `/api/cameras/${cameraId}/calibration/revisions/${revisionId}/activate`,
      { method: "POST", body: JSON.stringify({ confirm: true }) }),
  validateRevision: (cameraId: number, revisionId: number, body: Record<string, unknown> = {}) =>
    request<{ validation: ValidationDetail; revision: CalibrationRevision }>(
      `/api/cameras/${cameraId}/calibration/revisions/${revisionId}/validate`,
      { method: "POST", body: JSON.stringify(body) }),
  exportCalibration: (cameraId: number, revisionId?: number) =>
    request<Record<string, unknown>>(
      `/api/cameras/${cameraId}/calibration/export${revisionId ? `?revision_id=${revisionId}` : ""}`),

  // projection
  imageToWorld: (cameraId: number, body: Record<string, unknown>) =>
    request<ProjectionResult>(`/api/cameras/${cameraId}/projection/image-to-world`,
      { method: "POST", body: JSON.stringify(body) }),
  worldToImage: (cameraId: number, body: Record<string, unknown>) =>
    request<ProjectionResult>(`/api/cameras/${cameraId}/projection/world-to-image`,
      { method: "POST", body: JSON.stringify(body) }),
  projectionOverlay: (cameraId: number, spacing = 2, extent = 20) =>
    request<Record<string, unknown>>(
      `/api/cameras/${cameraId}/projection/overlay?grid_spacing_m=${spacing}&extent_m=${extent}`),

  // factory map
  factoryMap: (systemId: number) => request<FactoryMap>(`/api/factory-map/${systemId}`),
  coverage: (systemId: number) =>
    request<Record<string, unknown>>(`/api/factory-map/${systemId}/coverage`),
  checkGroundPoint: (body: Record<string, unknown>) =>
    request<GroundCheckResult>("/api/factory-map/check-ground-point",
      { method: "POST", body: JSON.stringify(body) }),
  alignFloorPlan: (planId: number, body: Record<string, unknown>) =>
    request<Record<string, unknown>>(`/api/factory-map/floor-plans/${planId}/alignment`,
      { method: "PUT", body: JSON.stringify(body) }),

  // preview
  startPreview: (id: number) =>
    request<PreviewSession>(`/api/preview/${id}/start`, { method: "POST" }),
  stopPreview: (sessionId: string) =>
    request<void>(`/api/preview/${sessionId}/stop`, { method: "POST" }),
};
