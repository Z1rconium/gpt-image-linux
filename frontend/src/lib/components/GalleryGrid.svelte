<script lang="ts">
  import { onDestroy } from 'svelte';
import type { GalleryEntry, GalleryResponse } from '$lib/api/types/gallery';
  import GalleryFilterToolbar from '$lib/components/gallery/GalleryFilterToolbar.svelte';
  import GalleryPagination from '$lib/components/gallery/GalleryPagination.svelte';
  import { t } from '$lib/i18n';
  import type { GalleryFilters, GalleryOperationStatus } from '$lib/stores/gallery';
  import { displayImageSize, formatBytes, thumbnailUrl } from '$lib/utils/format';

  export let gallery: GalleryResponse | null = null;
  export let filters: GalleryFilters;
  export let loading = false;
  export let operationStatus: GalleryOperationStatus | null = null;
  export let canSyncR2 = false;
  export let onFilter: (key: keyof GalleryFilters, value: string | boolean) => void = () => {};
  export let onResetFilters: () => void = () => {};
  export let onPage: (page: number, direction?: 'next' | 'prev' | 'jump') => void = () => {};
  export let onLoadStats: () => void = () => {};
  export let onFavorite: (image: GalleryEntry) => void = () => {};
  export let onDelete: (image: GalleryEntry) => void = () => {};
  export let onDeleteAll: () => void = () => {};
  export let onImport: (file: File) => void = () => {};
  export let onExport: () => void = () => {};
  export let onSync: () => void = () => {};
  export let onOpen: (image: GalleryEntry) => void = () => {};
  export let onEdit: (image: GalleryEntry) => void = () => {};
  export let onUsePrompt: (image: GalleryEntry) => void = () => {};
  export let onUseAll: (image: GalleryEntry) => void = () => {};
  export let selectionMode = false;
  export let selectedIds: Set<string> = new Set();
  export let selectionTokenCount = 0;
  export let onSelectionMode: (enabled: boolean) => void = () => {};
  export let onToggleSelection: (image: GalleryEntry) => void = () => {};
  export let onSelectPage: () => void = () => {};
  export let onSelectFiltered: () => void = () => {};
  export let onClearSelection: () => void = () => {};
  export let onBatchDelete: () => void = () => {};
  export let onBatchFavorite: (favorite: boolean) => void = () => {};
  export let onBatchDownload: () => void = () => {};
  export let canAiAnalyze = false;
  export let onBatchAiAnalyze: () => void = () => {};

  const skeletonCards = Array.from({ length: 6 });
  const EAGER_THUMB_COUNT = 3;
  const THUMBNAIL_RETRY_DELAYS_MS = [1200, 2400, 4800, 9600, 16000];
  const THUMBNAIL_PLACEHOLDER_SRC = 'data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=';

  let importInput: HTMLInputElement;
  let failedThumbnailIds = new Set<string>();
  const thumbnailRetryTimers = new Map<string, ReturnType<typeof setTimeout>>();
  const thumbnailRetryCounts = new Map<string, number>();

  $: images = gallery?.images || [];
  $: currentPage = gallery?.page || 1;
  $: totalPages = Math.max(gallery?.total_pages || 1, 1);
  $: initialLoading = loading && images.length === 0;
  $: busy = loading || Boolean(operationStatus);
  $: selectedAllFiltered = selectionTokenCount > 0;
  $: selectedCount = selectedAllFiltered ? selectionTokenCount : selectedIds.size;
  $: pageSelectedCount = selectedAllFiltered ? images.length : images.filter((image) => selectedIds.has(image.id)).length;
  $: hasSelection = selectedCount > 0;
  $: selectionSummary = selectedAllFiltered
    ? $t.gallery.filteredSelection(selectedCount)
    : selectedCount > pageSelectedCount
      ? $t.gallery.crossPageSelection(pageSelectedCount, selectedCount)
      : $t.gallery.pageSelection(selectedCount);
  $: hasFilters = Boolean(
    filters.prompt.trim() ||
      filters.model ||
      filters.preset ||
      filters.size ||
      filters.dateFrom ||
      filters.dateTo ||
      filters.favorite
  );

  function importSelected() {
    const file = importInput.files?.[0];
    if (file) onImport(file);
    importInput.value = '';
  }

  function handleImageClick(image: GalleryEntry) {
    if (selectionMode) {
      onToggleSelection(image);
      return;
    }
    onOpen(image);
  }

  function handleGalleryAction(event: MouseEvent, action: () => void) {
    event.preventDefault();
    event.stopPropagation();
    action();
  }

  function galleryImageSrc(image: GalleryEntry) {
    if (!thumbnailReady(image)) return THUMBNAIL_PLACEHOLDER_SRC;
    if (failedThumbnailIds.has(image.id)) {
      return THUMBNAIL_PLACEHOLDER_SRC;
    }
    const retryAttempt = thumbnailRetryCounts.get(image.id) || 0;
    return retryAttempt > 0 ? thumbnailRequestUrl(image, retryAttempt) : thumbnailUrl(image.filename, image.thumbnail_url);
  }

  function thumbnailReady(image: GalleryEntry) {
    return !image.thumbnail_status || image.thumbnail_status === 'ready';
  }

  function thumbnailRequestUrl(image: GalleryEntry, attempt: number) {
    const base = thumbnailUrl(image.filename, image.thumbnail_url);
    if (!attempt) return base;
    try {
      const url = new URL(base, window.location.origin);
      url.searchParams.set('retry', String(attempt));
      return url.origin === window.location.origin ? `${url.pathname}${url.search}${url.hash}` : url.toString();
    } catch {
      return base;
    }
  }

  function clearThumbnailRetry(imageId: string) {
    const timer = thumbnailRetryTimers.get(imageId);
    if (timer) clearTimeout(timer);
    thumbnailRetryTimers.delete(imageId);
    thumbnailRetryCounts.delete(imageId);
    failedThumbnailIds = new Set([...failedThumbnailIds].filter((id) => id !== imageId));
  }

  function scheduleThumbnailRetry(image: GalleryEntry) {
    const attempt = thumbnailRetryCounts.get(image.id) || 0;
    if (attempt >= THUMBNAIL_RETRY_DELAYS_MS.length) return;
    if (thumbnailRetryTimers.has(image.id)) return;

    failedThumbnailIds = new Set(failedThumbnailIds).add(image.id);
    const delay = THUMBNAIL_RETRY_DELAYS_MS[attempt];
    const timer = setTimeout(() => {
      thumbnailRetryTimers.delete(image.id);
      failedThumbnailIds = new Set([...failedThumbnailIds].filter((id) => id !== image.id));
      thumbnailRetryCounts.set(image.id, attempt + 1);
    }, delay);
    thumbnailRetryTimers.set(image.id, timer);
  }

  function handleThumbnailLoad(image: GalleryEntry) {
    if (!thumbnailReady(image)) return;
    clearThumbnailRetry(image.id);
  }

  function handleThumbnailError(event: Event, image: GalleryEntry) {
    if (!thumbnailReady(image)) {
      failedThumbnailIds = new Set(failedThumbnailIds).add(image.id);
      return;
    }
    scheduleThumbnailRetry(image);
  }

  function isImageSelected(image: GalleryEntry) {
    return selectedAllFiltered || selectedIds.has(image.id);
  }

  onDestroy(() => {
    thumbnailRetryTimers.forEach((timer) => clearTimeout(timer));
    thumbnailRetryTimers.clear();
    thumbnailRetryCounts.clear();
    failedThumbnailIds = new Set();
  });
