<script lang="ts">
  import { onMount, untrack } from "svelte";
  import TopBar from "./components/TopBar.svelte";
  import BandBar from "./components/BandBar.svelte";
  import ErrorBanner from "./components/ErrorBanner.svelte";
  import Spectrum from "./components/Spectrum.svelte";
  import Waterfall from "./components/Waterfall.svelte";
  import StatusBar from "./components/StatusBar.svelte";
  import MemoriesDrawer from "./components/MemoriesDrawer.svelte";
  import MemoryDialog from "./components/MemoryDialog.svelte";
  import {
    app, fullSpan, noteFrame, reconcileAudio, recallMemory, refreshDevices, refreshMemories, setRadio,
  } from "./lib/store.svelte";
  import { memoryKey } from "./lib/memories";
  import { getBandplan, getSettings, getState } from "./lib/api";
  import { ReconnectingSocket } from "./lib/ws";
  import { parseSpectrumFrame } from "./lib/protocol";
  import { frameBus } from "./lib/framebus";
  import { clampView, snapHz } from "./lib/freq";
  import type { ServerEvent } from "./lib/types";

  let statusH = $state(40);

  const events = new ReconnectingSocket("/ws/events", {
    onStatus: (ok) => (app.connected = ok),
    onMessage: (ev) => {
      const msg = JSON.parse(ev.data as string) as ServerEvent;
      if (msg.type === "state") app.radio = msg.state;
      else if (msg.type === "error") app.error = msg.message;
      else if (msg.type === "stats") app.stats = msg.stats;
      else if (msg.type === "memories") void refreshMemories(); // changed here or in another window
    },
  });

  const spectrum = new ReconnectingSocket("/ws/spectrum", {
    onOpen: () => sendView(),
    onMessage: (ev) => {
      try {
        const frame = parseSpectrumFrame(ev.data as ArrayBuffer);
        noteFrame(frame);
        frameBus.emit(frame);
      } catch (e) {
        console.warn(e);
      }
    },
  });

  function sendView() {
    const v = app.view;
    if (v.hiHz > v.loHz) spectrum.send(JSON.stringify({ width: app.plotWidth, f_lo: v.loHz, f_hi: v.hiHz }));
  }

  onMount(() => {
    (async () => {
      try {
        const [settings, plan, state] = await Promise.all([getSettings(), getBandplan(), getState()]);
        app.settings = settings;
        app.bands = plan.bands;
        app.radio = state;
        await Promise.all([refreshDevices(), refreshMemories()]);
        const last = settings.last_device_id;
        if (last && app.devices.some((d) => d.id === last)) app.selectedDevice = last;
      } catch (e) {
        app.error = (e as Error).message;
      }
    })();
    events.start();
    spectrum.start();
    return () => {
      events.stop();
      spectrum.stop();
    };
  });

  // Keep the visible range inside the SDR span; jump to full span if the tuned frequency left the view.
  $effect(() => {
    const r = app.radio;
    if (!r) return;
    const limit = fullSpan(r);
    const tune = r.tune_hz;
    untrack(() => {
      const cur = app.view;
      let next = cur.hiHz > cur.loHz ? clampView(cur, limit) : limit;
      if (tune < next.loHz || tune > next.hiHz) next = limit;
      if (next.loHz !== cur.loHz || next.hiHz !== cur.hiHz) app.view = next;
    });
  });

  $effect(() => {
    void app.view.loHz;
    void app.view.hiHz;
    void app.plotWidth;
    sendView();
  });

  $effect(() => {
    document.documentElement.dataset.theme = app.settings?.theme ?? "dark";
  });

  // If the radio stops server-side (device lost, closed elsewhere), drop local audio too.
  $effect(() => {
    void app.radio?.running;
    void app.audioOn;
    untrack(reconcileAudio);
  });

  // Band plan depends on the region setting
  $effect(() => {
    const region = app.settings?.region;
    if (region) untrack(() => getBandplan().then((p) => (app.bands = p.bands)).catch(() => {}));
  });

  function onKey(e: KeyboardEvent) {
    if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    if ((e.target as HTMLElement).closest("input, select, textarea")) return;
    if (app.memoryDialog) return; // a modal is open
    const mk = memoryKey(e, false);
    if (mk?.kind === "save" && app.radio) {
      e.preventDefault();
      app.memoryDialog = { memory: null };
      return;
    }
    if (mk?.kind === "recall") {
      const m = app.memories[mk.index];
      if (m) {
        e.preventDefault();
        void recallMemory(m);
      }
      return;
    }
    const r = app.radio;
    if (!r?.running) return;
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      e.preventDefault();
      const dir = e.key === "ArrowRight" ? 1 : -1;
      setRadio({ tune_hz: snapHz(r.tune_hz + dir * app.stepHz, app.stepHz) });
    }
  }
</script>

<svelte:window onkeydown={onKey} />

<div class="shell" style:--status-h="{statusH}px">
  <div class="full"><TopBar /></div>
  <div class="full"><BandBar /></div>
  <main class="card">
    <section class="spectrum" aria-label="Spectrum"><Spectrum /></section>
    <section class="waterfall" aria-label="Waterfall"><Waterfall /></section>
  </main>
  {#if app.memoriesOpen}<MemoriesDrawer />{/if}
  <div class="statusbar full" bind:clientHeight={statusH}><StatusBar /></div>
  <ErrorBanner />
  {#if app.memoryDialog}<MemoryDialog />{/if}
</div>

<style>
  .shell {
    display: grid;
    grid-template-rows: auto auto minmax(0, 1fr) auto;
    grid-template-columns: minmax(0, 1fr) auto;
    height: 100vh;
    background: var(--md-surface);
  }
  /* Spectrum and waterfall share one card; the plots themselves are never animated. */
  .card {
    display: grid;
    grid-template-rows: 3fr 7fr;
    min-height: 0;
    margin: 8px;
    border-radius: var(--md-shape-lg);
    background: var(--md-surface-container-low);
    overflow: hidden;
  }
  .full { grid-column: 1 / -1; min-width: 0; }
  .spectrum, .waterfall { position: relative; min-height: 0; }
  .spectrum { border-bottom: 1px solid var(--md-outline-variant); }
</style>
