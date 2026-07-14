<script lang="ts">
  import { t } from '$lib/i18n';

  export let enabled = false;
  export let visionModel = '';
  export let healthChecking = false;
  export let onCheck: () => void | Promise<void> = () => {};
</script>

<section class="border-t border-zinc-800 pt-4">
  <div class="mb-3 flex items-center justify-between gap-3">
    <div>
      <h3 class="text-sm font-semibold text-zinc-200">{$t.settings.aiAssistant}</h3>
      <p class="mt-1 text-xs text-zinc-500">{$t.settings.aiAssistantHint}</p>
    </div>
    <label class="flex items-center gap-2 text-xs font-medium text-zinc-300">
      <input bind:checked={enabled} type="checkbox" class="control-focus accent-emerald-500" />
      {$t.settings.aiAssistantEnabled}
    </label>
  </div>
  <div class="space-y-4">
    <p class="rounded-md border border-zinc-800 bg-zinc-950 p-3 text-xs text-zinc-400">{$t.settings.aiAssistantSharedConfigHint}</p>
    <label class="block">
      <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.settings.aiAssistantVisionModel}</span>
      <input bind:value={visionModel} class="control-focus w-full rounded-md border border-zinc-700 bg-zinc-950 px-3 py-2.5 font-mono text-sm text-zinc-100 focus:border-emerald-500" placeholder="gpt-4o-mini" />
    </label>
    <button type="button" disabled={healthChecking} class="control-focus w-full rounded-md border border-emerald-500/40 px-3 py-2.5 text-sm font-semibold text-emerald-200 hover:bg-emerald-500/10 disabled:opacity-50" on:click={onCheck}>
      {healthChecking ? $t.settings.aiAssistantHealthChecking : $t.settings.aiAssistantHealthCheck}
    </button>
  </div>
</section>
