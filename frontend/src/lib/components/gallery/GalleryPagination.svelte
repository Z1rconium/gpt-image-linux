<script lang="ts">
  import { t } from '$lib/i18n';

  export let currentPage = 1;
  export let totalPages = 1;
  export let hasPrevious = false;
  export let hasNext = false;
  export let loading = false;
  export let onPage: (page: number, direction?: 'next' | 'prev' | 'jump') => void = () => {};

  let pageInput = '1';
  let syncedPage = currentPage;
  $: if (currentPage !== syncedPage) {
    syncedPage = currentPage;
    pageInput = String(currentPage);
  }

  function clampPage(page: number) {
    return Math.min(Math.max(page, 1), totalPages);
  }

  function commitPageInput() {
    const requested = /^\d+$/.test(pageInput.trim()) ? Number.parseInt(pageInput, 10) : Number.NaN;
    if (!Number.isFinite(requested)) {
      pageInput = String(currentPage);
      return;
    }
    const nextPage = clampPage(requested);
    pageInput = String(nextPage);
    if (nextPage !== currentPage) onPage(nextPage, 'jump');
  }
</script>

<nav class="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between" aria-label={$t.gallery.page(currentPage, totalPages)}>
  <button type="button" disabled={loading || !hasPrevious} class="ui-button-secondary text-xs" on:click={() => onPage(clampPage(currentPage - 1), 'prev')}>{$t.gallery.previous}</button>
  <label class="flex items-center justify-center gap-2 text-xs text-stone-500 dark:text-zinc-400">
    <span>{$t.gallery.pageInputPrefix}</span>
    <input type="number" min="1" max={totalPages} inputmode="numeric" value={pageInput} disabled={loading} aria-label={$t.gallery.jumpPageLabel} title={$t.gallery.jumpPageHint(totalPages)} class="ui-field w-16 text-center" on:input={(event) => (pageInput = event.currentTarget.value)} on:keydown={(event) => { if (event.key === 'Enter') { event.preventDefault(); commitPageInput(); } }} on:blur={commitPageInput} />
    <span>{$t.gallery.pageInputSuffix(totalPages)}</span>
  </label>
  <button type="button" disabled={loading || !hasNext} class="ui-button-secondary text-xs" on:click={() => onPage(clampPage(currentPage + 1), 'next')}>{$t.gallery.next}</button>
</nav>
