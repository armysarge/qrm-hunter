<script lang="ts">
  import { app, tuneBand } from "../lib/store.svelte";
  import { GROUPS, groupLabel } from "../lib/bands";
  import type { BandGroup } from "../lib/types";

  const activeBand = $derived.by(() => {
    const r = app.radio;
    return r ? app.bands.find((b) => r.tune_hz >= b.lo_hz && r.tune_hz <= b.hi_hz) : undefined;
  });
  const active = $derived(activeBand?.name);

  // The chip row shows one group at a time; it follows the tuned band unless the user picks another.
  let picked = $state<BandGroup | null>(null);
  let lastActive: BandGroup | undefined;
  $effect(() => {
    const g = activeBand?.group;
    if (g && g !== lastActive) picked = null;
    lastActive = g;
  });
  const group = $derived<BandGroup>(picked ?? activeBand?.group ?? "HF");
  const shown = $derived(app.bands.filter((b) => (b.group ?? "HF") === group));
</script>

<nav class="bands" aria-label="Bands">
  <div class="seg" role="group" aria-label="Band range">
    {#each GROUPS as g (g)}
      <button class:on={group === g} aria-pressed={group === g} onclick={() => (picked = g)}>{groupLabel(g)}</button>
    {/each}
  </div>
  <div class="chips">
    {#each shown as b (b.name)}
      <button class="chip" class:selected={active === b.name} aria-pressed={active === b.name} aria-label={b.name} disabled={!app.radio?.running}
        title="{(b.lo_hz / 1e6).toFixed(3)}–{(b.hi_hz / 1e6).toFixed(3)} MHz"
        onclick={() => tuneBand(b)}>
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
  /* M3 segmented button */
  .seg { flex: none; display: flex; border: 1px solid var(--md-outline); border-radius: 16px; overflow: hidden; }
  .seg button {
    height: 32px;
    padding: 0 12px;
    border: 0;
    background: transparent;
    color: var(--md-on-surface);
    font-size: 13px;
    font-weight: 500;
    transition: background-color 200ms var(--md-motion);
  }
  .seg button { white-space: nowrap; }
  .seg button + button { border-left: 1px solid var(--md-outline); }
  .seg button.on { background: var(--md-secondary-container); color: var(--md-on-secondary-container); }
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
