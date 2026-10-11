import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { clampCentre, planPanRequest, planPanRetune, planTuneRetune, RetuneController } from "./retune";

const RTL: [number, number] = [500_000, 1_766_000_000];
const SYN: [number, number] = [100_000, 1_800_000_000];
const span = (c: number, rate = 2_048_000) => ({ loHz: c - rate / 2, hiHz: c + rate / 2 });

describe("clampCentre", () => {
  it("keeps the whole span inside the device range", () => {
    expect(clampCentre(0, 2_048_000, RTL)).toBe(500_000 + 1_024_000);
    expect(clampCentre(2e9, 2_048_000, RTL)).toBe(1_766_000_000 - 1_024_000);
    expect(clampCentre(7_100_000, 2_048_000, RTL)).toBe(7_100_000);
  });
  it("centres a span wider than the range", () => expect(clampCentre(1e6, 3e6, [0, 2e6])).toBe(1e6));
});

describe("planPanRetune", () => {
  const s = span(7_100_000);
  it("does nothing while the view is inside the span", () => {
    expect(planPanRetune({ loHz: 6_200_000, hiHz: 8_000_000 }, s, SYN)).toBeNull();
    expect(planPanRetune(s, s, SYN)).toBeNull();
  });
  it("ignores a tiny pan of a zoomed-in view that stays inside", () => {
    expect(planPanRetune({ loHz: 7_000_000, hiHz: 7_010_000 }, s, SYN)).toBeNull();
  });
  it("centres the new span on a view panned past the right edge", () => {
    const view = { loHz: s.loHz + 300_000, hiHz: s.hiHz + 300_000 };
    expect(planPanRetune(view, s, SYN)).toBe(7_400_000);
  });
  it("follows a pan past the left edge", () => {
    const view = { loHz: s.loHz - 500_000, hiHz: s.hiHz - 500_000 };
    expect(planPanRetune(view, s, SYN)).toBe(6_600_000);
  });
  it("keeps a zoomed-in view inside the new span", () => {
    const view = { loHz: s.hiHz + 100_000, hiHz: s.hiHz + 110_000 };
    const c = planPanRetune(view, s, SYN)!;
    const next = span(c);
    expect(view.loHz).toBeGreaterThanOrEqual(next.loHz);
    expect(view.hiHz).toBeLessThanOrEqual(next.hiHz);
  });
  it("clamps at the device maximum and then sends nothing", () => {
    const edge = span(1_800_000_000 - 1_024_000);
    expect(planPanRetune({ loHz: edge.hiHz - 1_000_000, hiHz: edge.hiHz + 1_000_000 }, edge, SYN)).toBeNull();
    const near = span(1_799_000_000);
    expect(planPanRetune({ loHz: 1_799_900_000, hiHz: 1_800_900_000 }, near, SYN)).toBe(1_800_000_000 - 1_024_000);
  });
  it("clamps at the RTL-SDR minimum", () => {
    const low = span(1_524_000);
    expect(planPanRetune({ loHz: 400_000, hiHz: 1_000_000 }, low, RTL)).toBeNull();
    expect(planPanRetune({ loHz: 500_000, hiHz: 2_548_000 }, span(1_600_000), RTL)).toBe(1_524_000);
  });
  it("ignores an empty view", () => expect(planPanRetune({ loHz: 0, hiHz: 0 }, s, SYN)).toBeNull());
});

describe("planPanRetune edge cases", () => {
  it("does not loop for a range as narrow as one span", () => {
    const s = span(1_000_000, 1_000_000);
    expect(planPanRetune({ loHz: 700_000, hiHz: 1_300_000 }, s, [500_000, 1_500_000])).toBeNull();
    expect(planPanRetune({ loHz: 700_000, hiHz: 1_300_000 }, s, [600_000, 1_400_000])).toBeNull(); // narrower: centred already
  });
  it("moves a too-wide span to the middle of a narrow range once", () => {
    expect(planPanRetune({ loHz: 0, hiHz: 100 }, { loHz: 1_000, hiHz: 3_000 }, [0, 1_000])).toBe(500);
  });
  it("recentres a view wider than the span once, then settles", () => {
    const s = span(7_100_000);
    const wide = { loHz: s.loHz - 100_000, hiHz: s.hiHz + 300_000 };
    const c = planPanRetune(wide, s, SYN)!;
    expect(c).toBe(7_200_000);
    expect(planPanRetune(wide, span(c), SYN)).toBeNull();
  });
});

describe("planPanRequest", () => {
  const s = span(7_100_000);
  it("leaves the tuned frequency alone while it stays in the usable span", () => {
    const view = { loHz: s.loHz + 400_000, hiHz: s.hiHz + 400_000 };
    expect(planPanRequest(view, s, SYN, 7_100_000, 1000)).toEqual({ center_hz: 7_500_000 });
  });
  it("makes the tuned frequency follow the view centre, snapped to the step, when it would leave", () => {
    const view = { loHz: 9_000_000, hiHz: 9_200_000 };
    expect(planPanRequest(view, s, SYN, 7_100_000, 1000)).toEqual({ center_hz: 9_100_000, tune_hz: 9_100_000 });
    expect(planPanRequest({ loHz: 9_000_300, hiHz: 9_200_300 }, s, SYN, 7_100_000, 5000))
      .toEqual({ center_hz: 9_100_300, tune_hz: 9_100_000 });
  });
  it("keeps the followed tune inside the usable span and the device range", () => {
    const r = planPanRequest({ loHz: 100_000, hiHz: 100_500 }, { loHz: 3_000_000, hiHz: 5_048_000 }, SYN, 4_000_000, 1000)!;
    expect(r.tune_hz).toBeGreaterThanOrEqual(100_000);
    expect(Math.abs(r.tune_hz! - r.center_hz)).toBeLessThanOrEqual(0.45 * 2_048_000);
  });
  it("sends nothing when no centre change is needed", () => {
    expect(planPanRequest({ loHz: 7_000_000, hiHz: 7_100_000 }, s, SYN, 7_050_000, 1000)).toBeNull();
  });
});

