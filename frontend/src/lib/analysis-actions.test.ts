import { beforeEach, describe, expect, it, vi } from "vitest";

const analyse = vi.fn();
const recordSnapshot = vi.fn();
vi.mock("./api", () => ({ analyse: (r: unknown) => analyse(r), recordSnapshot: (r: unknown) => recordSnapshot(r) }));

import { analyseLive, analyseRecording, closeAnalysis, saveAnalysedClip } from "./analysis-actions";
import { fixtureResult } from "./analysis-fixture";
import { app } from "./store.svelte";

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: Error) => void;
  const promise = new Promise<T>((a, b) => { resolve = a; reject = b; });
  return { promise, resolve, reject };
}

beforeEach(() => {
  analyse.mockReset();
  recordSnapshot.mockReset();
  app.analysis = { state: "idle" };
  app.analysisOpen = false;
  app.memoriesOpen = false;
  app.error = null;
  app.view = { loHz: 7_000_000, hiHz: 7_200_000 };
  app.region = null;
  app.spanChoice = "visible";
});

describe("analysis actions", () => {
  it("goes running then done and opens the panel", async () => {
    const d = deferred<ReturnType<typeof fixtureResult>>();
    analyse.mockReturnValue(d.promise);
    const p = analyseLive();
    expect(app.analysis.state).toBe("running");
    expect(app.analysisOpen).toBe(true);
    d.resolve(fixtureResult());
    await p;
    expect(app.analysis).toMatchObject({ state: "done", saved: null });
    expect(analyse).toHaveBeenCalledWith({ source: "live", seconds: app.recordSeconds, lo_hz: 7_000_000, hi_hz: 7_200_000 });
  });

  it("reports an error", async () => {
    analyse.mockRejectedValue(new Error("busy"));
    await analyseRecording("r1", "clip");
    expect(app.analysis).toEqual({ state: "error", message: "busy" });
  });

  it("ignores a second run while one is running", async () => {
    const d = deferred<ReturnType<typeof fixtureResult>>();
    analyse.mockReturnValue(d.promise);
    const p = analyseLive();
    await analyseLive();
    expect(analyse).toHaveBeenCalledTimes(1);
    d.resolve(fixtureResult());
    await p;
  });

  it("reopens the panel when the outcome arrives after it was closed", async () => {
    const d = deferred<ReturnType<typeof fixtureResult>>();
    analyse.mockReturnValue(d.promise);
    const p = analyseLive();
    closeAnalysis();
    expect(app.analysisOpen).toBe(false);
    d.resolve(fixtureResult());
    await p;
    expect(app.analysisOpen).toBe(true);
  });

  it("saves the analysed clip once", async () => {
    app.analysis = { state: "done", result: fixtureResult(), saved: null };
    const d = deferred<{ id: string }>();
    recordSnapshot.mockReturnValue(d.promise);
    const p = saveAnalysedClip();
    void saveAnalysedClip();
    expect(app.analysis).toMatchObject({ saving: true });
    d.resolve({ id: "r9" });
    await p;
    expect(recordSnapshot).toHaveBeenCalledTimes(1);
    expect(recordSnapshot).toHaveBeenCalledWith({ analysis_id: "a1" });
    expect(app.analysis).toMatchObject({ state: "done", saved: "r9", saving: false });
  });

  it("returns to Save and shows the error when saving fails", async () => {
    app.analysis = { state: "done", result: fixtureResult(), saved: null };
    recordSnapshot.mockRejectedValue(new Error("gone"));
    await saveAnalysedClip();
    expect(app.analysis).toMatchObject({ saved: null, saving: false });
    expect(app.error).toBe("gone");
  });

  it("does not clobber a newer analysis when a save finishes", async () => {
    app.analysis = { state: "done", result: fixtureResult(), saved: null };
    const save = deferred<{ id: string }>();
    recordSnapshot.mockReturnValue(save.promise);
    const p = saveAnalysedClip();
    const run = deferred<ReturnType<typeof fixtureResult>>();
    analyse.mockReturnValue(run.promise);
    app.analysis = { state: "idle" };
    const q = analyseLive();
    save.resolve({ id: "r9" });
    await p;
    expect(app.analysis.state).toBe("running");
    run.resolve(fixtureResult({ id: "a2" }));
    await q;
    expect(app.analysis).toMatchObject({ state: "done", saved: null });
  });
});
