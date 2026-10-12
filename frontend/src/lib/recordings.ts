import { withToken } from "./api";
import { formatBytes, formatElapsed } from "./recording";
import { describeSpan } from "./region";
import type { DeviceInfo, RecordingInfo } from "./types";

export const MAX_NAME = 80;

/** "2026-10-10T14:30:05Z" -> "2026-10-10 14:30 UTC" (SigMF times are UTC). */
export function formatWhen(created: string): string {
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(created);
  return m ? `${m[1]} ${m[2]} UTC` : created;
}

export const recordingMeta = (r: RecordingInfo) =>
  `${describeSpan(r.lo_hz, r.hi_hz)} · ${formatElapsed(r.duration_s)} · ${formatBytes(r.size_bytes)}`;

const TRUNCATED: Record<string, string> = {
  device_lost: "the radio was lost",
  low_disk: "disk space ran low",
  closed: "the radio was closed",
  error: "an error stopped it",
};

export function truncationNote(r: RecordingInfo): string | null {
  return r.truncated ? `Truncated: ${TRUNCATED[r.end_reason] ?? r.end_reason}` : null;
}

export function nameError(name: string): string | null {
  const n = name.trim();
  if (!n) return "The name cannot be empty";
  return n.length > MAX_NAME ? `Use at most ${MAX_NAME} characters` : null;
}

export function matchesRecording(r: RecordingInfo, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const hay = [r.name, r.top_cause ?? "", r.band ?? "", (r.center_hz / 1e6).toFixed(4), r.id].join(" ").toLowerCase();
  return hay.includes(q);
}

export const thumbUrl = (r: RecordingInfo, search?: string) =>
  withToken(`/api/recordings/${encodeURIComponent(r.id)}/waterfall.png`, search);

/** A device-list entry for playing a recording through the replay device (its id is "replay:<meta path>"). */
export function replayDevice(r: RecordingInfo): DeviceInfo {
  const half = r.sample_rate / 2;
  return {
    id: r.device_id, driver: "replay", label: `Recording: ${r.name}`, serial: "",
    freq_range: [r.center_hz - half, r.center_hz + half], sample_rates: [r.sample_rate], gain_range: [0, 0],
    has_direct_sampling: false,
  };
}
