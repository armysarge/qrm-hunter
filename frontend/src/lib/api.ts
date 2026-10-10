import type {
  Band, DeviceInfo, ImportFormat, Memory, MemoryFields, RadioState, Settings, SettingsPatch, StatePatch,
} from "./types";

export function getToken(search: string = typeof location !== "undefined" ? location.search : ""): string | null {
  return new URLSearchParams(search).get("token");
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body) headers.set("Content-Type", "application/json");
  const res = await fetch(path, { ...init, headers });
  if (!res.ok) {
    let detail = "";
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
      else if (body?.detail != null) detail = JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    if (!detail.trim()) detail = res.statusText ? `HTTP ${res.status} ${res.statusText}` : `HTTP ${res.status}`;
    throw new ApiError(res.status, detail);
  }
  return res;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await request(path, init);
  return (res.status === 204 ? undefined : await res.json()) as T;
}

const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });

export const listDevices = () => api<DeviceInfo[]>("/api/devices");
export const openSession = (deviceId: string, config?: StatePatch) =>
  api<RadioState>("/api/session/open", json("POST", { device_id: deviceId, config }));
// Every state-changing request carries a JSON body: the server refuses non-JSON POST/PATCH
// (cross-site "simple requests") with 415.
export const closeSession = () => api<RadioState>("/api/session/close", json("POST", {}));
export const getState = () => api<RadioState>("/api/state");
export const patchState = (patch: StatePatch) => api<RadioState>("/api/state", json("PATCH", patch));
export const getBandplan = () => api<{ region: number; bands: Band[] }>("/api/bandplan");
export const getSettings = () => api<Settings>("/api/settings");
export const patchSettings = (patch: SettingsPatch) => api<Settings>("/api/settings", json("PATCH", patch));

export const listMemories = () => api<Memory[]>("/api/memories");
export const createMemory = (fields: MemoryFields) => api<Memory>("/api/memories", json("POST", fields));
export const updateMemory = (id: string, patch: Partial<MemoryFields>) =>
  api<Memory>(`/api/memories/${encodeURIComponent(id)}`, json("PATCH", patch));
export const deleteMemory = (id: string) => api<void>(`/api/memories/${encodeURIComponent(id)}`, json("DELETE", {}));
export const importMemories = (format: ImportFormat, data: string, replace = false) =>
  api<{ added: number; skipped: number }>("/api/memories/import", json("POST", { format, data, replace }));
/** The export file as a Blob (fetched rather than linked so the bearer token is sent as a header). */
export const exportMemories = async (format: ImportFormat) =>
  (await request(`/api/memories/export?format=${format}`)).blob();
