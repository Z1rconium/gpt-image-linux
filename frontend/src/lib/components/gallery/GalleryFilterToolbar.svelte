<script lang="ts">
  import type { GalleryResponse } from '$lib/api/types';
  import { t } from '$lib/i18n';
  import type { GalleryFilters } from '$lib/stores/gallery';

  export let gallery: GalleryResponse | null = null;
  export let filters: GalleryFilters;
  export let onFilter: (key: keyof GalleryFilters, value: string | boolean) => void = () => {};
  export let onReset: () => void = () => {};

  $: hasFilters = Boolean(
    filters.prompt.trim() || filters.model || filters.preset || filters.size || filters.dateFrom || filters.dateTo || filters.favorite
  );
</script>

<div class="mb-4 border-y border-stone-200 py-3 dark:border-zinc-800">
  <div class="mb-2 flex items-center justify-between gap-3 px-1">
    <div class="text-xs font-semibold text-stone-600 dark:text-zinc-300">{$t.gallery.filters}</div>
    {#if hasFilters}
      <button type="button" class="control-focus rounded-md px-2 py-1 text-xs font-medium text-emerald-700 hover:bg-emerald-500/10 dark:text-emerald-300" on:click={onReset}>{$t.gallery.resetFilters}</button>
    {/if}
  </div>

  <div class="grid gap-2">
    <div class="grid gap-2 xl:grid-cols-[minmax(220px,1.6fr)_minmax(160px,1fr)_minmax(160px,1fr)_minmax(140px,0.9fr)]">
      <input type="search" name="gallery_prompt" value={filters.prompt} placeholder={$t.gallery.filterPrompt} autocomplete="off" aria-label={$t.gallery.filterPrompt} class="ui-field min-w-0" on:input={(event) => onFilter('prompt', event.currentTarget.value)} />
      <select value={filters.model} aria-label={$t.common.model} class="form-select control-focus min-w-0" on:change={(event) => onFilter('model', event.currentTarget.value)}>
        <option value="">{$t.gallery.allModels}</option>
        {#each gallery?.filter_options.models || [] as model}<option value={model}>{model}</option>{/each}
      </select>
      <select value={filters.preset} aria-label={$t.common.preset} class="form-select control-focus min-w-0" on:change={(event) => onFilter('preset', event.currentTarget.value)}>
        <option value="">{$t.gallery.allPresets}</option>
        {#each gallery?.filter_options.presets || [] as preset}<option value={preset}>{preset}</option>{/each}
      </select>
      <select value={filters.size} aria-label={$t.common.size} class="form-select control-focus min-w-0" on:change={(event) => onFilter('size', event.currentTarget.value)}>
        <option value="">{$t.gallery.allSizes}</option>
        {#each gallery?.filter_options.sizes || [] as size}<option value={size}>{size}</option>{/each}
      </select>
    </div>

    <div class="grid gap-2 xl:grid-cols-[minmax(0,1fr)_minmax(210px,0.45fr)]">
      <div class="grid min-w-0 grid-cols-[1fr_auto_1fr] items-center rounded-md border border-stone-200 bg-white dark:border-zinc-800 dark:bg-zinc-950" role="group" aria-label={$t.gallery.dateRange}>
        <input id="gallery-date-from" type="date" value={filters.dateFrom} aria-label={$t.gallery.dateFrom} class="control-focus h-10 min-w-0 border-0 bg-transparent px-2 text-sm text-stone-900 dark:text-zinc-100" on:change={(event) => onFilter('dateFrom', event.currentTarget.value)} />
        <span class="px-1 text-xs text-stone-400" aria-hidden="true">-</span>
        <input id="gallery-date-to" type="date" value={filters.dateTo} aria-label={$t.gallery.dateTo} class="control-focus h-10 min-w-0 border-0 bg-transparent px-2 text-sm text-stone-900 dark:text-zinc-100" on:change={(event) => onFilter('dateTo', event.currentTarget.value)} />
      </div>
      <label class={`control-focus flex min-h-10 min-w-0 items-center justify-center gap-2 rounded-md border px-3 text-sm font-medium ${filters.favorite ? 'border-emerald-500/50 bg-emerald-500/10 text-emerald-700 dark:text-emerald-200' : 'border-stone-200 bg-white text-stone-700 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-300'}`}>
        <input type="checkbox" class="accent-emerald-500" checked={filters.favorite} on:change={(event) => onFilter('favorite', event.currentTarget.checked)} />
        <span class="whitespace-nowrap">{$t.gallery.favorites}</span>
      </label>
    </div>
  </div>
</div>
