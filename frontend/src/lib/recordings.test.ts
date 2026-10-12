import { describe, expect, it } from "vitest";
import { formatWhen, matchesRecording, nameError, recordingMeta, replayDevice, thumbUrl, truncationNote } from "./recordings";
import type { RecordingInfo } from "./types";

const rec = (over: Partial<RecordingInfo> = {}): RecordingInfo => ({
  id: "2026-10-10_143005_7100k", name: "Neighbour's charger", created: "2026-10-10T14:30:05.123Z",
  center_hz: 7_100_000, sample_rate: 25_000, lo_hz: 7_090_000, hi_hz: 7_110_000, span_hz: 20_000, duration_s: 65,
  size_bytes: 3_250_000, has_analysis: true, top_cause: "Switch-mode power supply", truncated: false,
  end_reason: "stopped", device_id: "replay:/home/x/rec/iq.sigmf-meta", band: "40m", callsign: null, ...over,
});

describe("recordings helpers", () => {
  it("formats the time in UTC", () => {
    expect(formatWhen(rec().created)).toBe("2026-10-10 14:30 UTC");
    expect(formatWhen("")).toBe("");
  });
  it("summarises span, duration and size", () =>
    expect(recordingMeta(rec())).toBe("7.0900–7.1100 MHz (20.0 kHz) · 1:05 · 3.1 MB"));
  it("explains truncated files", () => {
    expect(truncationNote(rec())).toBeNull();
    expect(truncationNote(rec({ truncated: true, end_reason: "device_lost" }))).toBe("Truncated: the radio was lost");
    expect(truncationNote(rec({ truncated: true, end_reason: "low_disk" }))).toBe("Truncated: disk space ran low");
  });
  it("validates names", () => {
    expect(nameError("ok")).toBeNull();
    expect(nameError("   ")).toMatch(/empty/);
    expect(nameError("x".repeat(81))).toMatch(/80/);
  });
  it("searches name, cause, band and frequency", () => {
    for (const q of ["charger", "switch-mode", "40m", "7.100", ""]) expect(matchesRecording(rec(), q)).toBe(true);
    expect(matchesRecording(rec(), "plasma")).toBe(false);
  });
  it("builds the thumbnail URL and the replay device", () => {
    expect(thumbUrl(rec(), "?token=abc")).toBe("/api/recordings/2026-10-10_143005_7100k/waterfall.png?token=abc");
    const d = replayDevice(rec());
    expect(d.id).toBe("replay:/home/x/rec/iq.sigmf-meta");
    expect(d.label).toBe("Recording: Neighbour's charger");
    expect(d.has_direct_sampling).toBe(false);
    expect(d.freq_range).toEqual([7_087_500, 7_112_500]);
  });
});
