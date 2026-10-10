import { beforeEach, describe, expect, it, vi } from "vitest";

const patchState = vi.fn(async (p: unknown) => ({ ...(p as object), running: true }));
vi.mock("./api", () => ({ patchState: (p: unknown) => patchState(p), getState: vi.fn() }));

import { app, noteRds, tuneBand } from "./store.svelte";
import type { Band, RdsState } from "./types";

const rds = (extra: Partial<RdsState> = {}): RdsState => ({
  pi: "6201", ps: "QRM DEMO", rt: null, pty: 10, pty_name: "Pop Music", tp: true, ta: false, ms: true,
  ct: null, rtplus: null, stereo: true, synced: true, block_error_rate: 0, ...extra,
});
const BC: Band = { name: "FM BC", lo_hz: 87_500_000, hi_hz: 108_000_000, default_hz: 97_750_000, group: "BC" };
const M20: Band = { name: "20m", lo_hz: 14_000_000, hi_hz: 14_350_000, default_hz: 14_175_000, group: "HF" };

beforeEach(() => {
  app.rds = null;
  app.taSnack = null;
  app.prevMode = null;
  patchState.mockClear();
});

describe("RDS in the store", () => {
  it("raises and clears the traffic-announcement snackbar on TA transitions", () => {
    noteRds(rds());
    expect(app.taSnack).toBeNull();
    noteRds(rds({ ta: true }));
    expect(app.taSnack).toEqual({ ps: "QRM DEMO" });
    noteRds(rds({ ta: true, rt: "news" }));
    expect(app.taSnack).toEqual({ ps: "QRM DEMO" });
    noteRds(rds({ ta: false }));
    expect(app.taSnack).toBeNull();
    expect(app.rds?.ta).toBe(false);
  });

  it("clears everything when the engine reports no RDS", () => {
    noteRds(rds({ ta: true }));
    noteRds(null);
    expect(app.rds).toBeNull();
    expect(app.taSnack).toBeNull();
  });
});

describe("tuneBand", () => {
  it("goes to WFM on the broadcast band and back to the previous mode", async () => {
    app.radio = { running: true, mode: "USB", tune_hz: 14_074_000 } as any;
    tuneBand(BC);
    expect(app.radio!.mode).toBe("WFM"); // optimistic
    expect(patchState).toHaveBeenLastCalledWith({ tune_hz: 97_750_000, mode: "WFM" });
    await Promise.resolve();
    expect(app.prevMode).toBe("USB");
    app.radio = { running: true, mode: "WFM", tune_hz: 97_750_000 } as any;
    tuneBand(M20);
    expect(patchState).toHaveBeenLastCalledWith({ tune_hz: 14_175_000, mode: "USB" });
    expect(app.prevMode).toBeNull();
  });
});
