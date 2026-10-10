<script lang="ts">
  import { app } from "../lib/store.svelte";
  import { taMessage } from "../lib/rds";
</script>

<!-- Shown while a traffic announcement is on air; the engine's TA flag going off dismisses it. -->
{#if app.taSnack}
  <div class="snackbar" class:raised={!!app.error || !app.connected} role="status" aria-live="polite">
    <span class="material-symbols-rounded" aria-hidden="true">traffic</span>
    <span class="msg">{taMessage(app.taSnack)}</span>
    <button class="text close" aria-label="Dismiss" onclick={() => (app.taSnack = null)}>
      <span class="material-symbols-rounded" aria-hidden="true">close</span>
    </button>
  </div>
{/if}

<style>
  /* M3 snackbar, bottom-centre above the status bar; above the error snackbar when one is up. */
  .snackbar {
    position: fixed;
    left: 50%;
    bottom: calc(var(--status-h, 40px) + 24px);
    z-index: 10;
    display: flex;
    align-items: center;
    gap: 12px;
    width: max-content;
    min-width: min(344px, calc(100vw - 32px));
    max-width: min(560px, calc(100vw - 32px));
    min-height: 48px;
    padding: 4px 4px 4px 16px;
    border-radius: var(--md-shape-xs);
    background: var(--md-inverse-surface);
    color: var(--md-inverse-on-surface);
    box-shadow: var(--md-elev-3);
    font-size: 14px;
    line-height: 20px;
    transform: translateX(-50%);
    animation: snack-in 200ms var(--md-motion) both;
  }
  .snackbar.raised { bottom: calc(var(--status-h, 40px) + 88px); }
  .snackbar > .material-symbols-rounded { color: var(--md-inverse-primary); }
  .msg { flex: 1; padding: 10px 0; overflow-wrap: anywhere; }
  .close { width: 40px; padding: 0; color: var(--md-inverse-primary); background: transparent; }
  @keyframes snack-in {
    from { opacity: 0; transform: translate(-50%, 16px); }
    to { opacity: 1; transform: translate(-50%, 0); }
  }
  @media (prefers-reduced-motion: reduce) {
    .snackbar { animation: none; }
  }
</style>
