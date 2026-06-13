<script lang="ts">
  import type { GalleryEntry, GalleryResponse } from '$lib/api/types';
  import { t } from '$lib/i18n';
  import type { GalleryFilters, GalleryOperationStatus } from '$lib/stores/gallery';
  import { displayImageSize, formatBytes, imageUrl, thumbnailUrl } from '$lib/utils/format';

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

  const skeletonCards = Array.from({ length: 6 });

  let importInput: HTMLInputElement;
  let pageInput = '1';

  const EAGER_THUMB_COUNT = 6;

  $: images = gallery?.images || [];
  $: currentPage = gallery?.page || 1;
  $: totalPages = Math.max(gallery?.total_pages || 1, 1);
  $: pageInput = String(currentPage);
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

  function clampPage(page: number) {
    return Math.min(Math.max(page, 1), totalPages);
  }

  function commitPageInput() {
    const value = pageInput.trim();
    const requestedPage = /^\d+$/.test(value) ? Number.parseInt(value, 10) : Number.NaN;
    if (!Number.isFinite(requestedPage)) {
      pageInput = String(currentPage);
      return;
    }

    const nextPage = clampPage(requestedPage);
    pageInput = String(nextPage);
    if (nextPage !== currentPage) onPage(nextPage);
  }

  function handlePageInputKeydown(event: KeyboardEvent) {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    commitPageInput();
  }

  function handleSearchInput(event: Event) {
    const value = (event.currentTarget as HTMLInputElement).value;
    onFilter('prompt', value);
  }

  function galleryImageSrc(image: GalleryEntry) {
    return thumbnailReady(image) ? thumbnailUrl(image.filename, image.thumbnail_url) : imageUrl(image.filename, image.image_url);
  }

  function thumbnailSrcset(image: GalleryEntry) {
    const fullWidth = image.image_width && image.image_width > 512 ? image.image_width : 1024;
    return `${thumbnailUrl(image.filename, image.thumbnail_url)} 512w, ${imageUrl(image.filename, image.image_url)} ${fullWidth}w`;
  }

  function thumbnailReady(image: GalleryEntry) {
    return !image.thumbnail_status || image.thumbnail_status === 'ready';
  }

  function handleThumbnailError(event: Event, image: GalleryEntry) {
    const img = event.currentTarget as HTMLImageElement;
    const fallback = imageUrl(image.filename, image.image_url);
    const absoluteFallback = new URL(fallback, window.location.origin).href;
    if (img.dataset.fallbackSrc === fallback || img.getAttribute('src') === fallback || img.src === absoluteFallback) {
      img.removeAttribute('srcset');
      img.removeAttribute('src');
      img.classList.add('opacity-0');
      return;
    }
    img.dataset.fallbackSrc = fallback;
    img.srcset = '';
    img.src = fallback;
  }

  function isImageSelected(image: GalleryEntry) {
    return selectedAllFiltered || selectedIds.has(image.id);
  }
</script>

