<script lang="ts">
  import { onMount } from "svelte";
  import { app, plotOptions } from "../lib/store.svelte";
  import { frameBus } from "../lib/framebus";
  import { WaterfallRenderer } from "../lib/waterfall-gl";
  import { buildLut } from "../lib/colormap";
  import { passband, resampleToView } from "../lib/freq";
  import { attachPlotInteractions } from "../lib/interact";

  let canvas: HTMLCanvasElement;
  let wrap: HTMLDivElement;
  let renderer: WaterfallRenderer | null = null;
  let dirty = true;
  let glError = $state<string | null>(null);

  const overlay = $derived.by(() => {
    const r = app.radio;
    const v = app.view;
    if (!r || v.hiHz <= v.loHz) return null;
    const [lo, hi] = passband(r.tune_hz, r.mode, r.bw_hz);
    const span = v.hiHz - v.loHz;
    return { left: ((lo - v.loHz) / span) * 100, width: ((hi - lo) / span) * 100, tune: ((r.tune_hz - v.loHz) / span) * 100 };
  });

  onMount(() => {
    try {
      renderer = new WaterfallRenderer(canvas, buildLut(app.settings?.colormap ?? "turbo"));
    } catch (e) {
      glError = (e as Error).message;
      return;
    }
    const r = renderer;
    let resizeTimer: ReturnType<typeof setTimeout> | undefined;
    const applySize = () => {
      const dpr = devicePixelRatio || 1;
      canvas.width = Math.min(4096, Math.max(16, Math.round(wrap.clientWidth * dpr)));
      canvas.height = Math.max(16, Math.round(wrap.clientHeight * dpr));
      r.resize(canvas.width, canvas.height);
      app.plotWidth = r.width;
      dirty = true;
    };
    applySize();
    // Debounced: recreating the texture wipes the history, so wait for the drag to settle.
    const ro = new ResizeObserver(() => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(applySize, 150);
    });
    ro.observe(wrap);
    const off = frameBus.on((f) => {
      r.pushRow(resampleToView(f.bins, f.centerHz - f.spanHz / 2, f.centerHz + f.spanHz / 2, app.view, r.width));
      dirty = true;
    });
    const detach = attachPlotInteractions(canvas, plotOptions());
    let raf = 0;
    const loop = () => {
      if (dirty) {
        r.draw();
        dirty = false;
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => {
      cancelAnimationFrame(raf);
      clearTimeout(resizeTimer);
      off();
      detach();
      ro.disconnect();
      r.dispose();
      renderer = null;
    };
  });

  $effect(() => {
    const lut = buildLut(app.settings?.colormap ?? "turbo");
    if (renderer) {
      renderer.setColormap(lut);
      dirty = true;
    }
  });
</script>

<div class="wrap" bind:this={wrap}>
  <canvas bind:this={canvas} aria-label="Waterfall: click to tune, wheel to zoom, drag to pan"></canvas>
  {#if overlay}
    <div class="pb" style:left="{overlay.left}%" style:width="{overlay.width}%"></div>
    <div class="tune" style:left="{overlay.tune}%"></div>
  {/if}
  {#if glError}<p class="err">{glError}</p>{/if}
</div>

<style>
  .wrap { position: absolute; inset: 0; overflow: hidden; background: #000; }
  canvas { display: block; width: 100%; height: 100%; cursor: crosshair; touch-action: none; }
  .pb { position: absolute; top: 0; bottom: 0; background: var(--accent-soft); pointer-events: none; }
  .tune { position: absolute; top: 0; bottom: 0; width: 1px; background: var(--accent); opacity: 0.8; pointer-events: none; }
  .err { position: absolute; inset: 0; margin: auto; height: fit-content; text-align: center; color: var(--err); }
</style>
