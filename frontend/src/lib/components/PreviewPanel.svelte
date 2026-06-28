<script lang="ts">
  import type { GenerateJobImage, GenerateJobStatus } from '$lib/api/types';
  import { t } from '$lib/i18n';
  import { displayImageSize, downloadUrl, filenameFromImageUrl, formatBeijingTime, stageLabel, statusLabel } from '$lib/utils/format';

  export let loading = false;
  export let error = '';
  export let job: GenerateJobStatus | null = null;
  export let imageUrl = '';
  export let filename = '';
  export let prompt = '';
  export let onRegenerate: () => void = () => {};
  export let onClear: () => void = () => {};

  let activeJobId = '';
  let selectedImageId = '';

  function normalizePreviewImages(currentJob: GenerateJobStatus | null, fallbackUrl: string, fallbackFilename: string): GenerateJobImage[] {
    const jobImages = currentJob?.images?.filter((image) => image.image_url || image.filename) || [];
    if (jobImages.length) {
      return jobImages.map((image, index) => {
        const image_url = image.image_url || `/api/image/${encodeURIComponent(image.filename)}`;
        const filename = image.filename || filenameFromImageUrl(image_url);
        return {
          image_id: image.image_id || filename || `${currentJob?.job_id || 'image'}-${index}`,
          image_url,
          filename,
          image_width: image.image_width ?? null,
          image_height: image.image_height ?? null
        };
      });
    }
    if (!fallbackUrl) return [];
    return [
      {
        image_id: currentJob?.image_id || fallbackFilename || fallbackUrl,
        image_url: fallbackUrl,
        filename: fallbackFilename || filenameFromImageUrl(fallbackUrl),
        image_width: currentJob?.image_width ?? null,
        image_height: currentJob?.image_height ?? null
      }
    ];
  }

  $: resultImages = normalizePreviewImages(job, imageUrl, filename);
  $: if ((job?.job_id || '') !== activeJobId) {
    activeJobId = job?.job_id || '';
    selectedImageId = resultImages[0]?.image_id || '';
  }
  $: if (resultImages.length && !resultImages.some((image) => image.image_id === selectedImageId)) {
    selectedImageId = resultImages[0].image_id;
  }
  $: selectedImage = resultImages.find((image) => image.image_id === selectedImageId) || resultImages[0] || null;
  $: selectedImageUrl = selectedImage?.image_url || imageUrl;
  $: selectedFilename = selectedImage?.filename || filename;
  $: selectedImageIndex = selectedImage ? resultImages.findIndex((image) => image.image_id === selectedImage.image_id) : -1;
  $: previewWidth = selectedImage?.image_width || job?.image_width || undefined;
  $: previewHeight = selectedImage?.image_height || job?.image_height || undefined;
  $: previewSize = displayImageSize({
    size: job?.size || null,
    image_width: selectedImage?.image_width ?? job?.image_width ?? null,
    image_height: selectedImage?.image_height ?? job?.image_height ?? null
  });
</script>

