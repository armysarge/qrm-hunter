import { describe, expect, it } from "vitest";
import {
  defaultName, groupByBand, importFormat, layoutMarkers, markerAt, matchesQuery, memoryFromRadio, memoryKey,
  openConfig, recallPatch, truncate,
} from "./memories";
import type { Memory, RadioState } from "./types";

const mem = (name: string, tune: number, extra: Partial<Memory> = {}): Memory => ({
  id: name, created: "2026-01-01T00:00:00+00:00", name, tune_hz: tune, mode: "USB", bw_hz: 2700,
  gain_db: null, direct_sampling: null, note: "", ...extra,
});
const radio = (extra: Partial<RadioState> = {}) => ({
  running: true, center_hz: 14_100_000, sample_rate: 2_048_000, tune_hz: 14_074_000, mode: "USB",
  bw_hz: 2700, gain_db: 20, direct_sampling: "off", ...extra,
}) as RadioState;
const key = (k: string, mods: Partial<Record<"altKey" | "ctrlKey" | "metaKey" | "shiftKey" | "repeat", boolean>> = {}) =>
  ({ key: k, altKey: false, ctrlKey: false, metaKey: false, shiftKey: false, ...mods });

describe("naming and saving", () => {
  it("defaults the name to MHz and mode", () => {
    expect(defaultName(14_074_000, "USB")).toBe("14.074 USB");
    expect(defaultName(7_100_000, "AM")).toBe("7.100 AM");
  });
  it("captures the radio's tune, mode, bandwidth, gain and HF input", () => {
    expect(memoryFromRadio(radio({ gain_db: null, direct_sampling: "q" }), "x", "n")).toEqual({
      name: "x", tune_hz: 14_074_000, mode: "USB", bw_hz: 2700, gain_db: null, direct_sampling: "q", note: "n" });
  });
});

describe("recallPatch", () => {
  it("sends tune, mode and bandwidth, and gain/HF input only when the memory sets them", () => {
    expect(recallPatch(mem("a", 14_070_000), radio())).toEqual({ tune_hz: 14_070_000, mode: "USB", bw_hz: 2700 });
    expect(recallPatch(mem("a", 14_070_000, { gain_db: 12.5, direct_sampling: "q" }), radio(), true)).toEqual({
      tune_hz: 14_070_000, mode: "USB", bw_hz: 2700, gain_db: 12.5, direct_sampling: "q" });
  });
  it("sends direct_sampling only to a device that has it, and only when it changes", () => {
    const m = mem("a", 14_070_000, { direct_sampling: "q" });
    expect("direct_sampling" in recallPatch(m, radio(), false)).toBe(false); // e.g. Airspy, synthetic
    expect("direct_sampling" in recallPatch(m, radio({ direct_sampling: "q" }), true)).toBe(false); // unchanged
    expect(recallPatch(m, radio({ direct_sampling: "off" }), true).direct_sampling).toBe("q");
    expect(recallPatch(mem("b", 1e6), null, true)).toEqual({ tune_hz: 1e6, mode: "USB", bw_hz: 2700 });
  });
  it("keeps the AGC choice (gain_db null) out of the patch", () => {
    expect("gain_db" in recallPatch(mem("a", 14_070_000), radio())).toBe(false);
  });
  it("moves the centre when the frequency is outside the usable span", () => {
    const p = recallPatch(mem("a", 7_074_000), radio());
    expect(p.center_hz).toBe(7_074_000 + 2_048_000 / 4);
    // just inside the usable span: no centre change
    expect(recallPatch(mem("b", 14_100_000 + 0.44 * 2_048_000), radio()).center_hz).toBeUndefined();
  });
  it("drops the centre from the open() config used by Start", () => {
    const pending = recallPatch(mem("a", 7_074_000, { gain_db: 30 }), radio({ running: false }));
    expect(openConfig(7_074_000, pending)).toEqual({ tune_hz: 7_074_000, mode: "USB", bw_hz: 2700, gain_db: 30 });
    expect(openConfig(7_100_000, null)).toEqual({ tune_hz: 7_100_000 });
  });
  it("lets the recalled frequency win over the displayed one", () => {
    expect(openConfig(14_175_000, { tune_hz: 7_074_000, mode: "LSB" })).toEqual({ tune_hz: 7_074_000, mode: "LSB" });
  });
});

