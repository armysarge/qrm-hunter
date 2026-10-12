<script lang="ts">
  import { onMount } from "svelte";
  import { app, saveSettings } from "../lib/store.svelte";
  import { refreshAppInfo, refreshRecordings } from "../lib/recordings-actions";
  import { formFromSettings, memoryNote, validateSettings, type SettingsForm } from "../lib/settings-form";

  let dialog: HTMLDialogElement;
  let form = $state<SettingsForm>(app.settings ? formFromSettings(app.settings) : { prebuffer: "10", folder: "", callsign: "" });
  let busy = $state(false);
  let saveError = $state<string | null>(null);
  const checked = $derived(validateSettings(form));
  const rate = $derived(app.radio?.sample_rate ?? 2_048_000);
  const seconds = $derived(checked.patch.prebuffer_s ?? 0);

  onMount(() => dialog.showModal());

  // State is cleared directly: the native close event is only a backstop.
  function closeDialog() {
    app.settingsOpen = false;
    if (dialog.open) dialog.close();
  }

  async function submit(e: SubmitEvent) {
    e.preventDefault();
    if (busy || Object.keys(checked.errors).length) return;
    busy = true;
    saveError = null;
    // Only the fields that differ are sent, so saving here cannot touch any other setting.
    const current = app.settings;
    const patch = Object.fromEntries(
      Object.entries(checked.patch).filter(([k, v]) => !current || current[k as keyof typeof current] !== v),
    );
    if (Object.keys(patch).length) saveError = await saveSettings(patch);
    busy = false;
    if (saveError !== null) return;
    closeDialog();
    void refreshAppInfo();
    if (app.recordingsOpen) void refreshRecordings();
  }
</script>

<dialog bind:this={dialog} class="dialog" aria-labelledby="settings-title" onclose={() => (app.settingsOpen = false)} oncancel={() => (app.settingsOpen = false)}>
  <form onsubmit={submit}>
    <span class="material-symbols-rounded hero-icon" aria-hidden="true">settings</span>
    <h2 id="settings-title">Settings</h2>
    <label class="field">
      <span class="lbl label-sm">Pre-buffer seconds</span>
      <input type="text" inputmode="numeric" bind:value={form.prebuffer} aria-label="Pre-buffer seconds" />
    </label>
    <p class="sub label-sm">{checked.errors.prebuffer ?? `${memoryNote(seconds, rate)}. Applies at the next Start.`}</p>
    <label class="field">
      <span class="lbl label-sm">Recordings folder</span>
      <input type="text" bind:value={form.folder} placeholder={app.appInfo?.default_recordings_dir ?? "Documents/QRM Hunter/recordings"}
        aria-label="Recordings folder" />
    </label>
    <p class="sub label-sm">{checked.errors.folder ?? "Leave empty for the default folder."}</p>
    <label class="field">
      <span class="lbl label-sm">Callsign</span>
      <input type="text" bind:value={form.callsign} maxlength="12" placeholder="Optional" aria-label="Callsign" />
    </label>
    <p class="sub label-sm">{checked.errors.callsign ?? "Stored in each recording's SigMF metadata."}</p>
    {#if app.appInfo}
      <p class="sub label-sm">
        QRM Hunter {app.appInfo.version} · classifier model: {app.appInfo.ranker === "ok" ? "beta" : `off (${app.appInfo.ranker})`}
      </p>
      {#if app.appInfo.signatures_warning}<p class="error" role="alert">{app.appInfo.signatures_warning}</p>{/if}
    {/if}
    {#if saveError}<p class="error" role="alert">{saveError}</p>{/if}
    <div class="actions">
      <button type="button" class="text" onclick={closeDialog}>Cancel</button>
      <button type="submit" class="text" disabled={busy || Object.keys(checked.errors).length > 0}>Save</button>
    </div>
  </form>
</dialog>

<style>
  /* M3 basic dialog */
  .dialog {
    --field-bg: var(--md-surface-container-high);
    width: min(400px, calc(100vw - 32px));
    padding: 24px;
    border: none;
    border-radius: 28px;
    background: var(--md-surface-container-high);
    color: var(--md-on-surface);
    box-shadow: var(--md-elev-3);
  }
  .dialog::backdrop { background: rgb(0 0 0 / 0.4); }
  form { display: flex; flex-direction: column; gap: 16px; }
  .hero-icon { align-self: center; font-size: 24px; color: var(--md-secondary); }
  h2 { margin: 0; text-align: center; font-size: 24px; line-height: 32px; font-weight: 400; }
  .sub { margin: -8px 0 4px; text-align: center; color: var(--md-on-surface-variant); }
  .field input { width: 100%; }
  .error {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    margin: -4px 0 0;
    color: var(--md-error);
    font-size: 12px;
    line-height: 16px;
    overflow-wrap: anywhere;
  }
  .actions { display: flex; justify-content: flex-end; gap: 8px; }
  .sub { margin: -12px 0 0; text-align: left; }
</style>
