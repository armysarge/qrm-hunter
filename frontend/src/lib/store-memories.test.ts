import { beforeEach, describe, expect, it, vi } from "vitest";

const patchState = vi.fn(async (p: Record<string, unknown>) => ({ ...(globalThis as any).__radio, ...p }));
const openSession = vi.fn(async (_id: string, config: Record<string, unknown>) =>
  ({ ...(globalThis as any).__radio, ...config, running: true }));
const createMemory = vi.fn();
const deleteMemory = vi.fn(async () => undefined);
vi.mock("./api", () => ({
  patchState: (p: any) => patchState(p),
  openSession: (id: string, c: any) => openSession(id, c),
  createMemory: (f: any) => createMemory(f),
  deleteMemory: (id: string) => (deleteMemory as any)(id),
  getState: vi.fn(),
  closeSession: vi.fn(),
  listDevices: vi.fn(),
  listMemories: vi.fn(),
  updateMemory: vi.fn(),
  patchSettings: vi.fn(),
}));

import { app, recallMemory, removeMemory, saveMemory, startStop } from "./store.svelte";
import type { Memory, RadioState } from "./types";

const radio = (running: boolean) => ({
  running, center_hz: 14_100_000, sample_rate: 2_048_000, tune_hz: 14_074_000, mode: "USB", bw_hz: 2700,
  gain_db: null, direct_sampling: "off",
}) as RadioState;
const mem = (name: string, tune: number, extra: Partial<Memory> = {}): Memory => ({
  id: name, created: "", name, tune_hz: tune, mode: "AM", bw_hz: 6000, gain_db: null, direct_sampling: null,
  note: "", ...extra,
});

beforeEach(() => {
  patchState.mockClear();
  openSession.mockClear();
  app.pendingRecall = null;
  app.memories = [];
  app.selectedDevice = "synthetic";
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

  it("keeps the recall for Start when stopped, which opens with it", async () => {
    app.radio = radio(false);
    (globalThis as any).__radio = { ...app.radio };
    await recallMemory(mem("v", 7_200_000, { direct_sampling: "q" }));
    expect(patchState).not.toHaveBeenCalled();
    expect(app.radio!.tune_hz).toBe(7_200_000);
    expect(app.radio!.mode).toBe("AM");
    await startStop();
    expect(openSession).toHaveBeenCalledWith("synthetic",
      { tune_hz: 7_200_000, mode: "AM", bw_hz: 6000, direct_sampling: "q" });
    expect(app.pendingRecall).toBeNull();
    expect(app.radio!.running).toBe(true);
  });
});

describe("memory list", () => {
  it("keeps the list sorted by frequency after save and drops deleted ones", async () => {
    app.memories = [mem("b", 14e6)];
    createMemory.mockResolvedValueOnce(mem("a", 7e6));
    expect(await saveMemory(mem("a", 7e6))).toBe(true);
    expect(app.memories.map((m) => m.name)).toEqual(["a", "b"]);
    await removeMemory("a");
    expect(deleteMemory).toHaveBeenCalledWith("a");
    expect(app.memories.map((m) => m.name)).toEqual(["b"]);
  });
});
