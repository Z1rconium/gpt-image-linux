<script lang="ts">
  import { Upload, X } from 'lucide-svelte';
  import { t } from '$lib/i18n';
  import { MAX_EDIT_SOURCE_IMAGES } from '$lib/stores/editSource';

  export let sources: { id: string; label: string; previewUrl: string; kind: 'upload' | 'gallery' }[] = [];
  export let onChange: (event: Event) => void = () => {};
  export let onDropFiles: (files: File[]) => void = () => {};
  export let onPreview: (sourceId: string) => void = () => {};
  export let onRemove: (sourceId: string) => void = () => {};
  export let onClear: () => void = () => {};

  let input: HTMLInputElement;
  let dragDepth = 0;
  let isDraggingFiles = false;

  export function openPicker() {
    input?.click();
  }

  export function reset() {
    if (input) input.value = '';
  }

  function hasFiles(event: DragEvent) {
    return Boolean(event.dataTransfer?.files?.length) || Array.from(event.dataTransfer?.types || []).includes('Files');
  }

  function handleDragEnter(event: DragEvent) {
    if (!hasFiles(event)) return;
    event.preventDefault();
    dragDepth += 1;
    isDraggingFiles = true;
  }

  function handleDragOver(event: DragEvent) {
    if (!hasFiles(event)) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
    isDraggingFiles = true;
  }

  function handleDragLeave(event: DragEvent) {
    if (!hasFiles(event)) return;
    event.preventDefault();
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) isDraggingFiles = false;
  }

  function handleDrop(event: DragEvent) {
    if (!hasFiles(event)) return;
    event.preventDefault();
    dragDepth = 0;
    isDraggingFiles = false;
    const files = Array.from(event.dataTransfer?.files || []);
    if (files.length) onDropFiles(files);
  }
</script>

<div>
  <p class="mb-1.5 text-xs font-medium text-stone-600 dark:text-zinc-400">
    {$t.promptForm.editSourceDropLabel}
  </p>
  <div
    class={`min-w-0 rounded-xl border-2 border-dashed p-3 transition-all ${
      isDraggingFiles
        ? 'cursor-copy border-emerald-500 bg-emerald-500/10 shadow-inner'
        : 'border-stone-300/90 bg-stone-50/50 hover:border-stone-400 dark:border-zinc-800 dark:bg-zinc-950/30 dark:hover:border-zinc-700'
    }`}
    data-dragging={isDraggingFiles}
    role="group"
    aria-label={$t.promptForm.editSourceDropLabel}
    on:dragenter={handleDragEnter}
    on:dragover={handleDragOver}
    on:dragleave={handleDragLeave}
    on:drop={handleDrop}
  >
  <input
    bind:this={input}
    type="file"
    multiple
    accept="image/png,image/jpeg,image/webp,image/gif,image/avif,image/bmp,image/heic,image/heif,image/x-icon,image/tiff"
    aria-label={$t.promptForm.uploadEditImage}
    class="hidden"
    on:change={onChange}
  />
  <button
    type="button"
    class="control-focus flex min-h-[58px] w-full items-center justify-center gap-3.5 rounded-lg px-3 py-2 text-left transition-colors hover:bg-stone-100/80 dark:hover:bg-zinc-900/80"
    on:click={openPicker}
  >
    <span class="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-stone-200 bg-white text-stone-600 shadow-sm dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300" aria-hidden="true">
      <Upload size={18} strokeWidth={1.9} />
    </span>
    <span class="min-w-0">
      <span class="block text-sm font-medium text-stone-800 dark:text-zinc-200">
        {isDraggingFiles ? $t.promptForm.editSourceDropActive : $t.promptForm.uploadEditImage}
      </span>
      <span class="mt-0.5 block text-xs text-stone-500 dark:text-zinc-400">
        {$t.promptForm.editSourceDropHint}
      </span>
    </span>
  </button>
  {#if sources.length}
    <div class="mt-3 flex min-h-10 items-center justify-between gap-3 border-t border-stone-200/80 pt-2.5 dark:border-zinc-800/80">
      <p class="min-w-0 text-xs font-medium text-stone-600 dark:text-zinc-400" aria-live="polite">
        {$t.promptForm.editSourceCount(sources.length, MAX_EDIT_SOURCE_IMAGES)}
      </p>
      {#if sources.length >= 2}
        <button
          type="button"
          class="control-focus min-h-8 shrink-0 rounded-md px-2.5 py-1 text-xs font-medium text-stone-600 hover:bg-stone-200/60 hover:text-stone-950 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
          aria-label={$t.promptForm.clearEditSources}
          title={$t.promptForm.clearEditSources}
          on:click={onClear}
        >
          {$t.promptForm.clearAllEditSources}
        </button>
      {/if}
    </div>
    <ul class="mt-2 grid max-w-full grid-cols-1 gap-2.5 min-[480px]:grid-cols-2 sm:grid-cols-3 lg:grid-cols-4" aria-label={$t.promptForm.selectedEditSources}>
      {#each sources as source (source.id)}
        <li class="relative min-w-0 overflow-hidden rounded-lg border border-stone-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900" data-source-id={source.id} data-source-kind={source.kind}>
          <button
            type="button"
            class="control-focus flex w-full min-w-0 items-center gap-2.5 rounded-lg p-1.5 pr-11 text-left transition-colors hover:bg-stone-50 dark:hover:bg-zinc-800/80"
            aria-label={$t.promptForm.previewEditLabel(source.label)}
            title={$t.promptForm.previewEditLabel(source.label)}
            on:click={() => onPreview(source.id)}
          >
            <img src={source.previewUrl} alt="" class="h-12 w-12 shrink-0 rounded-md object-cover ring-1 ring-black/5 dark:ring-white/10" />
            <span class="min-w-0">
              <span class="block truncate text-xs font-medium text-stone-800 dark:text-zinc-200" title={source.label}>{source.label}</span>
              <span class="mt-0.5 inline-block rounded bg-stone-100 px-1.5 py-0.5 text-[10px] font-medium text-stone-600 dark:bg-zinc-800 dark:text-zinc-400">
                {source.kind === 'gallery' ? $t.promptForm.gallerySourceBadge : $t.promptForm.uploadSourceBadge}
              </span>
            </span>
          </button>
          <button
            type="button"
            class="control-focus absolute right-1.5 top-1.5 flex h-8 w-8 items-center justify-center rounded-md text-stone-400 hover:bg-red-50 hover:text-red-600 dark:text-zinc-500 dark:hover:bg-red-950/40 dark:hover:text-red-400"
            aria-label={$t.promptForm.removeEditLabel(source.label)}
            title={$t.promptForm.removeEditLabel(source.label)}
            on:click|stopPropagation={() => onRemove(source.id)}
          >
            <X size={16} strokeWidth={2} aria-hidden="true" />
          </button>
        </li>
      {/each}
    </ul>
  {/if}
  </div>
</div>
