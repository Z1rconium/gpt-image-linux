<script lang="ts">
  import { t } from '$lib/i18n';

  export let enabled = false;
  export let apiUrl = '';
  export let model = '';
  export let timeoutSeconds: number | string = 60;
  export let apiKey = '';
  export let apiKeyInputType = 'password';
  export let healthChecking = false;
  export let onNormalizeTimeout: () => void = () => {};
  export let onOpenSystemPrompt: () => void | Promise<void> = () => {};
  export let onCheck: () => void | Promise<void> = () => {};
</script>

<section class="border-t border-stone-200 pt-4 dark:border-zinc-800">
  <div class="mb-3 flex items-center justify-between gap-3">
    <div>
      <h3 class="text-sm font-semibold text-stone-800 dark:text-zinc-200">{$t.settings.promptOptimizer}</h3>
      <p class="mt-1 text-xs text-stone-500 dark:text-zinc-500">{$t.settings.promptOptimizerHint}</p>
    </div>
    <label class="flex items-center gap-2 text-xs font-medium text-stone-700 dark:text-zinc-300">
      <input bind:checked={enabled} type="checkbox" class="control-focus accent-emerald-500" />
      {$t.settings.promptOptimizerEnabled}
    </label>
  </div>
  <div class="space-y-4">
    <label class="block"><span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.promptOptimizerApiUrl}</span><input bind:value={apiUrl} class="control-focus w-full rounded-md border border-stone-300 bg-stone-50 px-3 py-2.5 font-mono text-sm text-stone-900 focus:border-emerald-500 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100" placeholder="https://api.openai.com/v1/chat/completions" /></label>
    <label class="block"><span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.promptOptimizerModel}</span><input bind:value={model} class="control-focus w-full rounded-md border border-stone-300 bg-stone-50 px-3 py-2.5 font-mono text-sm text-stone-900 focus:border-emerald-500 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100" placeholder="gpt-4o-mini" /></label>
    <label class="block">
      <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.promptOptimizerTimeout}</span>
      <input bind:value={timeoutSeconds} type="number" min="1" step="1" inputmode="numeric" class="control-focus w-full rounded-md border border-stone-300 bg-stone-50 px-3 py-2.5 font-mono text-sm text-stone-900 focus:border-emerald-500 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100" on:blur={onNormalizeTimeout} />
    </label>
    <label class="block">
      <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.promptOptimizerApiKey}</span>
      <input bind:value={apiKey} type={apiKeyInputType} class="control-focus w-full rounded-md border border-stone-300 bg-stone-50 px-3 py-2.5 font-mono text-sm text-stone-900 focus:border-emerald-500 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100" />
      <span class="mt-1.5 block text-xs text-stone-500 dark:text-zinc-500">{$t.settings.apiKeyHint}</span>
    </label>
    <button type="button" class="control-focus w-full rounded-md border border-stone-300 px-3 py-2.5 text-sm font-semibold text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800" on:click={onOpenSystemPrompt}>{$t.settings.editSystemPrompt}</button>
    <button type="button" disabled={healthChecking} class="control-focus w-full rounded-md border border-emerald-500/40 px-3 py-2.5 text-sm font-semibold text-emerald-700 hover:bg-emerald-500/10 disabled:opacity-50 dark:text-emerald-200" on:click={onCheck}>
      {healthChecking ? $t.settings.promptOptimizerHealthChecking : $t.settings.promptOptimizerHealthCheck}
    </button>
  </div>
</section>
