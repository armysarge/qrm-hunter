import { describe, expect, it } from "vitest";
import { describeSpan, keepRegion, MIN_REGION_HZ, regionBetween, regionOverlay, targetSpan } from "./region";

const limit = { loHz: 6_076_000, hiHz: 8_124_000 };
const view = { loHz: 7_000_000, hiHz: 7_200_000 };

describe("regionBetween", () => {
  it("orders the edges", () => expect(regionBetween(7_150_000, 7_100_000, limit)).toEqual({ loHz: 7_100_000, hiHz: 7_150_000 }));
  it("clips to the receiver span", () =>
    expect(regionBetween(5_000_000, 6_100_000, limit)).toEqual({ loHz: 6_076_000, hiHz: 6_100_000 }));
  it("is null when narrower than the minimum", () => {
    expect(regionBetween(7_100_000, 7_100_000 + MIN_REGION_HZ - 1, limit)).toBeNull();
    expect(regionBetween(7_100_000, 7_100_000, limit)).toBeNull();
  });
  it("is null entirely outside the span", () => expect(regionBetween(9e6, 9.1e6, limit)).toBeNull());
});

describe("regionOverlay", () => {
  it("gives percentages of the view", () =>
    expect(regionOverlay({ loHz: 7_050_000, hiHz: 7_100_000 }, view)).toEqual({ left: 25, width: 25 }));
  it("clips at the view edges", () =>
    expect(regionOverlay({ loHz: 6_900_000, hiHz: 7_020_000 }, view)).toEqual({ left: 0, width: 10 }));
  it("is null out of view or without a region", () => {
    expect(regionOverlay({ loHz: 7_300_000, hiHz: 7_400_000 }, view)).toBeNull();
    expect(regionOverlay(null, view)).toBeNull();
  });
});

describe("keepRegion and targetSpan", () => {
  it("drops a region that a retune moved out of the span", () => {
    const r = { loHz: 7_100_000, hiHz: 7_110_000 };
    expect(keepRegion(r, limit)).toEqual(r);
    expect(keepRegion(r, { loHz: 13e6, hiHz: 15e6 })).toBeNull();
  });
  it("uses the selection only when chosen and present", () => {
    const r = { loHz: 7_100_000.4, hiHz: 7_110_000.6 };
    expect(targetSpan("selection", r, view)).toEqual({ lo_hz: 7_100_000, hi_hz: 7_110_001 });
    expect(targetSpan("visible", r, view)).toEqual({ lo_hz: 7_000_000, hi_hz: 7_200_000 });
    expect(targetSpan("selection", null, view)).toEqual({ lo_hz: 7_000_000, hi_hz: 7_200_000 });
  });
  it("describes HF and UHF spans", () => {
    expect(describeSpan(7_095_000, 7_105_000)).toBe("7.0950–7.1050 MHz (10.0 kHz)");
    expect(describeSpan(434_000_000, 436_048_000)).toBe("434.0000–436.0480 MHz (2.05 MHz)");
    expect(describeSpan(7_100_000, 7_100_500)).toBe("7.1000–7.1005 MHz (500 Hz)");
  });
});
