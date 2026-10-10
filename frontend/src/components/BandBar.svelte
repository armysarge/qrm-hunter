<script lang="ts">
  import { app, setRadio } from "../lib/store.svelte";

  const active = $derived.by(() => {
    const r = app.radio;
    return r ? app.bands.find((b) => r.tune_hz >= b.lo_hz && r.tune_hz <= b.hi_hz)?.name : undefined;
  });
</script>

<nav class="bands" aria-label="Amateur bands">
  <span class="label label-sm">Band</span>
  <div class="chips">
    {#each app.bands as b (b.name)}
      <button class="chip" class:selected={active === b.name} aria-pressed={active === b.name} aria-label={b.name} disabled={!app.radio?.running}
        title="{(b.lo_hz / 1e6).toFixed(3)}–{(b.hi_hz / 1e6).toFixed(3)} MHz"
        onclick={() => setRadio({ tune_hz: b.default_hz })}>
        {#if active === b.name}<span class="material-symbols-rounded" aria-hidden="true">check</span>{/if}{b.name}
      </button>
    {/each}
  </div>
</nav>

<style>
  .bands {
    display: flex;
    align-items: center;
    gap: 12px;
    min-width: 0;
    padding: 4px 16px 8px;
    background: var(--md-surface-container);
  }
  .label { flex: none; color: var(--md-on-surface-variant); text-transform: uppercase; }
  /* The chip row is the only thing allowed to scroll horizontally. */
  .chips {
    display: flex;
    gap: 8px;
    min-width: 0;
    overflow-x: auto;
    scrollbar-width: thin;
    padding: 2px 0;
  }
  /* M3 filter chip */
  .chip {
    flex: none;
    height: 32px;
    padding: 0 16px;
    gap: 8px;
    border-radius: var(--md-shape-sm);
    border: 1px solid var(--md-outline-variant);
    background: transparent;
    color: var(--md-on-surface-variant);
    font-size: 14px;
    font-weight: 500;
    transition: background-color 200ms var(--md-motion), border-color 200ms var(--md-motion),
      color 200ms var(--md-motion), padding 200ms var(--md-motion);
  }
  .chip.selected {
    padding-left: 8px;
    border-color: transparent;
    background: var(--md-secondary-container);
    color: var(--md-on-secondary-container);
  }
  .chip .material-symbols-rounded { font-size: 18px; }
</style>
