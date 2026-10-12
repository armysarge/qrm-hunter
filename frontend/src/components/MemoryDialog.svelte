<script lang="ts">
  import { onMount } from "svelte";
  import { app, editMemory, saveMemory } from "../lib/store.svelte";
  import { formatHz } from "../lib/freq";
  import { defaultName, memoryFromRadio } from "../lib/memories";

  // Mounted only while app.memoryDialog is set: saves the current tune, or edits an existing memory.
  const editing = app.memoryDialog?.memory ?? null;
  const r = app.radio;
  let name = $state(editing?.name ?? (r ? defaultName(r.tune_hz, r.mode) : ""));
  let note = $state(editing?.note ?? "");
  let busy = $state(false);
  let error = $state<string | null>(null);
  let dialog: HTMLDialogElement;
  let nameInput: HTMLInputElement;
  const tune = editing?.tune_hz ?? r?.tune_hz ?? 0;
  const mode = editing?.mode ?? r?.mode ?? "";
  const valid = $derived(name.trim().length > 0 && name.trim().length <= 60 && note.length <= 500);

  onMount(() => {
    dialog.showModal();
    nameInput.select();
  });

  // State is cleared directly: the native close event is only a backstop.
  function closeDialog() {
    app.memoryDialog = null;
    if (dialog.open) dialog.close();
  }

  async function submit(e: SubmitEvent) {
    e.preventDefault();
    if (!valid || busy) return;
    busy = true;
    error = editing
      ? await editMemory(editing.id, { name: name.trim(), note })
      : r ? await saveMemory(memoryFromRadio(r, name.trim(), note)) : "Radio state not loaded yet";
    busy = false;
    if (error === null) closeDialog();
  }
</script>

<dialog bind:this={dialog} class="dialog" aria-labelledby="memory-dialog-title" onclose={() => (app.memoryDialog = null)} oncancel={() => (app.memoryDialog = null)}>
  <form onsubmit={submit}>
    <span class="material-symbols-rounded hero-icon" aria-hidden="true">{editing ? "edit" : "star"}</span>
    <h2 id="memory-dialog-title">{editing ? "Edit memory" : "Save memory"}</h2>
    <p class="sub num">{formatHz(tune)} Hz · {mode}</p>
    <label class="field">
      <span class="lbl label-sm">Name</span>
      <input type="text" bind:this={nameInput} bind:value={name} maxlength="60" required aria-label="Memory name" />
    </label>
    <label class="field">
      <span class="lbl label-sm">Note</span>
      <textarea bind:value={note} maxlength="500" rows="3" aria-label="Memory note" placeholder="Optional"></textarea>
    </label>
    {#if error}
      <p class="error" role="alert">
        <span class="material-symbols-rounded" aria-hidden="true">error</span>{error}
      </p>
    {/if}
    <div class="actions">
      <button type="button" class="text" onclick={closeDialog}>Cancel</button>
      <button type="submit" class="text" disabled={!valid || busy}>{editing ? "Save changes" : "Save"}</button>
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
  .field input, textarea { width: 100%; }
  textarea {
    min-height: 72px;
    padding: 10px 12px;
    resize: vertical;
    border-radius: var(--md-shape-xs);
    border: 1px solid var(--md-outline);
    background: transparent;
    color: var(--md-on-surface);
    font: inherit;
  }
  textarea:focus { outline: none; border: 2px solid var(--md-primary); padding: 9px 11px; }
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
  .error .material-symbols-rounded { font-size: 16px; }
  .actions { display: flex; justify-content: flex-end; gap: 8px; }
</style>
