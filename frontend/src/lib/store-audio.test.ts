import { beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => {
  const sockets: { path: string; start: ReturnType<typeof vi.fn>; stop: ReturnType<typeof vi.fn> }[] = [];
  const players: { start: ReturnType<typeof vi.fn>; stop: ReturnType<typeof vi.fn> }[] = [];
  return {
    sockets,
    players,
    patchState: vi.fn(),
    openSession: vi.fn(),
    closeSession: vi.fn(),
  };
});

vi.mock("./api", () => ({
  patchState: (...a: unknown[]) => h.patchState(...a),
  getState: vi.fn(),
  listDevices: vi.fn(),
  openSession: (...a: unknown[]) => h.openSession(...a),
  closeSession: () => h.closeSession(),
  patchSettings: vi.fn(),
}));

vi.mock("./audio", () => ({
  AudioPlayer: class {
    start = vi.fn(() => new Promise<void>((r) => setTimeout(r, 5)));
    stop = vi.fn();
    setGain = vi.fn();
    setRate = vi.fn();
    push = vi.fn();
    constructor() {
      h.players.push(this);
    }
  },
}));

vi.mock("./ws", () => ({
  ReconnectingSocket: class {
    start = vi.fn();
    stop = vi.fn();
    constructor(public path: string) {
      h.sockets.push(this);
    }
  },
}));

import { app, applyAutoRange, noteFrame, player, reconcileAudio, startStop, toggleAudio } from "./store.svelte";

const radio = (over: Record<string, unknown> = {}) =>
  ({ tune_hz: 7_100_000, running: true, mode: "AM", audio: false, db_min: -120, db_max: -20, ...over }) as any;

beforeEach(() => {
  h.sockets.length = 0;
  h.patchState.mockReset();
  h.patchState.mockImplementation(async (p: Record<string, unknown>) => ({ ...app.radio, ...p }));
  h.openSession.mockReset();
  h.closeSession.mockReset();
  (player.start as any).mockClear();
  (player.stop as any).mockClear();
  app.radio = radio();
  app.audioOn = false;
  app.audioBusy = false;
  app.error = null;
});

describe("audio lifecycle", () => {
  it("tears down audio locally, without a PATCH, when the radio stops server-side", async () => {
    await toggleAudio();
    expect(app.audioOn).toBe(true);
    expect(h.sockets).toHaveLength(1);
    h.patchState.mockClear();

    app.radio = radio({ running: false }); // e.g. a state event after the device was lost
    reconcileAudio();

    expect(app.audioOn).toBe(false);
    expect(h.sockets[0].stop).toHaveBeenCalledTimes(1);
    expect(player.stop).toHaveBeenCalled();
    expect(h.patchState).not.toHaveBeenCalled();
  });

  it("leaves audio alone while the radio is running", async () => {
    await toggleAudio();
    reconcileAudio();
    expect(app.audioOn).toBe(true);
    expect(h.sockets[0].stop).not.toHaveBeenCalled();
  });

  it("ignores a second toggle while the first is still starting", async () => {
    const a = toggleAudio();
    const b = toggleAudio();
    await Promise.all([a, b]);
    expect(player.start).toHaveBeenCalledTimes(1);
    expect(h.sockets).toHaveLength(1);
    expect(app.audioOn).toBe(true);
    expect(app.audioBusy).toBe(false);
  });

  it("does not open the audio socket if the radio stopped while the AudioContext was starting", async () => {
    const p = toggleAudio();
    app.radio = radio({ running: false });
    await p;
    expect(h.sockets).toHaveLength(0);
    expect(app.audioOn).toBe(false);
    expect(player.stop).toHaveBeenCalled();
  });

  it("mute stops the socket and tells the server", async () => {
    await toggleAudio();
    h.patchState.mockClear();
    await toggleAudio();
    expect(app.audioOn).toBe(false);
    expect(h.sockets[0].stop).toHaveBeenCalled();
    expect(h.patchState).toHaveBeenCalledWith({ audio: false });
  });
});

describe("auto range", () => {
  const frame = (fill: number) => ({ seq: 1, centerHz: 7e6, spanHz: 2e6, dbMin: -120, dbMax: -20, bins: new Uint8Array(512).fill(fill) });

  it("sets db_min/db_max from the latest frame", async () => {
    noteFrame(frame(102)); // -80 dB floor
    await applyAutoRange();
    expect(h.patchState).toHaveBeenCalledWith({ db_min: -86, db_max: -30 });
  });

  it("runs once automatically a few frames after Start", async () => {
    app.radio = radio({ running: false });
    h.openSession.mockResolvedValue(radio());
    await startStop();
    for (let i = 0; i < 9; i++) noteFrame(frame(102));
    expect(h.patchState).not.toHaveBeenCalled();
    noteFrame(frame(102));
    await new Promise((r) => setTimeout(r, 0));
    expect(h.patchState).toHaveBeenCalledTimes(1);
    expect(h.patchState).toHaveBeenCalledWith({ db_min: -86, db_max: -30 });
    for (let i = 0; i < 20; i++) noteFrame(frame(102));
    expect(h.patchState).toHaveBeenCalledTimes(1);
  });
});
