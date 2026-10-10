<script lang="ts">
  import { app, recallMemory, refreshMemories, removeMemory } from "../lib/store.svelte";
  import { exportMemories, importMemories } from "../lib/api";
  import { formatHz } from "../lib/freq";
  import { groupByBand, importFormat, matchesQuery, truncate } from "../lib/memories";
  import type { ImportFormat, Memory } from "../lib/types";

  let query = $state("");
  let menuFor = $state<string | null>(null);
  let confirming = $state<Memory | null>(null);
  let status = $state("");
  let fileInput: HTMLInputElement;
  let confirmDialog = $state<HTMLDialogElement>();

  const groups = $derived(groupByBand(app.memories.filter((m) => matchesQuery(m, query)), app.bands));
  const close = () => (app.memoriesOpen = false);

  function askDelete(m: Memory) {
    menuFor = null;
    confirming = m;
    queueMicrotask(() => confirmDialog?.showModal());
  }
  async function confirmDelete() {
    const m = confirming;
    confirmDialog?.close();
    if (m) await removeMemory(m.id);
  }
  function edit(m: Memory) {
    menuFor = null;
    app.memoryDialog = { memory: m };
  }

  async function onFile(e: Event & { currentTarget: HTMLInputElement }) {
    const file = e.currentTarget.files?.[0];
    e.currentTarget.value = "";
    if (!file) return;
    const format = importFormat(file.name);
    if (!format) {
      status = "Choose a .json or .csv file";
      return;
    }
    try {
      const res = await importMemories(format, await file.text());
      status = `Imported ${res.added}, skipped ${res.skipped}`;
      await refreshMemories();
    } catch (err) {
      status = (err as Error).message;
    }
  }

  async function download(format: ImportFormat) {
    try {
      const url = URL.createObjectURL(await exportMemories(format));
      const a = Object.assign(document.createElement("a"), { href: url, download: `qrm-hunter-memories.${format}` });
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      status = "";
    } catch (err) {
      status = (err as Error).message;
    }
  }

  function onWindowClick(e: MouseEvent) {
    if (menuFor && !(e.target as HTMLElement).closest(".menu, .more")) menuFor = null;
  }
  function onWindowKey(e: KeyboardEvent) {
    if (e.key !== "Escape" || app.memoryDialog || confirming) return;
    if (menuFor) menuFor = null;
    else close();
  }
</script>

<svelte:window onclick={onWindowClick} onkeydown={onWindowKey} />

<button class="scrim" aria-label="Close memories" tabindex="-1" onclick={close}></button>
<aside class="drawer" aria-label="Memories">
  <header>
    <h2>Memories</h2>
    <button class="icon" aria-label="Close memories" title="Close" onclick={close}>
      <span class="material-symbols-rounded" aria-hidden="true">close</span>
    </button>
  </header>

  <label class="search">
    <span class="material-symbols-rounded" aria-hidden="true">search</span>
    <input type="text" bind:value={query} placeholder="Search name, note or frequency" aria-label="Search memories" />
  </label>

  <div class="list">
    {#each groups as g (g.band)}
      <section>
        <h3 class="label-sm">{g.band}</h3>
        {#each g.items as m (m.id)}
          <div class="row" class:menu-open={menuFor === m.id}>
            <button class="recall" onclick={() => recallMemory(m)} title="Recall {m.name}">
              <span class="name">{m.name}</span>
              <span class="meta">
                <span class="freq num">{formatHz(m.tune_hz)}</span>
                <span class="chip label-sm">{m.mode}</span>
              </span>
              {#if m.note}<span class="note">{truncate(m.note, 80)}</span>{/if}
            </button>
            <button class="icon more" aria-label="More actions for {m.name}" aria-haspopup="menu" aria-expanded={menuFor === m.id}
              onclick={() => (menuFor = menuFor === m.id ? null : m.id)}>
              <span class="material-symbols-rounded" aria-hidden="true">more_vert</span>
            </button>
            {#if menuFor === m.id}
              <div class="menu" role="menu">
                <button role="menuitem" onclick={() => edit(m)}>
                  <span class="material-symbols-rounded" aria-hidden="true">edit</span>Rename / edit
                </button>
                <button role="menuitem" onclick={() => askDelete(m)}>
                  <span class="material-symbols-rounded" aria-hidden="true">delete</span>Delete
                </button>
              </div>
            {/if}
          </div>
        {/each}
      </section>
    {:else}
      <p class="empty">{app.memories.length ? "No memories match." : "No memories yet. Press ★ or M to save the current frequency."}</p>
    {/each}
  </div>

  <footer>
    {#if status}<p class="status label-sm" role="status">{status}</p>{/if}
    <div class="buttons">
      <button class="text" onclick={() => fileInput.click()}>
        <span class="material-symbols-rounded" aria-hidden="true">upload</span>Import
      </button>
      <button class="text" onclick={() => download("json")}>
        <span class="material-symbols-rounded" aria-hidden="true">download</span>JSON
      </button>
      <button class="text" onclick={() => download("csv")}>
        <span class="material-symbols-rounded" aria-hidden="true">download</span>CSV
      </button>
    </div>
    <input bind:this={fileInput} type="file" accept=".json,.csv,application/json,text/csv" hidden onchange={onFile} />
  </footer>
</aside>

{#if confirming}
  <dialog bind:this={confirmDialog} class="confirm" aria-labelledby="confirm-title" onclose={() => (confirming = null)}>
    <h2 id="confirm-title">Delete memory?</h2>
    <p>“{confirming.name}” will be removed.</p>
    <div class="actions">
      <button class="text" onclick={() => confirmDialog?.close()}>Cancel</button>
      <button class="text" onclick={confirmDelete}>Delete</button>
    </div>
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
  h3 { margin: 12px 16px 4px; color: var(--md-on-surface-variant); text-transform: uppercase; }
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
  .freq { color: var(--md-primary); font-weight: 500; }
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
</style>
