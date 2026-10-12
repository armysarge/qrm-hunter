import { beforeEach, describe, expect, it, vi } from "vitest";

const openSession = vi.fn(async (_id: string, config: Record<string, unknown>) => ({ running: true, device_id: _id, ...config }));
const closeSession = vi.fn(async () => ({ running: false, device_id: null, tune_hz: 7_100_000 }));
const patchSettings = vi.fn(async (p: Record<string, unknown>) => ({ prebuffer_s: 10, ...p }));
vi.mock("./api", () => ({
  openSession: (id: string, c: any) => openSession(id, c),
  closeSession: () => closeSession(),
  patchSettings: (p: any) => patchSettings(p),
  listRecordings: vi.fn(async () => []),
  renameRecording: vi.fn(), trashRecording: vi.fn(), openRecordingFolder: vi.fn(), getAppInfo: vi.fn(),
  patchState: vi.fn(), getState: vi.fn(), listDevices: vi.fn(),
}));

import { playRecording } from "./recordings-actions";
import { app, saveSettings } from "./store.svelte";
import type { RadioState, RecordingInfo, Settings } from "./types";

const rec = {
  id: "r1", name: "Charger", center_hz: 7_100_000, sample_rate: 25_000, device_id: "replay:/x/iq.sigmf-meta",
} as RecordingInfo;

beforeEach(() => {
  openSession.mockClear();
  closeSession.mockClear();
  app.devices = [];
  app.selectedDevice = "synthetic";
  app.radio = { running: true, device_id: "synthetic", tune_hz: 14_000_000 } as RadioState;
});

describe("playRecording", () => {
  it("stops the radio, lists the recording as a device and opens it at its centre", async () => {
    await playRecording(rec);
    expect(closeSession).toHaveBeenCalledOnce();
    expect(app.devices.map((d) => d.id)).toEqual(["replay:/x/iq.sigmf-meta"]);
    expect(openSession).toHaveBeenCalledOnce();
    const [id, config] = openSession.mock.calls[0];
    expect(id).toBe("replay:/x/iq.sigmf-meta");
    expect(config).toMatchObject({ tune_hz: 7_100_000 });
  });
});

describe("saveSettings", () => {
  it("re-clamps Save last N s to a shorter pre-buffer", async () => {
    app.settings = { prebuffer_s: 30 } as Settings;
    app.recordSeconds = 20;
    await saveSettings({ prebuffer_s: 5 });
    expect(app.recordSeconds).toBe(5);
  });
});
