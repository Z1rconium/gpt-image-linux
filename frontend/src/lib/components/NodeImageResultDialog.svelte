<script lang="ts">
  import { onDestroy } from 'svelte';
  import { dialog } from '$lib/actions/dialog';
  import { t } from '$lib/i18n';
  import { nodeImageResult, type NodeImageResultItem, type NodeImageResultState } from '$lib/stores/nodeImage';
  import { uiStore } from '$lib/stores/ui';
  import { copyText } from '$lib/utils/format';

  let copiedKey = '';
  let copiedTimer: ReturnType<typeof setTimeout> | null = null;
  let visibleSuccessCount = 20;
  let lastResultState: NodeImageResultState | null = null;

  $: if ($nodeImageResult !== lastResultState) {
    lastResultState = $nodeImageResult;
    visibleSuccessCount = 20;
    copiedKey = '';
  }
  $: failedItems = ($nodeImageResult?.items || []).filter((item) => item.status === 'error');
  $: cancelledItems = ($nodeImageResult?.items || []).filter((item) => item.status === 'cancelled');
  $: successfulItems = ($nodeImageResult?.items || []).filter((item) => item.status === 'ok');
  $: visibleSuccessItems = successfulItems.slice(0, visibleSuccessCount);
  $: remainingSuccessCount = Math.max(0, successfulItems.length - visibleSuccessItems.length);
  $: resultGroups = [
    { key: 'error', label: $t.gallery.nodeImageFailed, items: failedItems },
    { key: 'cancelled', label: $t.gallery.nodeImageCancelled, items: cancelledItems },
    { key: 'ok', label: $t.gallery.nodeImageUploaded, items: visibleSuccessItems }
  ];

  function close() {
    copiedKey = '';
    visibleSuccessCount = 20;
    nodeImageResult.clear();
  }

  async function copyValue(value: string, key: string) {
    try {
      await copyText(value);
      copiedKey = key;
      if (copiedTimer) clearTimeout(copiedTimer);
      copiedTimer = setTimeout(() => {
        copiedKey = '';
        copiedTimer = null;
      }, 1600);
    } catch {
      uiStore.showToast($t.gallery.nodeImageCopyFailed, 'error');
    }
  }

  function copyAll(items: NodeImageResultItem[], field: 'url' | 'markdown', key: string) {
    return copyValue(
      items.map((item) => item[field]).join('\n'),
      key
    );
  }

  onDestroy(() => {
    if (copiedTimer) clearTimeout(copiedTimer);
  });
</script>

