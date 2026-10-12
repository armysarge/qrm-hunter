import { getAppInfo, openRecordingFolder, renameRecording, trashRecording } from "./api";
import { replayDevice } from "./recordings";
import { app, refreshRecordings, startStop } from "./store.svelte";
import type { RecordingInfo } from "./types";

export { refreshRecordings };

export async function refreshAppInfo(): Promise<void> {
  try {
    app.appInfo = await getAppInfo();
  } catch {
    // shown as unknown in Settings; everything else still works
  }
}

export function toggleRecordings(): void {
  app.recordingsOpen = !app.recordingsOpen;
  if (app.recordingsOpen) {
    app.analysisOpen = false;
    app.memoriesOpen = false;
    void refreshRecordings();
  }
}

export async function playRecording(r: RecordingInfo): Promise<void> {
  if (app.radio?.running) {
    await startStop();
    if (app.radio?.running) return; // stopping failed; the error is already shown
  }
  if (!app.devices.some((d) => d.id === r.device_id)) app.devices = [...app.devices, replayDevice(r)];
  app.selectedDevice = r.device_id;
  app.pendingRecall = null;
  if (app.radio) app.radio = { ...app.radio, tune_hz: r.center_hz };
  await startStop();
}

/** Returns null on success or the error message (shown inline in the rename dialog). */
export async function renameRec(id: string, name: string): Promise<string | null> {
  try {
    const updated = await renameRecording(id, name.trim());
    app.recordings = app.recordings.map((r) => (r.id === id ? updated : r));
    void refreshRecordings(); // supersedes any older list still in flight
    return null;
  } catch (e) {
    return (e as Error).message;
  }
}

export async function trashRec(id: string): Promise<void> {
  try {
    await trashRecording(id);
    app.recordings = app.recordings.filter((r) => r.id !== id);
    void refreshRecordings();
  } catch (e) {
    app.error = (e as Error).message;
  }
}

export async function revealRec(id: string): Promise<void> {
  try {
    await openRecordingFolder(id);
  } catch (e) {
    app.error = (e as Error).message;
  }
}
