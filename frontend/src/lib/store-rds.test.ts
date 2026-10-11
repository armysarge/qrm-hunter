import { beforeEach, describe, expect, it, vi } from "vitest";

const patchState = vi.fn(async (p: unknown) => ({ ...(p as object), running: true }) as unknown);
const getState = vi.fn();
vi.mock("./api", () => ({ patchState: (p: unknown) => patchState(p), getState: () => getState() }));

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
  app.error = null;
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
    const pending = tuneBand(BC);
    expect(app.radio!.mode).toBe("WFM"); // optimistic
    expect(patchState).toHaveBeenLastCalledWith({ tune_hz: 97_750_000, mode: "WFM" });
    await pending;
    expect(app.prevMode).toBe("USB");
    app.radio = { running: true, mode: "WFM", tune_hz: 97_750_000 } as any;
    await tuneBand(M20);
    expect(patchState).toHaveBeenLastCalledWith({ tune_hz: 14_175_000, mode: "USB" });
    expect(app.prevMode).toBeNull();
  });
});

describe("tuneBand when the engine refuses WFM", () => {
  it("shows the engine's message and remembers nothing", async () => {
    const msg = "Broadcast FM needs a sample rate of at least 1 MS/s";
    patchState.mockRejectedValueOnce(new Error(msg));
    getState.mockResolvedValueOnce({ running: true, mode: "USB", tune_hz: 14_074_000, sample_rate: 250_000 });
    app.radio = { running: true, mode: "USB", tune_hz: 14_074_000, sample_rate: 250_000 } as any;
    await tuneBand(BC);
    expect(app.error).toBe(msg);
    expect(app.radio!.mode).toBe("USB");
    expect(app.prevMode).toBeNull();
  });
});
