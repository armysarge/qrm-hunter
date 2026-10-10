import { patchState } from "./api";
import { passband, snapHz, type View } from "./freq";
import type { PlotInteractionOptions } from "./interact";
import type { Band, DeviceInfo, RadioState, Settings, StatePatch, Stats } from "./types";

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
      app.radio = await patchState(next);
    }
    app.error = null;
  } catch (e) {
    pending = {};
    app.error = (e as Error).message;
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