<section class="rounded-2xl border border-stone-200 bg-white/80 p-4 shadow-sm shadow-stone-200/60 sm:p-5 dark:border-zinc-800 dark:bg-zinc-900/60 dark:shadow-none">
  <div class="mb-4 flex items-center justify-between gap-3">
    <div class="min-w-0">
      <h2 class="text-sm font-semibold text-stone-950 dark:text-zinc-100">{$t.preview.title}</h2>
      <p class="mt-1 truncate text-xs text-stone-500 dark:text-zinc-500">{prompt || $t.preview.subtitle}</p>
    </div>
    <div class="flex shrink-0 items-center gap-2">
      {#if selectedFilename}
        <a href={downloadUrl(selectedFilename)} class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-xs font-medium text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800">{$t.common.download}</a>
      {/if}
      <button type="button" class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-xs font-medium text-stone-700 hover:bg-stone-100 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" disabled={!job && !imageUrl} on:click={onRegenerate}>
        {$t.preview.regenerate}
      </button>
      <button type="button" class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-xs font-medium text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" on:click={onClear}>
        {$t.common.clear}
      </button>
    </div>
  </div>

  {#if error}
    <div class="rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">{error}</div>
  {/if}

  <div class={`mt-4 flex min-h-[360px] items-center justify-center overflow-hidden rounded-xl border border-stone-200 dark:border-zinc-800 ${selectedImageUrl ? 'bg-stone-100 dark:bg-zinc-950' : 'preview-empty'}`}>
    {#if loading}
      <div class="flex max-w-sm flex-col items-center px-6 text-center" role="status" aria-live="polite" aria-atomic="true">
        <span class="spinner"></span>
        <p class="mt-4 text-sm font-semibold text-stone-900 dark:text-zinc-100">{stageLabel(job, $t.stages) || $t.preview.working}</p>
        <p class="mt-2 text-xs text-stone-500 dark:text-zinc-400">{statusLabel(job?.status, $t.statuses) || $t.preview.queued}</p>
      </div>
    {:else if selectedImageUrl}
      <div class="flex h-full w-full flex-col">
        <div class="flex min-h-[320px] flex-1 items-center justify-center p-3">
          <img
            src={selectedImageUrl}
            alt={$t.preview.generatedAlt}
            class="max-h-[640px] max-w-full rounded-lg object-contain"
            loading="eager"
            fetchpriority="high"
            decoding="async"
            width={previewWidth}
            height={previewHeight}
          />
        </div>
        {#if resultImages.length > 1}
          <div class="border-t border-stone-200 p-3 dark:border-zinc-800">
            <div class="mb-2 flex items-center justify-between text-xs text-stone-500 dark:text-zinc-500">
              <span>{$t.preview.resultCount(resultImages.length)}</span>
              <span>{selectedImageIndex + 1} / {resultImages.length}</span>
            </div>
            <div class="grid grid-cols-4 gap-2 sm:grid-cols-5">
              {#each resultImages as result, index (result.image_id)}
                <button
                  type="button"
                  aria-label={$t.preview.selectResult(index + 1)}
                  class={`control-focus relative aspect-square overflow-hidden rounded-lg border ${result.image_id === selectedImage?.image_id ? 'border-emerald-400 ring-1 ring-emerald-400/40' : 'border-stone-200 hover:border-stone-400 dark:border-zinc-800 dark:hover:border-zinc-600'}`}
                  on:click={() => (selectedImageId = result.image_id)}
                >
                  <img src={result.image_url} alt={$t.preview.resultThumbAlt(index + 1)} class="h-full w-full object-cover" loading="lazy" decoding="async" />
                  <span class="absolute left-1.5 top-1.5 rounded bg-white/90 px-1.5 py-0.5 text-[10px] font-semibold text-stone-700 dark:bg-zinc-950/80 dark:text-zinc-200">{index + 1}</span>
                </button>
              {/each}
            </div>
          </div>
        {/if}
      </div>
    {:else}
      <div class="px-6 text-center">
        <p class="text-sm font-medium text-stone-700 dark:text-zinc-300">{$t.preview.noPreview}</p>
        <p class="mt-2 text-xs text-stone-500 dark:text-zinc-500">{$t.preview.noPreviewHint}</p>
      </div>
    {/if}
  </div>

  {#if job}
    <div class="mt-4 grid grid-cols-2 gap-2 text-xs text-stone-500 dark:text-zinc-500 sm:grid-cols-5">
      <div class="rounded-lg border border-stone-200 bg-stone-50/80 px-3 py-2 dark:border-zinc-800 dark:bg-zinc-950/50">
        <div class="text-stone-400 dark:text-zinc-600">{$t.common.status}</div>
        <div class="mt-1 text-stone-700 dark:text-zinc-300">{statusLabel(job.status, $t.statuses)}</div>
      </div>
      <div class="rounded-lg border border-stone-200 bg-stone-50/80 px-3 py-2 dark:border-zinc-800 dark:bg-zinc-950/50">
        <div class="text-stone-400 dark:text-zinc-600">{$t.common.completedAt}</div>
        <div class="mt-1 whitespace-nowrap text-stone-700 dark:text-zinc-300">{formatBeijingTime(job.completed_at)}</div>
      </div>
      <div class="rounded-lg border border-stone-200 bg-stone-50/80 px-3 py-2 dark:border-zinc-800 dark:bg-zinc-950/50">
        <div class="text-stone-400 dark:text-zinc-600">{$t.common.size}</div>
        <div class="mt-1 text-stone-700 dark:text-zinc-300">{previewSize}</div>
      </div>
      <div class="rounded-lg border border-stone-200 bg-stone-50/80 px-3 py-2 dark:border-zinc-800 dark:bg-zinc-950/50">
        <div class="text-stone-400 dark:text-zinc-600">{$t.common.model}</div>
        <div class="mt-1 truncate text-stone-700 dark:text-zinc-300">{job.model || '-'}</div>
      </div>
      <div class="rounded-lg border border-stone-200 bg-stone-50/80 px-3 py-2 dark:border-zinc-800 dark:bg-zinc-950/50">
        <div class="text-stone-400 dark:text-zinc-600">{$t.common.duration}</div>
        <div class="mt-1 text-stone-700 dark:text-zinc-300">{job.duration || '-'}</div>
      </div>
    </div>
  {/if}
</section>
