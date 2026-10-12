<script lang="ts">
  import { app } from "../lib/store.svelte";
  import { analyseRecording } from "../lib/analysis-actions";
  import { playRecording, refreshRecordings, renameRec, revealRec, trashRec } from "../lib/recordings-actions";
  import {
    formatWhen, matchesRecording, MAX_NAME, nameError, recordingMeta, thumbUrl, truncationNote,
  } from "../lib/recordings";
  import type { RecordingInfo } from "../lib/types";

  let query = $state("");
  let menuFor = $state<string | null>(null);
  let confirming = $state<RecordingInfo | null>(null);
  let renaming = $state<RecordingInfo | null>(null);
  let newName = $state("");
  let renameErr = $state<string | null>(null);
  let confirmDialog = $state<HTMLDialogElement>();
  let renameDialog = $state<HTMLDialogElement>();

  const shown = $derived(app.recordings.filter((r) => matchesRecording(r, query)));
  const recordingBusy = $derived(app.record.phase === "recording" || app.record.phase === "stopping");
  const close = () => (app.recordingsOpen = false);

  function askDelete(r: RecordingInfo) {
    menuFor = null;
    confirming = r;
    queueMicrotask(() => confirmDialog?.showModal());
  }
  // State is cleared directly: the native close event is only a backstop.
  function closeConfirm() {
    confirming = null;
    confirmDialog?.close();
  }
  function closeRename() {
    renaming = null;
    renameDialog?.close();
  }
  async function confirmDelete() {
    const r = confirming;
    closeConfirm();
    if (r) await trashRec(r.id);
  }
  function askRename(r: RecordingInfo) {
    menuFor = null;
    renaming = r;
    newName = r.name;
    renameErr = null;
    queueMicrotask(() => renameDialog?.showModal());
  }
  async function submitRename(e: SubmitEvent) {
    e.preventDefault();
    const r = renaming;
    if (!r) return;
    renameErr = nameError(newName) ?? (await renameRec(r.id, newName));
    if (renameErr === null) closeRename();
  }
  function onWindowClick(e: MouseEvent) {
    if (menuFor && !(e.target as HTMLElement).closest(".menu, .more")) menuFor = null;
  }
  // Closing the drawer itself on Esc is App's job (it knows about regions and dialogs); here only the row menu.
  function onWindowKey(e: KeyboardEvent) {
    if (e.key === "Escape" && menuFor && !confirming && !renaming) menuFor = null;
  }
</script>

<svelte:window onclick={onWindowClick} onkeydown={onWindowKey} />

