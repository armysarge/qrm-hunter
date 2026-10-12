<script lang="ts">
  import { app } from "../lib/store.svelte";
  import { saveLast, startRecording, stopRecording } from "../lib/record-actions";
  import { clampSeconds, primaryLabel } from "../lib/recording";
  import { describeSpan, targetSpan } from "../lib/region";

  let menuOpen = $state(false);
  const running = $derived(!!app.radio?.running);
  const phase = $derived(app.record.phase);
  const recording = $derived(phase === "recording" || phase === "stopping");
  const busy = $derived(phase === "saving" || phase === "starting" || phase === "stopping");
  const prebuffer = $derived(app.settings?.prebuffer_s ?? 10);
  const span = $derived(targetSpan(app.spanChoice, app.region, app.view));
  const primaryTitle = $derived(recording ? "Stop recording"
    : prebuffer <= 0 ? "The pre-buffer is off; set Pre-buffer seconds in Settings"
    : `Save the last ${app.recordSeconds} s of ${describeSpan(span.lo_hz, span.hi_hz)}`);

  function primary() {
    if (recording) void stopRecording();
    else void saveLast();
  }
  function toggleRecording() {
    menuOpen = false;
    if (recording) void stopRecording();
    else void startRecording();
  }
  function setSeconds(e: Event & { currentTarget: HTMLInputElement }) {
    app.recordSeconds = clampSeconds(Number(e.currentTarget.value), prebuffer);
    // When the clamp leaves the state unchanged nothing re-renders, so show the clamped value here.
    e.currentTarget.value = String(app.recordSeconds);
  }
  function onWindowClick(e: MouseEvent) {
    if (menuOpen && !(e.target as HTMLElement).closest(".record")) menuOpen = false;
  }
  function onWindowKey(e: KeyboardEvent) {
    if (e.key === "Escape" && menuOpen) menuOpen = false;
  }
</script>

<svelte:window onclick={onWindowClick} onkeydown={onWindowKey} />

<div class="record" class:live={recording}>
  <button class="primary" disabled={(!running && !recording) || busy || (!recording && prebuffer <= 0)} title={primaryTitle} onclick={primary}>
    {#if recording}<span class="dot" aria-hidden="true"></span>{:else}<span class="material-symbols-rounded" aria-hidden="true">save</span>{/if}
    <span class="num">{primaryLabel(app.record, app.recordSeconds)}</span>
  </button>
  <button class="arrow" aria-label="Recording options" aria-haspopup="menu" aria-expanded={menuOpen}
    onclick={() => (menuOpen = !menuOpen)}>
    <span class="material-symbols-rounded" aria-hidden="true">arrow_drop_down</span>
  </button>
  {#if menuOpen}
    <div class="menu" role="menu">
      <button role="menuitem" disabled={(!running && !recording) || busy} onclick={toggleRecording}>
        <span class="material-symbols-rounded" aria-hidden="true">{recording ? "stop_circle" : "fiber_manual_record"}</span>
        {recording ? "Stop recording" : "Start recording"}
      </button>
      <fieldset>
        <legend class="label-sm">Span</legend>
        <label><input type="radio" name="record-span" value="visible" bind:group={app.spanChoice} />Visible span</label>
        <label title={app.region ? "" : "Shift+drag on the spectrum or waterfall to select a region"}>
          <input type="radio" name="record-span" value="selection" bind:group={app.spanChoice} disabled={!app.region} />Selection
        </label>
      </fieldset>
      <label class="secs">
        <span class="label-sm">Seconds to save</span>
        <input type="number" min="2" max={Math.max(2, prebuffer)} step="1" value={app.recordSeconds}
          onchange={setSeconds} />
      </label>
      <p class="label-sm hint num">{describeSpan(span.lo_hz, span.hi_hz)}</p>
    </div>
  {/if}
</div>

<style>
  .record { position: relative; display: inline-flex; }
  .record > button {
    height: 40px;
    border: 1px solid var(--md-outline);
    background: transparent;
    color: var(--md-primary);
  }
  .primary { display: inline-flex; align-items: center; gap: 8px; padding: 0 12px 0 16px; border-radius: var(--md-shape-full) 0 0 var(--md-shape-full); }
  .arrow { width: 36px; padding: 0; border-left: none !important; border-radius: 0 var(--md-shape-full) var(--md-shape-full) 0; }
  .live > button { color: var(--md-error); border-color: var(--md-error); }
  .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--md-error); animation: pulse 1.2s var(--md-motion) infinite; }
  @keyframes pulse { 50% { opacity: 0.25; } }
  @media (prefers-reduced-motion: reduce) { .dot { animation: none; } }
  .menu {
    position: absolute;
    top: calc(100% + 4px);
    right: 0;
    z-index: 30;
    display: flex;
    flex-direction: column;
    gap: 8px;
    min-width: 240px;
    padding: 8px 0;
    border-radius: var(--md-shape-xs);
    background: var(--md-surface-container);
    box-shadow: var(--md-elev-3);
  }
  .menu > button { display: flex; align-items: center; gap: 12px; height: 48px; padding: 0 16px; border-radius: 0;
    background: transparent; color: var(--md-on-surface); justify-content: flex-start; }
  .menu > button:hover:not(:disabled) { background: var(--md-surface-container-high); }
  fieldset { display: flex; flex-direction: column; gap: 4px; margin: 0 16px; padding: 0; border: none; }
  fieldset label { display: flex; align-items: center; gap: 8px; }
  .secs { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin: 0 16px; }
  .secs input { width: 72px; }
  .hint { margin: 0 16px; color: var(--md-on-surface-variant); }
</style>
