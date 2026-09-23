import type {
  Area, Camera, CameraSummary, FloorPlan, PreviewSession, Site, TestResult,
} from "./types";

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

  // preview
  startPreview: (id: number) =>
    request<PreviewSession>(`/api/preview/${id}/start`, { method: "POST" }),
  stopPreview: (sessionId: string) =>
    request<void>(`/api/preview/${sessionId}/stop`, { method: "POST" }),
};
