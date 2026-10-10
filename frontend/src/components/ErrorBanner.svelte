<script lang="ts">
  import { app, startStop } from "../lib/store.svelte";
</script>

{#if app.error}
  <div class="snackbar" role="alert">
    <span class="msg">{app.error}</span>
    <div class="actions">
      {#if app.radio && !app.radio.running && app.selectedDevice}
        <button class="text" onclick={startStop}>Reconnect</button>
      {/if}
      <button class="text close" aria-label="Dismiss" onclick={() => (app.error = null)}>
        <span class="material-symbols-rounded" aria-hidden="true">close</span>
      </button>
    </div>
  </div>
{:else if !app.connected}
  <div class="pill label-sm" role="status">
    <span class="spinner" aria-hidden="true"></span>Connecting to the QRM Hunter engine…
  </div>
{/if}

<style>
  /* M3 snackbar, bottom-centre, 24 px above the status bar (App sets --status-h). */
  .snackbar, .pill {
    position: fixed;
    left: 50%;
    bottom: calc(var(--status-h, 40px) + 24px);
    z-index: 10;
    transform: translateX(-50%);
    animation: snack-in 200ms var(--md-motion) both;
  }
  .snackbar {
    display: flex;
    align-items: center;
    gap: 8px;
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
  }
  .msg { flex: 1; padding: 10px 0; overflow-wrap: anywhere; }
  .actions { display: flex; align-items: center; flex: none; }
  .text { color: var(--md-inverse-primary); }
  .close { width: 40px; padding: 0; }
  .pill {
    display: flex;
    align-items: center;
    gap: 10px;
    height: 32px;
    padding: 0 16px 0 12px;
    border-radius: var(--md-shape-full);
    background: var(--md-surface-container-highest);
    color: var(--md-on-surface-variant);
    box-shadow: var(--md-elev-1);
    white-space: nowrap;
  }
  .spinner {
    width: 14px;
    height: 14px;
    border-radius: 50%;
    border: 2px solid var(--md-primary);
    border-right-color: transparent;
    animation: spin 900ms linear infinite;
  }
  @keyframes snack-in {
    from { opacity: 0; transform: translate(-50%, 16px); }
    to { opacity: 1; transform: translate(-50%, 0); }
  }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