describe("planTuneRetune at the device edges", () => {
  it("does not move when the centre is already pinned at the maximum", () => {
    expect(planTuneRetune(1_799_990_000, span(1_800_000_000 - 1_024_000), SYN)).toBeNull();
  });
  it("does not move when pinned at the minimum", () => {
    expect(planTuneRetune(100_000, span(100_000 + 1_024_000), SYN)).toBeNull();
  });
});

describe("planTuneRetune", () => {
  const s = span(7_100_000);
  it("leaves the middle of the span alone", () => expect(planTuneRetune(7_100_000, s, SYN)).toBeNull());
  it("moves when tuned within 5 % of the edge", () => {
    expect(planTuneRetune(s.hiHz - 50_000, s, SYN)).toBe(s.hiHz - 50_000 + 512_000);
  });
  it("just inside the margin does not move", () => expect(planTuneRetune(s.hiHz - 110_000, s, SYN)).toBeNull());
  it("moves when tuned outside the span", () => expect(planTuneRetune(20_000_000, s, SYN)).toBe(20_512_000));
  it("does not try outside the device range", () => expect(planTuneRetune(50_000, s, SYN)).toBeNull());
});

describe("RetuneController", () => {
  let t = 0;
  beforeEach(() => {
    vi.useFakeTimers();
    t = 0;
  });
  afterEach(() => vi.useRealTimers());
  const make = (
    plan: () => number | null, send: (c: number) => Promise<unknown>, extra: { onSettled?(c: number): void } = {},
  ) => new RetuneController<number>({ plan, send, minIntervalMs: 150, now: () => t, ...extra });

  it("sends the first retune immediately", () => {
    const send = vi.fn().mockResolvedValue(undefined);
    make(() => 5, send).poke();
    expect(send).toHaveBeenCalledWith(5);
  });
  it("sends nothing when no move is planned", () => {
    const send = vi.fn();
    const c = make(() => null, send);
    c.poke();
    expect(send).not.toHaveBeenCalled();
    expect(c.active).toBe(false);
  });
  it("keeps one in flight and coalesces pokes into a single follow-up with the latest centre", async () => {
    let target = 1;
    let release!: () => void;
    const send = vi.fn(() => new Promise<void>((r) => { release = r; }));
    const c = make(() => target, send);
    c.poke();
    target = 2;
    c.poke();
    target = 3;
    c.poke();
    expect(send).toHaveBeenCalledTimes(1);
    expect(c.active).toBe(true);
    t = 10;
    release();
    await vi.advanceTimersByTimeAsync(0);
    expect(send).toHaveBeenCalledTimes(1); // still cooling down
    t = 150;
    await vi.advanceTimersByTimeAsync(150);
    expect(send).toHaveBeenCalledTimes(2);
    expect(send).toHaveBeenLastCalledWith(3);
  });
  it("enforces the minimum interval between retunes", async () => {
    const send = vi.fn().mockResolvedValue(undefined);
    const c = make(() => 7, send);
    c.poke();
    await vi.advanceTimersByTimeAsync(0);
    t = 50;
    c.poke();
    expect(send).toHaveBeenCalledTimes(1);
    t = 150;
    await vi.advanceTimersByTimeAsync(100);
    expect(send).toHaveBeenCalledTimes(2);
  });
  it("goes idle after the last retune and survives a failed send", async () => {
    const send = vi.fn().mockRejectedValue(new Error("busy"));
    const c = make(() => 7, send);
    c.poke();
    await vi.advanceTimersByTimeAsync(0);
    expect(c.active).toBe(false);
    t = 1000;
    c.poke();
    expect(send).toHaveBeenCalledTimes(2);
  });
  it("reports a timer-pending retune as active, and cancel() clears it", async () => {
    const send = vi.fn().mockResolvedValue(undefined);
    const c = make(() => 7, send);
    c.poke();
    await vi.advanceTimersByTimeAsync(0);
    t = 50;
    c.poke();
    expect(c.active).toBe(true);
    c.cancel();
    expect(c.active).toBe(false);
    await vi.advanceTimersByTimeAsync(500);
    expect(send).toHaveBeenCalledTimes(1);
  });
  it("retries on a poke during the cooldown after a failed send", async () => {
    const send = vi.fn().mockRejectedValueOnce(new Error("x")).mockResolvedValue(undefined);
    const c = make(() => 7, send);
    c.poke();
    await vi.advanceTimersByTimeAsync(0);
    t = 40;
    c.poke();
    t = 150;
    await vi.advanceTimersByTimeAsync(150);
    expect(send).toHaveBeenCalledTimes(2);
  });
  it("calls onSettled after the send with active already false, even if the centre was not applied", async () => {
    // setRadio never rejects: after a failed request it rolls back and resolves.
    const applied = { centre: 1 };
    const seen: { active: boolean; applied: number }[] = [];
    let c!: RetuneController<number>;
    c = make(() => 9, async () => undefined, { onSettled: () => seen.push({ active: c.active, applied: applied.centre }) });
    c.poke();
    await vi.advanceTimersByTimeAsync(0);
    expect(seen).toEqual([{ active: false, applied: 1 }]);
  });
  it("exposes movement that arrived mid-flight as queued", () => {
    let release!: () => void;
    const c = make(() => 7, () => new Promise<void>((r) => { release = r; }));
    c.poke();
    expect(c.queued).toBe(false);
    c.poke();
    expect(c.queued).toBe(true);
    release();
  });
});
