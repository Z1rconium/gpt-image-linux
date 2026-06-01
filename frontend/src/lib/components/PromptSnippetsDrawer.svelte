<script lang="ts">
  import { tick } from 'svelte';
  import type { PromptSnippet, PromptSnippetCreateInput, PromptSnippetUpdateInput } from '$lib/api/types';
  import { dialog } from '$lib/actions/dialog';
  import { plainTextInput } from '$lib/actions/plainTextInput';
  import { swipeClose } from '$lib/actions/swipeClose';
  import { t } from '$lib/i18n';

  type MaybePromise = void | Promise<void>;

  export let open = false;
  export let snippets: PromptSnippet[] = [];
  export let loading = false;
  export let saving = false;
  export let currentPrompt = '';
  export let onClose: () => void = () => {};
  export let onSearch: (query: string) => MaybePromise = () => {};
  export let onCreate: (input: PromptSnippetCreateInput) => MaybePromise = () => {};
  export let onUpdate: (snippetId: string, input: PromptSnippetUpdateInput) => MaybePromise = () => {};
  export let onDelete: (snippet: PromptSnippet) => MaybePromise = () => {};
  export let onUse: (snippet: PromptSnippet) => void = () => {};
  export let onCopy: (snippet: PromptSnippet) => MaybePromise = () => {};

  let query = '';
  let title = '';
  let promptText = '';
  let favorite = false;
  let editingId = '';
  let searchTimer: ReturnType<typeof setTimeout> | null = null;
  let titleInput: HTMLInputElement | null = null;

  $: isEditing = Boolean(editingId);
  $: formReady = Boolean(title.trim() && promptText.trim()) && !saving;
  $: hasCurrentPrompt = Boolean(currentPrompt.trim());
  $: emptyLabel = query.trim() ? $t.promptSnippets.noMatch : $t.promptSnippets.noSnippets;
  $: emptyHint = query.trim() ? $t.promptSnippets.noMatchHint : $t.promptSnippets.noSnippetsHint;

  function resetForm() {
    editingId = '';
    title = '';
    promptText = '';
    favorite = false;
  }

  function snippetTitleFromPrompt(prompt: string) {
    const firstLine = prompt.trim().split('\n').find(Boolean) || $t.promptSnippets.newTitle;
    return firstLine.slice(0, 80);
  }

  function scheduleSearch() {
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      void onSearch(query);
    }, 200);
  }

  async function saveCurrentPrompt() {
    const prompt = currentPrompt.trim();
    if (!prompt || saving) return;
    await onCreate({
      title: snippetTitleFromPrompt(prompt),
      prompt,
      favorite: false
    });
  }

  async function editSnippet(snippet: PromptSnippet) {
    editingId = snippet.id;
    title = snippet.title;
    promptText = snippet.prompt;
    favorite = snippet.favorite;
    await tick();
    titleInput?.focus();
  }

  async function submitForm() {
    if (!formReady) return;
    const input = {
      title: title.trim(),
      prompt: promptText.trim(),
      favorite
    };
    if (editingId) await onUpdate(editingId, input);
    else await onCreate(input);
    resetForm();
  }

  $: if (!open) {
    if (searchTimer) clearTimeout(searchTimer);
    query = '';
    resetForm();
  }
</script>

