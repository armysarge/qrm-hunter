<script lang="ts">
  import FrequencyDisplay from "./FrequencyDisplay.svelte";
  import { app, player, refreshDevices, setRadio, startStop, toggleAudio } from "../lib/store.svelte";
  import { BW_OPTIONS, STEP_OPTIONS } from "../lib/freq";
  import type { DirectSampling, Mode } from "../lib/types";

  const MODES: Mode[] = ["AM", "USB", "LSB", "CW"];
  const running = $derived(!!app.radio?.running);
  const device = $derived(app.devices.find((d) => d.id === (running ? app.radio?.device_id : app.selectedDevice)));
  const agc = $derived(app.radio?.gain_db === null);
  const fmtBw = (bw: number) => (bw >= 1000 ? `${bw / 1000} kHz` : `${bw} Hz`);
  const fmtStep = (s: number) => (s >= 1000 ? `${s / 1000} kHz` : `${s} Hz`);
</script>

<header class="top">
  <div class="brand">
    <span class="material-symbols-rounded mark" aria-hidden="true">radar</span>
    <span class="title">QRM Hunter</span>
  </div>

  <div class="group device">
    <label class="field">
      <span class="lbl label-sm">Device</span>
      <select bind:value={app.selectedDevice} disabled={running} aria-label="SDR device">
        {#each app.devices as d (d.id)}<option value={d.id}>{d.label}</option>{/each}
      </select>
    </label>
    <button class="icon" onclick={refreshDevices} disabled={running} title="Rescan for devices" aria-label="Rescan devices">
      <span class="material-symbols-rounded" aria-hidden="true">refresh</span>
    </button>
    <button class="filled go" onclick={startStop} disabled={!app.selectedDevice}>
      <span class="material-symbols-rounded" aria-hidden="true">{running ? "stop" : "play_arrow"}</span>
      {running ? "Stop" : "Start"}
    </button>
  </div>

  <div class="hero">
    <FrequencyDisplay hz={app.radio?.tune_hz ?? 0} disabled={!running} onchange={(hz) => setRadio({ tune_hz: hz })} />
  </div>

  <div class="tools">
    <div class="seg" role="group" aria-label="Demodulation mode">
      {#each MODES as m (m)}
        <button class:on={app.radio?.mode === m} aria-pressed={app.radio?.mode === m} disabled={!running}
          onclick={() => setRadio({ mode: m })}>
          {#if app.radio?.mode === m}<span class="material-symbols-rounded" aria-hidden="true">check</span>{/if}{m}
        </button>
      {/each}
    </div>

    <label class="field">
      <span class="lbl label-sm">Bandwidth</span>
      <select class="narrow" value={app.radio?.bw_hz} disabled={!running} onchange={(e) => setRadio({ bw_hz: Number(e.currentTarget.value) })}>
        {#each BW_OPTIONS[app.radio?.mode ?? "AM"] as bw (bw)}<option value={bw}>{fmtBw(bw)}</option>{/each}
      </select>
    </label>

    <label class="field">
      <span class="lbl label-sm">Step</span>
      <select class="narrow" bind:value={app.stepHz}>
        {#each STEP_OPTIONS as s (s)}<option value={s}>{fmtStep(s)}</option>{/each}
      </select>
    </label>

    <div class="group gain">
      <label class="toggle-chip" title="Automatic gain control">
        <input type="checkbox" checked={agc} disabled={!running}
          onchange={(e) => setRadio({ gain_db: e.currentTarget.checked ? null : ((device?.gain_range[1] ?? 40) / 2) })} />
        <span class="material-symbols-rounded" aria-hidden="true">auto_mode</span>AGC
      </label>
      <input type="range" min={device?.gain_range[0] ?? 0} max={device?.gain_range[1] ?? 50} step="0.5"
        value={app.radio?.gain_db ?? 0} disabled={!running || agc} aria-label="RF gain"
        oninput={(e) => setRadio({ gain_db: Number(e.currentTarget.value) })} />
      <span class="num gain-val label-sm">{agc ? "auto" : `${app.radio?.gain_db?.toFixed(1)} dB`}</span>
    </div>

    {#if device?.has_direct_sampling}
      <label class="field" title="RTL-SDR v3: use Direct Q below 24 MHz. RTL-SDR v4: leave on Tuner.">
        <span class="lbl label-sm">HF input</span>
        <select class="narrow" value={app.radio?.direct_sampling} disabled={!running}
          onchange={(e) => setRadio({ direct_sampling: e.currentTarget.value as DirectSampling })}>
          <option value="off">Tuner</option><option value="q">Direct Q</option><option value="i">Direct I</option>
        </select>
      </label>
    {/if}
  </div>

  <div class="group audio">
    <button class="listen" class:on={app.audioOn} aria-pressed={app.audioOn} disabled={!running} onclick={toggleAudio}>
      <span class="material-symbols-rounded" aria-hidden="true">{app.audioOn ? "volume_off" : "volume_up"}</span>
      {app.audioOn ? "Mute" : "Listen"}
    </button>
    <input class="vol" type="range" min="0" max="1" step="0.01" bind:value={app.volume} aria-label="Volume"
      oninput={() => player.setGain(app.volume)} />
  </div>
</header>

<style>
  /* Wide: one 64 px app bar. Medium: the app bar row plus a tool row. Narrow: frequency first. */
  .top {
    --field-bg: var(--md-surface-container);
    display: grid;
    grid-template-columns: auto auto minmax(0, 1fr) auto;
    grid-template-areas:
      "brand device hero audio"
      "tools tools tools tools";
    align-items: center;
    gap: 4px 16px;
    padding: 6px 16px 4px;
    background: var(--md-surface-container);
  }
  .brand, .group, .tools { display: flex; align-items: center; min-width: 0; }
  .brand { grid-area: brand; }
  .device { grid-area: device; }
  .hero { grid-area: hero; display: flex; justify-content: center; min-width: 0; }
  .tools { grid-area: tools; }
  .audio { grid-area: audio; }

  @media (min-width: 1900px) {
    .top {
      grid-template-columns: auto auto auto minmax(0, 1fr) auto;
      grid-template-areas: "brand device hero tools audio";
      min-height: 64px;
      padding: 6px 16px;
    }
    .tools { justify-content: center; }
  }
  @media (max-width: 1099px) {
    .top {
      grid-template-columns: auto minmax(0, 1fr) auto;
      grid-template-areas:
        "hero device audio"
        "tools tools tools";
    }
    .brand { display: none; }
    .hero { justify-content: flex-start; }
    .device { justify-self: end; }
  }
  @media (max-width: 760px) {
    .top { display: flex; flex-wrap: wrap; }
  }

  .brand { gap: 8px; height: 52px; padding-right: 4px; }
  .mark { font-size: 26px; color: var(--md-primary); }
  .title { font-size: 18px; font-weight: 500; letter-spacing: 0; white-space: nowrap; color: var(--md-on-surface); }
  .group { gap: 8px; }
  .tools { flex-wrap: wrap; gap: 8px 16px; min-height: 52px; padding-top: 4px; }

  .device select { width: 240px; }
  select.narrow { width: 110px; }
  .go { min-width: 104px; padding: 0 20px 0 16px; }
  .go .material-symbols-rounded, .listen .material-symbols-rounded { font-size: 20px; }

  /* M3 segmented button */
  .seg {
    display: inline-flex;
    height: 40px;
    border: 1px solid var(--md-outline);
    border-radius: var(--md-shape-full);
    overflow: hidden;
    flex: none;
  }
  .seg button {
    height: 38px;
    min-width: 56px;
    padding: 0 14px;
    gap: 6px;
    border-radius: 0;
    background: transparent;
    color: var(--md-on-surface);
    font-size: 14px;
    transition: background-color 200ms var(--md-motion), color 200ms var(--md-motion);
  }
  .seg button + button { border-left: 1px solid var(--md-outline); }
  .seg button.on { background: var(--md-secondary-container); color: var(--md-on-secondary-container); }
  .seg .material-symbols-rounded { font-size: 18px; }

  .gain { gap: 10px; }
  .gain input[type="range"] { width: 96px; }
  .gain-val { min-width: 6ch; color: var(--md-on-surface-variant); }

  .listen { min-width: 112px; padding: 0 20px 0 16px; }
  .listen.on { background: var(--md-primary-container); color: var(--md-on-primary-container); }
  .vol { width: 84px; }
</style>