</script>

<section class="app-section px-1 py-1 sm:px-0">
  <div class="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
    <div>
      <h2 class="text-sm font-semibold text-stone-950 dark:text-zinc-100">{$t.gallery.title}</h2>
      <p class="mt-1 text-xs text-stone-500 dark:text-zinc-500">
        {gallery?.total ? $t.gallery.imageCount(gallery.total) : $t.gallery.noImages}
        {#if gallery?.total_bytes}
          <span class="ml-2">{formatBytes(gallery.total_bytes)}</span>
        {:else if gallery?.total}
          <button type="button" class="control-focus ml-2 rounded text-xs font-medium text-stone-600 hover:text-stone-900 dark:text-zinc-400 dark:hover:text-zinc-200" on:click={onLoadStats}>
            {$t.gallery.showSize}
          </button>
        {/if}
      </p>
    </div>
    <div class="flex flex-wrap gap-2">
      <button type="button" class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-xs text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" on:click={() => onSelectionMode(!selectionMode)}>
        {selectionMode ? $t.gallery.cancelSelection : $t.gallery.select}
      </button>
      <button type="button" class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-xs text-stone-700 hover:bg-stone-100 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" disabled={busy} on:click={() => importInput.click()}>
        {operationStatus?.kind === 'import' ? $t.gallery.importing : $t.gallery.import}
      </button>
      <button type="button" class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-xs text-stone-700 hover:bg-stone-100 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" disabled={busy} on:click={onExport}>
        {operationStatus?.kind === 'export' ? $t.gallery.exporting : $t.gallery.exportZip}
      </button>
      <button
        type="button"
        class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-xs text-stone-700 hover:bg-stone-100 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
        disabled={busy || !canSyncR2}
        title={canSyncR2 ? $t.gallery.syncR2 : $t.messages.r2BackupUnavailable}
        on:click={onSync}
      >
        {operationStatus?.kind === 'sync' ? $t.gallery.syncing : $t.gallery.syncR2}
      </button>
      <button type="button" class="control-focus rounded-lg border border-red-500/40 px-3 py-2 text-xs text-red-700 hover:bg-red-500/10 dark:text-red-300" on:click={onDeleteAll}>
        {$t.gallery.deleteAll}
      </button>
      <input bind:this={importInput} type="file" accept=".zip,application/zip" class="hidden" on:change={importSelected} />
    </div>
  </div>

  <GalleryFilterToolbar {gallery} {filters} {onFilter} onReset={onResetFilters} />

  {#if selectionMode}
    <div class="mb-4 flex flex-col gap-3 rounded-xl border border-emerald-500/30 bg-emerald-500/5 p-3 sm:flex-row sm:items-center sm:justify-between">
      <div class="text-xs font-medium text-emerald-800 dark:text-emerald-200">{selectionSummary}</div>
      <div class="flex flex-wrap gap-2">
        <button type="button" class="control-focus rounded-lg border border-stone-300 px-2.5 py-2 text-xs text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" on:click={onSelectPage}>{$t.gallery.selectAllPage}</button>
        <button type="button" class="control-focus rounded-lg border border-stone-300 px-2.5 py-2 text-xs text-stone-700 hover:bg-stone-100 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" disabled={!gallery?.total || busy} on:click={onSelectFiltered}>{$t.gallery.selectFiltered}</button>
        <button type="button" class="control-focus rounded-lg border border-stone-300 px-2.5 py-2 text-xs text-stone-700 hover:bg-stone-100 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" disabled={!hasSelection} on:click={onClearSelection}>{$t.gallery.clearSelection}</button>
        <button type="button" class="control-focus rounded-lg border border-stone-300 px-2.5 py-2 text-xs text-stone-700 hover:bg-stone-100 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" disabled={!hasSelection || busy} on:click={onBatchDownload}>{operationStatus?.kind === 'download' ? $t.gallery.downloading : $t.gallery.downloadSelected}</button>
        {#if canAiAnalyze}
          <button type="button" class="control-focus rounded-lg border border-stone-300 px-2.5 py-2 text-xs text-stone-700 hover:bg-stone-100 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" disabled={!hasSelection || busy} on:click={onBatchAiAnalyze}>{operationStatus?.kind === 'ai_analyze' ? $t.gallery.aiAnalyzing : $t.gallery.aiAnalyzeSelected}</button>
        {/if}
        <button type="button" class="control-focus rounded-lg border border-stone-300 px-2.5 py-2 text-xs text-stone-700 hover:bg-stone-100 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" disabled={!hasSelection || busy} on:click={() => onBatchFavorite(true)}>{$t.gallery.favoriteSelected}</button>
        <button type="button" class="control-focus rounded-lg border border-stone-300 px-2.5 py-2 text-xs text-stone-700 hover:bg-stone-100 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" disabled={!hasSelection || busy} on:click={() => onBatchFavorite(false)}>{$t.gallery.unfavoriteSelected}</button>
        <button type="button" class="control-focus rounded-lg border border-red-500/40 px-2.5 py-2 text-xs text-red-700 hover:bg-red-500/10 disabled:opacity-40 dark:text-red-300" disabled={!hasSelection || busy} on:click={onBatchDelete}>{$t.gallery.deleteSelected}</button>
      </div>
    </div>
  {/if}

  {#if operationStatus}
    <div class="mb-4 rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-3" role="status" aria-live="polite">
      <div class="flex items-start justify-between gap-3">
        <div>
          <p class="text-xs font-semibold text-emerald-950 dark:text-emerald-100">{operationStatus.label}</p>
          <p class="mt-1 text-xs text-emerald-800 dark:text-emerald-200/80">{operationStatus.detail}</p>
        </div>
        <div class="text-xs text-emerald-800 dark:text-emerald-200">{operationStatus.progress === null ? $t.gallery.notInterruptible : `${operationStatus.progress}%`}</div>
      </div>
      <div class="mt-3 h-1.5 overflow-hidden rounded-full bg-emerald-950/20 dark:bg-emerald-950/70">
        {#if operationStatus.progress === null}
          <div class="h-full w-1/3 animate-pulse rounded-full bg-emerald-500 dark:bg-emerald-300"></div>
        {:else}
          <div class="h-full rounded-full bg-emerald-500 transition-[width] dark:bg-emerald-300" style={`width: ${Number(operationStatus.progress) || 0}%`}></div>
        {/if}
      </div>
    </div>
  {/if}

  {#if initialLoading}
    <div class="grid gap-4 sm:grid-cols-2 md:gap-5 lg:grid-cols-3 lg:gap-4" aria-label={$t.gallery.loading}>
      {#each skeletonCards as _}
        <div class="overflow-hidden rounded-xl border border-stone-200 bg-stone-100/90 dark:border-zinc-800 dark:bg-zinc-950/45">
          <div class="aspect-square animate-pulse bg-stone-200/80 dark:bg-zinc-800/60"></div>
          <div class="space-y-3 p-3">
            <div class="h-4 w-5/6 animate-pulse rounded bg-stone-200 dark:bg-zinc-800/70"></div>
            <div class="h-3 w-1/2 animate-pulse rounded bg-stone-200 dark:bg-zinc-800/60"></div>
            <div class="flex gap-2">
              <div class="h-7 w-14 animate-pulse rounded bg-stone-200 dark:bg-zinc-800/60"></div>
              <div class="h-7 w-16 animate-pulse rounded bg-stone-200 dark:bg-zinc-800/60"></div>
            </div>
          </div>
        </div>
      {/each}
    </div>
  {:else if images.length === 0}
    <div class="rounded-xl border border-dashed border-stone-300 bg-stone-100/80 px-4 py-10 text-center dark:border-zinc-800 dark:bg-zinc-950/35">
      <p class="text-sm font-medium text-stone-700 dark:text-zinc-300">{hasFilters ? $t.gallery.noMatch : $t.gallery.empty}</p>
      <p class="mt-2 text-xs text-stone-500 dark:text-zinc-500">{hasFilters ? $t.gallery.noMatchHint : $t.gallery.emptyHint}</p>
    </div>
  {:else}
    <div class="relative" aria-busy={loading}>
      {#if loading}
        <div class="pointer-events-none absolute inset-0 z-10 rounded-xl bg-white/30 backdrop-blur-[1px] dark:bg-zinc-950/20">
          <div class="absolute right-3 top-3 rounded-lg border border-stone-300 bg-white/90 px-3 py-2 text-xs text-stone-700 shadow-lg dark:border-zinc-700 dark:bg-zinc-950/90 dark:text-zinc-300">
            {$t.gallery.loading}
          </div>
        </div>
      {/if}

      <div class={`grid gap-4 sm:grid-cols-2 md:gap-5 lg:grid-cols-3 lg:gap-4 ${loading ? 'opacity-70' : ''}`}>
        {#each images as image, index (image.id)}
          <article class={`gallery-card overflow-hidden rounded-xl border ${isImageSelected(image) ? 'border-emerald-400 bg-emerald-500/10' : 'border-stone-200 bg-white/85 dark:border-zinc-800 dark:bg-zinc-950/45'}`}>
            <button
              type="button"
              class="control-focus relative block aspect-square w-full bg-stone-100 dark:bg-zinc-950"
              aria-label={image.prompt}
              aria-pressed={selectionMode ? isImageSelected(image) : undefined}
              on:click={() => handleImageClick(image)}
            >
              {#if selectionMode}
                <span class="absolute left-2 top-2 z-10 rounded-md bg-white/90 px-2 py-1 text-xs font-medium text-stone-800 dark:bg-zinc-950/80 dark:text-zinc-100">
                  {isImageSelected(image) ? '✓' : ''}
                </span>
                <span class="sr-only">{isImageSelected(image) ? $t.gallery.selectedCount(1) : $t.gallery.select}</span>
              {/if}
              <picture class="block h-full w-full">
                <img
                  src={galleryImageSrc(image)}
                  alt={image.prompt}
                  class="gallery-image preview-empty h-full w-full object-cover"
                  loading={index < EAGER_THUMB_COUNT ? 'eager' : 'lazy'}
                  fetchpriority={index < EAGER_THUMB_COUNT ? 'high' : 'low'}
                  decoding="async"
                  width={image.image_width || undefined}
                  height={image.image_height || undefined}
                  on:load={() => handleThumbnailLoad(image)}
                  on:error={(event) => handleThumbnailError(event, image)}
                />
              </picture>
            </button>
            <div class="space-y-2 p-2.5">
              <div class="min-w-0">
                <p class="line-clamp-2 text-xs leading-5 text-stone-800 dark:text-zinc-200">{image.prompt}</p>
                <p class="mt-1 truncate text-[11px] leading-4 text-stone-500 dark:text-zinc-500">{displayImageSize(image)} / {image.model || '-'}</p>
              </div>
              <div class="grid grid-cols-6 gap-1">
                <button
                  type="button"
                  class="gallery-icon-action control-focus border-emerald-500/40 text-emerald-700 hover:bg-emerald-500/10 dark:text-emerald-200"
                  aria-label={$t.common.usePrompt}
                  title={$t.common.usePrompt}
                  on:click={(event) => handleGalleryAction(event, () => onUsePrompt(image))}
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4h10a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z"/><path d="M8 8h8M8 12h8M8 16h5"/></svg>
                </button>
                <button
                  type="button"
                  class="gallery-icon-action control-focus border-emerald-500/40 text-emerald-700 hover:bg-emerald-500/10 dark:text-emerald-200"
                  aria-label={$t.common.useAllParams}
                  title={$t.common.useAllParams}
                  on:click={(event) => handleGalleryAction(event, () => onUseAll(image))}
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/></svg>
                </button>
                <button
                  type="button"
                  class="gallery-icon-action control-focus border-stone-300 text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
                  aria-pressed={image.favorite}
                  aria-label={image.favorite ? $t.common.unfavorite : $t.common.favorite}
                  title={image.favorite ? $t.common.unfavorite : $t.common.favorite}
                  on:click={(event) => handleGalleryAction(event, () => onFavorite(image))}
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9L12 3Z"/></svg>
                </button>
                <button
                  type="button"
                  class="gallery-icon-action control-focus border-stone-300 text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
                  aria-label={$t.common.edit}
                  title={$t.common.edit}
                  on:click={(event) => handleGalleryAction(event, () => onEdit(image))}
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20h4l10.5-10.5a2.1 2.1 0 0 0-3-3L5 17v3Z"/><path d="m14 7 3 3"/></svg>
                </button>
                <a
                  href={`/api/download/${encodeURIComponent(image.filename)}`}
                  class="gallery-icon-action control-focus border-stone-300 text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
                  aria-label={$t.common.download}
                  title={$t.common.download}
                  on:click|stopPropagation
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v10"/><path d="m8 10 4 4 4-4"/><path d="M5 20h14"/></svg>
                </a>
                <button
                  type="button"
                  class="gallery-icon-action control-focus border-red-500/40 text-red-700 hover:bg-red-500/10 dark:text-red-300"
                  aria-label={$t.common.delete}
                  title={$t.common.delete}
                  on:click={(event) => handleGalleryAction(event, () => onDelete(image))}
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16"/><path d="M9 7V5h6v2"/><path d="M7 7l1 13h8l1-13"/><path d="M10 11v5M14 11v5"/></svg>
                </button>
              </div>
            </div>
          </article>
        {/each}
      </div>
    </div>

    <GalleryPagination {currentPage} {totalPages} hasPrevious={Boolean(gallery?.has_prev)} hasNext={Boolean(gallery?.has_next)} {loading} {onPage} />
  {/if}
</section>