describe("search, grouping and files", () => {
  const ms = [mem("FT8", 14_074_000, { note: "evening dx" }), mem("Plasma TV", 7_150_000), mem("CB", 27_005_000)];
  it("matches name, note and frequency", () => {
    expect(ms.filter((m) => matchesQuery(m, "ft8")).map((m) => m.name)).toEqual(["FT8"]);
    expect(ms.filter((m) => matchesQuery(m, "DX")).map((m) => m.name)).toEqual(["FT8"]);
    expect(ms.filter((m) => matchesQuery(m, "14.074")).map((m) => m.name)).toEqual(["FT8"]);
    expect(ms.filter((m) => matchesQuery(m, "7150")).map((m) => m.name)).toEqual(["Plasma TV"]);
    expect(ms.filter((m) => matchesQuery(m, "  ")).length).toBe(3);
  });
  it("groups by band with Other for out-of-band", () => {
    const bands = [{ name: "40m", lo_hz: 7e6, hi_hz: 7.2e6, default_hz: 7.1e6 },
      { name: "20m", lo_hz: 14e6, hi_hz: 14.35e6, default_hz: 14.1e6 }];
    const sorted = [...ms].sort((a, b) => a.tune_hz - b.tune_hz);
    expect(groupByBand(sorted, bands).map((g) => [g.band, g.items.map((m) => m.name)])).toEqual([
      ["40m", ["Plasma TV"]], ["20m", ["FT8"]], ["Other", ["CB"]]]);
  });
  it("picks the import format from the extension", () => {
    expect(importFormat("my.JSON")).toBe("json");
    expect(importFormat("a.b.csv")).toBe("csv");
    expect(importFormat("notes.txt")).toBeNull();
  });
});

describe("layoutMarkers", () => {
  const view = { loHz: 0, hiHz: 1000 };
  const measure = (s: string) => s.length * 6;
  it("places visible memories and skips ones whose labels would overlap", () => {
    const ms = [mem("aaaa", 100), mem("bbbb", 110), mem("cccc", 200), mem("out", 2000)];
    const out = layoutMarkers(ms, view, 1000, measure);
    expect(out.map((k) => k.memory.name)).toEqual(["aaaa", "cccc"]);
    expect(out[0]).toMatchObject({ x: 100, x0: 98, x1: 128, label: "aaaa" });
  });
  it("truncates long labels and sorts by frequency", () => {
    const out = layoutMarkers([mem("z", 900), mem("a very long memory name", 100)], view, 1000, measure, 8);
    expect(out.map((k) => k.label)).toEqual(["a very …", "z"]);
    expect(truncate("abc", 3)).toBe("abc");
  });
  it("hit-tests marker boxes", () => {
    const out = layoutMarkers([mem("aaaa", 100)], view, 1000, measure);
    expect(markerAt(out, 120)?.memory.name).toBe("aaaa");
    expect(markerAt(out, 140)).toBeNull();
  });
  it("keeps labels inside the right edge, with the box covering tick and label", () => {
    const [k] = layoutMarkers([mem("edge", 995)], view, 1000, measure); // label is 24 px wide
    expect(k.x).toBe(995);
    expect(k.labelX).toBe(1000 - 2 - 24);
    expect(k.x0).toBe(974);
    expect(k.x1).toBe(998);
    expect(markerAt([k], 980)?.memory.name).toBe("edge");
  });
  it("skips a marker that collides with an edge-clamped label", () => {
    const out = layoutMarkers([mem("left", 960), mem("edge", 995)], view, 1000, measure);
    expect(out.map((k) => k.memory.name)).toEqual(["left"]);
  });
  it("returns nothing for an empty view", () => {
    expect(layoutMarkers([mem("a", 1)], { loHz: 0, hiHz: 0 }, 100, measure)).toEqual([]);
  });
});

describe("memoryKey", () => {
  it("maps M to save and 1-9 to recall", () => {
    expect(memoryKey(key("m"), false)).toEqual({ kind: "save" });
    expect(memoryKey(key("M"), false)).toEqual({ kind: "save" });
    expect(memoryKey(key("1"), false)).toEqual({ kind: "recall", index: 0 });
    expect(memoryKey(key("9"), false)).toEqual({ kind: "recall", index: 8 });
    expect(memoryKey(key("0"), false)).toBeNull();
    expect(memoryKey(key("x"), false)).toBeNull();
  });
  it("is ignored while typing in a field", () => {
    expect(memoryKey(key("m"), true)).toBeNull();
    expect(memoryKey(key("3"), true)).toBeNull();
  });
  it("is ignored with any modifier or on auto-repeat", () => {
    for (const mod of ["altKey", "ctrlKey", "metaKey", "shiftKey", "repeat"] as const) {
      expect(memoryKey(key("m", { [mod]: true }), false)).toBeNull();
      expect(memoryKey(key("1", { [mod]: true }), false)).toBeNull();
    }
  });
});
