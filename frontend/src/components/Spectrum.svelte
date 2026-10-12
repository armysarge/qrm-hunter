<script lang="ts">
  import { onMount } from "svelte";
  import { app, plotOptions, recallMemory } from "../lib/store.svelte";
  import { layoutMarkers, markerAt, type Marker } from "../lib/memories";
  import { frameBus } from "../lib/framebus";
  import { formatHz, formatTick, hzToX, niceTicks, passband, resampleToView, xToHz } from "../lib/freq";
  import { attachPlotInteractions } from "../lib/interact";

  const MONO = "ui-monospace, 'Cascadia Mono', Consolas, monospace";
  let canvas: HTMLCanvasElement;
  let wrap: HTMLDivElement;
  let latest: Uint8Array | null = null;
  let peak: Uint8Array | null = null;
  let dbMin = -120;
  let dbMax = -20;
  let cursor: { x: number; y: number } | null = null;
  let dirty = true;
  let ctx: CanvasRenderingContext2D | null = null;
  // Memory markers in CSS px, recomputed only when the view, the memories or the size change.
  let markers: Marker[] = [];
  const MARKER_TOP = 18; // below the band-name row
  const MARKER_H = 16;

  let colorCache: ReturnType<typeof readColors> | null = null;
  function colors() {
    return (colorCache ??= readColors());
  }
  function readColors() {
    const cs = getComputedStyle(document.documentElement);
    const v = (n: string) => cs.getPropertyValue(n).trim();
    return { bg: v("--md-surface-container-low"), grid: v("--grid"), text: v("--muted"), accent: v("--md-primary"),
      accentSoft: v("--accent-soft"), trace: v("--trace"), band: v("--band"), line: v("--line"),
      marker: v("--md-tertiary") };
  }

  const markerFont = (dpr: number) => `${Math.round(10.5 * dpr)}px ${MONO}`;

  function layout() {
    if (!ctx) return;
    const dpr = devicePixelRatio || 1;
    ctx.font = markerFont(dpr);
    const c = ctx;
    markers = layoutMarkers(app.memories, app.view, canvas.width / dpr, (s) => c.measureText(s).width / dpr);
    dirty = true;
  }

  function draw(ctx: CanvasRenderingContext2D) {
    const c = colors();
    const dpr = devicePixelRatio || 1;
    const W = canvas.width, H = canvas.height;
    const axisH = Math.round(18 * dpr);
    const plotH = H - axisH;
    const view = app.view;
    ctx.fillStyle = c.bg;
    ctx.fillRect(0, 0, W, H);
    if (view.hiHz <= view.loHz) return;
    ctx.font = `${Math.round(10.5 * dpr)}px ${MONO}`;

    for (const b of app.bands) {
      if (b.hi_hz < view.loHz || b.lo_hz > view.hiHz) continue;
      const x0 = hzToX(b.lo_hz, view, W), x1 = hzToX(b.hi_hz, view, W);
      ctx.fillStyle = c.band;
      ctx.fillRect(x0, 0, x1 - x0, plotH);
      ctx.fillStyle = c.text;
      ctx.fillText(b.name, Math.max(x0, 0) + 4 * dpr, 12 * dpr);
    }

    const sel = app.region;
    if (sel && sel.hiHz > view.loHz && sel.loHz < view.hiHz) {
      const x0 = hzToX(sel.loHz, view, W), x1 = hzToX(sel.hiHz, view, W);
      ctx.fillStyle = c.marker;
      ctx.globalAlpha = 0.18;
      ctx.fillRect(x0, 0, x1 - x0, plotH);
      ctx.globalAlpha = 1;
      ctx.fillRect(Math.round(x0), 0, Math.max(1, Math.round(dpr)), plotH);
      ctx.fillRect(Math.round(x1), 0, Math.max(1, Math.round(dpr)), plotH);
    }

    ctx.strokeStyle = c.grid;
    ctx.lineWidth = 1;
    ctx.fillStyle = c.text;
    for (let db = Math.ceil(dbMin / 10) * 10; db <= dbMax; db += 10) {
      const y = Math.round(plotH - ((db - dbMin) / (dbMax - dbMin)) * plotH) + 0.5;
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
      ctx.fillText(`${db}`, 4 * dpr, y - 3 * dpr);
    }
    const { ticks, step } = niceTicks(view.loHz, view.hiHz, Math.max(2, Math.floor(W / (110 * dpr))));
    ctx.textAlign = "center";
    for (const t of ticks) {
      const x = Math.round(hzToX(t, view, W)) + 0.5;
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, plotH); ctx.stroke();
      ctx.fillText(formatTick(t, step), x, H - 5 * dpr);
    }
    ctx.textAlign = "left";
    ctx.strokeStyle = c.line;
    ctx.beginPath(); ctx.moveTo(0, plotH + 0.5); ctx.lineTo(W, plotH + 0.5); ctx.stroke();

    const r = app.radio;
    if (r) {
      const [lo, hi] = passband(r.tune_hz, r.mode, r.bw_hz);
      const x0 = hzToX(lo, view, W), x1 = hzToX(hi, view, W);
      ctx.fillStyle = c.accentSoft;
      ctx.fillRect(x0, 0, Math.max(1, x1 - x0), plotH);
      ctx.fillStyle = c.accent;
      ctx.fillRect(Math.round(hzToX(r.tune_hz, view, W)), 0, Math.max(1, dpr), plotH);
    }

    if (markers.length) {
      ctx.fillStyle = c.marker;
      ctx.font = markerFont(dpr);
      for (const k of markers) {
        const x = Math.round(k.x * dpr);
        ctx.fillRect(x, 0, Math.max(1, Math.round(dpr)), (MARKER_TOP + MARKER_H) * dpr);
        ctx.fillText(k.label, k.labelX * dpr, (MARKER_TOP + 11) * dpr);
      }
    }

    const trace = (data: Uint8Array) => {
      ctx.beginPath();
      for (let x = 0; x < data.length; x++) {
        const y = plotH - (data[x] / 255) * plotH;
        if (x === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
    };
    if (peak) {
      ctx.strokeStyle = c.text;
      ctx.globalAlpha = 0.6;
      trace(peak);
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
    if (latest) {
      trace(latest);
      ctx.lineTo(latest.length - 1, plotH);
      ctx.lineTo(0, plotH);
      ctx.closePath();
      ctx.fillStyle = c.trace;
      ctx.globalAlpha = 0.14;
      ctx.fill();
      ctx.globalAlpha = 1;
      trace(latest);
      ctx.strokeStyle = c.trace;
      ctx.lineWidth = Math.max(1, dpr);
      ctx.stroke();
      ctx.lineWidth = 1;
    }

    if (cursor) {
      const x = cursor.x * dpr, y = cursor.y * dpr;
      if (y <= plotH) {
        ctx.strokeStyle = c.text;
        ctx.globalAlpha = 0.5;
        ctx.beginPath(); ctx.moveTo(x + 0.5, 0); ctx.lineTo(x + 0.5, plotH); ctx.stroke();
        ctx.globalAlpha = 1;
        const hz = xToHz(x, view, W);
        const db = dbMin + (1 - y / plotH) * (dbMax - dbMin);
        const label = `${formatHz(hz)} Hz  ${db.toFixed(1)} dB`;
        ctx.textAlign = "right";
        ctx.fillStyle = c.accent;
        ctx.fillText(label, W - 6 * dpr, 12 * dpr);
        ctx.textAlign = "left";
      }
    }
  }

  // Registered before the plot interactions so a marker click recalls instead of click-tuning.
  function onMarkerDown(e: PointerEvent) {
    if (e.button !== 0 || !markers.length) return;
    const rect = canvas.getBoundingClientRect();
    const y = e.clientY - rect.top;
    if (y > MARKER_TOP + MARKER_H) return;
    const hit = markerAt(markers, e.clientX - rect.left);
    if (!hit) return;
    e.stopImmediatePropagation();
    void recallMemory(hit.memory);
  }

  onMount(() => {
    const ctx2d = canvas.getContext("2d")!;
    ctx = ctx2d;
    const ro = new ResizeObserver(() => {
      const dpr = devicePixelRatio || 1;
      canvas.width = Math.max(16, Math.round(wrap.clientWidth * dpr));
      canvas.height = Math.max(16, Math.round(wrap.clientHeight * dpr));
      peak = null;
      layout();
    });
    ro.observe(wrap);
    const off = frameBus.on((f) => {
      const w = canvas.width;
      latest = resampleToView(f.bins, f.centerHz - f.spanHz / 2, f.centerHz + f.spanHz / 2, app.view, w);
      dbMin = f.dbMin;
      dbMax = f.dbMax;
      if (!app.peakHold) peak = null;
      else if (!peak || peak.length !== w) peak = latest.slice();
      else for (let i = 0; i < w; i++) peak[i] = Math.max(peak[i], latest[i]);
      dirty = true;
    });
    canvas.addEventListener("pointerdown", onMarkerDown);
    const detach = attachPlotInteractions(canvas, plotOptions((x, y) => {
      cursor = x === null || y === null ? null : { x, y };
      dirty = true;
    }));
    let raf = 0;
    const loop = () => {
      if (dirty) {
        draw(ctx2d);
        dirty = false;
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => {
      cancelAnimationFrame(raf);
      canvas.removeEventListener("pointerdown", onMarkerDown);
      ctx = null;
      off();
      detach();
      ro.disconnect();
    };
  });

  $effect(() => {
    // redraw when anything drawn from reactive state changes
    void app.view.loHz; void app.view.hiHz; void app.bands.length;
    void app.settings?.theme;
    colorCache = null;
    void app.radio?.tune_hz; void app.radio?.bw_hz; void app.radio?.mode;
    void app.region;
    if (!app.peakHold) peak = null;
    dirty = true;
  });

  $effect(() => {
    void app.view.loHz; void app.view.hiHz; void app.memories;
    layout();
  });
</script>

<div class="wrap" bind:this={wrap}>
  <canvas bind:this={canvas} aria-label="Spectrum: click to tune, wheel to zoom, drag to pan, Shift+drag to select a region"></canvas>
</div>

<style>
  .wrap { position: absolute; inset: 0; overflow: hidden; }
  canvas { display: block; width: 100%; height: 100%; cursor: crosshair; touch-action: none; }
</style>
