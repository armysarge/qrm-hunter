import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const deferreds: { resolve: (v: unknown) => void; reject: (e: unknown) => void }[] = [];
const patchState = vi.fn(
  () => new Promise((resolve, reject) => deferreds.push({ resolve, reject })),
);
const getState = vi.fn();
vi.mock("./api", () => ({ patchState: (...a: unknown[]) => (patchState as any)(...a), getState: () => getState() }));

import { app, plotOptions, retune, setRadio, tuneRadio } from "./store.svelte";

const radio = (tune: number) => ({ tune_hz: tune, running: true, mode: "AM" }) as any;
const tick = () => new Promise((r) => setTimeout(r, 0));

beforeEach(() => {
  deferreds.length = 0;
  patchState.mockClear();
  getState.mockReset();
  app.radio = radio(1000);
  app.error = null;
});

describe("setRadio", () => {
  it("keeps newer optimistic values over a stale response and ends on the last response", async () => {
    const p1 = setRadio({ tune_hz: 2000 });
    await tick();
    const p2 = setRadio({ tune_hz: 3000 });
    expect(app.radio!.tune_hz).toBe(3000);
    deferreds[0].resolve(radio(2000));
    await tick();
    expect(app.radio!.tune_hz).toBe(3000);
    expect(patchState).toHaveBeenCalledTimes(2);
    deferreds[1].resolve(radio(3000));
    await Promise.all([p1, p2]);
    expect(app.radio!.tune_hz).toBe(3000);
    expect(app.error).toBeNull();
  });

  it("refetches the true state after an error", async () => {
    getState.mockResolvedValue(radio(1000));
    const p = setRadio({ tune_hz: 5000 });
    await tick();
    expect(app.radio!.tune_hz).toBe(5000);
    deferreds[0].reject(new Error("boom"));
    await p;
    expect(getState).toHaveBeenCalledTimes(1);
    expect(app.radio!.tune_hz).toBe(1000);
    expect(app.error).toBe("boom");
  });

  it("keeps the error if the refetch also fails", async () => {
    getState.mockRejectedValue(new Error("down"));
    const p = setRadio({ tune_hz: 5000 });
    await tick();
    deferreds[0].reject(new Error("boom"));
    await p;
    expect(app.error).toBe("boom");
  });
});

describe("pan retune", () => {
  const live = (center: number, tune = center) =>
    ({ device_id: "d", running: true, mode: "AM", center_hz: center, tune_hz: tune, sample_rate: 2_048_000 }) as any;
  beforeEach(async () => {
    retune.cancel();
    await new Promise((r) => setTimeout(r, 170)); // let the controller's cooldown from an earlier test pass
    app.devices = [{ id: "d", freq_range: [100_000, 1_800_000_000] }] as any;
    app.stepHz = 1000;
    app.radio = live(7_100_000);
    app.view = { loHz: 7_900_000, hiHz: 9_000_000 };
  });

  it("sends the new centre with the tuned frequency following the view", async () => {
    retune.poke();
    await tick();
    expect(patchState).toHaveBeenCalledWith({ center_hz: 8_450_000, tune_hz: 8_450_000 });
    deferreds[0].resolve(live(8_450_000));
    await tick();
    expect(app.view).toEqual({ loHz: 7_900_000, hiHz: 9_000_000 });
  });

  it("snaps the view back into the span when the request fails", async () => {
    getState.mockResolvedValue(live(7_100_000));
    retune.poke();
    await tick();
    deferreds[0].reject(new Error("device busy"));
    await tick();
    await tick();
    expect(app.error).toBe("device busy");
    expect(app.view.hiHz).toBeLessThanOrEqual(8_124_000);
    expect(app.view.hiHz - app.view.loHz).toBe(1_100_000);
    expect(retune.active).toBe(false);
  });

  describe("while a recording holds the centre", () => {
    beforeEach(() => {
      app.record = { ...app.record, phase: "recording" };
      app.view = { loHz: 6_100_000, hiHz: 7_100_000 };
    });
    afterEach(async () => {
      app.record = { ...app.record, phase: "idle" };
      // Let the send loop finish so the next test starts with none in flight.
      for (const d of deferreds) d.resolve(live(7_100_000));
      await tick();
    });

    it("keeps a pan past the span edge inside the span and sends no retune", async () => {
      plotOptions().panBy(5_000_000);
      await tick();
      expect(app.view).toEqual({ loHz: 7_124_000, hiHz: 8_124_000 });
      retune.poke();
      await tick();
      expect(patchState).not.toHaveBeenCalled();
    });

    it("tunes near the edge without moving the centre", async () => {
      void tuneRadio(8_100_000); // outer 5 % of the span: would normally move the centre
      await tick();
      expect(patchState).toHaveBeenCalledWith({ tune_hz: 8_021_600 }); // centre + 0.45 * rate
      expect(app.radio!.center_hz).toBe(7_100_000);
    });

    it("still tunes freely within the span", async () => {
      void tuneRadio(7_300_000);
      await tick();
      expect(patchState).toHaveBeenCalledWith({ tune_hz: 7_300_000 });
    });

    it("lets pan and edge tuning move the centre again once the recording has ended", async () => {
      app.record = { ...app.record, phase: "idle" };
      void tuneRadio(8_100_000);
      await tick();
      expect(patchState).toHaveBeenCalledWith(expect.objectContaining({ tune_hz: 8_100_000, center_hz: expect.any(Number) }));
    });
  });

  it("waits for the real round trip even when another send is already in flight", async () => {
    const other = setRadio({ gain_db: 10 });
    await tick();
    let done = false;
    const p = setRadio({ center_hz: 8_450_000 }).then(() => (done = true));
    await tick();
    expect(done).toBe(false);
    deferreds[0].resolve(live(7_100_000));
    await tick();
    expect(patchState).toHaveBeenCalledTimes(2);
    expect(done).toBe(false);
    deferreds[1].resolve(live(8_450_000));
    await Promise.all([other, p]);
    expect(done).toBe(true);
  });
});
