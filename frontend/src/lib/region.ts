import type { View } from "./freq";

export interface Region { loHz: number; hiHz: number; }
/** What Record and Analyse use: the visible span, or the Shift+drag selection. */
export type SpanChoice = "visible" | "selection";
export const MIN_REGION_HZ = 100;

/** The region between two frequencies, clipped to the receiver span; null when under 100 Hz wide. */
export function regionBetween(aHz: number, bHz: number, limit: View): Region | null {
  const lo = Math.max(Math.min(aHz, bHz), limit.loHz);
  const hi = Math.min(Math.max(aHz, bHz), limit.hiHz);
  return hi - lo >= MIN_REGION_HZ ? { loHz: lo, hiHz: hi } : null;
}

/** Overlay position as percentages of the plot width, or null when nothing of it is in view. */
export function regionOverlay(r: Region | null, view: View): { left: number; width: number } | null {
  if (!r || view.hiHz <= view.loHz || r.hiHz <= view.loHz || r.loHz >= view.hiHz) return null;
  const span = view.hiHz - view.loHz;
  const lo = Math.max(r.loHz, view.loHz);
  const hi = Math.min(r.hiHz, view.hiHz);
  return { left: ((lo - view.loHz) / span) * 100, width: ((hi - lo) / span) * 100 };
}

/** The part of a region still inside the receiver span after a retune, or null. */
export function keepRegion(r: Region | null, limit: View): Region | null {
  return r ? regionBetween(r.loHz, r.hiHz, limit) : null;
}

export function targetSpan(choice: SpanChoice, region: Region | null, view: View): { lo_hz: number; hi_hz: number } {
  const r = choice === "selection" && region ? region : view;
  return { lo_hz: Math.round(r.loHz), hi_hz: Math.round(r.hiHz) };
}

export function describeSpan(lo: number, hi: number): string {
  const w = hi - lo;
  const width = w >= 1e6 ? `${(w / 1e6).toFixed(2)} MHz` : w >= 1e3 ? `${(w / 1e3).toFixed(1)} kHz` : `${Math.round(w)} Hz`;
  return `${(lo / 1e6).toFixed(4)}–${(hi / 1e6).toFixed(4)} MHz (${width})`;
}