<section class="rounded-2xl border border-zinc-800 bg-zinc-900/60 p-4 sm:p-5">
  <div class="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
    <div>
      <h2 class="text-sm font-semibold text-zinc-100">{$t.gallery.title}</h2>
      <p class="mt-1 text-xs text-zinc-500">
        {gallery?.total ? $t.gallery.imageCount(gallery.total) : $t.gallery.noImages}
        {#if gallery?.total_bytes}
          <span class="ml-2">{formatBytes(gallery.total_bytes)}</span>
        {:else if gallery?.total}
          <button type="button" class="control-focus ml-2 rounded text-xs font-medium text-zinc-400 hover:text-zinc-200" on:click={onLoadStats}>
            {$t.gallery.showSize}
          </button>
        {/if}
      </p>
    </div>
    <div class="flex flex-wrap gap-2">
      <button type="button" class="control-focus rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800" on:click={() => onSelectionMode(!selectionMode)}>
        {selectionMode ? $t.gallery.cancelSelection : $t.gallery.select}
      </button>
      <button type="button" class="control-focus rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800 disabled:opacity-40" disabled={busy} on:click={() => importInput.click()}>
        {operationStatus?.kind === 'import' ? $t.gallery.importing : $t.gallery.import}
      </button>
      <button type="button" class="control-focus rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800 disabled:opacity-40" disabled={busy} on:click={onExport}>
        {operationStatus?.kind === 'export' ? $t.gallery.exporting : $t.gallery.exportZip}
      </button>
      <button
        type="button"
        class="control-focus rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800 disabled:opacity-40"
        disabled={busy || !canSyncR2}
        title={canSyncR2 ? $t.gallery.syncR2 : $t.messages.r2BackupUnavailable}
        on:click={onSync}
      >
        {operationStatus?.kind === 'sync' ? $t.gallery.syncing : $t.gallery.syncR2}
      </button>
      <button type="button" class="control-focus rounded-lg border border-red-500/40 px-3 py-2 text-xs text-red-300 hover:bg-red-500/10" on:click={onDeleteAll}>
        {$t.gallery.deleteAll}
      </button>
      <input bind:this={importInput} type="file" accept=".zip,application/zip" class="hidden" on:change={importSelected} />
    </div>
  </div>

  <div class="mb-4 flex flex-wrap gap-2">
    <input
      type="search"
      name="gallery_prompt"
      value={filters.prompt}
      placeholder={$t.gallery.filterPrompt}
      autocomplete="off"
      aria-label={$t.gallery.filterPrompt}
      class="control-focus min-w-[160px] flex-1 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 focus:border-emerald-500"
      on:input={handleSearchInput}
    />
    <select value={filters.model} aria-label={$t.common.model} class="control-focus min-w-[140px] rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 focus:border-emerald-500" on:change={(event) => onFilter('model', event.currentTarget.value)}>
      <option value="">{$t.gallery.allModels}</option>
      {#each gallery?.filter_options.models || [] as model}
        <option value={model}>{model}</option>
      {/each}
    </select>
    <select value={filters.preset} aria-label={$t.common.preset} class="control-focus min-w-[140px] rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 focus:border-emerald-500" on:change={(event) => onFilter('preset', event.currentTarget.value)}>
      <option value="">{$t.gallery.allPresets}</option>
      {#each gallery?.filter_options.presets || [] as preset}
        <option value={preset}>{preset}</option>
      {/each}
    </select>
    <select value={filters.size} aria-label={$t.common.size} class="control-focus min-w-[120px] rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 focus:border-emerald-500" on:change={(event) => onFilter('size', event.currentTarget.value)}>
      <option value="">{$t.gallery.allSizes}</option>
      {#each gallery?.filter_options.sizes || [] as size}
        <option value={size}>{size}</option>
      {/each}
    </select>
    <input
      type="date"
      value={filters.dateFrom}
      aria-label={$t.gallery.dateFrom}
      placeholder={$t.gallery.dateFrom}
      class="control-focus w-[135px] rounded-lg border border-zinc-800 bg-zinc-950 px-2 py-2 text-sm text-zinc-100 focus:border-emerald-500"
      on:change={(event) => onFilter('dateFrom', event.currentTarget.value)}
    />
    <input
      type="date"
      value={filters.dateTo}
      aria-label={$t.gallery.dateTo}
      placeholder={$t.gallery.dateTo}
      class="control-focus w-[135px] rounded-lg border border-zinc-800 bg-zinc-950 px-2 py-2 text-sm text-zinc-100 focus:border-emerald-500"
      on:change={(event) => onFilter('dateTo', event.currentTarget.value)}
    />
    <label class="flex items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-300">
      <input type="checkbox" class="control-focus accent-emerald-500" checked={filters.favorite} on:change={(event) => onFilter('favorite', event.currentTarget.checked)} />
      {$t.gallery.favorites}
    </label>
  </div>

  {#if hasFilters}
    <button type="button" class="control-focus mb-4 rounded text-xs font-medium text-emerald-300 hover:text-emerald-200" on:click={onResetFilters}>{$t.gallery.resetFilters}</button>
  {/if}

  {#if selectionMode}
    <div class="mb-4 flex flex-col gap-3 rounded-xl border border-emerald-500/30 bg-emerald-500/5 p-3 sm:flex-row sm:items-center sm:justify-between">
      <div class="text-xs font-medium text-emerald-200">{selectionSummary}</div>
      <div class="flex flex-wrap gap-2">
        <button type="button" class="control-focus rounded-lg border border-zinc-700 px-2.5 py-2 text-xs text-zinc-300 hover:bg-zinc-800" on:click={onSelectPage}>{$t.gallery.selectAllPage}</button>
        <button type="button" class="control-focus rounded-lg border border-zinc-700 px-2.5 py-2 text-xs text-zinc-300 hover:bg-zinc-800 disabled:opacity-40" disabled={!gallery?.total || busy} on:click={onSelectFiltered}>{$t.gallery.selectFiltered}</button>
        <button type="button" class="control-focus rounded-lg border border-zinc-700 px-2.5 py-2 text-xs text-zinc-300 hover:bg-zinc-800 disabled:opacity-40" disabled={!hasSelection} on:click={onClearSelection}>{$t.gallery.clearSelection}</button>
        <button type="button" class="control-focus rounded-lg border border-zinc-700 px-2.5 py-2 text-xs text-zinc-300 hover:bg-zinc-800 disabled:opacity-40" disabled={!hasSelection || busy} on:click={onBatchDownload}>{operationStatus?.kind === 'download' ? $t.gallery.downloading : $t.gallery.downloadSelected}</button>
        <button type="button" class="control-focus rounded-lg border border-zinc-700 px-2.5 py-2 text-xs text-zinc-300 hover:bg-zinc-800 disabled:opacity-40" disabled={!hasSelection || busy} on:click={() => onBatchFavorite(true)}>{$t.gallery.favoriteSelected}</button>
        <button type="button" class="control-focus rounded-lg border border-zinc-700 px-2.5 py-2 text-xs text-zinc-300 hover:bg-zinc-800 disabled:opacity-40" disabled={!hasSelection || busy} on:click={() => onBatchFavorite(false)}>{$t.gallery.unfavoriteSelected}</button>
        <button type="button" class="control-focus rounded-lg border border-red-500/40 px-2.5 py-2 text-xs text-red-300 hover:bg-red-500/10 disabled:opacity-40" disabled={!hasSelection || busy} on:click={onBatchDelete}>{$t.gallery.deleteSelected}</button>
      </div>
    </div>
  {/if}

  {#if operationStatus}
    <div class="mb-4 rounded-xl border border-sky-500/30 bg-sky-500/10 p-3" role="status" aria-live="polite">
      <div class="flex items-start justify-between gap-3">
        <div>
          <p class="text-xs font-semibold text-sky-100">{operationStatus.label}</p>
          <p class="mt-1 text-xs text-sky-200/80">{operationStatus.detail}</p>
        </div>
        <div class="text-xs text-sky-200">{operationStatus.progress === null ? $t.gallery.notInterruptible : `${operationStatus.progress}%`}</div>
      </div>
      <div class="mt-3 h-1.5 overflow-hidden rounded-full bg-sky-950/70">
        {#if operationStatus.progress === null}
          <div class="h-full w-1/3 animate-pulse rounded-full bg-sky-300"></div>
        {:else}
          <div class="h-full rounded-full bg-sky-300 transition-[width]" style={`width: ${Number(operationStatus.progress) || 0}%`}></div>
        {/if}
      </div>
    </div>
  {/if}

  {#if initialLoading}
    <div class="grid gap-4 sm:grid-cols-2 md:gap-5 lg:grid-cols-3 lg:gap-4" aria-label={$t.gallery.loading}>
      {#each skeletonCards as _}
        <div class="overflow-hidden rounded-xl border border-zinc-800 bg-zinc-950/45">
          <div class="aspect-square animate-pulse bg-zinc-800/60"></div>
          <div class="space-y-3 p-3">
            <div class="h-4 w-5/6 animate-pulse rounded bg-zinc-800/70"></div>
            <div class="h-3 w-1/2 animate-pulse rounded bg-zinc-800/60"></div>
            <div class="flex gap-2">
              <div class="h-7 w-14 animate-pulse rounded bg-zinc-800/60"></div>
              <div class="h-7 w-16 animate-pulse rounded bg-zinc-800/60"></div>
            </div>
          </div>
        </div>
      {/each}
    </div>
  {:else if images.length === 0}
    <div class="rounded-xl border border-dashed border-zinc-800 bg-zinc-950/35 px-4 py-10 text-center">
      <p class="text-sm font-medium text-zinc-300">{hasFilters ? $t.gallery.noMatch : $t.gallery.empty}</p>
      <p class="mt-2 text-xs text-zinc-500">{hasFilters ? $t.gallery.noMatchHint : $t.gallery.emptyHint}</p>
    </div>
  {:else}
    <div class="relative" aria-busy={loading}>
      {#if loading}
        <div class="pointer-events-none absolute inset-0 z-10 rounded-xl bg-zinc-950/20 backdrop-blur-[1px]">
          <div class="absolute right-3 top-3 rounded-lg border border-zinc-700 bg-zinc-950/90 px-3 py-2 text-xs text-zinc-300 shadow-lg">
            {$t.gallery.loading}
          </div>
        </div>
      {/if}

      <div class={`grid gap-4 sm:grid-cols-2 md:gap-5 lg:grid-cols-3 lg:gap-4 ${loading ? 'opacity-70' : ''}`}>
        {#each images as image, index (image.id)}
          <article class={`gallery-card overflow-hidden rounded-xl border ${isImageSelected(image) ? 'border-emerald-400 bg-emerald-500/10' : 'border-zinc-800 bg-zinc-950/45'}`}>
            <button type="button" class="control-focus relative block aspect-square w-full bg-zinc-950" aria-label={image.prompt} on:click={() => handleImageClick(image)}>
              {#if selectionMode}
                <span class="absolute left-2 top-2 z-10 rounded-md bg-zinc-950/80 px-2 py-1 text-xs font-medium text-zinc-100">
                  {isImageSelected(image) ? '✓' : ''}
                </span>
              {/if}
              <picture class="block h-full w-full">
                {#if thumbnailReady(image)}
                  <source
                    media="(max-width: 1023px)"
                    srcset={thumbnailSrcset(image)}
                    sizes="(max-width: 639px) calc(100vw - 32px), calc((100vw - 64px) / 2)"
                  />
                {/if}
                <img
                  src={galleryImageSrc(image)}
                  alt={image.prompt}
                  class="h-full w-full object-cover"
                  loading={index < EAGER_THUMB_COUNT ? 'eager' : 'lazy'}
                  fetchpriority={index < EAGER_THUMB_COUNT ? 'high' : 'auto'}
                  decoding="async"
                  width={image.image_width || undefined}
                  height={image.image_height || undefined}
                  on:error={(event) => handleThumbnailError(event, image)}
                />
              </picture>
            </button>
            <div class="space-y-2 p-2.5">
              <div class="min-w-0">
                <p class="line-clamp-2 text-xs leading-5 text-zinc-200">{image.prompt}</p>
                <p class="mt-1 truncate text-[11px] leading-4 text-zinc-500">{displayImageSize(image)} / {image.model || '-'}</p>
              </div>
              <div class="grid grid-cols-6 gap-1">
                <button
                  type="button"
                  class="gallery-icon-action control-focus border-emerald-500/40 text-emerald-200 hover:bg-emerald-500/10"
                  aria-label={$t.common.usePrompt}
                  title={$t.common.usePrompt}
                  on:click={(event) => handleGalleryAction(event, () => onUsePrompt(image))}
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4h10a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z"/><path d="M8 8h8M8 12h8M8 16h5"/></svg>
                </button>
                <button
                  type="button"
                  class="gallery-icon-action control-focus border-emerald-500/40 text-emerald-200 hover:bg-emerald-500/10"
                  aria-label={$t.common.useAllParams}
                  title={$t.common.useAllParams}
                  on:click={(event) => handleGalleryAction(event, () => onUseAll(image))}
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/></svg>
                </button>
                <button
                  type="button"
                  class="gallery-icon-action control-focus border-zinc-700 text-zinc-300 hover:bg-zinc-800"
                  aria-label={$t.common.edit}
                  title={$t.common.edit}
                  on:click={(event) => handleGalleryAction(event, () => onEdit(image))}
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20h4l10.5-10.5a2.1 2.1 0 0 0-3-3L5 17v3Z"/><path d="m14 7 3 3"/></svg>
                </button>
                <button
                  type="button"
                  class="gallery-icon-action control-focus border-zinc-700 text-zinc-300 hover:bg-zinc-800"
                  aria-label={image.favorite ? $t.common.unfavorite : $t.common.favorite}
                  title={image.favorite ? $t.common.unfavorite : $t.common.favorite}
                  on:click={(event) => handleGalleryAction(event, () => onFavorite(image))}
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9L12 3Z"/></svg>
                </button>
                <a
                  href={`/api/download/${encodeURIComponent(image.filename)}`}
                  class="gallery-icon-action control-focus border-zinc-700 text-zinc-300 hover:bg-zinc-800"
                  aria-label={$t.common.download}
                  title={$t.common.download}
                  on:click|stopPropagation
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v10"/><path d="m8 10 4 4 4-4"/><path d="M5 20h14"/></svg>
                </a>
                <button
                  type="button"
                  class="gallery-icon-action control-focus border-red-500/40 text-red-300 hover:bg-red-500/10"
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

    <div class="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <button type="button" disabled={loading || !gallery?.has_prev} class="control-focus rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800 disabled:opacity-40" on:click={() => onPage(clampPage(currentPage - 1), 'prev')}>
        {$t.gallery.previous}
      </button>
      <label class="flex items-center justify-center gap-2 text-xs text-zinc-500">
        <span>{$t.gallery.pageInputPrefix}</span>
        <input
          type="number"
          min="1"
          max={totalPages}
          inputmode="numeric"
          value={pageInput}
          disabled={loading}
          aria-label={$t.gallery.jumpPageLabel}
          title={$t.gallery.jumpPageHint(totalPages)}
          class="control-focus h-9 w-16 rounded-lg border border-zinc-800 bg-zinc-950 px-2 text-center text-sm text-zinc-100 focus:border-emerald-500 disabled:opacity-50"
          on:input={(event) => (pageInput = event.currentTarget.value)}
          on:keydown={handlePageInputKeydown}
        />
        <span>{$t.gallery.pageInputSuffix(totalPages)}</span>
      </label>
      <button type="button" disabled={loading || !gallery?.has_next} class="control-focus rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800 disabled:opacity-40" on:click={() => onPage(clampPage(currentPage + 1), 'next')}>
        {$t.gallery.next}
      </button>
    </div>
  {/if}
</section>
