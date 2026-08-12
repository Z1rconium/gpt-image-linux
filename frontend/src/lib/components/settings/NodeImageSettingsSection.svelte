<script lang="ts">
  import { t } from '$lib/i18n';

  export let enabled = false;
  export let apiKey = '';
</script>

<section class="border-t border-stone-200 pt-4 dark:border-zinc-800">
  <div class="mb-3 flex items-start justify-between gap-3">
    <div class="min-w-0">
      <h3 class="text-sm font-semibold text-stone-800 dark:text-zinc-200">{$t.settings.nodeImageSection}</h3>
      <p class="mt-1 text-xs leading-5 text-stone-500 dark:text-zinc-500">{$t.settings.nodeImageSectionHint}</p>
    </div>
    <label class="flex shrink-0 items-center gap-2 text-xs font-medium text-stone-700 dark:text-zinc-300">
      <input bind:checked={enabled} type="checkbox" class="control-focus accent-emerald-500" />
      {$t.settings.nodeImageEnabled}
    </label>
  </div>

  <div class="space-y-3">
    <label class="block">
      <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.nodeImageApiKey}</span>
      <input
        bind:value={apiKey}
        type="password"
        autocomplete="off"
        spellcheck="false"
        class="control-focus w-full rounded-md border border-stone-300 bg-stone-50 px-3 py-2.5 font-mono text-sm text-stone-900 focus:border-emerald-500 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
      />
      <span class="mt-1.5 block text-xs leading-5 text-stone-500 dark:text-zinc-500">{$t.settings.nodeImageApiKeyHint}</span>
    </label>

    <a
      href="https://nodeimage.com"
      target="_blank"
      rel="noopener noreferrer"
      class="control-focus inline-flex min-h-10 w-full items-center justify-center gap-2 rounded-md border border-stone-300 px-3 py-2.5 text-sm font-semibold text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
    >
      <span>{$t.settings.nodeImageGetApiKey}</span>
      <svg class="h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M14 5h5v5" />
        <path d="m10 14 9-9" />
        <path d="M19 13v6H5V5h6" />
      </svg>
    </a>
  </div>
</section>
