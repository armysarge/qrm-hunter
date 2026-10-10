import { panView, xToHz, zoomView, type View } from "./freq";

/** True when a click lands close in time and space to the previous click-tune (second half of a double-click). */
export function isRepeatClick(prev: { t: number; x: number } | null, t: number, x: number, maxMs = 400, maxPx = 6): boolean {
  return prev !== null && t - prev.t <= maxMs && Math.abs(x - prev.x) <= maxPx;
}

export interface PlotInteractionOptions {
  getView(): View;
  getLimit(): View;
  setView(v: View): void;
  tune(hz: number): void;
  tuneBy(deltaHz: number): void;
  resetView(): void;
  getPassband?(): [number, number] | null;
  onCursor?(x: number | null, y: number | null): void;
}

/** Wheel = zoom around cursor, drag = pan (or drag the passband to fine-tune), click = tune,
 *  double-click = full span. Returns a detach function. */
export function attachPlotInteractions(el: HTMLElement, o: PlotInteractionOptions): () => void {
  let downX: number | null = null;
  let lastX = 0;
  let dragging = false;
  let tuningDrag = false;
  let lastTune: { t: number; x: number } | null = null;

  const rect = () => el.getBoundingClientRect();
  const hzAt = (clientX: number) => {
    const r = rect();
    return xToHz(clientX - r.left, o.getView(), r.width);
  };

  const onWheel = (e: WheelEvent) => {
    e.preventDefault();
    if (e.deltaY === 0) return;
    // Normalise lines/pages to pixels, then clamp so trackpads do not zoom wildly.
    const px = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1);
    const mag = Math.min(Math.abs(px) / 100, 1) * 0.25 + 0.05;
    const factor = px > 0 ? 1 + mag : 1 / (1 + mag);
    o.setView(zoomView(o.getView(), hzAt(e.clientX), factor, o.getLimit()));
  };
  const onDown = (e: PointerEvent) => {
    if (e.button !== 0) return;
    el.setPointerCapture(e.pointerId);
    downX = lastX = e.clientX;
    dragging = false;
    const pb = o.getPassband?.();
    const hz = hzAt(e.clientX);
    tuningDrag = !!pb && hz >= pb[0] && hz <= pb[1];
  };
  const onMove = (e: PointerEvent) => {
    const r = rect();
    o.onCursor?.(e.clientX - r.left, e.clientY - r.top);
    if (downX === null) return;
    if (!dragging && Math.abs(e.clientX - downX) > 4) dragging = true;
    if (!dragging) return;
    const v = o.getView();
    const hzPerPx = (v.hiHz - v.loHz) / r.width;
    const dx = e.clientX - lastX;
    lastX = e.clientX;
    if (tuningDrag) o.tuneBy(dx * hzPerPx);
    else o.setView(panView(v, -dx * hzPerPx, o.getLimit()));
  };
  const onUp = (e: PointerEvent) => {
    // PointerEvent.detail is 0 in Chromium, so detect the second click of a double-click by time and position.
    // dblclick then resets the view instead of tuning twice.
    if (downX !== null && !dragging) {
      const t = performance.now();
      if (!isRepeatClick(lastTune, t, e.clientX)) {
        lastTune = { t, x: e.clientX };
        o.tune(hzAt(e.clientX));
      }
    }
    downX = null;
    dragging = false;
  };
  const onCancel = () => {
    downX = null;
    dragging = false;
    tuningDrag = false;
  };
  const onLeave = () => o.onCursor?.(null, null);
  const onDbl = () => o.resetView();

  el.addEventListener("wheel", onWheel, { passive: false });
  el.addEventListener("pointerdown", onDown);
  el.addEventListener("pointermove", onMove);
  el.addEventListener("pointerup", onUp);
  el.addEventListener("pointercancel", onCancel);
  el.addEventListener("lostpointercapture", onCancel);
  el.addEventListener("pointerleave", onLeave);
  el.addEventListener("dblclick", onDbl);
  return () => {
    el.removeEventListener("wheel", onWheel);
    el.removeEventListener("pointerdown", onDown);
    el.removeEventListener("pointermove", onMove);
    el.removeEventListener("pointerup", onUp);
    el.removeEventListener("pointercancel", onCancel);
    el.removeEventListener("lostpointercapture", onCancel);
    el.removeEventListener("pointerleave", onLeave);
    el.removeEventListener("dblclick", onDbl);
  };
}
