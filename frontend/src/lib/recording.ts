import type { RecordStatus, RecordingInfo } from "./types";

export type RecordPhase = "idle" | "saving" | "starting" | "recording" | "stopping";
export interface RecordUi { phase: RecordPhase; elapsedS: number; bytes: number; lastSaved: RecordingInfo | null; }
export type RecordAction =
  | { type: "save" }
  | { type: "saved"; info: RecordingInfo }
  | { type: "start" }
  | { type: "started"; status: RecordStatus }
  | { type: "progress"; elapsedS: number; bytes: number }
  | { type: "stop" }
  | { type: "stopped"; info: RecordingInfo | null }
  | { type: "status"; status: RecordStatus }
  | { type: "failed" };

export const initialRecord = (): RecordUi => ({ phase: "idle", elapsedS: 0, bytes: 0, lastSaved: null });

export function recordReducer(s: RecordUi, a: RecordAction): RecordUi {
  switch (a.type) {
    case "save":
      return s.phase === "idle" ? { ...s, phase: "saving" } : s;
    case "saved":
      return s.phase === "saving" ? { ...s, phase: "idle", lastSaved: a.info } : s;
    case "start":
      return s.phase === "idle" ? { ...s, phase: "starting", elapsedS: 0, bytes: 0 } : s;
    case "started":
      return s.phase === "starting" ? { ...s, phase: "recording", elapsedS: a.status.elapsed_s, bytes: a.status.bytes } : s;
    case "progress":
      if (s.phase === "saving") return s;
      return { ...s, phase: s.phase === "stopping" ? "stopping" : "recording", elapsedS: a.elapsedS, bytes: a.bytes };
    case "stop":
      return s.phase === "recording" ? { ...s, phase: "stopping" } : s;
    case "stopped":
      return { ...s, phase: s.phase === "saving" ? "saving" : "idle", elapsedS: 0, bytes: 0, lastSaved: a.info ?? s.lastSaved };
    case "status":
      if (a.status.recording) {
        return s.phase === "idle" || s.phase === "starting"
          ? { ...s, phase: "recording", elapsedS: a.status.elapsed_s, bytes: a.status.bytes } : s;
      }
      return s.phase === "recording" || s.phase === "stopping" ? { ...s, phase: "idle", elapsedS: 0, bytes: 0 } : s;
    case "failed":
      return { ...s, phase: s.phase === "stopping" ? "recording" : s.phase === "recording" ? "recording" : "idle" };
  }
}

export function formatBytes(n: number): string {
  if (n < 1024 ** 2) return `${Math.round(n / 1024)} kB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

export function formatElapsed(seconds: number): string {
  const t = Math.max(0, Math.floor(seconds));
  const h = Math.floor(t / 3600), m = Math.floor(t / 60) % 60, s = t % 60;
  const ss = String(s).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

export function primaryLabel(s: RecordUi, seconds: number): string {
  switch (s.phase) {
    case "saving": return "Saving…";
    case "starting": return "Starting…";
    case "stopping": return "Stopping…";
    case "recording": return `${formatElapsed(s.elapsedS)} · ${formatBytes(s.bytes)}`;
    default: return `Save last ${seconds} s`;
  }
}

/** Seconds for "Save last N s": a whole number from 2 up to the pre-buffer length. */
export function clampSeconds(n: number, prebufferS: number): number {
  const max = Math.max(2, Math.floor(prebufferS));
  return Number.isFinite(n) ? Math.min(max, Math.max(2, Math.round(n))) : max;
}

const STOPPED: Record<string, string> = {
  cap: "Recording stopped at the 30 minute limit.",
  low_disk: "Recording stopped: less than 500 MB of disk space is left.",
  device_lost: "Recording stopped: the radio was lost. The file is kept and marked truncated.",
  retuned: "Recording stopped because the frequency or sample rate changed.",
  closed: "Recording stopped because the radio was closed. The file is kept and marked truncated.",
  error: "Recording stopped after an error. The file is kept and marked truncated.",
};

export function stoppedMessage(reason: string): string | null {
  return STOPPED[reason] ?? null;
}
