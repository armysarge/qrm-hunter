<script lang="ts">
  import { app } from "../lib/store.svelte";
  import { formatClock, nowPlaying } from "../lib/rds";

  const rds = $derived(app.rds);
  const playing = $derived(nowPlaying(rds));
  const clock = $derived(formatClock(rds?.ct ?? null));
  const synced = $derived(!!rds?.synced);
  const meta = $derived([rds?.pi ? `PI ${rds.pi}` : "", rds?.pty_name ?? ""].filter(Boolean).join(" · "));
  const errPct = $derived(rds ? Math.round(rds.block_error_rate * 100) : 100);
</script>

<!-- M3 filled card: a strip above the spectrum while the radio is in broadcast FM. -->
<section class="rds" aria-label="RDS">
  <div class="station">
    <span class="material-symbols-rounded icon" aria-hidden="true">radio</span>
    <div class="names">
      <span class="ps num" class:dim={!rds?.ps}>{rds?.ps?.trim() || (synced ? "—" : "No RDS")}</span>
      <span class="meta label-sm num">{meta}</span>
    </div>
  </div>

  <div class="now" title={playing ?? ""}>
    {#if playing}
      <span class="material-symbols-rounded" aria-hidden="true">music_note</span>
      <span class="text">{playing}</span>
    {:else}
      <span class="text dim">{synced ? "No RadioText" : "Listening for RDS…"}</span>
    {/if}
  </div>

  {#if clock}
    <span class="clock label-sm num" title={rds?.ct ?? ""}>
      <span class="material-symbols-rounded" aria-hidden="true">schedule</span>{clock}
    </span>
  {/if}

  <div class="chips" role="group" aria-label="RDS flags">
    <span class="chip" class:on={rds?.stereo} title={rds?.stereo ? "Stereo pilot present" : "Mono (no stereo pilot)"}>ST</span>
    <span class="chip" class:on={rds?.tp} title="Traffic programme">TP</span>
    <span class="chip ta" class:on={rds?.ta} class:pulse={!!(rds?.ta && rds?.tp)} title="Traffic announcement">TA</span>
    <span class="sync" class:on={synced} role="img"
      aria-label={synced ? `RDS synchronised, ${errPct}% block errors` : "RDS not synchronised"}
      title={synced ? `RDS synchronised · ${errPct}% block errors` : "RDS not synchronised"}>
      <span class="material-symbols-rounded" aria-hidden="true">{synced ? "link" : "link_off"}</span>
    </span>
  </div>
</section>

<style>
  .rds {
    display: flex;
    align-items: center;
    gap: 8px 20px;
    min-height: 64px;
    margin: 8px 8px 0;
    padding: 8px 12px 8px 16px;
    border-radius: var(--md-shape-md);
    background: var(--md-surface-container-highest);
    color: var(--md-on-surface);
    min-width: 0;
  }
  .station { display: flex; align-items: center; gap: 12px; flex: none; min-width: 0; }
  .icon { font-size: 28px; color: var(--md-primary); }
  .names { display: flex; flex-direction: column; min-width: 0; }
  .ps {
    font-size: 24px;
    line-height: 30px;
    font-weight: 500;
    letter-spacing: 0.04em;
    white-space: pre;
  }
  .meta { color: var(--md-on-surface-variant); white-space: nowrap; }
  .dim { color: var(--md-on-surface-variant); opacity: 0.7; }

  .now {
    flex: 1;
    display: flex;
    align-items: center;
    gap: 6px;
    min-width: 0;
    font-size: 15px;
  }
  .now .material-symbols-rounded { flex: none; font-size: 18px; color: var(--md-on-surface-variant); }
  .now .text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  .clock { display: inline-flex; align-items: center; gap: 4px; flex: none; color: var(--md-on-surface-variant); }
  .clock .material-symbols-rounded { font-size: 16px; }

  .chips { display: flex; align-items: center; gap: 6px; flex: none; }
  /* Small assist-chip style indicators: outlined when off, tonal when on. */
  .chip {
    display: inline-flex;
    align-items: center;
    height: 24px;
    padding: 0 8px;
    border-radius: var(--md-shape-sm);
    border: 1px solid var(--md-outline-variant);
    color: var(--md-on-surface-variant);
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.06em;
    opacity: 0.55;
    transition: background-color 200ms var(--md-motion), color 200ms var(--md-motion), opacity 200ms var(--md-motion);
  }
  .chip.on {
    opacity: 1;
    border-color: transparent;
    background: var(--md-secondary-container);
    color: var(--md-on-secondary-container);
  }
  .chip.ta.on { background: var(--md-error-container); color: var(--md-on-error-container); }
  .chip.pulse { animation: ta-pulse 1.2s var(--md-motion) infinite; }
  @keyframes ta-pulse {
    0%, 100% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--md-error) 60%, transparent); }
    50% { box-shadow: 0 0 0 5px color-mix(in srgb, var(--md-error) 0%, transparent); }
  }
  .sync {
    display: inline-flex;
    width: 28px;
    height: 28px;
    align-items: center;
    justify-content: center;
    color: var(--md-on-surface-variant);
    opacity: 0.4;
    transition: opacity 200ms var(--md-motion), color 200ms var(--md-motion);
  }
  .sync.on { opacity: 1; color: var(--md-success); }
  .sync .material-symbols-rounded { font-size: 18px; }

  /* Narrow: station and flags on the first line, now playing (and clock) below. */
  @media (max-width: 760px) {
    .rds { flex-wrap: wrap; padding: 8px 8px 8px 12px; gap: 4px 12px; }
    .station { flex: 1; }
    .ps { font-size: 20px; line-height: 26px; }
    .icon { font-size: 24px; }
    .chips { order: 0; }
    .now { order: 1; flex-basis: 100%; flex: 1 1 100%; font-size: 14px; }
    .clock { order: 2; }
    .now:has(+ .clock) { flex-basis: 60%; }
  }
  @media (prefers-reduced-motion: reduce) {
    .chip.pulse { animation: none; }
    .chip, .sync { transition: none; }
  }
</style>