<button class="scrim" aria-label="Close recordings" tabindex="-1" onclick={close}></button>
<aside class="drawer" aria-label="Recordings">
  <header>
    <h2>Recordings</h2>
    <button class="icon" aria-label="Close recordings" title="Close" onclick={close}>
      <span class="material-symbols-rounded" aria-hidden="true">close</span>
    </button>
  </header>

  <label class="search">
    <span class="material-symbols-rounded" aria-hidden="true">search</span>
    <input type="text" bind:value={query} placeholder="Search name, cause, band or frequency" aria-label="Search recordings" />
  </label>

  <div class="list">
    {#each shown as r (r.id)}
      <div class="row" class:menu-open={menuFor === r.id}>
        <img class="thumb" src={thumbUrl(r)} alt="" loading="lazy" width="96" height="56" />
        <div class="recall">
          <span class="name" title={r.name}>{r.name}</span>
          <span class="note label-sm num">{formatWhen(r.created)}</span>
          <span class="note label-sm num">{recordingMeta(r)}</span>
          <span class="meta">
            {#if r.top_cause}<span class="chip label-sm">{r.top_cause}</span>{/if}
            {#if truncationNote(r)}<span class="note label-sm warn">{truncationNote(r)}</span>{/if}
          </span>
        </div>
        <button class="icon" aria-label="Play {r.name}" disabled={recordingBusy}
          title={recordingBusy ? "Stop the recording first: playing would end it" : "Play this recording"} onclick={() => playRecording(r)}>
          <span class="material-symbols-rounded" aria-hidden="true">play_arrow</span>
        </button>
        <button class="icon" title="Analyse this recording" aria-label="Analyse {r.name}"
          disabled={app.analysis.state === "running"} onclick={() => analyseRecording(r.id, r.name)}>
          <span class="material-symbols-rounded" aria-hidden="true">troubleshoot</span>
        </button>
        <button class="icon more" aria-label="More actions for {r.name}" aria-haspopup="menu" aria-expanded={menuFor === r.id}
          onclick={() => (menuFor = menuFor === r.id ? null : r.id)}>
          <span class="material-symbols-rounded" aria-hidden="true">more_vert</span>
        </button>
        {#if menuFor === r.id}
          <div class="menu" role="menu">
            <button role="menuitem" onclick={() => askRename(r)}>
              <span class="material-symbols-rounded" aria-hidden="true">edit</span>Rename
            </button>
            {#if app.appInfo?.desktop}
              <button role="menuitem" onclick={() => { menuFor = null; void revealRec(r.id); }}>
                <span class="material-symbols-rounded" aria-hidden="true">folder_open</span>Open folder
              </button>
            {/if}
            <button role="menuitem" onclick={() => askDelete(r)}>
              <span class="material-symbols-rounded" aria-hidden="true">delete</span>Move to recycle bin
            </button>
          </div>
        {/if}
      </div>
    {:else}
      <p class="empty">{app.recordings.length ? "No recordings match." : "No recordings yet. Use Save last N s or Start recording."}</p>
    {/each}
  </div>

  <footer>
    <p class="status label-sm" title={app.appInfo?.recordings_dir}>{app.appInfo?.recordings_dir ?? ""}</p>
    <div class="buttons">
      <button class="text" onclick={refreshRecordings}>
        <span class="material-symbols-rounded" aria-hidden="true">refresh</span>Refresh
      </button>
    </div>
  </footer>
</aside>

{#if confirming}
  <dialog bind:this={confirmDialog} class="confirm" aria-labelledby="rec-confirm-title" onclose={() => (confirming = null)} oncancel={() => (confirming = null)}>
    <h2 id="rec-confirm-title">Move to the recycle bin?</h2>
    <p>“{confirming.name}” will be moved to the recycle bin, where you can still restore it.</p>
    <div class="actions">
      <button class="text" onclick={closeConfirm}>Cancel</button>
      <button class="text" onclick={confirmDelete}>Move</button>
    </div>
  </dialog>
{/if}

{#if renaming}
  <dialog bind:this={renameDialog} class="confirm" aria-labelledby="rec-rename-title" onclose={() => (renaming = null)} oncancel={() => (renaming = null)}>
    <form onsubmit={submitRename}>
      <h2 id="rec-rename-title">Rename recording</h2>
      <label class="field rename">
        <span class="lbl label-sm">Name</span>
        <input type="text" bind:value={newName} maxlength={MAX_NAME} required aria-label="Recording name" />
      </label>
      {#if renameErr}<p class="status label-sm" role="alert">{renameErr}</p>{/if}
      <div class="actions">
        <button type="button" class="text" onclick={closeRename}>Cancel</button>
        <button type="submit" class="text">Rename</button>
      </div>
    </form>
  </dialog>
{/if}

<style>
  /* M3 navigation drawer on the right. Wide: a standard drawer beside the plots (App places it in
     the grid). Narrow (< 760 px): modal, over a scrim. */
  .drawer {
    display: flex;
    flex-direction: column;
    width: 320px;
    min-height: 0;
    margin: 8px 8px 8px 0;
    border-radius: var(--md-shape-lg);
    background: var(--md-surface-container-low);
    overflow: hidden;
  }
  .scrim { display: none; }
  @media (max-width: 759px) {
    .scrim {
      display: block;
      position: fixed;
      inset: 0;
      z-index: 20;
      width: auto;
      height: auto;
      padding: 0;
      border-radius: 0;
      background: rgb(0 0 0 / 0.4);
    }
    .scrim::after { display: none; }
    .drawer {
      position: fixed;
      top: 0;
      right: 0;
      bottom: 0;
      z-index: 21;
      width: min(360px, calc(100vw - 56px));
      margin: 0;
      border-radius: var(--md-shape-lg) 0 0 var(--md-shape-lg);
      background: var(--md-surface-container-low);
      box-shadow: var(--md-elev-3);
      animation: slide-in 250ms var(--md-motion) both;
    }
  }
  @keyframes slide-in { from { transform: translateX(100%); } to { transform: none; } }

  header { display: flex; align-items: center; justify-content: space-between; padding: 12px 8px 4px 20px; }
  h2 { margin: 0; font-size: 16px; line-height: 24px; font-weight: 500; }
  .search {
    display: flex;
    align-items: center;
    gap: 8px;
    height: 48px;
    margin: 4px 12px 8px;
    padding: 0 16px;
    border-radius: var(--md-shape-full);
    background: var(--md-surface-container-high);
    color: var(--md-on-surface-variant);
  }
  .search input, .search input:focus { flex: 1; min-width: 0; height: 100%; padding: 0; border: none; background: transparent; }
  .list { flex: 1; min-height: 0; overflow-y: auto; padding: 0 12px 8px; }
  .empty { margin: 24px 16px; color: var(--md-on-surface-variant); }

  .row { position: relative; display: flex; align-items: center; border-radius: var(--md-shape-lg); }
  .row:hover, .row.menu-open { background: color-mix(in srgb, var(--md-on-surface) 6%, transparent); }
  .recall {
    flex: 1;
    min-width: 0;
    height: auto;
    min-height: 56px;
    padding: 8px 4px 8px 16px;
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    justify-items: start;
    align-items: center;
    gap: 2px 8px;
    border-radius: var(--md-shape-lg);
    background: transparent;
    color: var(--md-on-surface);
    text-align: left;
  }
  .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%; font-size: 14px; }
  .meta { display: flex; align-items: center; gap: 8px; justify-self: end; }
  .chip {
    padding: 0 8px;
    line-height: 20px;
    border-radius: var(--md-shape-sm);
    background: var(--md-secondary-container);
    color: var(--md-on-secondary-container);
  }
  .note {
    grid-column: 1 / -1;
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-weight: 400;
    font-size: 12px;
    line-height: 16px;
    color: var(--md-on-surface-variant);
  }
  .more { flex: none; }

  /* M3 menu */
  .menu {
    position: absolute;
    top: calc(100% - 8px);
    right: 8px;
    z-index: 5;
    display: flex;
    flex-direction: column;
    min-width: 168px;
    padding: 8px 0;
    border-radius: var(--md-shape-xs);
    background: var(--md-surface-container);
    box-shadow: var(--md-elev-3);
  }
  .menu button {
    justify-content: flex-start;
    height: 48px;
    padding: 0 12px;
    gap: 12px;
    border-radius: 0;
    background: transparent;
    color: var(--md-on-surface);
    font-weight: 400;
  }
  .menu .material-symbols-rounded { font-size: 22px; color: var(--md-on-surface-variant); }

  footer { padding: 4px 8px 8px; border-top: 1px solid var(--md-outline-variant); }
  .buttons { display: flex; justify-content: space-between; flex-wrap: wrap; }
  .status { margin: 6px 12px 0; color: var(--md-on-surface-variant); overflow-wrap: anywhere; }

  .confirm {
    width: min(320px, calc(100vw - 32px));
    padding: 24px;
    border: none;
    border-radius: 28px;
    background: var(--md-surface-container-high);
    color: var(--md-on-surface);
    box-shadow: var(--md-elev-3);
  }
  .confirm::backdrop { background: rgb(0 0 0 / 0.4); }
  .confirm h2 { font-size: 24px; line-height: 32px; font-weight: 400; }
  .confirm p { margin: 16px 0 24px; color: var(--md-on-surface-variant); }
  .actions { display: flex; justify-content: flex-end; gap: 8px; }
  .thumb {
    flex: none; width: 96px; height: 56px; margin: 8px 0 8px 8px; object-fit: cover;
    border-radius: var(--md-shape-sm); background: var(--md-surface-container-lowest);
  }
  .recall { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; cursor: default; }
  .warn { color: var(--md-error); }
  .rename { display: flex; width: 100%; margin: 16px 0 8px; }
  .rename input { width: 100%; }
  @media (min-width: 760px) { .drawer { width: 440px; } }
</style>
