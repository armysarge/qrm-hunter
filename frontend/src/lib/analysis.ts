import { describeSpan } from "./region";
import type { AnalysisResult } from "./types";

export type AnalysisUi =
  | { state: "idle" }
  | { state: "running"; label: string; startedAt: number }
  | { state: "done"; result: AnalysisResult; saved: string | null }
  | { state: "error"; message: string };

export const HUNT_PLACEHOLDER = "Step-by-step hunting guides are coming in a later version.";

export const confidencePct = (score: number) => Math.round(Math.min(1, Math.max(0, score)) * 100);

export function confidenceTone(score: number): "high" | "mid" | "low" {
  return score >= 0.6 ? "high" : score >= 0.35 ? "mid" : "low";
}

export function formatFeature(v: number | null): string {
  if (v === null || !Number.isFinite(v)) return "–";
  const a = Math.abs(v);
  if (a >= 1000) return v.toFixed(0);
  if (a >= 10) return v.toFixed(1);
  return Number(v.toPrecision(3)).toString();
}

/** One row per feature, in the order the engine sends them (its FEATURE_IDS order). Ids without a
 *  dot (floor_db, occupied_bw_hz, ...) are the spec's Level group. */
export function featureRows(features: Record<string, number | null>): { id: string; group: string; value: string }[] {
  return Object.keys(features).map((id) => ({
    id, group: id.includes(".") ? id.split(".")[0] : "level", value: formatFeature(features[id]),
  }));
}

export function sourceLabel(r: AnalysisResult): string {
  const s = r.source;
  return `${s.kind === "live" ? "Live" : "Recording"} · ${describeSpan(s.lo_hz, s.hi_hz)} · ${s.seconds.toFixed(1)} s`;
}

export const rankerNote = (r: AnalysisResult) => (r.ranker === "ok" ? null : `Rules only (${r.ranker})`);
