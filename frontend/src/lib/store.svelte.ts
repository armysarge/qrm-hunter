import { closeSession, getState, listDevices, openSession, patchSettings, patchState } from "./api";
import { AudioPlayer } from "./audio";
import { ReconnectingSocket } from "./ws";
import { passband, snapHz, type View } from "./freq";
import type { PlotInteractionOptions } from "./interact";
import type { Band, DeviceInfo, RadioState, Settings, SettingsPatch, StatePatch, Stats } from "./types";

export const app = $state({
  devices: [] as DeviceInfo[],
  selectedDevice: "",
  radio: null as RadioState | null,
  stats: { dropped: 0, overflows: 0, frame_rate: 0, clients: 0 } as Stats,
  bands: [] as Band[],
  settings: null as Settings | null,
  error: null as string | null,
  connected: false,
  view: { loHz: 0, hiHz: 0 } as View,
  plotWidth: 1024,
  peakHold: false,
  stepHz: 1000,
  volume: 0.8,
  audioOn: false,
});

export function fullSpan(r: RadioState): View {
  return { loHz: r.center_hz - r.sample_rate / 2, hiHz: r.center_hz + r.sample_rate / 2 };
}

let pending: StatePatch = {};
let inflight = false;

/** Optimistically apply a patch, then send it. Rapid patches (drag-tuning) are coalesced. */
export async function setRadio(patch: StatePatch): Promise<void> {
  if (app.radio) Object.assign(app.radio, patch);
  Object.assign(pending, patch);
  if (inflight) return;
  inflight = true;
  try {
    while (Object.keys(pending).length) {
      const next = pending;
      pending = {};
      const res = await patchState(next);
      // Newer optimistic edits made while this request was in flight win over the stale response.
      app.radio = Object.keys(pending).length ? { ...res, ...pending } : res;
    }
    app.error = null;
  } catch (e) {
    pending = {};
    app.error = (e as Error).message;
    try {
      app.radio = await getState();
    } catch {
      // keep the original error; the refetch failure adds nothing actionable
    }
  } finally {
    inflight = false;
  }
}

export function plotOptions(onCursor?: (x: number | null, y: number | null) => void): PlotInteractionOptions {
  return {
    getView: () => app.view,
    getLimit: () => (app.radio ? fullSpan(app.radio) : app.view),
    setView: (v) => {
      app.view = v;
    },
    tune: (hz) => {
      if (app.radio?.running) setRadio({ tune_hz: snapHz(hz, Math.min(app.stepHz, 1000)) });
    },
    tuneBy: (delta) => {
      const r = app.radio;
      if (r?.running) setRadio({ tune_hz: Math.round(r.tune_hz + delta) });
    },
    resetView: () => {
      if (app.radio) app.view = fullSpan(app.radio);
    },
    getPassband: () => (app.radio ? passband(app.radio.tune_hz, app.radio.mode, app.radio.bw_hz) : null),
    onCursor,
  };
}

export const player = new AudioPlayer();
let audioSocket: ReconnectingSocket | null = null;

const fail = (e: unknown) => {
  app.error = (e as Error).message;
};

export async function refreshDevices(): Promise<void> {
  try {
    app.devices = await listDevices();
    if (!app.devices.some((d) => d.id === app.selectedDevice)) app.selectedDevice = app.devices[0]?.id ?? "";
  } catch (e) {
    fail(e);
  }
}

export async function startStop(): Promise<void> {
  try {
    if (app.radio?.running) {
      if (app.audioOn) await toggleAudio();
      app.radio = await closeSession();
    } else {
      const tune = app.radio?.tune_hz ?? app.settings?.last_tune_hz ?? 7_100_000;
      app.radio = await openSession(app.selectedDevice, { tune_hz: tune });
      app.error = null;
    }
  } catch (e) {
    fail(e);
  }
}

export async function toggleAudio(): Promise<void> {
  if (app.audioOn) {
    audioSocket?.stop();
    audioSocket = null;
    player.stop();
    app.audioOn = false;
    if (app.radio?.running) await setRadio({ audio: false });
    return;
  }
  try {
    await player.start();
    player.setGain(app.volume);
    audioSocket = new ReconnectingSocket("/ws/audio", {
      onMessage: (ev) => {
        if (typeof ev.data === "string") {
          const msg = JSON.parse(ev.data);
          if (msg.type === "format") player.setRate(msg.rate);
        } else player.push(ev.data as ArrayBuffer);
      },
    });
    audioSocket.start();
    app.audioOn = true;
    await setRadio({ audio: true });
  } catch (e) {
    fail(e);
  }
}

export async function saveSettings(patch: SettingsPatch): Promise<void> {
  try {
    app.settings = await patchSettings(patch);
  } catch (e) {
    fail(e);
  }
}
