import { analyse, recordSnapshot } from "./api";
import { describeSpan, targetSpan } from "./region";
import { app } from "./store.svelte";
import type { AnalyseRequest } from "./types";

function openPanel(): void {
  app.analysisOpen = true;
  app.recordingsOpen = false;
  app.memoriesOpen = false;
}

/** An outcome arrived while another side panel was open; the next Analyse shows it. */
let unseen = false;

async function run(req: AnalyseRequest, label: string): Promise<void> {
  if (app.analysis.state === "running") return;
  unseen = false;
  openPanel();
  app.analysis = { state: "running", label, startedAt: Date.now() };
  try {
    app.analysis = { state: "done", result: await analyse(req), saved: null };
  } catch (e) {
    app.analysis = { state: "error", message: (e as Error).message };
  }
  // The outcome is shown even if the panel was closed meanwhile, but a drawer the user opened
  // in the meantime stays open.
  if (app.recordingsOpen || app.memoriesOpen) unseen = true;
  else app.analysisOpen = true;
}

export function analyseLive(): Promise<void> {
  if (unseen && app.analysis.state !== "running") {
    unseen = false;
    openPanel();
    return Promise.resolve();
  }
  const span = targetSpan(app.spanChoice, app.region, app.view);
  return run({ source: "live", seconds: app.recordSeconds, ...span },
    `Analysing the last ${app.recordSeconds} s of ${describeSpan(span.lo_hz, span.hi_hz)}`);
}

export function analyseRecording(id: string, name: string): Promise<void> {
  return run({ source: "recording", id }, `Analysing ${name}`);
}

export async function saveAnalysedClip(): Promise<void> {
  const a = app.analysis;
  if (a.state !== "done" || a.result.source.kind !== "live" || a.saved || a.saving) return;
  const id = a.result.id;
  app.analysis = { ...a, saving: true };
  // Only touch the panel if it still shows the result that was being saved.
  const same = () => app.analysis.state === "done" && app.analysis.result.id === id;
  try {
    const info = await recordSnapshot({ analysis_id: id });
    if (same()) app.analysis = { ...(app.analysis as typeof a), saved: info.id, saving: false };
  } catch (e) {
    if (same()) app.analysis = { ...(app.analysis as typeof a), saving: false };
    app.error = (e as Error).message;
  }
}

export function closeAnalysis(): void {
  app.analysisOpen = false;
}
