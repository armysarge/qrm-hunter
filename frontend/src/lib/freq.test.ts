import { describe, expect, it } from "vitest";
import {
  autoRange, clampView, formatDigits, formatHz, formatTick, hzToX, niceTicks, panView, parseFreqInput, passband,
  resampleToView, snapHz, xToHz, zoomView,
} from "./freq";

const limit = { loHz: 0, hiHz: 1_000_000 };

describe("view maths", () => {
  it("maps hz <-> x", () => {
    const v = { loHz: 7_000_000, hiHz: 7_200_000 };
    expect(hzToX(7_100_000, v, 1000)).toBe(500);
    expect(xToHz(250, v, 1000)).toBe(7_050_000);
  });
  it("zooms around the anchor", () => {
    expect(zoomView(limit, 250_000, 0.5, limit)).toEqual({ loHz: 125_000, hiHz: 625_000 });
  });
  it("never zooms out past the limit or in past the minimum span", () => {
    expect(zoomView({ loHz: 100, hiHz: 900_100 }, 500_000, 4, limit)).toEqual(limit);
    const tight = zoomView(limit, 500_000, 1e-9, limit, 1000);
    expect(tight.hiHz - tight.loHz).toBe(1000);
  });
  it("pans and clamps", () => {
    expect(panView({ loHz: 0, hiHz: 100 }, -50, limit)).toEqual({ loHz: 0, hiHz: 100 });
    expect(panView({ loHz: 0, hiHz: 100 }, 50, limit)).toEqual({ loHz: 50, hiHz: 150 });
    expect(clampView({ loHz: 999_950, hiHz: 1_000_050 }, limit)).toEqual({ loHz: 999_900, hiHz: 1_000_000 });
  });
  it("resamples frames into the current view", () => {
    const bins = Uint8Array.from([0, 1, 2, 3, 4, 5, 6, 7]);
    expect(Array.from(resampleToView(bins, 0, 8, { loHz: 0, hiHz: 8 }, 8))).toEqual([0, 1, 2, 3, 4, 5, 6, 7]);
    expect(Array.from(resampleToView(bins, 0, 8, { loHz: 4, hiHz: 12 }, 8))).toEqual([4, 5, 6, 7, 0, 0, 0, 0]);
  });
});

describe("formatting and parsing", () => {
  it("formats Hz with dot groups", () => {
    expect(formatHz(7_074_000)).toBe("7.074.000");
    expect(formatHz(14_200_500)).toBe("14.200.500");
  });
  it("builds frequency digit cells", () => {
    const cells = formatDigits(7_074_000);
    expect(cells.map((c) => c.ch).join("")).toBe("07.074.000");
    expect(cells.filter((c) => c.lead).length).toBe(1);
    expect(cells.find((c) => c.ch === "7")!.power).toBe(6);
    expect(formatDigits(1_296_000_000).map((c) => c.ch).join("")).toBe("1.296.000.000");
  });
  it("parses typed frequencies (kHz by default)", () => {
    expect(parseFreqInput("7074")).toBe(7_074_000);
    expect(parseFreqInput("7.074M")).toBe(7_074_000);
    expect(parseFreqInput("14200 khz")).toBe(14_200_000);
    expect(parseFreqInput("7074000hz")).toBe(7_074_000);
    expect(parseFreqInput("abc")).toBeNull();
    expect(parseFreqInput("")).toBeNull();
  });
  it("snaps to step", () => {
    expect(snapHz(7_074_430, 1000)).toBe(7_074_000);
    expect(snapHz(7_074_500, 1000)).toBe(7_075_000);
  });
  it("chooses nice ticks", () => {
    const { ticks, step } = niceTicks(7_000_000, 7_200_000, 8);
    expect(step).toBe(50_000);
    expect(ticks[0]).toBe(7_000_000);
    expect(ticks.at(-1)).toBe(7_200_000);
    expect(formatTick(7_050_000, 50_000)).toBe("7.05");
    expect(formatTick(14_000_000, 1_000_000)).toBe("14");
  });
  it("computes passbands per mode", () => {
    expect(passband(7_000_000, "USB", 2700)).toEqual([7_000_000, 7_002_700]);
    expect(passband(7_000_000, "LSB", 2700)).toEqual([6_997_300, 7_000_000]);
    expect(passband(7_000_000, "AM", 6000)).toEqual([6_997_000, 7_003_000]);
  });
});

describe("autoRange", () => {
  it("puts the 20th-percentile floor 6 dB above db_min and 50 dB below db_max", () => {
    // dB range -120..-20 → 100 dB over 255 steps; a floor of bin 102 is -80 dB
    const bins = new Uint8Array(1000).fill(102);
    for (let i = 0; i < 50; i++) bins[i * 20] = 250; // strong carriers don't move the floor
    expect(autoRange(bins, -120, -20)).toEqual({ db_min: -86, db_max: -30 });
  });
  it("uses the low percentile, not the minimum", () => {
    const bins = new Uint8Array(100);
    for (let i = 0; i < 100; i++) bins[i] = i; // percentile 20 → bin 19
    const r = autoRange(bins, -100, 155)!; // 1 dB per bin step
    expect(r).toEqual({ db_min: -100 + 19 - 6, db_max: -100 + 19 + 50 });
  });
  it("rounds to whole dB", () => {
    const r = autoRange(new Uint8Array(10).fill(100), -120, -20)!;
    expect(Number.isInteger(r.db_min) && Number.isInteger(r.db_max)).toBe(true);
    expect(r.db_max - r.db_min).toBe(56);
  });
  it("returns null for empty input or a bad range", () => {
    expect(autoRange(new Uint8Array(0), -120, -20)).toBeNull();
    expect(autoRange(new Uint8Array(4), -20, -20)).toBeNull();
  });
});