{#if open}
  <div class="mobile-drawer-root fixed inset-0 z-50">
    <button class="drawer-backdrop absolute inset-0" type="button" tabindex="-1" aria-label={$t.promptSnippets.closeLabel} on:click={onClose}></button>
    <aside
      class="mobile-drawer-panel fade-in absolute right-0 top-0 flex h-full w-full max-w-lg flex-col border-l border-zinc-800 bg-zinc-900 shadow-2xl"
      aria-labelledby="prompt-snippets-drawer-title"
      use:dialog={{ open, onClose }}
      use:swipeClose={{ enabled: open, onClose }}
    >
      <div class="flex items-center justify-between border-b border-zinc-800 p-5">
        <div class="min-w-0">
          <h2 id="prompt-snippets-drawer-title" class="text-lg font-semibold text-zinc-100">{$t.promptSnippets.title}</h2>
          <p class="mt-1 text-xs text-zinc-500">{$t.promptSnippets.subtitle}</p>
        </div>
        <button type="button" class="mobile-touch-target control-focus rounded-lg p-1.5 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100" aria-label={$t.promptSnippets.closeLabel} on:click={onClose}>x</button>
      </div>

      <div class="space-y-4 border-b border-zinc-800 p-5">
        <div class="flex gap-2">
          <input
            bind:value={query}
            name="prompt_snippet_search"
            autocomplete="off"
            placeholder={$t.promptSnippets.search}
            aria-label={$t.promptSnippets.search}
            class="control-focus min-w-0 flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 focus:border-emerald-500"
            on:input={scheduleSearch}
          />
          <button
            type="button"
            disabled={!hasCurrentPrompt || saving}
            class="control-focus shrink-0 rounded-lg border border-emerald-500/40 px-3 py-2 text-xs font-semibold text-emerald-200 hover:bg-emerald-500/10 disabled:cursor-not-allowed disabled:border-zinc-700 disabled:text-zinc-500"
            on:click={saveCurrentPrompt}
          >
            {$t.promptSnippets.saveCurrent}
          </button>
        </div>

        <section class="rounded-xl border border-zinc-800 bg-zinc-950/45 p-4" aria-labelledby="prompt-snippet-form-title">
          <div class="mb-3 flex items-center justify-between gap-3">
            <h3 id="prompt-snippet-form-title" class="text-sm font-semibold text-zinc-200">{isEditing ? $t.promptSnippets.editTitle : $t.promptSnippets.newTitle}</h3>
            {#if isEditing}
              <button type="button" class="control-focus rounded text-xs font-medium text-zinc-400 hover:text-zinc-100" on:click={resetForm}>
                {$t.promptSnippets.cancelEdit}
              </button>
            {/if}
          </div>
          <label class="block">
            <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.promptSnippets.titleLabel}</span>
            <input
              bind:this={titleInput}
              bind:value={title}
              maxlength="160"
              class="control-focus w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 focus:border-emerald-500"
              placeholder={$t.promptSnippets.titlePlaceholder}
            />
          </label>
          <label class="mt-3 block">
            <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.promptSnippets.promptLabel}</span>
            <textarea
              bind:value={promptText}
              maxlength="4000"
              rows="5"
              class="control-focus w-full resize-y rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm leading-6 text-zinc-100 focus:border-emerald-500"
              spellcheck="false"
              placeholder={$t.promptSnippets.promptPlaceholder}
              use:plainTextInput
            ></textarea>
          </label>
          <div class="mt-3 flex items-center justify-between gap-3">
            <label class="inline-flex items-center gap-2 text-xs font-medium text-zinc-300">
              <input bind:checked={favorite} type="checkbox" class="control-focus accent-emerald-500" />
              {$t.promptSnippets.favorite}
            </label>
            <button
              type="button"
              disabled={!formReady}
              class="control-focus rounded-lg bg-emerald-600 px-4 py-2 text-xs font-semibold text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
              on:click={submitForm}
            >
              {saving ? $t.promptSnippets.saving : isEditing ? $t.promptSnippets.update : $t.promptSnippets.create}
            </button>
          </div>
        </section>
      </div>

      <div class="mobile-drawer-scroll min-h-0 flex-1 overflow-y-auto p-5">
        {#if loading && snippets.length === 0}
          <div class="rounded-xl border border-dashed border-zinc-800 bg-zinc-950/35 px-4 py-10 text-center">
            <p class="text-sm font-medium text-zinc-300">{$t.promptSnippets.loading}</p>
          </div>
        {:else if snippets.length === 0}
          <div class="rounded-xl border border-dashed border-zinc-800 bg-zinc-950/35 px-4 py-10 text-center">
            <p class="text-sm font-medium text-zinc-300">{emptyLabel}</p>
            <p class="mt-2 text-xs text-zinc-500">{emptyHint}</p>
          </div>
        {:else}
          <div class="space-y-3" aria-busy={loading}>
            {#each snippets as snippet (snippet.id)}
              <article class="rounded-xl border border-zinc-800 bg-zinc-950/45 p-4">
                <div class="flex items-start justify-between gap-3">
                  <div class="min-w-0">
                    <h3 class="truncate text-sm font-semibold text-zinc-100">{snippet.title}</h3>
                    <p class="mt-2 line-clamp-3 whitespace-pre-wrap text-sm leading-6 text-zinc-300">{snippet.prompt}</p>
                  </div>
                  <button
                    type="button"
                    class="control-focus shrink-0 rounded-lg px-2 py-1 text-base leading-none text-amber-300 hover:bg-zinc-800 disabled:opacity-50"
                    aria-label={snippet.favorite ? $t.common.unfavorite : $t.common.favorite}
                    title={snippet.favorite ? $t.common.unfavorite : $t.common.favorite}
                    disabled={saving}
                    on:click={() => onUpdate(snippet.id, { favorite: !snippet.favorite })}
                  >
                    {snippet.favorite ? '★' : '☆'}
                  </button>
                </div>
                <div class="mt-4 flex flex-wrap justify-end gap-2">
                  <button type="button" class="control-focus rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800" on:click={() => onCopy(snippet)}>
                    {$t.promptSnippets.copy}
                  </button>
                  <button type="button" class="control-focus rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800" on:click={() => editSnippet(snippet)}>
                    {$t.promptSnippets.edit}
                  </button>
                  <button type="button" class="control-focus rounded-lg border border-red-500/40 px-3 py-2 text-xs text-red-300 hover:bg-red-500/10" on:click={() => onDelete(snippet)}>
                    {$t.common.delete}
                  </button>
                  <button type="button" class="control-focus rounded-lg border border-emerald-500/40 px-3 py-2 text-xs font-medium text-emerald-200 hover:bg-emerald-500/10" on:click={() => onUse(snippet)}>
                    {$t.promptSnippets.use}
                  </button>
                </div>
              </article>
            {/each}
          </div>
        {/if}
      </div>
    </aside>
  </div>
{/if}
