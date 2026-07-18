<script lang="ts">
import type { AssistantGalleryMetadataResponse } from '$lib/api/types/assistant';
import type { GalleryEntry } from '$lib/api/types/gallery';
  import { t } from '$lib/i18n';
  import {
    displayImageSize,
    downloadUrl,
    formatBeijingTime,
    imageUrl,
    thumbnailUrl
  } from '$lib/utils/format';
  import { dialog } from '$lib/actions/dialog';

  export let open = false;
  export let image: GalleryEntry | null = null;
  export let onClose: () => void = () => {};
  export let onEdit: (image: GalleryEntry) => void = () => {};
  export let onFavorite: (image: GalleryEntry) => void = () => {};
  export let onDelete: (image: GalleryEntry) => void = () => {};
  export let onCopyPrompt: (image: GalleryEntry) => void = () => {};
  export let onCopyUrl: (image: GalleryEntry) => void = () => {};
  export let onUsePrompt: (image: GalleryEntry) => void = () => {};
  export let onUseAll: (image: GalleryEntry) => void = () => {};
  export let aiAssistantEnabled = false;
  export let aiMetadata: AssistantGalleryMetadataResponse | null = null;
  export let aiLoadingImageId: string | null = null;
  export let onAiDescribe: (image: GalleryEntry) => void = () => {};
  export let onAiAnalyze: (image: GalleryEntry) => void = () => {};
  export let canNavigatePrevious = false;
  export let canNavigateNext = false;
  export let navigating = false;
  export let onNavigatePrevious: () => void = () => {};
  export let onNavigateNext: () => void = () => {};

  const SWIPE_MIN_DISTANCE = 52;
  const SWIPE_MAX_DURATION_MS = 800;
  const SWIPE_AXIS_RATIO = 1.25;
  const SWIPE_IGNORE_SELECTOR = 'a, button, input, select, textarea, [role="button"], [data-swipe-ignore]';

  let swipePointerId: number | null = null;
  let swipeStartX = 0;
  let swipeStartY = 0;
  let swipeStartTime = 0;
  let loadedImageSrc = '';
  let failedImageSrc = '';

  $: aiLoading = Boolean(image && aiLoadingImageId === image.id);
  $: fullImageSrc = image ? imageUrl(image.filename, image.image_url) : '';
  $: previewImageSrc = image?.thumbnail_status === 'ready'
    ? thumbnailUrl(image.filename, image.thumbnail_url)
    : '';
  $: fullImageLoaded = Boolean(fullImageSrc && loadedImageSrc === fullImageSrc);
  $: fullImageFailed = Boolean(fullImageSrc && failedImageSrc === fullImageSrc);

  function handleFullImageLoad() {
    loadedImageSrc = fullImageSrc;
    failedImageSrc = '';
  }

  function handleFullImageError() {
    failedImageSrc = fullImageSrc;
  }

  function resetSwipe() {
    swipePointerId = null;
    swipeStartX = 0;
    swipeStartY = 0;
    swipeStartTime = 0;
  }

  function canStartSwipe(event: PointerEvent) {
    const target = event.target;
    return (
      event.isPrimary &&
      event.pointerType !== 'mouse' &&
      !navigating &&
      (canNavigatePrevious || canNavigateNext) &&
      !(target instanceof Element && target.closest(SWIPE_IGNORE_SELECTOR))
    );
  }

  function handleSwipePointerDown(event: PointerEvent) {
    if (!canStartSwipe(event)) return;
    swipePointerId = event.pointerId;
    swipeStartX = event.clientX;
    swipeStartY = event.clientY;
    swipeStartTime = Date.now();
    (event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId);
  }

  function handleSwipePointerUp(event: PointerEvent) {
    if (swipePointerId !== event.pointerId) return;

    const dx = event.clientX - swipeStartX;
    const dy = event.clientY - swipeStartY;
    const absX = Math.abs(dx);
    const absY = Math.abs(dy);
    const elapsed = Date.now() - swipeStartTime;
    const horizontalSwipe = absX >= SWIPE_MIN_DISTANCE && absX > absY * SWIPE_AXIS_RATIO && elapsed <= SWIPE_MAX_DURATION_MS;

    if (horizontalSwipe && dx < 0 && canNavigateNext) onNavigateNext();
    else if (horizontalSwipe && dx > 0 && canNavigatePrevious) onNavigatePrevious();

    (event.currentTarget as HTMLElement).releasePointerCapture?.(event.pointerId);
    resetSwipe();
  }
</script>

