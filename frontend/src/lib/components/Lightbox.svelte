<script lang="ts">
  import type { GalleryEntry } from '$lib/api/types';
  import { t } from '$lib/i18n';
  import { downloadUrl, formatBeijingTime, galleryImageSize, imageUrl } from '$lib/utils/format';
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
  export let canNavigatePrevious = false;
  export let canNavigateNext = false;
  export let navigating = false;
  export let onNavigatePrevious: () => void = () => {};
  export let onNavigateNext: () => void = () => {};
</script>

{#if open && image}
  <div class="fixed inset-0 z-[70] flex items-center justify-center bg-black/75 p-4">
    <button class="absolute inset-0" type="button" tabindex="-1" aria-label={$t.lightbox.closeLabel} on:click={onClose}></button>
    <div
      class="lightbox-shell relative"
      aria-labelledby="lightbox-title"
      use:dialog={{ open, onClose }}
    >
      <div class="lightbox-media">
        <div class="flex h-full min-h-0 w-full flex-col">
          <div class="flex min-h-0 flex-1 items-center justify-center">
            <img
              src={imageUrl(image.filename)}
              alt={image.prompt}
              class="lightbox-img"
              decoding="async"
              fetchpriority="high"
              width={image.image_width || undefined}
              height={image.image_height || undefined}
            />
          </div>
          <div class="mt-4 flex h-10 shrink-0 items-center justify-between">
            {#if canNavigatePrevious}
              <button
                type="button"
                class="control-focus inline-flex h-10 w-10 items-center justify-center rounded-lg border border-zinc-700 text-lg leading-none text-zinc-300 transition-colors hover:bg-zinc-800 hover:text-zinc-100 disabled:cursor-not-allowed disabled:opacity-40"
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
                class="control-focus inline-flex h-10 w-10 items-center justify-center rounded-lg border border-zinc-700 text-lg leading-none text-zinc-300 transition-colors hover:bg-zinc-800 hover:text-zinc-100 disabled:cursor-not-allowed disabled:opacity-40"
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
        <div class="flex items-start justify-between gap-3 border-b border-zinc-800 p-5">
          <div class="min-w-0">
            <h2 id="lightbox-title" class="text-sm font-semibold text-zinc-100">{$t.lightbox.title}</h2>
            <p class="mt-1 truncate text-xs text-zinc-500">{image.filename}</p>
          </div>
          <button type="button" class="control-focus rounded-lg p-1.5 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100" aria-label={$t.lightbox.closeLabel} on:click={onClose}>x</button>
        </div>
        <div class="min-h-0 flex-1 space-y-4 overflow-y-auto p-5">
          <div>
            <div class="mb-1 text-xs font-medium text-zinc-500">{$t.common.prompt}</div>
            <p class="whitespace-pre-wrap text-sm text-zinc-200">{image.prompt}</p>
          </div>
          <div class="grid grid-cols-2 gap-2 text-xs">
            <div class="rounded-lg border border-zinc-800 bg-zinc-950/50 px-3 py-2">
              <div class="text-zinc-600">{$t.common.size}</div>
              <div class="mt-1 text-zinc-300">{galleryImageSize(image)}</div>
            </div>
            <div class="rounded-lg border border-zinc-800 bg-zinc-950/50 px-3 py-2">
              <div class="text-zinc-600">{$t.common.model}</div>
              <div class="mt-1 truncate text-zinc-300">{image.model || '-'}</div>
            </div>
            <div class="rounded-lg border border-zinc-800 bg-zinc-950/50 px-3 py-2">
              <div class="text-zinc-600">{$t.common.completedAt}</div>
              <div class="mt-1 whitespace-nowrap text-zinc-300">{formatBeijingTime(image.completed_at)}</div>
            </div>
            <div class="rounded-lg border border-zinc-800 bg-zinc-950/50 px-3 py-2">
              <div class="text-zinc-600">{$t.common.preset}</div>
              <div class="mt-1 truncate text-zinc-300">{image.api_preset_name || '-'}</div>
            </div>
            <div class="rounded-lg border border-zinc-800 bg-zinc-950/50 px-3 py-2">
              <div class="text-zinc-600">{$t.common.duration}</div>
              <div class="mt-1 text-zinc-300">{image.duration || '-'}</div>
            </div>
          </div>
        </div>
        <div class="grid grid-cols-2 gap-2 border-t border-zinc-800 p-5">
          <button type="button" class="control-focus rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800" on:click={() => onEdit(image)}>{$t.common.edit}</button>
          <button type="button" class="control-focus rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800" on:click={() => onFavorite(image)}>{image.favorite ? $t.common.unfavorite : $t.common.favorite}</button>
          <button type="button" class="control-focus rounded-lg border border-emerald-500/40 px-3 py-2 text-xs text-emerald-200 hover:bg-emerald-500/10" on:click={() => onUsePrompt(image)}>{$t.common.usePrompt}</button>
          <button type="button" class="control-focus rounded-lg border border-emerald-500/40 px-3 py-2 text-xs text-emerald-200 hover:bg-emerald-500/10" on:click={() => onUseAll(image)}>{$t.common.useAllParams}</button>
          <button type="button" class="control-focus rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800" on:click={() => onCopyPrompt(image)}>{$t.common.copyPrompt}</button>
          <button type="button" class="control-focus rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800" on:click={() => onCopyUrl(image)}>{$t.common.copyUrl}</button>
          <a href={downloadUrl(image.filename)} class="control-focus rounded-lg border border-zinc-700 px-3 py-2 text-center text-xs text-zinc-300 hover:bg-zinc-800">{$t.common.download}</a>
          <button type="button" class="control-focus rounded-lg border border-red-500/40 px-3 py-2 text-xs text-red-300 hover:bg-red-500/10" on:click={() => onDelete(image)}>{$t.common.delete}</button>
        </div>
      </aside>
    </div>
  </div>
{/if}
