import type { Settings, SettingsPatch } from "./types";

export interface SettingsForm { prebuffer: string; folder: string; callsign: string; }
type Errors = Partial<Record<keyof SettingsForm, string>>;

export function formFromSettings(s: Settings): SettingsForm {
  return { prebuffer: String(s.prebuffer_s), folder: s.recordings_dir, callsign: s.callsign };
}

export function validateSettings(f: SettingsForm): { patch: SettingsPatch; errors: Errors } {
  const errors: Errors = {};
  const patch: SettingsPatch = {};
  const seconds = Number(f.prebuffer.trim());
  if (f.prebuffer.trim() === "" || !Number.isFinite(seconds) || seconds < 0 || seconds > 60) {
    errors.prebuffer = "Enter a number of seconds between 0 and 60";
  } else patch.prebuffer_s = seconds;
  const folder = f.folder.trim();
  if (folder.length > 1000) errors.folder = "The folder path is too long";
  else patch.recordings_dir = folder;
  const callsign = f.callsign.trim().toUpperCase();
  if (!/^[A-Z0-9/]{0,12}$/.test(callsign)) errors.callsign = "Letters, digits and / only, at most 12";
  else patch.callsign = callsign;
  return { patch, errors };
}

export function memoryNote(prebufferS: number, sampleRate: number): string {
  if (prebufferS <= 0) return "Off: Save last N s and live Analyse are unavailable";
  return `About ${Math.round((4 * sampleRate * prebufferS) / 1e6)} MB of memory at ${(sampleRate / 1e6).toFixed(3)} MS/s`;
}
