import { render } from "svelte/server";
import { describe, expect, it } from "vitest";
import AnalysisPanel from "./AnalysisPanel.svelte";
import { fixtureResult } from "../lib/analysis-fixture";
import type { AnalysisUi } from "../lib/analysis";

const html = (ui: AnalysisUi) => render(AnalysisPanel, { props: { ui, onclose: () => {}, onsave: () => {} } }).body;

describe("AnalysisPanel", () => {
  it("renders ranked causes with confidence, beta chips, evidence and features", () => {
    const body = html({ state: "done", result: fixtureResult(), saved: null });
    const first = body.indexOf("Switch-mode power supply");
    expect(first).toBeGreaterThan(-1);
    expect(first).toBeLessThan(body.indexOf("LED driver or dimmer"));
    expect(body).toContain("82%");
    expect(body).toContain("beta");
    expect(body).toContain("Comb of lines every 62.1 kHz");
    expect(body).toContain("How to find it");
    expect(body).toContain("comb.spacing_hz");
    expect(body).toContain("Save as recording");
  });
  it("hides Save for a recording and after saving", () => {
    const rec = fixtureResult({ source: { ...fixtureResult().source, kind: "recording", recording_id: "r1" } });
    expect(html({ state: "done", result: rec, saved: null })).not.toContain("Save as recording");
    expect(html({ state: "done", result: fixtureResult(), saved: "r2" })).toContain("Saved");
  });
  it("shows rules only without beta chips", () => {
    const r = fixtureResult({ ranker: "model file missing",
      causes: fixtureResult().causes.map((c) => ({ ...c, beta: false, model_score: null })) });
    const body = html({ state: "done", result: r, saved: null });
    expect(body).toContain("Rules only (model file missing)");
    expect(body).not.toMatch(/class="chip[^"]*"[^>]*>beta/);
  });
  it("shows progress and errors", () => {
    expect(html({ state: "running", label: "Analysing 7.0900–7.1100 MHz (20.0 kHz)", startedAt: 0 })).toContain(
      "Analysing 7.0900–7.1100 MHz");
    expect(html({ state: "error", message: "need at least 2 s of data at this frequency" })).toContain(
      "need at least 2 s of data");
  });
});
