<script lang="ts">
  import type { GalleryEntry } from '$lib/api/types/gallery';
  import { dialog } from '$lib/actions/dialog';
  import { t } from '$lib/i18n';

  export let open = false;
  export let image: GalleryEntry | null = null;
  export let onChoose: (reusePrompt: boolean) => void = () => {};
  export let onClose: () => void = () => {};
</script>

{#if open && image}
  <div class="fixed inset-0 z-[90] flex items-center justify-center bg-stone-950/60 p-4 dark:bg-zinc-950/75">
    <button class="absolute inset-0" type="button" tabindex="-1" aria-label={$t.galleryEditDialog.closeLabel} on:click={onClose}></button>
    <div
      class="fade-in relative flex w-full max-w-md flex-col overflow-hidden rounded-2xl border border-stone-200 bg-white shadow-2xl shadow-stone-300/50 dark:border-zinc-800 dark:bg-zinc-900 dark:shadow-none"
      aria-labelledby="gallery-edit-dialog-title"
      aria-describedby="gallery-edit-dialog-subtitle"
      use:dialog={{ open, onClose }}
    >
      <div class="border-b border-stone-200 p-5 dark:border-zinc-800">
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <h2 id="gallery-edit-dialog-title" class="text-base font-semibold text-stone-950 dark:text-zinc-100">{$t.galleryEditDialog.title}</h2>
            <p id="gallery-edit-dialog-subtitle" class="mt-2 text-sm leading-6 text-stone-600 dark:text-zinc-400">{$t.galleryEditDialog.subtitle}</p>
            <p class="mt-3 truncate rounded-lg border border-stone-200 bg-stone-50 px-3 py-2 text-xs text-stone-600 dark:border-zinc-800 dark:bg-zinc-950/60 dark:text-zinc-400" title={image.filename}>
              {$t.galleryEditDialog.imageLabel(image.filename)}
            </p>
          </div>
          <button type="button" class="control-focus rounded-lg p-1.5 text-stone-500 hover:bg-stone-100 hover:text-stone-950 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100" aria-label={$t.galleryEditDialog.closeLabel} on:click={onClose}>x</button>
        </div>
      </div>

      <div class="flex flex-col gap-2 p-5">
        <button
          type="button"
          data-autofocus
          class="control-focus rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-4 py-3 text-left text-sm font-semibold text-emerald-800 hover:bg-emerald-500/15 dark:text-emerald-100"
          on:click={() => onChoose(true)}
        >
          {$t.galleryEditDialog.reusePrompt}
        </button>
        <button
          type="button"
          class="control-focus rounded-lg border border-stone-300 px-4 py-3 text-left text-sm font-semibold text-stone-800 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-100 dark:hover:bg-zinc-800"
          on:click={() => onChoose(false)}
        >
          {$t.galleryEditDialog.clearPrompt}
        </button>
      </div>

      <div class="flex justify-end border-t border-stone-200 p-5 dark:border-zinc-800">
        <button type="button" class="control-focus rounded-lg border border-stone-300 px-4 py-2 text-sm text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" on:click={onClose}>
          {$t.galleryEditDialog.cancel}
        </button>
      </div>
    </div>
  </div>
{/if}
