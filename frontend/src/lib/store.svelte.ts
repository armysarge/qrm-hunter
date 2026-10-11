import {
  closeSession, createMemory, deleteMemory, getState, listDevices, listMemories, openSession, patchSettings,
  patchState, updateMemory,
} from "./api";
import { AudioPlayer } from "./audio";
import { ReconnectingSocket } from "./ws";
import { autoRange, passband, snapHz, type View } from "./freq";
import type { SpectrumFrame } from "./protocol";
import type { PlotInteractionOptions } from "./interact";
import { byFrequency, openConfig, recallPatch } from "./memories";
import { selectBand } from "./bands";
import { taSnackbar, type TaSnack } from "./rds";
import type {
  Band, DeviceInfo, Memory, MemoryFields, Mode, RadioState, RdsState, Settings, SettingsPatch, StatePatch, Stats,
} from "./types";

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
  audioBusy: false,
  memories: [] as Memory[],
  memoriesOpen: false,
  /** Save/edit dialog: null = closed; memory = editing that one, otherwise saving the current tune. */
  memoryDialog: null as { memory: Memory | null } | null,
  /** A memory recalled while stopped: applied by the next Start. */
  pendingRecall: null as StatePatch | null,
  /** Decoded RDS while in WFM (null otherwise, or until something is decoded). */
  rds: null as RdsState | null,
  /** The traffic-announcement snackbar, while one is showing. */
  taSnack: null as TaSnack | null,
  /** The mode to go back to when leaving the FM broadcast band. */
  prevMode: null as Mode | null,
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

/** A new RDS state from the engine: also drives the traffic-announcement snackbar. */
export function noteRds(next: RdsState | null): void {
  app.taSnack = taSnackbar(app.rds, next, app.taSnack);
  app.rds = next;
}

/** Tune to a band chip, switching to and from WFM for the FM broadcast band. The mode to restore
 *  is remembered only once the engine has accepted the switch (WFM needs at least 1 MS/s; a
 *  refusal shows the engine's message and leaves the mode as it was). */
export async function tuneBand(band: Band): Promise<void> {
  const choice = selectBand(band, app.radio?.mode, app.prevMode);
  await setRadio(choice.patch);
  if (choice.patch.mode === undefined || app.radio?.mode === choice.patch.mode) app.prevMode = choice.prevMode;
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
      lastFrame = null;
      app.radio = await openSession(app.selectedDevice, openConfig(tune, app.pendingRecall));
      app.pendingRecall = null;
      app.error = null;
      if (app.radio?.running) autoRangeCountdown = AUTO_RANGE_AFTER_FRAMES;
    }
  } catch (e) {
    fail(e);
  }
}

/** Tear down local audio (socket, AudioContext) without telling the server. */
export function stopAudioLocal(): void {
  audioSocket?.stop();
  audioSocket = null;
  player.stop();
  app.audioOn = false;
}

/** The radio stopped (device lost, closed elsewhere): audio can't continue, so drop it locally. */
export function reconcileAudio(): void {
  if (app.audioOn && !app.radio?.running) stopAudioLocal();
}

export async function toggleAudio(): Promise<void> {
  if (app.audioBusy) return; // a second click while starting/stopping must not double up
  app.audioBusy = true;
  try {
    if (app.audioOn) {
      stopAudioLocal();
      if (app.radio?.running) await setRadio({ audio: false });
      return;
    }
    try {
      await player.start();
      if (!app.radio?.running) {
        player.stop(); // radio stopped while the AudioContext was starting
        return;
      }
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
      stopAudioLocal();
      fail(e);
    }
  } finally {
    app.audioBusy = false;
  }
}

// --- Auto range: set the display dB range from the latest spectrum frame ---
const AUTO_RANGE_AFTER_FRAMES = 10; // let the averaging settle (~0.4 s) before the automatic run
let lastFrame: SpectrumFrame | null = null;
let autoRangeCountdown = 0;

/** Called for every spectrum frame (non-reactive). Triggers the one automatic auto-range after Start. */
export function noteFrame(f: SpectrumFrame): void {
  lastFrame = f;
  if (autoRangeCountdown > 0 && --autoRangeCountdown === 0) void applyAutoRange();
}

export async function applyAutoRange(): Promise<void> {
  const f = lastFrame;
  if (!f || !app.radio?.running) return;
  const r = autoRange(f.bins, f.dbMin, f.dbMax);
  if (r) await setRadio(r);
}


export async function saveSettings(patch: SettingsPatch): Promise<void> {
  try {
    app.settings = await patchSettings(patch);
  } catch (e) {
    fail(e);
  }
}

// --- Frequency memories ---

// Bumped by every fetch and every local change, so an older in-flight list cannot overwrite newer data.
let memorySeq = 0;

export async function refreshMemories(): Promise<void> {
  const seq = ++memorySeq;
  try {
    const list = (await listMemories()).sort(byFrequency);
    if (seq === memorySeq) app.memories = list;
  } catch (e) {
    if (seq === memorySeq) fail(e);
  }
}

function setMemories(list: Memory[]): void {
  memorySeq++;
  app.memories = list.sort(byFrequency);
}

const message = (e: unknown) => (e as Error).message || "Could not save the memory";

/** Returns null on success or the error message (the dialog shows it inline). */
export async function saveMemory(fields: MemoryFields): Promise<string | null> {
  try {
    const m = await createMemory(fields);
    setMemories([...app.memories.filter((x) => x.id !== m.id), m]);
    return null;
  } catch (e) {
    return message(e);
  }
}

/** Returns null on success or the error message (the dialog shows it inline). */
export async function editMemory(id: string, patch: Partial<MemoryFields>): Promise<string | null> {
  try {
    const m = await updateMemory(id, patch);
    setMemories(app.memories.map((x) => (x.id === id ? m : x)));
    return null;
  } catch (e) {
    return message(e);
  }
}

export async function removeMemory(id: string): Promise<void> {
  try {
    await deleteMemory(id);
    setMemories(app.memories.filter((x) => x.id !== id));
  } catch (e) {
    fail(e);
  }
}

/** The device a recall applies to: the running one, else the one Start would open. */
function targetDevice(): DeviceInfo | undefined {
  const id = app.radio?.running ? app.radio.device_id : app.selectedDevice;
  return app.devices.find((d) => d.id === id);
}

/** Tune to a memory. While stopped (or before the state has loaded) it is shown and kept for
 *  Start, which opens with it. */
export async function recallMemory(m: Memory): Promise<void> {
  const r = app.radio;
  const patch = recallPatch(m, r, !!targetDevice()?.has_direct_sampling);
  if (r?.running) {
    app.pendingRecall = null;
    await setRadio(patch);
    return;
  }
  app.pendingRecall = patch;
  if (r) Object.assign(r, { tune_hz: patch.tune_hz, mode: patch.mode, bw_hz: patch.bw_hz });
}
