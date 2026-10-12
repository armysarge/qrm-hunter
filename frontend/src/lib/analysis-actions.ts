import { analyse, recordSnapshot } from "./api";
import { describeSpan, targetSpan } from "./region";
import { app } from "./store.svelte";
import type { AnalyseRequest } from "./types";

function openPanel(): void {
  app.analysisOpen = true;
  app.recordingsOpen = false;
  app.memoriesOpen = false;
}

async function run(req: AnalyseRequest, label: string): Promise<void> {
  if (app.analysis.state === "running") return;
  openPanel();
  app.analysis = { state: "running", label, startedAt: Date.now() };
  try {
    app.analysis = { state: "done", result: await analyse(req), saved: null };
  } catch (e) {
    app.analysis = { state: "error", message: (e as Error).message };
  }
}

export function analyseLive(): Promise<void> {
  const span = targetSpan(app.spanChoice, app.region, app.view);
  return run({ source: "live", seconds: app.recordSeconds, ...span },
    `Analysing the last ${app.recordSeconds} s of ${describeSpan(span.lo_hz, span.hi_hz)}`);
}

export function analyseRecording(id: string, name: string): Promise<void> {
  return run({ source: "recording", id }, `Analysing ${name}`);
}

export async function saveAnalysedClip(): Promise<void> {
  const a = app.analysis;
  if (a.state !== "done" || a.result.source.kind !== "live" || a.saved) return;
  try {
    const info = await recordSnapshot({ analysis_id: a.result.id });
    app.analysis = { ...a, saved: info.id };
  } catch (e) {
    app.error = (e as Error).message;
  }
}

export function closeAnalysis(): void {
  app.analysisOpen = false;
}
