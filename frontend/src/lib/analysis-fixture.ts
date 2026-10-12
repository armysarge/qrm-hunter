import type { AnalysisResult } from "./types";

/** A realistic result, used by the panel and helper tests. */
export function fixtureResult(over: Partial<AnalysisResult> = {}): AnalysisResult {
  return {
    id: "a1",
    created: "2026-10-10T12:00:00+00:00",
    source: { kind: "live", center_hz: 7_100_000, sample_rate: 25_000, lo_hz: 7_090_000, hi_hz: 7_110_000, seconds: 10,
      recording_id: null },
    causes: [
      { id: "smps", name: "Switch-mode power supply", score: 0.82, rule_score: 0.9, model_score: 0.7,
        evidence: ["Comb of lines every 62.1 kHz", "Lines drift 140 Hz"], hunt: "home-electronics",
        description: "Chargers and adapters.", beta: true },
      { id: "led_dimmer", name: "LED driver or dimmer", score: 0.41, rule_score: 0.4, model_score: 0.43,
        evidence: ["Buzz at 100 Hz"], hunt: "lighting", description: "LED lamps.", beta: true },
      { id: "unknown", name: "Unknown", score: 0.1, rule_score: 0.1, model_score: 0.1, evidence: [],
        hunt: "general", description: "", beta: true },
    ],
    // a subset of the engine's FEATURE_IDS, in the engine's order (the API keeps that order)
    features: { "floor_db": -131.27, "comb.spacing_hz": 62_100.4, "mains.depth": 0.0213, "ofdm.spacing_hz": null },
    timing_ms: { features: 812.3, rules: 1.2, ranker: 3.4, total: 817.9 },
    versions: { engine: "0.1.0", features: "1", model: "2026-10-10" },
    ranker: "ok",
    ...over,
  };
}
