<script lang="ts">
  import { onDestroy } from 'svelte';
  import { dialog } from '$lib/actions/dialog';
  import type { AssistantImagePromptOptimizeResponse, AssistantImagePromptResponse } from '$lib/api/types/assistant';
  import { language, t } from '$lib/i18n';
  import { assistantStore, isAbortError } from '$lib/stores/assistant';
  import { settingsStore } from '$lib/stores/settings';

  type MaybePromise = void | Promise<void>;
  type Operation = 'reverse' | 'optimize' | null;

  export let open = false;
  export let available = false;
  export let onClose: () => void = () => {};
  export let onApply: (prompt: string) => MaybePromise = () => {};
  export let onSave: (prompt: string) => MaybePromise = () => {};
  export let onCopy: (prompt: string) => MaybePromise = () => {};

  const FALLBACK_MAX_UPLOAD_BYTES = 50 * 1024 * 1024;
  const FALLBACK_MAX_IMAGE_PIXELS = 100_000_000;
  const SAFE_IMAGE_EXTENSIONS = /\.(avif|bmp|gif|heic|heif|ico|jpe?g|png|tiff?|webp)$/i;
  const GENERATIONS_API_PATH = '/v1/images/generations';

  let input: HTMLInputElement | null = null;
  let selectedFile: File | null = null;
  let previewUrl = '';
  let result: AssistantImagePromptResponse | null = null;
  let optimizedResult: AssistantImagePromptOptimizeResponse | null = null;
  let iterationCount = 0;
  let error = '';
  let operation: Operation = null;
  let dragging = false;
  let requestController: AbortController | null = null;
  let selectionSequence = 0;
  let wasOpen = false;

  $: maxUploadBytes = $settingsStore.settings?.image_upload_limits?.max_file_size_bytes || FALLBACK_MAX_UPLOAD_BYTES;
  $: maxImagePixels = $settingsStore.settings?.image_upload_limits?.max_image_pixels || FALLBACK_MAX_IMAGE_PIXELS;
  $: maxUploadSize = formatUploadSize(maxUploadBytes);
  $: busy = operation !== null;
  $: latestPrompt = optimizedResult?.prompt || result?.prompt || '';
  $: trialImageUrl = optimizedResult
    ? `data:${optimizedResult.temporary_image.mime_type};base64,${optimizedResult.temporary_image.b64}`
    : '';
  $: optimizationUnavailableReason = getOptimizationUnavailableReason();

  function formatUploadSize(bytes: number) {
    const megabytes = bytes / (1024 * 1024);
    return `${Number.isInteger(megabytes) ? megabytes : megabytes.toFixed(1)} MB`;
  }

  function getOptimizationUnavailableReason() {
    const settings = $settingsStore.settings;
    if (!settings) return $t.imagePrompt.optimizeNeedsSettings;
    if (settings.api_path !== GENERATIONS_API_PATH) return $t.imagePrompt.optimizePathMismatch(settings.api_path);
    if (!settings.api_url) return $t.imagePrompt.optimizeNeedsUrl;
    if (!settings.has_api_key) return $t.imagePrompt.optimizeNeedsKey;
    if (!settings.default_model) return $t.imagePrompt.optimizeNeedsModel;
    return '';
  }

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
    operation = null;
  }

  function clearResults() {
    result = null;
    optimizedResult = null;
    iterationCount = 0;
  }

  function resetState() {
    selectionSequence += 1;
    abortRequest();
    revokePreview();
    selectedFile = null;
    clearResults();
    error = '';
    dragging = false;
    if (input) input.value = '';
  }

  function clearSelection() {
    if (busy) return;
    selectionSequence += 1;
    abortRequest();
    revokePreview();
    selectedFile = null;
    clearResults();
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
      if (!width || !height || width * height > maxImagePixels) {
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
    if (!file || busy) return;
    selectionSequence += 1;
    const sequence = selectionSequence;
    abortRequest();
    revokePreview();
    selectedFile = null;
    clearResults();
    error = '';
    if (input) input.value = '';

    if (!supportedImage(file)) {
      error = $t.imagePrompt.formatError;
      return;
    }
    if (file.size > maxUploadBytes) {
      error = $t.imagePrompt.fileTooLarge(maxUploadSize);
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
    if (!available || !selectedFile || busy) return;
    error = '';
    clearResults();
    operation = 'reverse';
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
      if (sequence === selectionSequence) operation = null;
      if (requestController === controller) requestController = null;
    }
  }

  async function runOptimization() {
    if (!selectedFile || !latestPrompt || busy || optimizationUnavailableReason) return;
    error = '';
    operation = 'optimize';
    requestController?.abort();
    const controller = new AbortController();
    requestController = controller;
    const sequence = selectionSequence;
    try {
      const response = await assistantStore.optimizeImagePrompt(selectedFile, latestPrompt, $language, controller.signal);
      if (sequence === selectionSequence) {
        optimizedResult = response;
        iterationCount += 1;
      }
    } catch (caught) {
      if (!isAbortError(caught) && sequence === selectionSequence) error = errorText(caught);
    } finally {
      if (sequence === selectionSequence) operation = null;
      if (requestController === controller) requestController = null;
    }
  }

  async function applyResult() {
    if (!latestPrompt || busy) return;
    await onApply(latestPrompt);
    closeDialog();
  }

  async function saveResult() {
    if (!latestPrompt || busy) return;
    try {
      await onSave(latestPrompt);
      closeDialog();
    } catch (caught) {
      error = errorText(caught);
    }
  }

  async function copyResult() {
    if (latestPrompt && !busy) await onCopy(latestPrompt);
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
      role="dialog"
      aria-modal="true"
      aria-busy={busy}
      class="mobile-dvh-dialog relative flex max-h-[calc(100vh-32px)] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-stone-200 bg-stone-50 shadow-2xl dark:border-zinc-800 dark:bg-zinc-950"
      aria-labelledby="image-prompt-dialog-title"
      use:dialog={{ open, onClose: closeDialog }}
    >
      <div class="flex items-start justify-between gap-4 border-b border-stone-200 px-5 py-4 dark:border-zinc-800">
        <div class="min-w-0">
          <h2 id="image-prompt-dialog-title" class="text-lg font-semibold text-stone-950 dark:text-zinc-100">{$t.imagePrompt.title}</h2>
          <p class="mt-1 text-xs text-stone-500 dark:text-zinc-400">{$t.imagePrompt.subtitle}</p>
        </div>
        <button type="button" class="mobile-touch-target control-focus rounded-lg px-2 py-1 text-lg leading-none text-stone-500 hover:bg-stone-200 hover:text-stone-950 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100" aria-label={$t.imagePrompt.closeLabel} on:click={closeDialog}>x</button>
      </div>

      <div class="min-h-0 flex-1 overflow-x-hidden overflow-y-auto p-5">
        {#if !available}
          <div class="mb-4 rounded-lg border border-amber-500/35 bg-amber-500/10 px-3 py-2.5 text-sm text-amber-800 dark:text-amber-200">{$t.imagePrompt.unavailable}</div>
        {/if}

        {#if !selectedFile}
          <button
            type="button"
            disabled={busy}
            class:border-emerald-500={dragging}
            class="flex min-h-56 w-full flex-col items-center justify-center rounded-xl border border-dashed border-stone-300 bg-white px-5 py-8 text-center transition-colors hover:border-emerald-500/70 hover:bg-emerald-500/5 disabled:cursor-not-allowed disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-900/50 dark:hover:border-emerald-500/60"
            on:click={() => input?.click()}
            on:dragover|preventDefault={() => !busy && (dragging = true)}
            on:dragleave={() => (dragging = false)}
            on:drop={handleDrop}
          >
            <span class="text-sm font-semibold text-stone-800 dark:text-zinc-200">{$t.imagePrompt.choose}</span>
            <span class="mt-2 text-xs text-stone-500 dark:text-zinc-400">{$t.imagePrompt.dropHint}</span>
            <span class="mt-4 text-[11px] text-stone-500 dark:text-zinc-500">{$t.imagePrompt.fileHint(maxUploadSize)}</span>
          </button>
        {:else if optimizedResult}
          <div class="flex min-w-0 flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div class="min-w-0">
              <div class="truncate text-sm font-medium text-stone-800 dark:text-zinc-200">{selectedFile.name}</div>
              <div class="mt-1 text-xs text-stone-500 dark:text-zinc-400">{(selectedFile.size / (1024 * 1024)).toFixed(1)} MB</div>
            </div>
            <div class="flex shrink-0 gap-2">
              <button type="button" disabled={busy} class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-sm font-medium text-stone-700 hover:bg-stone-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" on:click={() => input?.click()}>{$t.imagePrompt.replace}</button>
              <button type="button" disabled={busy} class="control-focus rounded-lg border border-red-500/35 px-3 py-2 text-sm font-medium text-red-700 hover:bg-red-500/10 disabled:cursor-not-allowed disabled:opacity-50 dark:text-red-200" on:click={clearSelection}>{$t.imagePrompt.remove}</button>
            </div>
          </div>
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
                <div class="mt-1 text-xs text-stone-500 dark:text-zinc-400">{(selectedFile.size / (1024 * 1024)).toFixed(1)} MB</div>
              </div>
              <button type="button" disabled={busy} class="control-focus rounded-lg border border-stone-300 px-3 py-2.5 text-sm font-medium text-stone-700 hover:bg-stone-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" on:click={() => input?.click()}>{$t.imagePrompt.replace}</button>
              <button type="button" disabled={busy} class="control-focus rounded-lg border border-red-500/35 px-3 py-2.5 text-sm font-medium text-red-700 hover:bg-red-500/10 disabled:cursor-not-allowed disabled:opacity-50 dark:text-red-200" on:click={clearSelection}>{$t.imagePrompt.remove}</button>
            </div>
          </div>
        {/if}

        <input bind:this={input} type="file" disabled={busy} accept=".avif,.bmp,.gif,.heic,.heif,.ico,.jpg,.jpeg,.png,.tif,.tiff,.webp" class="hidden" aria-label={$t.imagePrompt.choose} on:change={handleInput} />

        {#if error}
          <div class="mt-4 rounded-lg border border-red-500/35 bg-red-500/10 px-3 py-2.5 text-sm text-red-800 dark:text-red-200" role="alert">{error}</div>
        {/if}
        {#if operation}
          <div class="mt-4 rounded-lg border border-cyan-500/30 bg-cyan-500/10 px-3 py-2.5 text-sm text-cyan-800 dark:text-cyan-200" aria-live="polite">
            {operation === 'optimize' ? $t.imagePrompt.optimizing : $t.imagePrompt.loading}
          </div>
        {/if}

        {#if result}
          <section class="mt-5 border-t border-stone-200 pt-5 dark:border-zinc-800" aria-labelledby="image-prompt-result-title">
            <div class="flex flex-wrap items-center justify-between gap-2">
              <h3 id="image-prompt-result-title" class="text-sm font-semibold text-stone-900 dark:text-zinc-100">
                {optimizedResult ? $t.imagePrompt.latestPrompt : $t.imagePrompt.result}
              </h3>
              <span class="text-xs text-stone-500 dark:text-zinc-400">
                {#if optimizedResult}
                  {$t.imagePrompt.iteration(iterationCount)} · {optimizedResult.model} · {optimizedResult.duration_ms} ms
                {:else}
                  {result.model} · {result.duration_ms} ms
                {/if}
              </span>
            </div>

            {#if optimizedResult}
              <div class="mt-4 grid min-w-0 gap-4 sm:grid-cols-2" data-testid="image-prompt-comparison">
                <figure class="min-w-0">
                  <figcaption class="mb-2 text-xs font-medium text-stone-600 dark:text-zinc-300">{$t.imagePrompt.targetImage}</figcaption>
                  <div class="flex aspect-[4/3] w-full items-center justify-center overflow-hidden rounded-lg bg-stone-200/70 p-2 dark:bg-zinc-900">
                    <img src={previewUrl} alt={$t.imagePrompt.targetImage} class="max-h-full max-w-full object-contain" decoding="async" />
                  </div>
                </figure>
                <figure class="min-w-0">
                  <figcaption class="mb-2 flex min-w-0 flex-wrap items-center justify-between gap-2 text-xs font-medium text-stone-600 dark:text-zinc-300">
                    <span>{$t.imagePrompt.trialImage}</span>
                    <span class="font-normal text-stone-500 dark:text-zinc-400">{optimizedResult.temporary_image.model} · {optimizedResult.temporary_image.width}x{optimizedResult.temporary_image.height} · {optimizedResult.temporary_image.duration_ms} ms</span>
                  </figcaption>
                  <div class="flex aspect-[4/3] w-full items-center justify-center overflow-hidden rounded-lg bg-stone-200/70 p-2 dark:bg-zinc-900">
                    <img src={trialImageUrl} alt={$t.imagePrompt.trialImage} class="max-h-full max-w-full object-contain" decoding="async" />
                  </div>
                </figure>
              </div>
              <div class="mt-4 border-b border-stone-200 pb-4 dark:border-zinc-800">
                <h4 class="text-xs font-semibold text-stone-700 dark:text-zinc-200">{$t.imagePrompt.comparison}</h4>
                <p class="mt-2 whitespace-pre-wrap text-sm leading-6 text-stone-700 dark:text-zinc-300">{optimizedResult.comparison_summary}</p>
              </div>
            {/if}

            <p class="mt-4 whitespace-pre-wrap text-sm leading-6 text-stone-800 dark:text-zinc-200">{latestPrompt}</p>
            {#if (optimizedResult?.warnings || result.warnings).length}
              <div class="mt-3 space-y-1 text-xs text-amber-800 dark:text-amber-200">
                {#each optimizedResult?.warnings || result.warnings as warning}
                  <p>{warning}</p>
                {/each}
              </div>
            {/if}
          </section>
        {/if}
      </div>

      <div class="flex flex-col gap-3 border-t border-stone-200 px-5 py-4 sm:flex-row sm:items-center dark:border-zinc-800">
        {#if result && optimizationUnavailableReason}
          <p class="min-w-0 flex-1 text-xs leading-5 text-amber-800 dark:text-amber-200" data-testid="image-prompt-optimize-reason">{optimizationUnavailableReason}</p>
        {:else}
          <div class="flex-1"></div>
        {/if}
        <div class="flex flex-wrap justify-end gap-2">
          <button type="button" class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-sm text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" on:click={closeDialog}>{$t.common.close}</button>
          <button type="button" disabled={!available || !selectedFile || busy} class="control-focus rounded-lg border border-emerald-500/40 px-3 py-2 text-sm font-semibold text-emerald-700 hover:bg-emerald-500/10 disabled:cursor-not-allowed disabled:opacity-50 dark:text-emerald-200" on:click={runPrompt}>{operation === 'reverse' ? $t.imagePrompt.loading : $t.imagePrompt.reverse}</button>
          {#if result}
            <button type="button" disabled={busy || Boolean(optimizationUnavailableReason)} title={optimizationUnavailableReason || undefined} class="control-focus rounded-lg border border-emerald-500/40 px-3 py-2 text-sm font-semibold text-emerald-700 hover:bg-emerald-500/10 disabled:cursor-not-allowed disabled:opacity-50 dark:text-emerald-200" on:click={runOptimization}>
              {operation === 'optimize' ? $t.imagePrompt.optimizing : optimizedResult ? $t.imagePrompt.optimizeAgain : $t.imagePrompt.optimize}
            </button>
            <button type="button" disabled={busy} class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-sm text-stone-700 hover:bg-stone-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" on:click={copyResult}>{$t.imagePrompt.copy}</button>
            <button type="button" disabled={busy} class="control-focus rounded-lg border border-cyan-500/35 px-3 py-2 text-sm font-medium text-cyan-700 hover:bg-cyan-500/10 disabled:cursor-not-allowed disabled:opacity-50 dark:text-cyan-200" on:click={saveResult}>{$t.imagePrompt.save}</button>
            <button type="button" disabled={busy} class="control-focus rounded-lg bg-emerald-600 px-3 py-2 text-sm font-semibold text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50" on:click={applyResult}>{$t.imagePrompt.apply}</button>
          {/if}
        </div>
      </div>
    </div>
  </div>
{/if}
