import { beforeEach, describe, expect, it, vi } from "vitest";

const deferreds: { resolve: (v: unknown) => void; reject: (e: unknown) => void }[] = [];
const patchState = vi.fn(
  () => new Promise((resolve, reject) => deferreds.push({ resolve, reject })),
);
const getState = vi.fn();
vi.mock("./api", () => ({ patchState: (...a: unknown[]) => (patchState as any)(...a), getState: () => getState() }));

import { app, setRadio } from "./store.svelte";

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
