<script lang="ts">
  import { onMount } from "svelte";
  import { app, plotOptions } from "../lib/store.svelte";
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

  function colors() {
    const cs = getComputedStyle(document.documentElement);
    const v = (n: string) => cs.getPropertyValue(n).trim();
    return { bg: v("--panel"), grid: v("--grid"), text: v("--muted"), accent: v("--accent"),
      accentSoft: v("--accent-soft"), trace: v("--trace"), band: v("--band"), line: v("--line") };
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

  onMount(() => {
    const ctx = canvas.getContext("2d")!;
    const ro = new ResizeObserver(() => {
      const dpr = devicePixelRatio || 1;
      canvas.width = Math.max(16, Math.round(wrap.clientWidth * dpr));
      canvas.height = Math.max(16, Math.round(wrap.clientHeight * dpr));
      peak = null;
      dirty = true;
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
    const detach = attachPlotInteractions(canvas, plotOptions((x, y) => {
      cursor = x === null || y === null ? null : { x, y };
      dirty = true;
    }));
    let raf = 0;
    const loop = () => {
      if (dirty) {
        draw(ctx);
        dirty = false;
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => {
      cancelAnimationFrame(raf);
      off();
      detach();
      ro.disconnect();
    };
  });

  $effect(() => {
    // redraw when anything drawn from reactive state changes
    void app.view.loHz; void app.view.hiHz; void app.bands.length; void app.settings?.theme;
    void app.radio?.tune_hz; void app.radio?.bw_hz; void app.radio?.mode;
    if (!app.peakHold) peak = null;
    dirty = true;
  });
</script>

<div class="wrap" bind:this={wrap}>
  <canvas bind:this={canvas} aria-label="Spectrum: click to tune, wheel to zoom, drag to pan"></canvas>
</div>

<style>
  .wrap { position: absolute; inset: 0; overflow: hidden; }
  canvas { display: block; width: 100%; height: 100%; cursor: crosshair; touch-action: none; }
</style>
