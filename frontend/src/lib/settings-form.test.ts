import { describe, expect, it } from "vitest";
import { formFromSettings, memoryNote, validateSettings } from "./settings-form";
import type { Settings } from "./types";

const settings = { region: 1, mains_hz: "auto", theme: "dark", colormap: "turbo", last_tune_hz: 7_100_000,
  prebuffer_s: 10, recordings_dir: "", callsign: "" } as Settings;

describe("settings form", () => {
  it("round-trips the current settings", () => {
    const f = formFromSettings(settings);
    expect(f).toEqual({ prebuffer: "10", folder: "", callsign: "" });
    expect(validateSettings(f)).toEqual({ patch: { prebuffer_s: 10, recordings_dir: "", callsign: "" }, errors: {} });
  });
  it("normalises and validates", () => {
    const ok = validateSettings({ prebuffer: " 30 ", folder: "  D:/QRM  ", callsign: "zs6shz/p" });
    expect(ok.patch).toEqual({ prebuffer_s: 30, recordings_dir: "D:/QRM", callsign: "ZS6SHZ/P" });
    const bad = validateSettings({ prebuffer: "61", folder: "", callsign: "ZS6 SHZ" });
    expect(Object.keys(bad.errors).sort()).toEqual(["callsign", "prebuffer"]);
    expect(validateSettings({ prebuffer: "abc", folder: "", callsign: "" }).errors.prebuffer).toMatch(/0 and 60/);
    expect(validateSettings({ prebuffer: "0", folder: "", callsign: "" }).errors).toEqual({});
  });
  it("estimates pre-buffer memory", () => {
    expect(memoryNote(10, 2_048_000)).toBe("About 82 MB of memory at 2.048 MS/s");
    expect(memoryNote(0, 2_048_000)).toBe("Off: Save last N s and live Analyse are unavailable");
  });
});
