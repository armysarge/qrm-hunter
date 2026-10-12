<script lang="ts">
  import {
    confidencePct, confidenceTone, featureRows, HUNT_PLACEHOLDER, rankerNote, sourceLabel, type AnalysisUi,
  } from "../lib/analysis";
  import { formatElapsed } from "../lib/recording";

  let { ui, onclose, onsave }: { ui: AnalysisUi; onclose: () => void; onsave: () => void } = $props();

  let now = $state(Date.now());
  // Tick only while an analysis runs.
  $effect(() => {
    if (ui.state !== "running") return;
    now = Date.now();
    const timer = setInterval(() => (now = Date.now()), 500);
    return () => clearInterval(timer);
  });
</script>

<aside class="panel" aria-label="Analysis">
  <header>
    <h2>Analysis</h2>
    <button class="icon" aria-label="Close analysis" title="Close" onclick={onclose}>
      <span class="material-symbols-rounded" aria-hidden="true">close</span>
    </button>
  </header>

  <div class="body">
    {#if ui.state === "running"}
      <div class="progress" role="progressbar" aria-label="Analysing"></div>
      <p role="status">{ui.label}…</p>
      {#if ui.startedAt}<p class="label-sm muted num">{formatElapsed((now - ui.startedAt) / 1000)}</p>{/if}
    {:else if ui.state === "error"}
      <p class="error" role="alert">
        <span class="material-symbols-rounded" aria-hidden="true">error</span>{ui.message}
      </p>
    {:else if ui.state === "done"}
      {@const r = ui.result}
      {@const note = rankerNote(r)}
      <p class="label-sm muted num">{sourceLabel(r)} · {(r.timing_ms.total / 1000).toFixed(1)} s to analyse</p>
      {#if note}<p class="label-sm muted">{note}</p>{/if}
      <ol class="causes">
        {#each r.causes as c (c.id)}
          <li>
            <div class="head">
              <span class="name">{c.name}</span>
              {#if c.beta}<span class="chip label-sm" title="The beta model contributed to this score">beta</span>{/if}
              <span class="pct num">{confidencePct(c.score)}%</span>
            </div>
            <div class="bar" aria-hidden="true"><span class={confidenceTone(c.score)} style:width="{confidencePct(c.score)}%"></span></div>
            {#if c.evidence.length}
              <details>
                <summary class="label-sm">Evidence</summary>
                <ul>{#each c.evidence as e, i (i)}<li>{e}</li>{/each}</ul>
              </details>
            {/if}
            <button class="text hunt" disabled title={HUNT_PLACEHOLDER}>
              <span class="material-symbols-rounded" aria-hidden="true">travel_explore</span>How to find it
            </button>
          </li>
        {/each}
      </ol>
      <details class="raw">
        <summary class="label-sm">Raw features</summary>
        <table class="num label-sm">
          <tbody>{#each featureRows(r.features) as row (row.id)}<tr><th>{row.id}</th><td>{row.value}</td></tr>{/each}</tbody>
        </table>
        <p class="label-sm muted">Engine {r.versions.engine} · features v{r.versions.features} · model {r.versions.model}</p>
      </details>
    {:else}
      <p class="muted">Press Analyse to identify the interference in the visible span or the selected region.</p>
    {/if}
  </div>

  {#if ui.state === "done" && ui.result.source.kind === "live"}
    <footer>
      {#if ui.saved}
        <span class="label-sm muted"><span class="material-symbols-rounded" aria-hidden="true">check</span>Saved</span>
      {:else}
        <button class="text" disabled={ui.saving} onclick={onsave}>
          <span class="material-symbols-rounded" aria-hidden="true">save</span>Save as recording
        </button>
      {/if}
    </footer>
  {/if}
</aside>

<style>
  .panel {
    display: flex; flex-direction: column; width: 360px; min-height: 0; margin: 8px 8px 8px 0;
    border-radius: var(--md-shape-lg); background: var(--md-surface-container-low); overflow: hidden;
  }
  header { display: flex; align-items: center; justify-content: space-between; padding: 8px 8px 8px 20px; }
  h2 { margin: 0; font-size: 22px; line-height: 28px; font-weight: 400; }
  .body { flex: 1; min-height: 0; overflow-y: auto; padding: 0 20px 16px; }
  .muted { color: var(--md-on-surface-variant); }
  .error { display: flex; gap: 8px; color: var(--md-error); overflow-wrap: anywhere; }
  .progress { height: 4px; border-radius: 2px; background: var(--md-surface-container-highest); position: relative; overflow: hidden; }
  .progress::after { content: ""; position: absolute; inset: 0 60% 0 0; background: var(--md-primary);
    animation: slide 1.4s var(--md-motion) infinite; }
  @keyframes slide { from { transform: translateX(-100%); } to { transform: translateX(250%); } }
  @media (prefers-reduced-motion: reduce) { .progress::after { animation: none; inset: 0; opacity: 0.5; } }
  .causes { list-style: none; margin: 8px 0; padding: 0; display: flex; flex-direction: column; gap: 16px; }
  .head { display: flex; align-items: center; gap: 8px; }
  .name { flex: 1; font-weight: 500; }
  .chip { padding: 0 8px; border-radius: var(--md-shape-sm); border: 1px solid var(--md-outline); color: var(--md-on-surface-variant); }
  .bar { height: 8px; margin: 6px 0; border-radius: 4px; background: var(--md-surface-container-highest); overflow: hidden; }
  .bar span { display: block; height: 100%; border-radius: 4px; }
  .bar .high { background: var(--md-primary); }
  .bar .mid { background: var(--md-tertiary); }
  .bar .low { background: var(--md-outline); }
  details ul { margin: 4px 0; padding-left: 20px; }
  .hunt { height: 32px; padding: 0 8px; }
  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; font-weight: 400; color: var(--md-on-surface-variant); }
  td { text-align: right; }
  footer { display: flex; justify-content: flex-end; padding: 8px 12px; border-top: 1px solid var(--md-outline-variant); }
  @media (max-width: 759px) {
    .panel {
      position: fixed; left: 0; right: 0; bottom: 0; z-index: 21; width: auto; max-height: 70vh; margin: 0;
      border-radius: var(--md-shape-lg) var(--md-shape-lg) 0 0; box-shadow: var(--md-elev-3);
      animation: rise 250ms var(--md-motion) both;
    }
    @keyframes rise { from { transform: translateY(100%); } }
  }
</style>
