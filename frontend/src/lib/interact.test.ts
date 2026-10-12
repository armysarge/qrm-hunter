import { describe, expect, it } from "vitest";
import { attachPlotInteractions, isRepeatClick } from "./interact";

describe("isRepeatClick", () => {
  it("is false with no previous click", () => expect(isRepeatClick(null, 100, 10)).toBe(false));
  it("is true within time and distance", () => expect(isRepeatClick({ t: 100, x: 10 }, 300, 13)).toBe(true));
  it("is false when too late", () => expect(isRepeatClick({ t: 100, x: 10 }, 600, 10)).toBe(false));
  it("is false when too far", () => expect(isRepeatClick({ t: 100, x: 10 }, 200, 30)).toBe(false));
});

describe("attachPlotInteractions gestures", () => {
  type Handler = (e: Partial<PointerEvent>) => void;
  function setup() {
    const handlers: Record<string, Handler> = {};
    const el = {
      addEventListener: (t: string, h: Handler) => (handlers[t] = h),
      removeEventListener: () => {},
      setPointerCapture: () => {},
      getBoundingClientRect: () => ({ left: 0, top: 0, width: 1000, height: 100 }),
    } as unknown as HTMLElement;
    const calls = { select: [] as [number, number][], pan: [] as number[], tune: [] as number[] };
    attachPlotInteractions(el, {
      getView: () => ({ loHz: 7_000_000, hiHz: 7_100_000 }),
      getLimit: () => ({ loHz: 7_000_000, hiHz: 7_100_000 }),
      setView: () => {},
      panBy: (d) => calls.pan.push(d),
      tune: (hz) => calls.tune.push(hz),
      tuneBy: () => {},
      resetView: () => {},
      select: (a, b) => calls.select.push([a, b]),
    });
    const ev = (clientX: number, shiftKey = false) => ({ button: 0, pointerId: 1, clientX, clientY: 50, shiftKey });
    return { handlers, calls, ev };
  }

  it("Shift+drag selects while dragging and on release, without panning or tuning", () => {
    const { handlers: h, calls, ev } = setup();
    h.pointerdown(ev(100, true));
    h.pointermove(ev(300, true));
    h.pointerup(ev(400, true));
    expect(calls.select).toEqual([[7_010_000, 7_030_000], [7_010_000, 7_040_000]]);
    expect(calls.pan).toEqual([]);
    expect(calls.tune).toEqual([]);
  });

  it("a plain drag still pans and a plain click still tunes", () => {
    const { handlers: h, calls, ev } = setup();
    h.pointerdown(ev(500));
    h.pointermove(ev(400));
    h.pointerup(ev(400));
    expect(calls.pan).toEqual([10_000]);
    h.pointerdown(ev(250));
    h.pointerup(ev(250));
    expect(calls.tune).toEqual([7_025_000]);
    expect(calls.select).toEqual([]);
  });

  it("Shift+click reports an empty selection, which clears it", () => {
    const { handlers: h, calls, ev } = setup();
    h.pointerdown(ev(200, true));
    h.pointerup(ev(200, true));
    expect(calls.select).toEqual([[7_020_000, 7_020_000]]);
  });
});
