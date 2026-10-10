<script lang="ts">
  import { app, applyAutoRange, saveSettings, setRadio } from "../lib/store.svelte";
  import type { ColormapName } from "../lib/types";

  const FFT_SIZES = [2048, 4096, 8192, 16384, 32768, 65536];
  const r = $derived(app.radio);
  const binHz = $derived(r ? r.sample_rate / r.fft_size : 0);
  const light = $derived(app.settings?.theme === "light");
</script>

<footer class="status label-sm">
  <div class="readouts num">
    <span class="dot" class:ok={app.connected} role="img" aria-label={app.connected ? "Connected to engine" : "Disconnected"}
      title={app.connected ? "Connected to engine" : "Disconnected"}></span>
    <span>{r ? `${(r.sample_rate / 1e6).toFixed(3)} MS/s` : "–"}</span>
    <span>{binHz ? `${binHz.toFixed(0)} Hz/bin` : ""}</span>
    <span>{app.stats.frame_rate.toFixed(0)} fps</span>
    <span class:warn={app.stats.dropped > 0} title="Sample blocks dropped because processing fell behind">drops {app.stats.dropped}</span>
    <span class:warn={app.stats.overflows > 0} title="Device-side USB overflows">ovf {app.stats.overflows}</span>
  </div>

  <div class="controls">
    <label class="ctl">FFT
      <select aria-label="FFT size" value={r?.fft_size} onchange={(e) => setRadio({ fft_size: Number(e.currentTarget.value) })}>
        {#each FFT_SIZES as n (n)}<option value={n}>{n}</option>{/each}
      </select>
    </label>
    <label class="ctl">Avg
      <input type="range" aria-label="Spectrum averaging" min="0.05" max="1" step="0.05" value={r?.avg_alpha ?? 0.3}
        onchange={(e) => setRadio({ avg_alpha: Number(e.currentTarget.value) })} />
    </label>
    <label class="ctl">dB
      <input class="db num" type="number" step="5" value={r?.db_min ?? -120} aria-label="Display floor (dB)"
        onchange={(e) => setRadio({ db_min: Number(e.currentTarget.value) })} />
      <span aria-hidden="true">–</span>
      <input class="db num" type="number" step="5" value={r?.db_max ?? -20} aria-label="Display ceiling (dB)"
        onchange={(e) => setRadio({ db_max: Number(e.currentTarget.value) })} />
    </label>
    <button class="icon dense-icon" aria-label="Auto range" title="Auto range: fit the dB scale to the noise floor"
      disabled={!r?.running} onclick={applyAutoRange}>
      <span class="material-symbols-rounded" aria-hidden="true">auto_fix_high</span>
    </button>
    <label class="ctl">Colours
      <select aria-label="Waterfall colours" value={app.settings?.colormap ?? "turbo"} onchange={(e) => saveSettings({ colormap: e.currentTarget.value as ColormapName })}>
        <option value="turbo">Turbo</option><option value="viridis">Viridis</option><option value="grey">Grey</option>
      </select>
    </label>
    <label class="toggle-chip dense"><input type="checkbox" bind:checked={app.peakHold} />
      <span class="material-symbols-rounded" aria-hidden="true">trending_up</span>Peak hold</label>
    <button class="icon theme" aria-label={light ? "Switch to dark theme" : "Switch to light theme"}
      title={light ? "Dark theme" : "Light theme"}
      onclick={() => saveSettings({ theme: light ? "dark" : "light" })}>
      <span class="material-symbols-rounded" aria-hidden="true">{light ? "dark_mode" : "light_mode"}</span>
    </button>
  </div>
</footer>

<style>
  /* Slim bottom bar. Wraps (rather than scrolling) when the window is narrow. */
  .status {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 4px 24px;
    min-height: 40px;
    padding: 4px 8px 4px 16px;
    color: var(--md-on-surface-variant);
    background: var(--md-surface-container);
  }
  .readouts, .controls { display: flex; align-items: center; flex-wrap: wrap; gap: 4px 16px; min-width: 0; }
  .readouts span { white-space: nowrap; }
  .controls { gap: 4px 12px; }
  .dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--md-error);
    transition: background-color 200ms var(--md-motion);
  }
  .dot.ok { background: var(--md-success); }
  .warn { color: var(--md-primary); }
  .ctl { display: flex; align-items: center; gap: 6px; white-space: nowrap; }

  /* Dense outlined fields */
  .ctl select, .ctl input[type="number"] { height: 28px; padding: 0 8px; font-size: 12px; }
  .ctl select:focus, .ctl input[type="number"]:focus { padding: 0 7px; }
  .ctl input[type="range"] { width: 80px; }
  .db { width: 64px; }

  .toggle-chip.dense { height: 28px; font-size: 12px; padding: 0 12px 0 8px; gap: 6px; }
  .theme, .dense-icon { width: 32px; height: 32px; }
  .dense-icon .material-symbols-rounded { font-size: 18px; }
  .theme .material-symbols-rounded { font-size: 18px; }
</style>
