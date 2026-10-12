import { recordSnapshot, recordStart, recordStatus, recordStop } from "./api";
import { recordReducer, stoppedMessage, type RecordAction } from "./recording";
import { targetSpan } from "./region";
import { app } from "./store.svelte";
import type { ServerEvent } from "./types";

export function dispatchRecord(a: RecordAction): void {
  app.record = recordReducer(app.record, a);
}

const fail = (e: unknown) => {
  app.error = (e as Error).message;
};
const span = () => targetSpan(app.spanChoice, app.region, app.view);

export async function saveLast(): Promise<void> {
  if (app.record.phase !== "idle") return;
  dispatchRecord({ type: "save" });
  try {
    dispatchRecord({ type: "saved", info: await recordSnapshot({ seconds: app.recordSeconds, ...span() }) });
  } catch (e) {
    dispatchRecord({ type: "failed" });
    fail(e);
  }
}

export async function startRecording(): Promise<void> {
  if (app.record.phase !== "idle") return;
  dispatchRecord({ type: "start" });
  try {
    dispatchRecord({ type: "started", status: await recordStart(span()) });
  } catch (e) {
    dispatchRecord({ type: "failed" });
    fail(e);
  }
}

export async function stopRecording(): Promise<void> {
  if (app.record.phase !== "recording") return;
  dispatchRecord({ type: "stop" });
  try {
    dispatchRecord({ type: "stopped", info: await recordStop() });
  } catch (e) {
    dispatchRecord({ type: "failed" });
    fail(e);
    void syncRecordStatus(); // a recording that already ended server-side returns the button to idle
  }
}

export async function syncRecordStatus(): Promise<void> {
  try {
    dispatchRecord({ type: "status", status: await recordStatus() });
  } catch {
    // the next event or reconnect brings the button back in line
  }
}

export function onRecordEvent(msg: ServerEvent): void {
  if (msg.type === "recording") dispatchRecord({ type: "progress", elapsedS: msg.elapsed_s, bytes: msg.bytes });
  else if (msg.type === "recording_stopped") {
    dispatchRecord({ type: "stopped", info: msg.recording });
    const note = msg.error ?? stoppedMessage(msg.reason);
    if (note) app.error = note;
  }
}