{#if $nodeImageResult}
  <div class="mobile-dialog-root fixed inset-0 z-[80] flex items-center justify-center bg-stone-950/60 p-4 dark:bg-zinc-950/75">
    <button class="absolute inset-0" type="button" tabindex="-1" aria-label={$t.gallery.closeNodeImageResults} on:click={close}></button>
    <div
      class="mobile-dvh-dialog fade-in relative flex max-h-[88vh] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-stone-200 bg-white shadow-2xl shadow-stone-300/50 dark:border-zinc-800 dark:bg-zinc-900 dark:shadow-none"
      role="dialog"
      aria-modal="true"
      aria-labelledby="nodeimage-result-title"
      use:dialog={{ open: true, onClose: close }}
    >
      <header class="flex items-start justify-between gap-4 border-b border-stone-200 p-5 dark:border-zinc-800">
        <div class="min-w-0">
          <h2 id="nodeimage-result-title" class="text-lg font-semibold text-stone-900 dark:text-zinc-100">{$t.gallery.nodeImageResultTitle}</h2>
          <p class="mt-1 text-xs leading-5 text-stone-500 dark:text-zinc-500">
            {$t.gallery.nodeImageResultSummary($nodeImageResult.uploadedCount, $nodeImageResult.failedCount, $nodeImageResult.cancelledCount)}
          </p>
        </div>
        <button type="button" class="mobile-touch-target control-focus shrink-0 rounded-md p-1.5 text-stone-500 hover:bg-stone-100 hover:text-stone-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100" aria-label={$t.gallery.closeNodeImageResults} on:click={close}>
          <svg class="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" /></svg>
        </button>
      </header>

      {#if successfulItems.length > 0}
        <div class="grid gap-2 border-b border-stone-200 p-5 sm:grid-cols-2 dark:border-zinc-800">
          <button
            type="button"
            class="ui-button-secondary flex min-w-0 items-center justify-center gap-2"
            aria-label={$t.gallery.copyAllNodeImageDirectLinks}
            title={copiedKey === 'all-urls' ? $t.gallery.nodeImageCopied : $t.gallery.copyAllNodeImageDirectLinks}
            on:click={() => copyAll(successfulItems, 'url', 'all-urls')}
          >
            <svg class="h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="8" y="8" width="11" height="11" rx="2" /><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3" /></svg>
            <span class="truncate">{$t.gallery.copyAllNodeImageDirectLinks}</span>
          </button>
          <button
            type="button"
            class="ui-button-secondary flex min-w-0 items-center justify-center gap-2"
            aria-label={$t.gallery.copyAllNodeImageMarkdownLinks}
            title={copiedKey === 'all-markdown' ? $t.gallery.nodeImageCopied : $t.gallery.copyAllNodeImageMarkdownLinks}
            on:click={() => copyAll(successfulItems, 'markdown', 'all-markdown')}
          >
            <svg class="h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="8" y="8" width="11" height="11" rx="2" /><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3" /></svg>
            <span class="truncate">{$t.gallery.copyAllNodeImageMarkdownLinks}</span>
          </button>
        </div>
      {/if}

      <div class="min-h-0 flex-1 overflow-y-auto px-5">
        {#each resultGroups as group (group.key)}
          {#if group.items.length > 0}
            <section class="border-b border-stone-200 py-2 last:border-b-0 dark:border-zinc-800" aria-labelledby={`nodeimage-result-group-${group.key}`}>
              <h3 id={`nodeimage-result-group-${group.key}`} class="py-2 text-xs font-semibold text-stone-600 dark:text-zinc-400">{group.label} ({group.key === 'ok' ? successfulItems.length : group.items.length})</h3>
              {#each group.items as item (item.imageId)}
                <article class="border-t border-stone-200 py-4 first:border-t-0 dark:border-zinc-800" aria-label={item.label}>
                  <div class="flex min-w-0 items-start justify-between gap-3">
                    <h4 class="min-w-0 break-all text-sm font-semibold text-stone-800 dark:text-zinc-200">{item.label}</h4>
                    <span class={`shrink-0 rounded-md border px-2 py-1 text-xs font-medium ${item.status === 'ok' ? 'border-emerald-500/40 text-emerald-700 dark:text-emerald-300' : item.status === 'cancelled' ? 'border-stone-400/60 text-stone-600 dark:border-zinc-600 dark:text-zinc-400' : 'border-red-500/40 text-red-700 dark:text-red-300'}`}>
                      {item.status === 'ok' ? $t.gallery.nodeImageUploaded : item.status === 'cancelled' ? $t.gallery.nodeImageCancelled : $t.gallery.nodeImageFailed}
                    </span>
                  </div>

                  {#if item.status === 'ok'}
                    <div class="mt-3 space-y-3">
                      <div class="flex min-w-0 items-center gap-2">
                        <div class="min-w-0 flex-1">
                          <div class="text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.gallery.nodeImageDirectLink}</div>
                          <a href={item.url} target="_blank" rel="noopener noreferrer" class="control-focus mt-1 block break-all rounded text-xs leading-5 text-emerald-700 underline decoration-emerald-500/40 underline-offset-2 hover:text-emerald-800 dark:text-emerald-300 dark:hover:text-emerald-200">{item.url}</a>
                        </div>
                        <button
                          type="button"
                          class="ui-icon-button shrink-0 border border-stone-300 dark:border-zinc-700"
                          aria-label={$t.gallery.copyNodeImageDirectLink}
                          title={copiedKey === `${item.imageId}-url` ? $t.gallery.nodeImageCopied : $t.gallery.copyNodeImageDirectLink}
                          on:click={() => copyValue(item.url, `${item.imageId}-url`)}
                        >
                          <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="8" y="8" width="11" height="11" rx="2" /><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3" /></svg>
                        </button>
                      </div>

                      <div class="flex min-w-0 items-center gap-2">
                        <div class="min-w-0 flex-1">
                          <div class="text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.gallery.nodeImageMarkdownLink}</div>
                          <code class="mt-1 block break-all text-xs leading-5 text-stone-700 dark:text-zinc-300">{item.markdown}</code>
                        </div>
                        <button
                          type="button"
                          class="ui-icon-button shrink-0 border border-stone-300 dark:border-zinc-700"
                          aria-label={$t.gallery.copyNodeImageMarkdownLink}
                          title={copiedKey === `${item.imageId}-markdown` ? $t.gallery.nodeImageCopied : $t.gallery.copyNodeImageMarkdownLink}
                          on:click={() => copyValue(item.markdown, `${item.imageId}-markdown`)}
                        >
                          <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="8" y="8" width="11" height="11" rx="2" /><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0 2 2v9a2 2 0 0 0 2 2h3" /></svg>
                        </button>
                      </div>
                    </div>
                  {:else}
                    <p class={`mt-3 break-words text-xs leading-5 ${item.status === 'cancelled' ? 'text-stone-600 dark:text-zinc-400' : 'text-red-700 dark:text-red-300'}`}>{item.error || $t.gallery.nodeImageUnknownError}</p>
                  {/if}
                </article>
              {/each}

              {#if group.key === 'ok' && remainingSuccessCount > 0}
                <button
                  type="button"
                  class="control-focus my-3 flex min-h-10 w-full items-center justify-center rounded-md border border-stone-300 px-3 py-2 text-sm font-medium text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
                  aria-label={$t.gallery.nodeImageShowMore(remainingSuccessCount)}
                  on:click={() => { visibleSuccessCount += 20; }}
                >
                  {$t.gallery.nodeImageShowMore(remainingSuccessCount)}
                </button>
              {/if}
            </section>
          {/if}
        {/each}
      </div>

      <footer class="flex flex-wrap items-center justify-between gap-3 border-t border-stone-200 p-5 dark:border-zinc-800">
        <span class="min-h-5 min-w-0 text-xs text-emerald-700 dark:text-emerald-300" role="status" aria-live="polite">
          {copiedKey ? $t.gallery.nodeImageCopied : ''}
        </span>
        <button type="button" class="ui-button-secondary shrink-0" on:click={close}>{$t.common.close}</button>
      </footer>
    </div>
  </div>
{/if}
