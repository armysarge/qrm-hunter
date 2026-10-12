<script lang="ts">
  import { onMount, untrack } from "svelte";
  import TopBar from "./components/TopBar.svelte";
  import BandBar from "./components/BandBar.svelte";
  import ErrorBanner from "./components/ErrorBanner.svelte";
  import Spectrum from "./components/Spectrum.svelte";
  import Waterfall from "./components/Waterfall.svelte";
  import Splitter from "./components/Splitter.svelte";
  import StatusBar from "./components/StatusBar.svelte";
  import MemoriesDrawer from "./components/MemoriesDrawer.svelte";
  import MemoryDialog from "./components/MemoryDialog.svelte";
  import AnalysisPanel from "./components/AnalysisPanel.svelte";
  import RecordingsDrawer from "./components/RecordingsDrawer.svelte";
  import SettingsDialog from "./components/SettingsDialog.svelte";
  import RdsCard from "./components/RdsCard.svelte";
  import TaSnackbar from "./components/TaSnackbar.svelte";
  import {
    app, fullSpan, noteFrame, noteRds, reconcileAudio, recallMemory, refreshDevices, refreshMemories, retune, isRetuneTune, tuneRadio,
    viewLimit,
  } from "./lib/store.svelte";
  import { memoryKey } from "./lib/memories";
  import { getBandplan, getRds, getSettings, getState } from "./lib/api";
  import { ReconnectingSocket } from "./lib/ws";
  import { parseSpectrumFrame } from "./lib/protocol";
  import { frameBus } from "./lib/framebus";
  import { clampView, snapHz } from "./lib/freq";
  import { clampSplit, loadSplit } from "./lib/split";
  import { closeAnalysis, saveAnalysedClip } from "./lib/analysis-actions";
  import { refreshAppInfo, refreshRecordings } from "./lib/recordings-actions";
  import { onRecordEvent, syncRecordStatus } from "./lib/record-actions";
  import { clampSeconds } from "./lib/recording";
  import { keepRegion } from "./lib/region";
  import type { ServerEvent } from "./lib/types";

  let statusH = $state(40);
  // The stored preference is never shrunk by a small window: only the rendered share is clamped.
  let split = $state(loadSplit());
  let cardEl = $state<HTMLElement>();
  let cardH = $state(0);
  const shownSplit = $derived(clampSplit(split, cardH - 8));

  const events = new ReconnectingSocket("/ws/events", {
    onStatus: (ok) => (app.connected = ok),
    // Memory events sent while disconnected were missed: refetch on every (re)connect.
    // A recording that started or ended while disconnected is picked up from its status.
    onOpen: () => {
      void refreshMemories();
      void refreshRecordings();
      void syncRecordStatus();
    },
    onMessage: (ev) => {
      const msg = JSON.parse(ev.data as string) as ServerEvent;
      if (msg.type === "state") app.radio = msg.state;
      else if (msg.type === "error") app.error = msg.message;
      else if (msg.type === "stats") app.stats = msg.stats;
      else if (msg.type === "rds") noteRds(msg.rds);
      else if (msg.type === "recording" || msg.type === "recording_stopped") onRecordEvent(msg);
      else if (msg.type === "recordings") void refreshRecordings(); // the folder changed
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
        const [settings, plan, state, rds] = await Promise.all([getSettings(), getBandplan(), getState(), getRds()]);
        app.settings = settings;
        app.recordSeconds = clampSeconds(10, settings.prebuffer_s);
        app.bands = plan.bands;
        app.radio = state;
        if (rds && !app.rds) noteRds(rds); // the events socket may already have sent a newer one
        await Promise.all([refreshDevices(), refreshMemories()]);
        void refreshAppInfo();
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

  // Keep the visible range inside the SDR span; jump to full span only when a retune moves the
  // tuned frequency out of view. Other state updates (averaging, gain, colours...) leave the zoom alone.
  // While a pan-driven centre change is pending the view may run ahead of the span, and a tuned
  // frequency that follows the new centre is not a reason to drop the zoom.
  let lastTune: number | null = null;
  $effect(() => {
    const r = app.radio;
    if (!r) return;
    const limit = fullSpan(r);
    const tune = r.tune_hz;
    untrack(() => {
      const cur = app.view;
      const following = retune.active;
      let next = cur.hiHz > cur.loHz ? clampView(cur, following ? viewLimit(r) : limit) : limit;
      const retuned = tune !== lastTune;
      lastTune = tune;
      const ours = following && isRetuneTune(tune); // the tuned frequency the pan retune itself asked for
      if (retuned && !ours && (tune < next.loHz || tune > next.hiHz)) next = limit;
      if (next.loHz !== cur.loHz || next.hiHz !== cur.hiHz) app.view = next;
      const kept = keepRegion(app.region, limit);
      if (kept?.loHz !== app.region?.loHz || kept?.hiHz !== app.region?.hiHz) app.region = kept;
      if (!kept && app.spanChoice === "selection") app.spanChoice = "visible";
    });
  });

  // A queued pan retune does not outlive the radio session or the device it was planned for.
  const sessionKey = $derived(`${app.radio?.running}|${app.radio?.device_id}|${app.selectedDevice}`);
  $effect(() => {
    void sessionKey;
    untrack(() => retune.cancel());
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
    if (document.querySelector("dialog[open]")) return; // a modal (save/edit, delete confirm) is open
    if (e.key === "Escape" && app.region && !document.querySelector("[role=menu]")) {
      app.region = null;
      app.spanChoice = "visible";
      return;
    }
    if (e.key === "Escape" && !app.region && !document.querySelector("[role=menu]")) {
      // One right-hand panel is open at a time; Esc closes whichever it is.
      if (app.analysisOpen || app.recordingsOpen || app.memoriesOpen) {
        if (app.analysisOpen) closeAnalysis();
        else if (app.recordingsOpen) app.recordingsOpen = false;
        else app.memoriesOpen = false;
        return;
      }
    }
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
      void tuneRadio(snapHz(r.tune_hz + dir * app.stepHz, app.stepHz));
    }
  }
</script>

<svelte:window onkeydown={onKey} />

<div class="shell" style:--status-h="{statusH}px">
  <div class="full"><TopBar /></div>
  <div class="full"><BandBar /></div>
  <div class="full">{#if app.radio?.running && app.radio.mode === "WFM"}<RdsCard />{/if}</div>
  <main class="card" bind:this={cardEl} bind:clientHeight={cardH} style:--split="{shownSplit}fr" style:--rest="{100 - shownSplit}fr">
    <section class="spectrum" aria-label="Spectrum"><Spectrum /></section>
    <Splitter value={shownSplit} container={cardEl} onchange={(v) => (split = v)} />
    <section class="waterfall" aria-label="Waterfall"><Waterfall /></section>
  </main>
  {#if app.analysisOpen}
    <AnalysisPanel ui={app.analysis} onclose={closeAnalysis} onsave={saveAnalysedClip} />
  {:else if app.recordingsOpen}
    <RecordingsDrawer />
  {:else if app.memoriesOpen}
    <MemoriesDrawer />
  {/if}
  <div class="statusbar full" bind:clientHeight={statusH}><StatusBar /></div>
  <ErrorBanner />
  <TaSnackbar />
  {#if app.memoryDialog}<MemoryDialog />{/if}
  {#if app.settingsOpen}<SettingsDialog />{/if}
</div>

<style>
  .shell {
    display: grid;
    grid-template-rows: auto auto auto minmax(0, 1fr) auto;
    grid-template-columns: minmax(0, 1fr) auto;
    height: 100vh;
    background: var(--md-surface);
  }
  /* Spectrum and waterfall share one card; the plots themselves are never animated. */
  .card {
    display: grid;
    grid-template-rows: minmax(0, var(--split)) 8px minmax(0, var(--rest));
    min-height: 0;
    margin: 8px;
    border-radius: var(--md-shape-lg);
    background: var(--md-surface-container-low);
    overflow: hidden;
  }
  .full { grid-column: 1 / -1; min-width: 0; }
  .spectrum, .waterfall { position: relative; min-height: 0; }
</style>
