import { beforeEach, describe, expect, it, vi } from "vitest";

const patchState = vi.fn(async (p: Record<string, unknown>) => ({ ...(globalThis as any).__radio, ...p }));
const openSession = vi.fn(async (_id: string, config: Record<string, unknown>) =>
  ({ ...(globalThis as any).__radio, ...config, running: true }));
const createMemory = vi.fn();
const deleteMemory = vi.fn(async () => undefined);
const listMemories = vi.fn();
vi.mock("./api", () => ({
  patchState: (p: any) => patchState(p),
  openSession: (id: string, c: any) => openSession(id, c),
  createMemory: (f: any) => createMemory(f),
  deleteMemory: (id: string) => (deleteMemory as any)(id),
  listMemories: () => listMemories(),
  getState: vi.fn(),
  closeSession: vi.fn(),
  listDevices: vi.fn(),
  updateMemory: vi.fn(),
  patchSettings: vi.fn(),
}));

import { app, recallMemory, refreshMemories, removeMemory, saveMemory, startStop } from "./store.svelte";
import type { DeviceInfo, Memory, RadioState } from "./types";

const radio = (running: boolean, extra: Partial<RadioState> = {}) => ({
  running, device_id: running ? "synthetic" : null, center_hz: 14_100_000, sample_rate: 2_048_000,
  tune_hz: 14_074_000, mode: "USB", bw_hz: 2700, gain_db: null, direct_sampling: "off", ...extra,
}) as RadioState;
const mem = (name: string, tune: number, extra: Partial<Memory> = {}): Memory => ({
  id: name, created: "", name, tune_hz: tune, mode: "AM", bw_hz: 6000, gain_db: null, direct_sampling: null,
  note: "", ...extra,
});
const device = (id: string, has_direct_sampling: boolean) => ({ id, has_direct_sampling }) as DeviceInfo;

beforeEach(() => {
  patchState.mockClear();
  openSession.mockClear();
  listMemories.mockReset();
  app.pendingRecall = null;
  app.memories = [];
  app.selectedDevice = "synthetic";
  app.devices = [device("synthetic", false), device("rtlsdr:0", true)];
});

describe("recallMemory", () => {
  it("patches the running radio, moving the centre when out of span", async () => {
    app.radio = radio(true);
    (globalThis as any).__radio = app.radio;
    await recallMemory(mem("bc", 7_200_000, { gain_db: 25 }));
    expect(patchState).toHaveBeenCalledWith({ tune_hz: 7_200_000, mode: "AM", bw_hz: 6000, gain_db: 25,
      center_hz: 7_200_000 + 512_000 });
    expect(app.pendingRecall).toBeNull();
  });

  it("omits direct_sampling on a device without it and sends it on one that has it", async () => {
    app.radio = radio(true);
    (globalThis as any).__radio = app.radio;
    await recallMemory(mem("v", 14_080_000, { direct_sampling: "q" }));
    expect(patchState.mock.calls[0][0]).not.toHaveProperty("direct_sampling");
    app.radio = radio(true, { device_id: "rtlsdr:0" });
    await recallMemory(mem("v", 14_080_000, { direct_sampling: "q" }));
    expect(patchState.mock.calls[1][0]).toMatchObject({ direct_sampling: "q" });
  });

  it("keeps the recall for Start when stopped, which opens with it", async () => {
    app.selectedDevice = "rtlsdr:0";
    app.radio = radio(false);
    (globalThis as any).__radio = { ...app.radio };
    await recallMemory(mem("v", 7_200_000, { direct_sampling: "q" }));
    expect(patchState).not.toHaveBeenCalled();
    expect(app.radio!.tune_hz).toBe(7_200_000);
    expect(app.radio!.mode).toBe("AM");
    await startStop();
    expect(openSession).toHaveBeenCalledWith("rtlsdr:0",
      { tune_hz: 7_200_000, mode: "AM", bw_hz: 6000, direct_sampling: "q" });
    expect(app.pendingRecall).toBeNull();
    expect(app.radio!.running).toBe(true);
  });

  it("still opens at the recalled frequency after a state event reverts the display", async () => {
    app.radio = radio(false);
    (globalThis as any).__radio = { ...app.radio };
    await recallMemory(mem("v", 7_200_000));
    app.radio = radio(false); // a {"type": "state"} event replaced the local state
    await startStop();
    expect(openSession).toHaveBeenCalledWith("synthetic", { tune_hz: 7_200_000, mode: "AM", bw_hz: 6000 });
  });

  it("works before the radio state has loaded", async () => {
    app.radio = null;
    (globalThis as any).__radio = radio(false);
    await recallMemory(mem("v", 3_600_000));
    expect(app.pendingRecall).toEqual({ tune_hz: 3_600_000, mode: "AM", bw_hz: 6000 });
    await startStop();
    expect(openSession).toHaveBeenCalledWith("synthetic", { tune_hz: 3_600_000, mode: "AM", bw_hz: 6000 });
  });
});

describe("memory list", () => {
  it("keeps the list sorted by frequency after save and drops deleted ones", async () => {
    app.memories = [mem("b", 14e6)];
    createMemory.mockResolvedValueOnce(mem("a", 7e6));
    expect(await saveMemory(mem("a", 7e6))).toBeNull();
    expect(app.memories.map((m) => m.name)).toEqual(["a", "b"]);
    await removeMemory("a");
    expect(deleteMemory).toHaveBeenCalledWith("a");
    expect(app.memories.map((m) => m.name)).toEqual(["b"]);
  });

  it("returns a save error for the dialog instead of raising the snackbar", async () => {
    app.error = null;
    createMemory.mockRejectedValueOnce(new Error("settings folder not writable"));
    expect(await saveMemory(mem("a", 7e6))).toBe("settings folder not writable");
    expect(app.error).toBeNull();
    expect(app.memories).toEqual([]);
  });

  it("ignores a list that arrives after a newer fetch or local change", async () => {
    let resolveOld!: (v: Memory[]) => void;
    listMemories.mockReturnValueOnce(new Promise((r) => (resolveOld = r)));
    listMemories.mockResolvedValueOnce([mem("new", 2e6)]);
    const older = refreshMemories();
    await refreshMemories();
    resolveOld([mem("stale", 1e6)]);
    await older;
    expect(app.memories.map((m) => m.name)).toEqual(["new"]);

    listMemories.mockReturnValueOnce(new Promise((r) => (resolveOld = r)));
    const inflight = refreshMemories();
    createMemory.mockResolvedValueOnce(mem("local", 3e6));
    await saveMemory(mem("local", 3e6));
    resolveOld([]);
    await inflight;
    expect(app.memories.map((m) => m.name)).toEqual(["new", "local"]);
  });
});
