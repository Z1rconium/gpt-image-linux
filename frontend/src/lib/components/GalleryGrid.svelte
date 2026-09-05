<script lang="ts">
  import { onDestroy } from 'svelte';
  import type { GalleryEntry, GalleryResponse } from '$lib/api/types/gallery';
  import GalleryFilterToolbar from '$lib/components/gallery/GalleryFilterToolbar.svelte';
  import GalleryPagination from '$lib/components/gallery/GalleryPagination.svelte';
  import { t } from '$lib/i18n';
  import type { GalleryFilters, GalleryOperationStatus } from '$lib/stores/gallery';
  import { CloudUpload, Download, FileText, Pencil, SlidersHorizontal, Star, Trash2, X } from 'lucide-svelte';
  import { displayImageSize, formatBytes, thumbnailUrl } from '$lib/utils/format';

  export let gallery: GalleryResponse | null = null;
  export let filters: GalleryFilters;
  export let loading = false;
  export let operationStatus: GalleryOperationStatus | null = null;
  export let canSyncR2 = false;
  export let canNodeImageUpload = false;
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
  export let onNodeImageUpload: (image: GalleryEntry) => void = () => {};
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
  export let onBatchNodeImageUpload: () => void = () => {};
  export let canAiAnalyze = false;
  export let onBatchAiAnalyze: () => void = () => {};

  const skeletonCards = Array.from({ length: 6 });
  const EAGER_THUMB_COUNT = 3;
  const THUMBNAIL_PLACEHOLDER_SRC = 'data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=';

  let importInput: HTMLInputElement;
  let failedThumbnailUrls = new Map<string, string>();
  let poppedFavoriteId = '';
  let favoritePopTimer: ReturnType<typeof setTimeout> | undefined;

  // One beat, on the way in only: turning a favourite off is not a celebration.
  function popFavorite(image: GalleryEntry) {
    if (image.favorite) return;
    clearTimeout(favoritePopTimer);
    poppedFavoriteId = '';
    requestAnimationFrame(() => {
      poppedFavoriteId = image.id;
    });
    favoritePopTimer = setTimeout(() => {
      poppedFavoriteId = '';
    }, 280);
  }

  onDestroy(() => clearTimeout(favoritePopTimer));

  $: images = gallery?.images || [];
  $: pruneFailedThumbnailUrls(images);
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
    const src = thumbnailUrl(image.filename, image.thumbnail_url);
    return failedThumbnailUrls.get(image.id) === src ? THUMBNAIL_PLACEHOLDER_SRC : src;
  }

  function pruneFailedThumbnailUrls(visibleImages: GalleryEntry[]) {
    const visibleIds = new Set(visibleImages.map((image) => image.id));
    if ([...failedThumbnailUrls.keys()].every((imageId) => visibleIds.has(imageId))) return;
    failedThumbnailUrls = new Map(
      [...failedThumbnailUrls].filter(([imageId]) => visibleIds.has(imageId))
    );
  }

  function thumbnailReady(image: GalleryEntry) {
    return !image.thumbnail_status || image.thumbnail_status === 'ready';
  }

  function handleThumbnailLoad(image: GalleryEntry) {
    if (!thumbnailReady(image)) return;
    if (!failedThumbnailUrls.has(image.id)) return;
    failedThumbnailUrls = new Map(failedThumbnailUrls);
    failedThumbnailUrls.delete(image.id);
  }

  function handleThumbnailError(image: GalleryEntry) {
    if (!thumbnailReady(image)) return;
    failedThumbnailUrls = new Map(failedThumbnailUrls).set(image.id, thumbnailUrl(image.filename, image.thumbnail_url));
  }

  function isImageSelected(image: GalleryEntry) {
    return selectedAllFiltered || selectedIds.has(image.id);
  }
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
        {#if canNodeImageUpload}
          <button type="button" class="control-focus rounded-lg border border-stone-300 px-2.5 py-2 text-xs text-stone-700 hover:bg-stone-100 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" disabled={!hasSelection || busy} on:click={onBatchNodeImageUpload}>
            {operationStatus?.kind === 'nodeimage_upload' ? $t.gallery.uploadingToNodeImage : $t.gallery.uploadSelectedToNodeImage}
          </button>
        {/if}
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
        <div class="flex shrink-0 items-center gap-2">
          <div class="text-xs text-emerald-800 dark:text-emerald-200">{operationStatus.progress === null ? $t.gallery.notInterruptible : `${operationStatus.progress}%`}</div>
          {#if operationStatus.cancel}
            <button
              type="button"
              class="control-focus inline-flex min-h-9 items-center gap-1.5 rounded-md border border-emerald-700/40 px-2.5 py-1.5 text-xs font-medium text-emerald-900 hover:bg-emerald-500/10 disabled:cursor-wait disabled:opacity-50 dark:border-emerald-300/40 dark:text-emerald-100 dark:hover:bg-emerald-300/10"
              disabled={operationStatus.cancelPending}
              on:click={() => void operationStatus.cancel?.()}
            >
              <X class="h-3.5 w-3.5" aria-hidden="true" />
              {$t.gallery.cancelOperation}
            </button>
          {/if}
        </div>
      </div>
      <div class="mt-3 h-1.5 overflow-hidden rounded-full bg-emerald-950/20 dark:bg-emerald-950/70">
        {#if operationStatus.progress === null}
          <div class="progress-indeterminate h-full w-1/3 rounded-full bg-emerald-500 dark:bg-emerald-300"></div>
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
        <div class="gallery-loading-overlay pointer-events-none absolute inset-0 z-10 rounded-xl">
          <div class="absolute right-3 top-3 rounded-lg border border-stone-300 bg-white/90 px-3 py-2 text-xs text-stone-700 shadow-lg dark:border-zinc-700 dark:bg-zinc-950/90 dark:text-zinc-300">
            {$t.gallery.loading}
          </div>
        </div>
      {/if}

      <div class={`gallery-grid grid gap-4 sm:grid-cols-2 md:gap-5 lg:grid-cols-3 lg:gap-4 ${loading ? 'opacity-70' : ''}`}>
        {#each images as image, index (image.id)}
          <article class={`gallery-card overflow-hidden rounded-xl border ${isImageSelected(image) ? 'border-emerald-400 bg-emerald-500/10' : 'border-stone-200 bg-white/85 dark:border-zinc-800 dark:bg-zinc-950/45'}`}>
            <button
              type="button"
              class="gallery-media-well control-focus relative block aspect-square w-full bg-stone-100 dark:bg-zinc-950"
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
                  on:error={() => handleThumbnailError(image)}
                />
              </picture>
            </button>
            <div class="space-y-2 p-2.5">
              <div class="min-w-0">
                <p class="line-clamp-2 text-xs leading-5 text-stone-800 dark:text-zinc-200">{image.prompt}</p>
                <p class="mt-1 truncate text-xs leading-4 text-stone-500 dark:text-zinc-500">{displayImageSize(image)} / {image.model || '-'}</p>
              </div>
              <div class="gallery-card-actions grid grid-cols-4 gap-1">
                <button
                  type="button"
                  class="gallery-icon-action control-focus border-emerald-500/40 text-emerald-700 hover:bg-emerald-500/10 dark:text-emerald-200"
                  aria-label={$t.common.usePrompt}
                  title={$t.common.usePrompt}
                  on:click={(event) => handleGalleryAction(event, () => onUsePrompt(image))}
                >
                  <FileText aria-hidden="true" />
                </button>
                <button
                  type="button"
                  class="gallery-icon-action control-focus border-emerald-500/40 text-emerald-700 hover:bg-emerald-500/10 dark:text-emerald-200"
                  aria-label={$t.common.useAllParams}
                  title={$t.common.useAllParams}
                  on:click={(event) => handleGalleryAction(event, () => onUseAll(image))}
                >
                  <SlidersHorizontal aria-hidden="true" />
                </button>
                <button
                  type="button"
                  class="gallery-icon-action control-focus border-stone-300 text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
                  aria-pressed={image.favorite}
                  aria-label={image.favorite ? $t.common.unfavorite : $t.common.favorite}
                  title={image.favorite ? $t.common.unfavorite : $t.common.favorite}
                  on:click={(event) =>
                    handleGalleryAction(event, () => {
                      popFavorite(image);
                      onFavorite(image);
                    })}
                >
                  <Star aria-hidden="true" class={poppedFavoriteId === image.id ? 'favorite-pop' : ''} />
                </button>
                <button
                  type="button"
                  class="gallery-icon-action control-focus border-stone-300 text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
                  aria-label={$t.common.edit}
                  title={$t.common.edit}
                  on:click={(event) => handleGalleryAction(event, () => onEdit(image))}
                >
                  <Pencil aria-hidden="true" />
                </button>
                <a
                  href={`/api/download/${encodeURIComponent(image.filename)}`}
                  class="gallery-icon-action control-focus border-stone-300 text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
                  aria-label={$t.common.download}
                  title={$t.common.download}
                  on:click|stopPropagation
                >
                  <Download aria-hidden="true" />
                </a>
                {#if canNodeImageUpload}
                  <button
                    type="button"
                    class="gallery-icon-action control-focus border-stone-300 text-stone-700 hover:bg-stone-100 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
                    disabled={busy}
                    aria-label={$t.gallery.uploadToNodeImage}
                    title={$t.gallery.uploadToNodeImage}
                    on:click={(event) => handleGalleryAction(event, () => onNodeImageUpload(image))}
                  >
                    <CloudUpload aria-hidden="true" />
                  </button>
                {/if}
                <button
                  type="button"
                  class="gallery-icon-action control-focus border-red-500/40 text-red-700 hover:bg-red-500/10 dark:text-red-300"
                  aria-label={$t.common.delete}
                  title={$t.common.delete}
                  on:click={(event) => handleGalleryAction(event, () => onDelete(image))}
                >
                  <Trash2 aria-hidden="true" />
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
