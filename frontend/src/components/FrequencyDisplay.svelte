<script lang="ts">
  import { formatDigits, parseFreqInput } from "../lib/freq";
  import { focusOnMount, wheel } from "../lib/actions";

  let { hz, disabled = false, onchange }: { hz: number; disabled?: boolean; onchange: (hz: number) => void } = $props();
  let editing = $state(false);
  let text = $state("");
  const cells = $derived(formatDigits(hz));

  function step(e: WheelEvent, power: number) {
    if (disabled) return;
    onchange(Math.max(0, hz + (e.deltaY < 0 ? 1 : -1) * 10 ** power));
  }
  function commit() {
    if (!editing) return;
    editing = false;
    const v = parseFreqInput(text);
    if (v !== null && v > 0) onchange(v);
  }
</script>

{#if editing}
  <input class="edit num" bind:value={text} use:focusOnMount placeholder="kHz, or 7.074M"
    onkeydown={(e) => { if (e.key === "Enter") commit(); if (e.key === "Escape") editing = false; }}
    onblur={commit} aria-label="Type a frequency" />
{:else}
  <button class="freq num" {disabled} title="Scroll a digit to tune · double-click to type a frequency"
    aria-label="Tuned frequency {hz} Hz. Double-click to type a frequency"
    ondblclick={() => { text = ""; editing = true; }}>
    {#each cells as c, i (i)}
      {#if c.sep}<span class="sep" aria-hidden="true">.</span>{:else}<span class="digit" class:lead={c.lead} aria-hidden="true" use:wheel={(e) => step(e, c.power)}>{c.ch}</span>{/if}
    {/each}
    <span class="unit" aria-hidden="true">Hz</span>
  </button>
{/if}

<style>
  /* Hero element: large tabular Roboto Flex digits in primary on a highest-container pill. */
  .freq, .edit {
    height: 52px;
    min-width: 11.5ch;
    border-radius: var(--md-shape-lg);
    background: var(--md-surface-container-highest);
    font-family: var(--sans);
    font-size: 34px;
    font-weight: 500;
    font-variant-numeric: tabular-nums;
    letter-spacing: 0;
  }
  .freq {
    display: inline-flex;
    align-items: baseline;
    justify-content: center;
    gap: 0;
    padding: 0 18px;
    line-height: 52px;
    color: var(--md-primary);
    cursor: ns-resize;
  }
  /* Per-digit state layers replace the whole-button one. */
  .freq::after { display: none; }
  .freq:disabled { opacity: 1; cursor: default; }
  .freq:disabled .digit:not(.lead), .freq:disabled .sep { color: var(--md-on-surface-variant); }
  .digit {
    display: inline-block;
    padding: 0 1px;
    border-radius: var(--md-shape-xs);
    line-height: 44px;
    transition: background-color 150ms var(--md-motion), color 150ms var(--md-motion);
  }
  .freq:not(:disabled) .digit:hover { background: color-mix(in srgb, var(--md-primary) 8%, transparent); }
  .freq:not(:disabled) .digit:active { background: color-mix(in srgb, var(--md-primary) 12%, transparent); }
  .lead { color: var(--md-on-surface-variant); opacity: 0.38; }
  .sep { color: var(--md-on-surface-variant); padding: 0 1px; }
  .unit {
    margin-left: 8px;
    font-size: 14px;
    font-weight: 500;
    letter-spacing: 0.05em;
    color: var(--md-on-surface-variant);
  }
  .edit {
    width: 11.5ch;
    padding: 0 16px;
    font-size: 28px;
    color: var(--md-on-surface);
    border: 2px solid var(--md-primary);
  }
  .edit:focus { padding: 0 16px; }
  .edit::placeholder { font-size: 16px; color: var(--md-on-surface-variant); }
</style>