{#if open && image}
  <div class="mobile-lightbox-root fixed inset-0 z-[70] flex items-center justify-center bg-black/75 p-4">
    <button class="absolute inset-0" type="button" tabindex="-1" aria-label={$t.lightbox.closeLabel} on:click={onClose}></button>
    <div
      class="lightbox-shell relative"
      aria-labelledby="lightbox-title"
      use:dialog={{ open, onClose }}
    >
      <div
        class="lightbox-media"
        role="group"
        aria-label={$t.lightbox.title}
        on:pointerdown={handleSwipePointerDown}
        on:pointerup={handleSwipePointerUp}
        on:pointercancel={resetSwipe}
      >
        <div class="flex h-full min-h-0 w-full flex-col">
          <div class="flex min-h-0 flex-1 items-center justify-center">
            {#key fullImageSrc}
              <div class="lightbox-image-stage">
                {#if previewImageSrc}
                  <img
                    src={previewImageSrc}
                    alt=""
                    aria-hidden="true"
                    class:lightbox-preview-hidden={fullImageLoaded}
                    class="lightbox-preview-img"
                    decoding="async"
                  />
                {/if}
                {#if !fullImageLoaded}
                  <div class:lightbox-load-failed={fullImageFailed} class="lightbox-load-status" role="status">
                    {#if !fullImageFailed}
                      <span class="lightbox-load-track" aria-hidden="true"><span></span></span>
                    {/if}
                    <span>{fullImageFailed ? $t.lightbox.originalLoadFailed : $t.lightbox.loadingOriginal}</span>
                  </div>
                {/if}
                <img
                  src={fullImageSrc}
                  alt={image.prompt}
                  class:lightbox-img-loaded={fullImageLoaded}
                  class="lightbox-img"
                  decoding="async"
                  fetchpriority="high"
                  width={image.image_width || undefined}
                  height={image.image_height || undefined}
                  on:load={handleFullImageLoad}
                  on:error={handleFullImageError}
                />
              </div>
            {/key}
          </div>
          <div class="mt-4 flex h-10 shrink-0 items-center justify-between">
            {#if canNavigatePrevious}
              <button
                type="button"
                class="mobile-touch-target control-focus inline-flex h-10 w-10 items-center justify-center rounded-lg border border-stone-300 text-lg leading-none text-stone-700 transition-colors hover:bg-stone-100 hover:text-stone-950 disabled:cursor-not-allowed disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
                aria-label={$t.lightbox.previousImage}
                disabled={navigating}
                on:click={onNavigatePrevious}
              >
                <span aria-hidden="true">&larr;</span>
              </button>
            {:else}
              <span class="h-10 w-10" aria-hidden="true"></span>
            {/if}

            {#if canNavigateNext}
              <button
                type="button"
                class="mobile-touch-target control-focus inline-flex h-10 w-10 items-center justify-center rounded-lg border border-stone-300 text-lg leading-none text-stone-700 transition-colors hover:bg-stone-100 hover:text-stone-950 disabled:cursor-not-allowed disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
                aria-label={$t.lightbox.nextImage}
                disabled={navigating}
                on:click={onNavigateNext}
              >
                <span aria-hidden="true">&rarr;</span>
              </button>
            {:else}
              <span class="h-10 w-10" aria-hidden="true"></span>
            {/if}
          </div>
        </div>
      </div>
      <aside class="lightbox-details flex min-h-0 flex-col">
        <div class="flex items-start justify-between gap-3 border-b border-stone-200 p-5 dark:border-zinc-800">
          <div class="min-w-0">
            <h2 id="lightbox-title" class="text-sm font-semibold text-stone-950 dark:text-zinc-100">{$t.lightbox.title}</h2>
            <p class="mt-1 truncate text-xs text-stone-500 dark:text-zinc-500">{image.filename}</p>
          </div>
          <button type="button" class="mobile-touch-target control-focus rounded-lg p-1.5 text-stone-500 hover:bg-stone-100 hover:text-stone-950 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100" aria-label={$t.lightbox.closeLabel} on:click={onClose}>x</button>
        </div>
        <div class="min-h-0 flex-1 space-y-4 overflow-y-auto p-5">
          <div>
            <div class="mb-1 text-xs font-medium text-stone-500 dark:text-zinc-500">{$t.common.prompt}</div>
            <p class="whitespace-pre-wrap text-sm text-stone-800 dark:text-zinc-200">{image.prompt}</p>
          </div>
          {#if aiAssistantEnabled}
            <section class="rounded-lg border border-stone-200 bg-stone-50/80 p-3 dark:border-zinc-800 dark:bg-zinc-950/50">
              <div class="mb-3 flex items-center justify-between gap-3">
                <h3 class="text-xs font-semibold text-stone-700 dark:text-zinc-300">{$t.aiAssistant.title}</h3>
                {#if aiLoading}
                  <span class="text-[11px] text-zinc-500">{$t.aiAssistant.working}</span>
                {/if}
              </div>
              <div class="grid grid-cols-2 gap-2">
                <button type="button" disabled={aiLoading} class="control-focus rounded-lg border border-zinc-700 px-2 py-2 text-xs text-zinc-300 hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50" on:click={() => onAiDescribe(image)}>
                  {$t.lightbox.aiDescribe}
                </button>
                <button type="button" disabled={aiLoading} class="control-focus rounded-lg border border-cyan-500/35 px-2 py-2 text-xs text-cyan-700 hover:bg-cyan-500/10 disabled:cursor-not-allowed disabled:opacity-50 dark:text-cyan-200" on:click={() => onAiAnalyze(image)}>
                  {$t.lightbox.aiAnalyze}
                </button>
              </div>
              {#if aiMetadata && aiMetadata.image_id === image.id && (aiMetadata.description || aiMetadata.prompt)}
                <div class="mt-3 space-y-3 text-xs leading-5 text-stone-700 dark:text-zinc-300">
                  {#if aiMetadata.description}
                    <div>
                      <div class="mb-1 font-semibold text-stone-500 dark:text-zinc-500">{$t.lightbox.aiDescription}</div>
                      <p class="whitespace-pre-wrap">{aiMetadata.description}</p>
                    </div>
                  {/if}
                  {#if aiMetadata.prompt}
                    <div>
                      <div class="mb-1 font-semibold text-stone-500 dark:text-zinc-500">{$t.lightbox.aiPromptResult}</div>
                      <p class="whitespace-pre-wrap">{aiMetadata.prompt}</p>
                    </div>
                  {/if}
                </div>
              {/if}
            </section>
          {/if}
          <div class="grid grid-cols-2 gap-2 text-xs">
            <div class="rounded-lg border border-stone-200 bg-stone-50/80 px-3 py-2 dark:border-zinc-800 dark:bg-zinc-950/50">
              <div class="text-stone-400 dark:text-zinc-600">{$t.common.size}</div>
              <div class="mt-1 text-stone-700 dark:text-zinc-300">{displayImageSize(image)}</div>
            </div>
            <div class="rounded-lg border border-stone-200 bg-stone-50/80 px-3 py-2 dark:border-zinc-800 dark:bg-zinc-950/50">
              <div class="text-stone-400 dark:text-zinc-600">{$t.common.model}</div>
              <div class="mt-1 truncate text-stone-700 dark:text-zinc-300">{image.model || '-'}</div>
            </div>
            <div class="rounded-lg border border-stone-200 bg-stone-50/80 px-3 py-2 dark:border-zinc-800 dark:bg-zinc-950/50">
              <div class="text-stone-400 dark:text-zinc-600">{$t.common.completedAt}</div>
              <div class="mt-1 whitespace-nowrap text-stone-700 dark:text-zinc-300">{formatBeijingTime(image.completed_at)}</div>
            </div>
            <div class="rounded-lg border border-stone-200 bg-stone-50/80 px-3 py-2 dark:border-zinc-800 dark:bg-zinc-950/50">
              <div class="text-stone-400 dark:text-zinc-600">{$t.common.preset}</div>
              <div class="mt-1 truncate text-stone-700 dark:text-zinc-300">{image.api_preset_name || '-'}</div>
            </div>
            <div class="rounded-lg border border-stone-200 bg-stone-50/80 px-3 py-2 dark:border-zinc-800 dark:bg-zinc-950/50">
              <div class="text-stone-400 dark:text-zinc-600">{$t.common.duration}</div>
              <div class="mt-1 text-stone-700 dark:text-zinc-300">{image.duration || '-'}</div>
            </div>
          </div>
        </div>
        <div class="lightbox-details-actions grid grid-cols-2 gap-2 border-t border-stone-200 p-5 dark:border-zinc-800">
          <button type="button" class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-xs text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" on:click={() => onEdit(image)}>{$t.common.edit}</button>
          <button type="button" class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-xs text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" on:click={() => onFavorite(image)}>{image.favorite ? $t.common.unfavorite : $t.common.favorite}</button>
          <button type="button" class="control-focus rounded-lg border border-emerald-500/40 px-3 py-2 text-xs text-emerald-700 hover:bg-emerald-500/10 dark:text-emerald-200" on:click={() => onUsePrompt(image)}>{$t.common.usePrompt}</button>
          <button type="button" class="control-focus rounded-lg border border-emerald-500/40 px-3 py-2 text-xs text-emerald-700 hover:bg-emerald-500/10 dark:text-emerald-200" on:click={() => onUseAll(image)}>{$t.common.useAllParams}</button>
          <button type="button" class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-xs text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" on:click={() => onCopyPrompt(image)}>{$t.common.copyPrompt}</button>
          <button type="button" class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-xs text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" on:click={() => onCopyUrl(image)}>{$t.common.copyUrl}</button>
          <a href={downloadUrl(image.filename)} class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-center text-xs text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800">{$t.common.download}</a>
          <button type="button" class="control-focus rounded-lg border border-red-500/40 px-3 py-2 text-xs text-red-300 hover:bg-red-500/10" on:click={() => onDelete(image)}>{$t.common.delete}</button>
        </div>
      </aside>
    </div>
  </div>
{/if}
