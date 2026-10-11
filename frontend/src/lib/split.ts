/** Spectrum/waterfall divider maths. Percentages are the spectrum's share of the height
 *  available to the two plots (the card minus the divider itself). */

export const SPLIT_DEFAULT = 30;
export const SPLIT_MIN = 15;
export const SPLIT_MAX = 85;
export const SPLIT_STEP = 5;
/** Neither plot is allowed to get shorter than this. */
export const MIN_PLOT_PX = 80;
export const SPLIT_STORAGE_KEY = "qrm-hunter.split";

/** Allowed range in percent for the given available height (falls back to the plain limits while unmeasured). */
export function splitBounds(availablePx: number): { min: number; max: number } {
  if (!(availablePx > 0)) return { min: SPLIT_MIN, max: SPLIT_MAX };
  const pad = (MIN_PLOT_PX / availablePx) * 100;
  const min = Math.max(SPLIT_MIN, pad);
  const max = Math.min(SPLIT_MAX, 100 - pad);
  return min <= max ? { min, max } : { min: 50, max: 50 }; // too small for two 80 px plots: split evenly
}

export function clampSplit(pct: number, availablePx: number): number {
  const { min, max } = splitBounds(availablePx);
  const v = Number.isFinite(pct) ? pct : SPLIT_DEFAULT;
  return Math.min(Math.max(v, min), max);
}

/** The value a key press moves to, or null when the key is not a splitter key. */
export function splitForKey(key: string, pct: number, availablePx: number): number | null {
  const { min, max } = splitBounds(availablePx);
  switch (key) {
    case "ArrowUp": return clampSplit(pct - SPLIT_STEP, availablePx);
    case "ArrowDown": return clampSplit(pct + SPLIT_STEP, availablePx);
    case "Home": return min;
    case "End": return max;
    default: return null;
  }
}

/** Percent for a pointer at `offsetPx` from the top of the available area. */
export function pxToSplit(offsetPx: number, availablePx: number): number {
  return availablePx > 0 ? clampSplit((offsetPx / availablePx) * 100, availablePx) : SPLIT_DEFAULT;
}

export function splitToPx(pct: number, availablePx: number): number {
  return (clampSplit(pct, availablePx) / 100) * availablePx;
}

export function parseStoredSplit(raw: string | null): number | null {
  if (raw === null) return null;
  const v = Number(raw);
  return raw.trim() !== "" && Number.isFinite(v) ? Math.min(Math.max(v, SPLIT_MIN), SPLIT_MAX) : null;
}

export function loadSplit(storage?: Pick<Storage, "getItem">): number {
  try {
    const s = storage ?? globalThis.localStorage;
    return parseStoredSplit(s.getItem(SPLIT_STORAGE_KEY)) ?? SPLIT_DEFAULT;
  } catch {
    return SPLIT_DEFAULT;
  }
}

export function saveSplit(pct: number, storage?: Pick<Storage, "setItem">): void {
  try {
    (storage ?? globalThis.localStorage).setItem(SPLIT_STORAGE_KEY, String(Math.round(pct * 10) / 10));
  } catch {
    // storage unavailable (private window, blocked site data): the split just is not remembered
  }
}
