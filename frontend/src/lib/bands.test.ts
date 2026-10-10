import { describe, expect, it } from "vitest";
import { GROUPS, groupLabel, naturalMode, selectBand } from "./bands";
import type { Band } from "./types";

const band = (name: string, lo: number, hi: number, group: Band["group"]): Band =>
  ({ name, lo_hz: lo, hi_hz: hi, default_hz: Math.round((lo + hi) / 2000) * 1000, group });
const BC = band("FM BC", 87_500_000, 108_000_000, "BC");
const M40 = band("40m", 7_000_000, 7_200_000, "HF");
const M20 = band("20m", 14_000_000, 14_350_000, "HF");
const M2 = band("2m", 144_000_000, 146_000_000, "VHF");
const CM70 = band("70cm", 430_000_000, 440_000_000, "UHF");

describe("band groups", () => {
  it("shows HF | VHF | UHF | FM BC", () => {
    expect(GROUPS.map(groupLabel)).toEqual(["HF", "VHF", "UHF", "FM BC"]);
  });

  it("knows each band's natural mode", () => {
    expect([BC, M40, M20, M2, CM70].map(naturalMode)).toEqual(["WFM", "LSB", "USB", "FM", "FM"]);
  });
});

describe("selectBand", () => {
  it("switches to WFM for the broadcast band and remembers the mode it left", () => {
    expect(selectBand(BC, "USB", null)).toEqual({ patch: { tune_hz: 97_750_000, mode: "WFM" }, prevMode: "USB" });
  });

  it("keeps WFM (and the remembered mode) when already in WFM", () => {
    expect(selectBand(BC, "WFM", "LSB")).toEqual({ patch: { tune_hz: 97_750_000 }, prevMode: "LSB" });
  });

  it("restores the previous mode when leaving the broadcast band", () => {
    expect(selectBand(M2, "WFM", "USB")).toEqual({ patch: { tune_hz: 145_000_000, mode: "USB" }, prevMode: null });
  });

  it("falls back to the band's natural mode when nothing was remembered", () => {
    expect(selectBand(M20, "WFM", null).patch.mode).toBe("USB");
    expect(selectBand(M40, "WFM", null).patch.mode).toBe("LSB");
    expect(selectBand(CM70, "WFM", null).patch.mode).toBe("FM");
  });

  it("leaves the mode alone between amateur bands", () => {
    expect(selectBand(M20, "CW", "LSB")).toEqual({ patch: { tune_hz: 14_175_000 }, prevMode: "LSB" });
  });

  it("round trip: 20m USB -> FM BC -> 20m is USB again", () => {
    const go = selectBand(BC, "USB", null);
    const back = selectBand(M20, go.patch.mode, go.prevMode);
    expect(back.patch.mode).toBe("USB");
  });
});
