// Pure helpers for frequency memories: naming, recall patches, search, grouping, spectrum markers, keys.
import { formatHz, hzToX, type View } from "./freq";
import type { Band, ImportFormat, Memory, MemoryFields, Mode, RadioState, StatePatch } from "./types";

/** Fraction of the sample rate either side of centre that is usable for tuning (engine SPAN_USABLE). */
export const SPAN_USABLE = 0.45;

export const defaultName = (tuneHz: number, mode: Mode) => `${(tuneHz / 1e6).toFixed(3)} ${mode}`;

export function memoryFromRadio(r: RadioState, name: string, note = ""): MemoryFields {
  return { name, tune_hz: r.tune_hz, mode: r.mode, bw_hz: r.bw_hz, gain_db: r.gain_db,
    direct_sampling: r.direct_sampling, note };
}

export const byFrequency = (a: Memory, b: Memory) => a.tune_hz - b.tune_hz || a.name.localeCompare(b.name);

/** The PATCH /api/state body that recalls `m`. gain_db/direct_sampling are only sent when the memory
 *  sets them. If the frequency is outside the receiver's usable span, the centre moves with it
 *  (same placement the engine uses: tune + rate/4), so the jump is one retune. */
export function recallPatch(m: Memory, r: RadioState | null): StatePatch {
  const patch: StatePatch = { tune_hz: m.tune_hz, mode: m.mode, bw_hz: m.bw_hz };
  if (m.gain_db !== null) patch.gain_db = m.gain_db;
  if (m.direct_sampling !== null) patch.direct_sampling = m.direct_sampling;
  if (r && Math.abs(m.tune_hz - r.center_hz) > SPAN_USABLE * r.sample_rate) {
    patch.center_hz = m.tune_hz + r.sample_rate / 4;
  }
  return patch;
}

/** The open() config for Start after a memory was recalled while stopped (open recentres itself). */
export function openConfig(tuneHz: number, pending: StatePatch | null): StatePatch {
  const { center_hz: _ignored, ...rest } = pending ?? {};
  return { ...rest, tune_hz: tuneHz };
}

export function matchesQuery(m: Memory, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  if (m.name.toLowerCase().includes(q) || m.note.toLowerCase().includes(q)) return true;
  const digits = q.replace(/[.,\s]/g, "");
  return formatHz(m.tune_hz).includes(q) || (digits !== "" && /^\d+$/.test(digits) &&
    Math.round(m.tune_hz).toString().includes(digits));
}

export interface MemoryGroup { band: string; items: Memory[]; }

/** Group memories (already sorted by frequency) by amateur band; anything outside a band is "Other". */
export function groupByBand(memories: Memory[], bands: Band[]): MemoryGroup[] {
  const groups = new Map<string, Memory[]>();
  for (const m of memories) {
    const name = bands.find((b) => m.tune_hz >= b.lo_hz && m.tune_hz <= b.hi_hz)?.name ?? "Other";
    const list = groups.get(name);
    if (list) list.push(m);
    else groups.set(name, [m]);
  }
  return [...groups].map(([band, items]) => ({ band, items }));
}

export function importFormat(filename: string): ImportFormat | null {
  const ext = filename.toLowerCase().split(".").pop();
  return ext === "json" || ext === "csv" ? ext : null;
}

export const truncate = (s: string, max: number) => (s.length > max ? `${s.slice(0, Math.max(1, max - 1))}…` : s);

export interface Marker { memory: Memory; x: number; label: string; x0: number; x1: number; }

/** Marker positions (CSS px) for memories inside `view`. Labels start at the tick; a marker whose
 *  label box would overlap the previous kept one is skipped (greedy, left to right). */
export function layoutMarkers(memories: Memory[], view: View, width: number, measure: (s: string) => number,
  maxChars = 14, gap = 6): Marker[] {
  const out: Marker[] = [];
  if (!(view.hiHz > view.loHz) || width <= 0) return out;
  const visible = memories.filter((m) => m.tune_hz >= view.loHz && m.tune_hz <= view.hiHz)
    .sort((a, b) => a.tune_hz - b.tune_hz);
  let right = -Infinity;
  for (const m of visible) {
    const x = hzToX(m.tune_hz, view, width);
    const label = truncate(m.name, maxChars);
    const x0 = x - 2;
    const x1 = x + 4 + measure(label);
    if (x0 < right + gap) continue;
    out.push({ memory: m, x, label, x0, x1 });
    right = x1;
  }
  return out;
}

export const markerAt = (markers: Marker[], x: number) => markers.find((k) => x >= k.x0 && x <= k.x1) ?? null;

export type MemoryKey = { kind: "save" } | { kind: "recall"; index: number } | null;

export interface KeyLike { key: string; altKey: boolean; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean; repeat?: boolean; }

/** M = save dialog, 1–9 = recall the nth memory. Ignored with any modifier, on auto-repeat, or when
 *  typing into a field (the caller says so via inField). */
export function memoryKey(e: KeyLike, inField: boolean): MemoryKey {
  if (inField || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey || e.repeat) return null;
  if (e.key === "m" || e.key === "M") return { kind: "save" };
  if (/^[1-9]$/.test(e.key)) return { kind: "recall", index: Number(e.key) - 1 };
  return null;
}
