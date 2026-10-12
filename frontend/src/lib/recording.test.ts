import { describe, expect, it } from "vitest";
import {
  clampSeconds, formatBytes, formatElapsed, initialRecord, primaryLabel, recordReducer, stoppedMessage,
  type RecordAction, type RecordUi,
} from "./recording";
import type { RecordingInfo } from "./types";

const info = { id: "2026-10-10_120000_7100k", name: "x" } as RecordingInfo;
const run = (actions: RecordAction[], from: RecordUi = initialRecord()) => actions.reduce(recordReducer, from);
const status = (recording: boolean, elapsed_s = 0, bytes = 0) => ({ recording, elapsed_s, bytes, id: recording ? "r" : null });

describe("record state machine", () => {
  it("saves", () => {
    expect(run([{ type: "save" }]).phase).toBe("saving");
    const done = run([{ type: "save" }, { type: "saved", info }]);
    expect(done.phase).toBe("idle");
    expect(done.lastSaved).toBe(info);
    expect(run([{ type: "save" }, { type: "failed" }]).phase).toBe("idle");
  });
  it("ignores a second save or a start while saving", () => {
    expect(run([{ type: "save" }, { type: "save" }]).phase).toBe("saving");
    expect(run([{ type: "save" }, { type: "start" }]).phase).toBe("saving");
  });
  it("starts, shows progress and stops", () => {
    const rec = run([{ type: "start" }, { type: "started", status: status(true, 0, 0) },
      { type: "progress", elapsedS: 3, bytes: 3_000_000 }]);
    expect(rec).toMatchObject({ phase: "recording", elapsedS: 3, bytes: 3_000_000 });
    expect(run([{ type: "stop" }], rec).phase).toBe("stopping");
    const stopped = run([{ type: "stop" }, { type: "stopped", info }], rec);
    expect(stopped).toMatchObject({ phase: "idle", elapsedS: 0, bytes: 0, lastSaved: info });
  });
  it("a failed start returns to idle; a failed stop keeps recording", () => {
    expect(run([{ type: "start" }, { type: "failed" }]).phase).toBe("idle");
    const rec = run([{ type: "start" }, { type: "started", status: status(true) }]);
    expect(run([{ type: "stop" }, { type: "failed" }], rec).phase).toBe("recording");
  });
  it("follows a recording started in another window", () =>
    expect(run([{ type: "progress", elapsedS: 5, bytes: 10 }]).phase).toBe("recording"));
  it("a server-side end (cap, device lost) returns to idle without a stop", () => {
    const rec = run([{ type: "progress", elapsedS: 5, bytes: 10 }]);
    expect(run([{ type: "stopped", info: null }], rec).phase).toBe("idle");
  });
  it("syncs from status after a reload", () => {
    expect(run([{ type: "status", status: status(true, 12, 99) }])).toMatchObject({ phase: "recording", elapsedS: 12 });
    const rec = run([{ type: "progress", elapsedS: 5, bytes: 10 }]);
    expect(run([{ type: "status", status: status(false) }], rec).phase).toBe("idle");
    expect(run([{ type: "save" }, { type: "status", status: status(false) }]).phase).toBe("saving");
  });
  it("a stop with nothing recording does nothing", () => expect(run([{ type: "stop" }]).phase).toBe("idle"));
});

describe("labels", () => {
  it("formats sizes and times", () => {
    expect(formatBytes(512_000)).toBe("500 kB");
    expect(formatBytes(3_250_000)).toBe("3.1 MB");
    expect(formatBytes(2.5 * 1024 ** 3)).toBe("2.50 GB");
    expect(formatElapsed(65.4)).toBe("1:05");
    expect(formatElapsed(3723)).toBe("1:02:03");
  });
  it("labels the primary action by phase", () => {
    expect(primaryLabel(initialRecord(), 10)).toBe("Save last 10 s");
    expect(primaryLabel(run([{ type: "save" }]), 10)).toBe("Saving…");
    expect(primaryLabel(run([{ type: "progress", elapsedS: 65, bytes: 3_250_000 }]), 10)).toBe("1:05 · 3.1 MB");
    // a recording started with a 10 s lead-in already holds 10 s of data
    expect(primaryLabel(run([{ type: "start" }, { type: "started", status: status(true, 10.02, 16_384_000) }]), 10))
      .toBe("0:10 · 15.6 MB");
  });
  it("clamps the seconds to the pre-buffer", () => {
    expect(clampSeconds(30, 10)).toBe(10);
    expect(clampSeconds(99, 10)).toBe(10); // typed over a state that is already 10
    expect(clampSeconds(0, 10)).toBe(2);
    expect(clampSeconds(1, 10)).toBe(2);
    expect(clampSeconds(7.6, 60)).toBe(8);
    expect(clampSeconds(Number.NaN, 10)).toBe(10);
  });
  it("explains why a recording stopped", () => {
    expect(stoppedMessage("low_disk")).toMatch(/500 MB/);
    expect(stoppedMessage("retuned")).toMatch(/frequency/);
    expect(stoppedMessage("stopped")).toBeNull();
  });
});
