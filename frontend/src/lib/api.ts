import type { Band, DeviceInfo, RadioState, Settings, SettingsPatch, StatePatch } from "./types";

export function getToken(search: string = typeof location !== "undefined" ? location.search : ""): string | null {
  return new URLSearchParams(search).get("token");
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
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
      else if (body?.detail !== undefined) detail = JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    if (!detail.trim()) detail = res.statusText ? `HTTP ${res.status} ${res.statusText}` : `HTTP ${res.status}`;
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });

export const listDevices = () => api<DeviceInfo[]>("/api/devices");
export const openSession = (deviceId: string, config?: StatePatch) =>
  api<RadioState>("/api/session/open", json("POST", { device_id: deviceId, config }));
export const closeSession = () => api<RadioState>("/api/session/close", { method: "POST" });
export const getState = () => api<RadioState>("/api/state");
export const patchState = (patch: StatePatch) => api<RadioState>("/api/state", json("PATCH", patch));
export const getBandplan = () => api<{ region: number; bands: Band[] }>("/api/bandplan");
export const getSettings = () => api<Settings>("/api/settings");
export const patchSettings = (patch: SettingsPatch) => api<Settings>("/api/settings", json("PATCH", patch));
