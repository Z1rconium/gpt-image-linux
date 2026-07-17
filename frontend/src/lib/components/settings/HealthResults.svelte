<script lang="ts">
  import { t } from '$lib/i18n';
  import type {
    AssistantHealthResponse,
    PresetHealthResponse,
    PromptOptimizerHealthResponse
  } from '$lib/api/types/settings';
  import type { PresetHealthStatus } from '$lib/api/types/common';

  export let health: PresetHealthResponse | null = null;
  export let promptOptimizerHealth: PromptOptimizerHealthResponse | null = null;
  export let aiAssistantHealth: AssistantHealthResponse | null = null;
  export let closePromptOptimizerHealth: () => void = () => {};
  export let closeAiAssistantHealth: () => void = () => {};
  export let closePresetHealth: () => void = () => {};

  function healthPanelClass(status: PresetHealthStatus) {
    if (status === 'ok') return 'border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-500/40 dark:bg-emerald-500/10 dark:text-emerald-100';
    if (status === 'warning') return 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-100';
    return 'border-red-200 bg-red-50 text-red-800 dark:border-red-500/40 dark:bg-red-500/10 dark:text-red-100';
  }

  function healthBadgeClass(status: PresetHealthStatus) {
    if (status === 'ok') return 'border-emerald-300 text-emerald-700 dark:border-emerald-500/40 dark:text-emerald-300';
    if (status === 'warning') return 'border-amber-300 text-amber-700 dark:border-amber-500/40 dark:text-amber-300';
    return 'border-red-300 text-red-700 dark:border-red-500/40 dark:text-red-300';
  }

  function healthStatusLabel(status: PresetHealthStatus) {
    if (status === 'ok') return $t.settings.healthOk;
    if (status === 'warning') return $t.settings.healthWarning;
    return $t.settings.healthError;
  }

  function presetHealthDisplayStatus(response: PresetHealthResponse): PresetHealthStatus {
    const blockingChecks = response.checks.filter((check) => check.name !== 'upstream_probe');
    if (blockingChecks.length === 0) return 'ok';
    if (blockingChecks.some((check) => check.status === 'error')) return 'error';
    if (blockingChecks.some((check) => check.status === 'warning')) return 'warning';
    return 'ok';
  }
</script>

        {#if aiAssistantHealth}
          <div class={`rounded-lg border p-3 text-xs ${healthPanelClass(aiAssistantHealth.status)}`} data-testid="ai-assistant-health-result">
            <div class="flex items-center justify-between gap-3">
              <div class="min-w-0">
                <span class="font-semibold">{$t.settings.aiAssistantHealth}</span>
                <div class="mt-1 text-[11px] text-inherit/70">
                  {aiAssistantHealth.model}
                  {#if aiAssistantHealth.duration_ms}{' '}- {aiAssistantHealth.duration_ms} ms{/if}
                </div>
              </div>
              <div class="flex items-center gap-2">
                <span class={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${healthBadgeClass(aiAssistantHealth.status)}`}>
                  {healthStatusLabel(aiAssistantHealth.status)}
                </span>
                <button
                  type="button"
                  class="mobile-touch-target control-focus rounded-lg p-1.5 text-inherit/70 hover:bg-black/10 hover:text-inherit"
                  aria-label={$t.common.close}
                  on:click={closeAiAssistantHealth}
                >
                  x
                </button>
              </div>
            </div>
            <div class="mt-2 rounded-md border border-stone-200 bg-white/70 p-2 text-stone-700 dark:border-zinc-800 dark:bg-zinc-950/50 dark:text-zinc-300">
              {aiAssistantHealth.message}
            </div>
          </div>
        {/if}
        {#if promptOptimizerHealth}
          <div class={`rounded-lg border p-3 text-xs ${healthPanelClass(promptOptimizerHealth.status)}`} data-testid="prompt-optimizer-health-result">
            <div class="flex items-center justify-between gap-3">
              <div class="min-w-0">
                <span class="font-semibold">{$t.settings.promptOptimizerHealth}</span>
                <div class="mt-1 text-[11px] text-inherit/70">
                  {promptOptimizerHealth.model}
                  {#if promptOptimizerHealth.duration_ms}{' '}- {promptOptimizerHealth.duration_ms} ms{/if}
                </div>
              </div>
              <div class="flex items-center gap-2">
                <span class={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${healthBadgeClass(promptOptimizerHealth.status)}`}>
                  {healthStatusLabel(promptOptimizerHealth.status)}
                </span>
                <button
                  type="button"
                  class="mobile-touch-target control-focus rounded-lg p-1.5 text-inherit/70 hover:bg-black/10 hover:text-inherit"
                  aria-label={$t.common.close}
                  on:click={closePromptOptimizerHealth}
                >
                  x
                </button>
              </div>
            </div>
            <div class="mt-2 rounded-md border border-stone-200 bg-white/70 p-2 text-stone-700 dark:border-zinc-800 dark:bg-zinc-950/50 dark:text-zinc-300">
              {promptOptimizerHealth.message}
            </div>
          </div>
        {/if}
        {#if health}
          {@const displayStatus = presetHealthDisplayStatus(health)}
          <div class={`rounded-lg border p-3 text-xs ${healthPanelClass(displayStatus)}`} data-testid="preset-health-result">
            <div class="flex items-center justify-between gap-3">
              <div class="min-w-0">
                <span class="font-semibold">{$t.settings.healthStatus}</span>
                <div class="mt-1 text-[11px] text-inherit/70">{$t.settings.healthTestResult}</div>
              </div>
              <div class="flex items-center gap-2">
                <span class={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${healthBadgeClass(displayStatus)}`}>
                  {healthStatusLabel(displayStatus)}
                </span>
                <button
                  type="button"
                  class="mobile-touch-target control-focus rounded-lg p-1.5 text-inherit/70 hover:bg-black/10 hover:text-inherit"
                  aria-label={$t.common.close}
                  on:click={closePresetHealth}
                >
                  x
                </button>
              </div>
            </div>
            <div class="mt-2 space-y-1.5">
              {#each health.checks as check}
                <div class="rounded-md border border-stone-200 bg-white/70 p-2 text-stone-700 dark:border-zinc-800 dark:bg-zinc-950/50 dark:text-zinc-300">
                  <div class="flex items-center justify-between gap-2">
                    <span class="font-mono text-[11px] text-stone-500 dark:text-zinc-500">{check.name}</span>
                    <span class={`rounded border px-1.5 py-0.5 text-[10px] font-semibold ${healthBadgeClass(check.status)}`}>
                      {healthStatusLabel(check.status)}
                    </span>
                  </div>
                  <div class="mt-1 leading-relaxed text-stone-600 dark:text-zinc-400">{check.message}</div>
                </div>
              {/each}
            </div>
          </div>
        {/if}
