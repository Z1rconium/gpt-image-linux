<script lang="ts">
  import { onDestroy } from 'svelte';
  import { dialog } from '$lib/actions/dialog';
  import { language, t } from '$lib/i18n';
  import type { AssistantImagePromptResponse } from '$lib/api/types';
  import { assistantStore, isAbortError } from '$lib/stores/assistant';

  type MaybePromise = void | Promise<void>;

  export let open = false;
  export let available = false;
  export let onClose: () => void = () => {};
  export let onApply: (prompt: string) => MaybePromise = () => {};
  export let onSave: (prompt: string) => MaybePromise = () => {};
  export let onCopy: (prompt: string) => MaybePromise = () => {};

  const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;
  const MAX_IMAGE_PIXELS = 100_000_000;
  const SAFE_IMAGE_EXTENSIONS = /\.(avif|bmp|gif|heic|heif|ico|jpe?g|png|tiff?|webp)$/i;

  let input: HTMLInputElement | null = null;
  let selectedFile: File | null = null;
  let previewUrl = '';
  let result: AssistantImagePromptResponse | null = null;
  let error = '';
  let loading = false;
  let dragging = false;
  let requestController: AbortController | null = null;
  let selectionSequence = 0;
  let wasOpen = false;

  $: if (open !== wasOpen) {
    wasOpen = open;
    if (!open) resetState();
  }

  function revokePreview() {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = '';
  }

  function abortRequest() {
    requestController?.abort();
    requestController = null;
    loading = false;
  }

  function resetState() {
    selectionSequence += 1;
    abortRequest();
    revokePreview();
    selectedFile = null;
    result = null;
    error = '';
    dragging = false;
    if (input) input.value = '';
  }

  function clearSelection() {
    selectionSequence += 1;
    abortRequest();
    revokePreview();
    selectedFile = null;
    result = null;
    error = '';
    if (input) input.value = '';
  }

  function closeDialog() {
    resetState();
    onClose();
  }

  function supportedImage(file: File) {
    return file.type !== 'image/svg+xml' && !/\.svg$/i.test(file.name) && SAFE_IMAGE_EXTENSIONS.test(file.name);
  }

  function errorText(caught: unknown) {
    if (caught instanceof Error && caught.message) return caught.message;
    return $t.imagePrompt.formatError;
  }

  async function decodePreview(url: string, sequence: number) {
    try {
      const image = new Image();
      image.src = url;
      await image.decode();
      const width = image.naturalWidth;
      const height = image.naturalHeight;
      if (sequence !== selectionSequence) return;
      if (!width || !height || width * height > MAX_IMAGE_PIXELS) {
        throw new Error($t.imagePrompt.pixelLimit);
      }
    } catch (caught) {
      if (sequence !== selectionSequence) return;
      revokePreview();
      selectedFile = null;
      error = caught instanceof Error && caught.message === $t.imagePrompt.pixelLimit ? caught.message : $t.imagePrompt.formatError;
      if (input) input.value = '';
    }
  }

  function chooseFile(file: File | undefined) {
    if (!file) return;
    selectionSequence += 1;
    const sequence = selectionSequence;
    abortRequest();
    revokePreview();
    selectedFile = null;
    result = null;
    error = '';
    if (input) input.value = '';

    if (!supportedImage(file)) {
      error = $t.imagePrompt.formatError;
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      error = $t.imagePrompt.fileTooLarge;
      return;
    }

    selectedFile = file;
    previewUrl = URL.createObjectURL(file);
    void decodePreview(previewUrl, sequence);
  }

  function handleInput(event: Event) {
    chooseFile((event.currentTarget as HTMLInputElement).files?.[0]);
  }

  function handleDrop(event: DragEvent) {
    event.preventDefault();
    dragging = false;
    chooseFile(event.dataTransfer?.files?.[0]);
  }

  async function runPrompt() {
    if (!available || !selectedFile || loading) return;
    error = '';
    result = null;
    loading = true;
    requestController?.abort();
    const controller = new AbortController();
    requestController = controller;
    const sequence = selectionSequence;
    try {
      const response = await assistantStore.promptFromImage(selectedFile, $language, controller.signal);
      if (sequence === selectionSequence) result = response;
    } catch (caught) {
      if (!isAbortError(caught) && sequence === selectionSequence) error = errorText(caught);
    } finally {
      if (sequence === selectionSequence) loading = false;
      if (requestController === controller) requestController = null;
    }
  }

  async function applyResult() {
    if (!result?.prompt) return;
    await onApply(result.prompt);
    closeDialog();
  }

  async function saveResult() {
    if (!result?.prompt) return;
    try {
      await onSave(result.prompt);
      closeDialog();
    } catch (caught) {
      error = errorText(caught);
    }
  }

  async function copyResult() {
    if (result?.prompt) await onCopy(result.prompt);
  }

  onDestroy(() => {
    abortRequest();
    revokePreview();
  });
