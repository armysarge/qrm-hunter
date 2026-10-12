import { describe, expect, it } from "vitest";
import { confidencePct, confidenceTone, featureRows, formatFeature, rankerNote, sourceLabel } from "./analysis";
import { fixtureResult } from "./analysis-fixture";

describe("analysis helpers", () => {
  it("turns scores into percentages and tones", () => {
    expect(confidencePct(0.824)).toBe(82);
    expect(confidencePct(1.7)).toBe(100);
    expect(confidencePct(-1)).toBe(0);
    expect([confidenceTone(0.8), confidenceTone(0.4), confidenceTone(0.2)]).toEqual(["high", "mid", "low"]);
  });
  it("formats features", () => {
    expect(formatFeature(null)).toBe("–");
    expect(formatFeature(62_100.4)).toBe("62100");
    expect(formatFeature(-131.27)).toBe("-131.3");
    expect(formatFeature(0.0213)).toBe("0.0213");
  });
  it("lists feature rows in the engine's id order, grouped by prefix", () => {
    const rows = featureRows(fixtureResult().features);
    expect(rows.map((r) => r.id)).toEqual(["floor_db", "comb.spacing_hz", "mains.depth", "ofdm.spacing_hz"]);
    expect(rows[0]).toEqual({ id: "floor_db", group: "level", value: "-131.3" });
    expect(rows[1]).toEqual({ id: "comb.spacing_hz", group: "comb", value: "62100" });
  });
  it("describes the source and the ranker", () => {
    expect(sourceLabel(fixtureResult())).toBe("Live · 7.0900–7.1100 MHz (20.0 kHz) · 10.0 s");
    expect(rankerNote(fixtureResult())).toBeNull();
    expect(rankerNote(fixtureResult({ ranker: "onnxruntime is not installed" }))).toBe(
      "Rules only (onnxruntime is not installed)");
  });
});
