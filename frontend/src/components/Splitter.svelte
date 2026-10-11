<script lang="ts">
  import { onDestroy } from "svelte";
  import { clampSplit, pxToSplit, saveSplit, splitBounds, splitForKey, SPLIT_DEFAULT } from "../lib/split";

  /** `value` is the top pane's share (percent) of `container`'s height minus the handle; `thickness` is the
   *  handle height in px. `onchange` reports user changes; only those are written to storage. */
  let { value, container, thickness = 8, onchange }: {
    value: number; container: HTMLElement | undefined; thickness?: number; onchange(pct: number): void;
  } = $props();

  let dragging = $state(false);
  let raf = 0;
  let queued: number | null = null;
  let moved = false;

  let height = $state(0);
  $effect(() => {
    if (!container) return;
    const el = container;
    const measure = () => (height = el.clientHeight);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  });
  const available = () => Math.max(0, (container?.clientHeight ?? 0) - thickness);
  const bounds = $derived(splitBounds(Math.max(0, height - thickness)));

  // Pointer moves arrive faster than frames; apply only the latest once per frame.
  function flush() {
    raf = 0;
    if (queued !== null) onchange(queued);
    queued = null;
  }
  function commit(next: number) {
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    queued = null;
    const v = clampSplit(next, available());
    onchange(v);
    saveSplit(v);
  }

  function onDown(e: PointerEvent) {
    if (e.button !== 0 || !container) return;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    dragging = true;
    moved = false;
    e.preventDefault(); // no text selection while dragging; this also stops the default focus, so focus here
    (e.currentTarget as HTMLElement).focus();
  }
  function onMove(e: PointerEvent) {
    if (!dragging || !container) return;
    const top = container.getBoundingClientRect().top;
    moved = true;
    queued = pxToSplit(e.clientY - top - thickness / 2, available());
    raf ||= requestAnimationFrame(flush);
  }
  function end() {
    if (!dragging) return;
    dragging = false;
    if (moved) commit(queued ?? value); // a plain click must not overwrite the stored preference
  }
  function onKey(e: KeyboardEvent) {
    if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    const next = splitForKey(e.key, value, available());
    if (next === null) return;
    e.preventDefault();
    commit(next);
  }

  onDestroy(() => {
    if (raf) cancelAnimationFrame(raf);
  });
</script>

<!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
<div
  class="splitter"
  class:dragging
  role="separator"
  aria-orientation="horizontal"
  aria-label="Resize spectrum and waterfall"
  aria-valuenow={Math.min(Math.max(Math.round(value), Math.ceil(bounds.min)), Math.floor(bounds.max))}
  aria-valuetext="Spectrum {Math.round(value)} %"
  aria-valuemin={Math.ceil(bounds.min)}
  aria-valuemax={Math.floor(bounds.max)}
  tabindex="0"
  onpointerdown={onDown}
  onpointermove={onMove}
  onpointerup={end}
  onpointercancel={end}
  onlostpointercapture={end}
  onkeydown={onKey}
  ondblclick={() => commit(SPLIT_DEFAULT)}
>
  <span class="grip" aria-hidden="true"></span>
</div>

<style>
  .splitter {
    position: relative;
    height: 8px;
    cursor: row-resize;
    touch-action: none;
    user-select: none;
    outline: none;
    background: var(--md-surface-container-low);
    z-index: 1;
  }
  /* the 1 px visible line */
  .splitter::before {
    content: "";
    position: absolute;
    left: 0; right: 0; top: 50%;
    height: 1px;
    margin-top: -0.5px;
    background: var(--md-outline-variant);
  }
  .grip {
    position: absolute;
    left: 50%; top: 50%;
    width: 32px; height: 4px;
    transform: translate(-50%, -50%);
    border-radius: 2px;
    background: var(--md-outline);
    opacity: 0.6;
  }
  .splitter:hover .grip,
  .splitter.dragging .grip { opacity: 1; background: var(--md-primary); }
  .splitter:focus-visible { box-shadow: inset 0 0 0 2px var(--md-primary); }
</style>
