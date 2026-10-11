import type { Mode } from "./types";

export interface View { loHz: number; hiHz: number; }

export const BW_OPTIONS: Record<Mode, number[]> = {
  AM: [3000, 4000, 6000, 8000, 10000],
  USB: [1800, 2400, 2700, 3000],
  LSB: [1800, 2400, 2700, 3000],
  CW: [250, 500, 1000],
  FM: [6000, 10000, 12500, 16000, 20000],
  WFM: [150000, 180000, 200000],
};
export const STEP_OPTIONS = [10, 100, 500, 1000, 5000, 9000, 10000, 12500, 25000];

const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), hi);

export const hzToX = (hz: number, v: View, width: number) => ((hz - v.loHz) / (v.hiHz - v.loHz)) * width;
export const xToHz = (x: number, v: View, width: number) => v.loHz + (x / width) * (v.hiHz - v.loHz);

export function clampView(v: View, limit: View): View {
  const span = Math.min(v.hiHz - v.loHz, limit.hiHz - limit.loHz);
  const lo = clamp(v.loHz, limit.loHz, limit.hiHz - span);
  return { loHz: lo, hiHz: lo + span };
}

/** `maxSpanHz` caps the zoomed-out width when `limit` is wider than the widest useful view. */
export function zoomView(
  v: View, anchorHz: number, factor: number, limit: View, minSpanHz = 1000, maxSpanHz = limit.hiHz - limit.loHz,
): View {
  const span = v.hiHz - v.loHz;
  const newSpan = clamp(span * factor, minSpanHz, Math.min(maxSpanHz, limit.hiHz - limit.loHz));
  const frac = (anchorHz - v.loHz) / span;
  const lo = anchorHz - frac * newSpan;
  return clampView({ loHz: lo, hiHz: lo + newSpan }, limit);
}

export function panView(v: View, deltaHz: number, limit: View): View {
  return clampView({ loHz: v.loHz + deltaHz, hiHz: v.hiHz + deltaHz }, limit);
}

/** Nearest-bin resample of a frame covering [frameLo, frameHi] into `width` columns of `view`. */
export function resampleToView(bins: Uint8Array, frameLo: number, frameHi: number, view: View, width: number): Uint8Array {
  const out = new Uint8Array(width);
  const n = bins.length;
  const fSpan = frameHi - frameLo;
  const vSpan = view.hiHz - view.loHz;
  if (n === 0 || fSpan <= 0 || vSpan <= 0) return out;
  for (let x = 0; x < width; x++) {
    const hz = view.loHz + ((x + 0.5) / width) * vSpan;
    const idx = Math.floor(((hz - frameLo) / fSpan) * n);
    if (idx >= 0 && idx < n) out[x] = bins[idx];
  }
  return out;
}

export function formatHz(hz: number): string {
  return Math.round(Math.abs(hz)).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ".");
}

export interface DigitCell { ch: string; power: number; lead: boolean; sep: boolean; }

export function formatDigits(hz: number, minDigits = 8): DigitCell[] {
  const s = Math.max(0, Math.round(hz)).toString();
  const n = Math.max(minDigits, s.length);
  const padded = s.padStart(n, "0");
  const cells: DigitCell[] = [];
  let leading = true;
  for (let i = 0; i < n; i++) {
    const power = n - 1 - i;
    const ch = padded[i];
    if (ch !== "0") leading = false;
    cells.push({ ch, power, lead: leading && power > 6, sep: false });
    if (power > 0 && power % 3 === 0) cells.push({ ch: ".", power: -1, lead: false, sep: true });
  }
  return cells;
}

export function parseFreqInput(text: string): number | null {
  const m = text.trim().toLowerCase().match(/^(\d+(?:\.\d+)?)\s*(hz|khz|k|mhz|m)?$/);
  if (!m) return null;
  const value = parseFloat(m[1]);
  const unit = m[2] ?? "k";
  const mult = unit.startsWith("m") ? 1e6 : unit === "hz" ? 1 : 1e3;
  return Math.round(value * mult);
}

export const snapHz = (hz: number, step: number) => Math.round(hz / step) * step;

export function niceTicks(lo: number, hi: number, maxTicks: number): { ticks: number[]; step: number } {
  const raw = (hi - lo) / Math.max(1, maxTicks);
  const pow = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * pow).find((s) => s >= raw) ?? 10 * pow;
  const ticks: number[] = [];
  for (let t = Math.ceil(lo / step) * step; t <= hi + 1e-6; t += step) ticks.push(Math.round(t));
  return { ticks, step };
}

export function formatTick(hz: number, step: number): string {
  const decimals = Math.max(0, Math.ceil(-Math.log10(step / 1e6) - 1e-9));
  return (hz / 1e6).toFixed(decimals);
}

export function passband(tuneHz: number, mode: Mode, bwHz: number): [number, number] {
  if (mode === "USB") return [tuneHz, tuneHz + bwHz];
  if (mode === "LSB") return [tuneHz - bwHz, tuneHz];
  return [tuneHz - bwHz / 2, tuneHz + bwHz / 2];
}

/** Display range from a spectrum frame: noise floor = 20th percentile of the bins (converted back
 *  to dB with the frame's own dbMin/dbMax), shown from floor-6 dB to floor+50 dB. */
export function autoRange(bins: Uint8Array, dbMin: number, dbMax: number, pct = 0.2): { db_min: number; db_max: number } | null {
  if (!bins.length || !(dbMax > dbMin)) return null;
  const sorted = Uint8Array.from(bins).sort();
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.floor(pct * (sorted.length - 1))));
  const floor = dbMin + (sorted[idx] / 255) * (dbMax - dbMin);
  return { db_min: Math.round(floor - 6), db_max: Math.round(floor + 50) };
}
