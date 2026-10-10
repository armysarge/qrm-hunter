import { describe, expect, it } from "vitest";
import { formatClock, nowPlaying, taMessage, taSnackbar } from "./rds";
import type { RdsState } from "./types";

const rds = (extra: Partial<RdsState> = {}): RdsState => ({
  pi: "6201", ps: "QRM DEMO", rt: "Now playing: ZS6SHZ - Noise Floor Blues", pty: 10, pty_name: "Pop Music",
  tp: true, ta: false, ms: true, ct: null, rtplus: null, stereo: true, synced: true, block_error_rate: 0,
  ...extra,
});

describe("nowPlaying", () => {
  it("prefers RT+ artist and title", () => {
    expect(nowPlaying(rds({ rtplus: { artist: "ZS6SHZ", title: "Noise Floor Blues" } }))).toBe("ZS6SHZ – Noise Floor Blues");
  });

  it("falls back to the RadioText", () => {
    expect(nowPlaying(rds())).toBe("Now playing: ZS6SHZ - Noise Floor Blues");
    expect(nowPlaying(rds({ rtplus: { artist: null, title: "Only a title" } }))).toBe("Now playing: ZS6SHZ - Noise Floor Blues");
  });

  it("uses a lone RT+ tag when there is no RadioText, else nothing", () => {
    expect(nowPlaying(rds({ rt: null, rtplus: { artist: null, title: "Only a title" } }))).toBe("Only a title");
    expect(nowPlaying(rds({ rt: "   " }))).toBeNull();
    expect(nowPlaying(null)).toBeNull();
  });
});

describe("formatClock", () => {
  it("shows the station's local time and offset", () => {
    expect(formatClock("2026-10-10T14:30:00+02:00")).toBe("14:30 UTC+02:00");
    expect(formatClock("2026-10-10T09:05:00-05:00")).toBe("09:05 UTC-05:00");
    expect(formatClock("2026-10-10T12:00:00+00:00")).toBe("12:00 UTC");
    expect(formatClock(null)).toBeNull();
    expect(formatClock("garbage")).toBeNull();
  });
});

describe("taSnackbar", () => {
  it("appears when TA goes on while TP is set and goes when TA clears", () => {
    const on = taSnackbar(rds(), rds({ ta: true }), null);
    expect(on).toEqual({ ps: "QRM DEMO" });
    expect(taMessage(on!)).toBe("Traffic announcement — QRM DEMO");
    expect(taSnackbar(rds({ ta: true }), rds({ ta: true }), on)).toEqual(on);
    expect(taSnackbar(rds({ ta: true }), rds({ ta: false }), on)).toBeNull();
  });

  it("needs TP", () => {
    expect(taSnackbar(rds({ tp: false }), rds({ tp: false, ta: true }), null)).toBeNull();
  });

  it("stays dismissed for the rest of the same announcement", () => {
    expect(taSnackbar(rds({ ta: true }), rds({ ta: true }), null)).toBeNull();
  });

  it("goes when RDS goes away", () => {
    expect(taSnackbar(rds({ ta: true }), null, { ps: "QRM DEMO" })).toBeNull();
  });

  it("works without a station name", () => {
    expect(taMessage({ ps: null })).toBe("Traffic announcement");
  });
});
