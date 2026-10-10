// Band switch logic: the group segments, and the mode that goes with tuning to a band.
import type { Band, BandGroup, Mode, StatePatch } from "./types";

export const GROUPS: BandGroup[] = ["HF", "VHF", "UHF", "BC"];

export const groupLabel = (g: BandGroup) => (g === "BC" ? "FM BC" : g);

/** The mode a band is normally used with: WFM for broadcast, FM on VHF/UHF, USB from 10 MHz up, LSB below. */
export function naturalMode(band: Band): Mode {
  if (band.group === "BC") return "WFM";
  if (band.group === "VHF" || band.group === "UHF") return "FM";
  return band.lo_hz >= 10_000_000 ? "USB" : "LSB";
}

export interface BandChoice {
  patch: StatePatch;
  /** The mode to restore when leaving the broadcast band (null = use the band's natural mode). */
  prevMode: Mode | null;
}

/** Tune to a band chip. The FM BC band switches to WFM and remembers the mode it came from; going
 *  from WFM to any other band restores that mode, or the band's natural one. Otherwise the mode stays. */
export function selectBand(band: Band, mode: Mode | undefined, prevMode: Mode | null): BandChoice {
  const patch: StatePatch = { tune_hz: band.default_hz };
  if (band.group === "BC") {
    if (mode === "WFM") return { patch, prevMode };
    patch.mode = "WFM";
    return { patch, prevMode: mode ?? null };
  }
  if (mode === "WFM") {
    patch.mode = prevMode ?? naturalMode(band);
    return { patch, prevMode: null };
  }
  return { patch, prevMode };
}