</script>

{#if open}
  <div class="mobile-dialog-root fixed inset-0 z-[85] flex items-center justify-center bg-black/75 p-4">
    <button class="absolute inset-0" type="button" tabindex="-1" aria-label={$t.imagePrompt.closeLabel} on:click={closeDialog}></button>
    <div
      data-testid="image-prompt-dialog"
      id="image-prompt-dialog"
      class="mobile-dvh-dialog relative flex max-h-[calc(100vh-32px)] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-stone-200 bg-stone-50 shadow-2xl dark:border-zinc-800 dark:bg-zinc-950"
      aria-labelledby="image-prompt-dialog-title"
      use:dialog={{ open, onClose: closeDialog }}
    >
      <div class="flex items-start justify-between gap-4 border-b border-stone-200 px-5 py-4 dark:border-zinc-800">
        <div class="min-w-0">
          <h2 id="image-prompt-dialog-title" class="text-lg font-semibold text-stone-950 dark:text-zinc-100">{$t.imagePrompt.title}</h2>
          <p class="mt-1 text-xs text-stone-500 dark:text-zinc-500">{$t.imagePrompt.subtitle}</p>
        </div>
        <button type="button" class="mobile-touch-target control-focus rounded-lg px-2 py-1 text-lg leading-none text-stone-500 hover:bg-stone-200 hover:text-stone-950 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100" aria-label={$t.imagePrompt.closeLabel} on:click={closeDialog}>x</button>
      </div>

      <div class="min-h-0 flex-1 overflow-y-auto p-5">
        {#if !available}
          <div class="mb-4 rounded-lg border border-amber-500/35 bg-amber-500/10 px-3 py-2.5 text-sm text-amber-800 dark:text-amber-200">{$t.imagePrompt.unavailable}</div>
        {/if}

        {#if !selectedFile}
          <button
            type="button"
            class:border-emerald-500={dragging}
            class="flex min-h-56 w-full flex-col items-center justify-center rounded-xl border border-dashed border-stone-300 bg-white px-5 py-8 text-center transition-colors hover:border-emerald-500/70 hover:bg-emerald-500/5 dark:border-zinc-700 dark:bg-zinc-900/50 dark:hover:border-emerald-500/60"
            on:click={() => input?.click()}
            on:dragover|preventDefault={() => (dragging = true)}
            on:dragleave={() => (dragging = false)}
            on:drop={handleDrop}
          >
            <span class="text-sm font-semibold text-stone-800 dark:text-zinc-200">{$t.imagePrompt.choose}</span>
            <span class="mt-2 text-xs text-stone-500 dark:text-zinc-500">{$t.imagePrompt.dropHint}</span>
            <span class="mt-4 text-[11px] text-stone-400 dark:text-zinc-600">{$t.imagePrompt.fileHint}</span>
          </button>
        {:else}
          <div class="grid gap-4 lg:grid-cols-[minmax(0,1fr)_240px]">
            <div class="flex min-h-64 items-center justify-center rounded-xl border border-stone-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900/50">
              {#if previewUrl}
                <img src={previewUrl} alt={selectedFile.name} class="max-h-[min(46vh,420px)] max-w-full rounded-lg object-contain" decoding="async" />
              {/if}
            </div>
            <div class="flex min-w-0 flex-col gap-3">
              <div class="rounded-lg border border-stone-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900/50">
                <div class="truncate text-sm font-medium text-stone-800 dark:text-zinc-200">{selectedFile.name}</div>
                <div class="mt-1 text-xs text-stone-500 dark:text-zinc-500">{(selectedFile.size / (1024 * 1024)).toFixed(1)} MB</div>
              </div>
              <button type="button" class="control-focus rounded-lg border border-stone-300 px-3 py-2.5 text-sm font-medium text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" on:click={() => input?.click()}>{$t.imagePrompt.replace}</button>
              <button type="button" class="control-focus rounded-lg border border-red-500/35 px-3 py-2.5 text-sm font-medium text-red-700 hover:bg-red-500/10 dark:text-red-200" on:click={clearSelection}>{$t.imagePrompt.remove}</button>
            </div>
          </div>
        {/if}

        <input bind:this={input} type="file" accept=".avif,.bmp,.gif,.heic,.heif,.ico,.jpg,.jpeg,.png,.tif,.tiff,.webp" class="hidden" aria-label={$t.imagePrompt.choose} on:change={handleInput} />

        {#if error}
          <div class="mt-4 rounded-lg border border-red-500/35 bg-red-500/10 px-3 py-2.5 text-sm text-red-800 dark:text-red-200">{error}</div>
        {/if}
        {#if loading}
          <div class="mt-4 rounded-lg border border-cyan-500/30 bg-cyan-500/10 px-3 py-2.5 text-sm text-cyan-800 dark:text-cyan-200" aria-live="polite">{$t.imagePrompt.loading}</div>
        {/if}
        {#if result}
          <section class="mt-4 rounded-xl border border-emerald-500/30 bg-emerald-500/5 p-4" aria-labelledby="image-prompt-result-title">
            <div class="flex items-center justify-between gap-3">
              <h3 id="image-prompt-result-title" class="text-sm font-semibold text-stone-900 dark:text-zinc-100">{$t.imagePrompt.result}</h3>
              <span class="text-[11px] text-stone-500 dark:text-zinc-500">{result.model} · {result.duration_ms} ms</span>
            </div>
            <p class="mt-3 whitespace-pre-wrap text-sm leading-6 text-stone-800 dark:text-zinc-200">{result.prompt}</p>
            {#if result.warnings.length}
              <div class="mt-3 space-y-1 text-xs text-amber-800 dark:text-amber-200">
                {#each result.warnings as warning}
                  <p>{warning}</p>
                {/each}
              </div>
            {/if}
          </section>
        {/if}
      </div>

      <div class="flex flex-wrap justify-end gap-2 border-t border-stone-200 px-5 py-4 dark:border-zinc-800">
        <button type="button" class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-sm text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" on:click={closeDialog}>{$t.common.close}</button>
        <button type="button" disabled={!available || !selectedFile || loading} class="control-focus rounded-lg border border-emerald-500/40 px-3 py-2 text-sm font-semibold text-emerald-700 hover:bg-emerald-500/10 disabled:cursor-not-allowed disabled:opacity-50 dark:text-emerald-200" on:click={runPrompt}>{loading ? $t.imagePrompt.loading : $t.imagePrompt.reverse}</button>
        {#if result}
          <button type="button" class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-sm text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" on:click={copyResult}>{$t.imagePrompt.copy}</button>
          <button type="button" class="control-focus rounded-lg border border-cyan-500/35 px-3 py-2 text-sm font-medium text-cyan-700 hover:bg-cyan-500/10 dark:text-cyan-200" on:click={saveResult}>{$t.imagePrompt.save}</button>
          <button type="button" class="control-focus rounded-lg bg-emerald-600 px-3 py-2 text-sm font-semibold text-white hover:bg-emerald-500" on:click={applyResult}>{$t.imagePrompt.apply}</button>
        {/if}
      </div>
    </div>
  </div>
{/if}
