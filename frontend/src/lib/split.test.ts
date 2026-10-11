import { describe, expect, it } from "vitest";
import {
  clampSplit, loadSplit, parseStoredSplit, pxToSplit, saveSplit, splitBounds, splitForKey, splitToPx, SPLIT_DEFAULT,
  SPLIT_STORAGE_KEY,
} from "./split";

describe("splitBounds", () => {
  it("is 15-85 on a tall card", () => expect(splitBounds(1000)).toEqual({ min: 15, max: 85 }));
  it("keeps both plots at least 80 px on a short card", () => {
    const { min, max } = splitBounds(300);
    expect((min / 100) * 300).toBeGreaterThanOrEqual(80);
    expect(((100 - max) / 100) * 300).toBeGreaterThanOrEqual(80);
  });
  it("splits evenly when two minimum plots cannot fit", () => expect(splitBounds(120)).toEqual({ min: 50, max: 50 }));
  it("uses the plain limits before the card is measured", () => expect(splitBounds(0)).toEqual({ min: 15, max: 85 }));
});

describe("clampSplit", () => {
  it("clamps both ends", () => {
    expect(clampSplit(2, 1000)).toBe(15);
    expect(clampSplit(99, 1000)).toBe(85);
    expect(clampSplit(40, 1000)).toBe(40);
  });
  it("applies the pixel floor", () => expect(clampSplit(15, 400)).toBe(20));
  it("replaces NaN with the default", () => expect(clampSplit(NaN, 1000)).toBe(SPLIT_DEFAULT));
});

describe("splitForKey", () => {
  it("moves 5 % per arrow", () => {
    expect(splitForKey("ArrowUp", 30, 1000)).toBe(25);
    expect(splitForKey("ArrowDown", 30, 1000)).toBe(35);
  });
  it("stops at the bounds", () => {
    expect(splitForKey("ArrowUp", 15, 1000)).toBe(15);
    expect(splitForKey("ArrowDown", 85, 1000)).toBe(85);
  });
  it("Home and End go to the bounds", () => {
    expect(splitForKey("Home", 40, 1000)).toBe(15);
    expect(splitForKey("End", 40, 1000)).toBe(85);
    expect(splitForKey("End", 40, 400)).toBe(80);
  });
  it("ignores other keys", () => expect(splitForKey("a", 40, 1000)).toBeNull());
});

describe("pixel conversion", () => {
  it("converts pointer offset to percent and back", () => {
    expect(pxToSplit(250, 1000)).toBe(25);
    expect(splitToPx(25, 1000)).toBe(250);
  });
  it("clamps a pointer dragged outside the card", () => {
    expect(pxToSplit(-50, 1000)).toBe(15);
    expect(pxToSplit(5000, 1000)).toBe(85);
  });
});

describe("storage", () => {
  it("parses stored values", () => {
    expect(parseStoredSplit("42")).toBe(42);
    expect(parseStoredSplit("500")).toBe(85);
    expect(parseStoredSplit("junk")).toBeNull();
    expect(parseStoredSplit("")).toBeNull();
    expect(parseStoredSplit(null)).toBeNull();
  });
  it("round-trips through a storage object", () => {
    const m = new Map<string, string>();
    const s = { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) };
    saveSplit(42.04, s);
    expect(m.get(SPLIT_STORAGE_KEY)).toBe("42");
    expect(loadSplit(s)).toBe(42);
  });
  it("survives storage that throws", () => {
    const bad = { getItem: () => { throw new Error("denied"); }, setItem: () => { throw new Error("denied"); } };
    expect(loadSplit(bad)).toBe(SPLIT_DEFAULT);
    expect(() => saveSplit(40, bad)).not.toThrow();
  });
});

describe("render-time clamping", () => {
  it("shrinks the shown split in a small window but restores the preference when it grows again", () => {
    const preference = 70;
    expect(clampSplit(preference, 200)).toBeLessThan(preference);
    expect((clampSplit(preference, 200) / 100) * 200).toBeGreaterThanOrEqual(80);
    expect(clampSplit(preference, 200) >= 50).toBe(true);
    expect(clampSplit(preference, 1000)).toBe(70);
  });
  it("keeps both plots at 80 px at the extremes of a short card", () => {
    for (const h of [180, 250, 400]) {
      const hi = clampSplit(99, h);
      const lo = clampSplit(0, h);
      expect(((100 - hi) / 100) * h).toBeGreaterThanOrEqual(80 - 1e-9);
      expect((lo / 100) * h).toBeGreaterThanOrEqual(80 - 1e-9);
    }
  });
});
