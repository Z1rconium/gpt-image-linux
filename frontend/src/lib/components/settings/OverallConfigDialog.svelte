<script lang="ts">
  import { dialogIn, dialogOut, overlayIn, overlayOut } from '$lib/motion';
  import { dialog } from '$lib/actions/dialog';
  import { t } from '$lib/i18n';
  import type { OverallConfigItem } from '$lib/api/types/settings';

  export let overallConfigOpen = false;
  export let overallConfigLoading = false;
  export let overallConfigSaving = false;
  export let overallConfigError = '';
  export let overallConfigGroups: Record<string, OverallConfigItem[]> = {};
  export let overallConfigGroupNames: string[] = [];
  export let closeOverallConfigModal: () => void = () => {};
  export let saveOverallConfigModal: () => void = () => {};
  export let overallDraftValue: (item: OverallConfigItem) => string | boolean | number = (item) => item.value;
  export let hasOverallDraft: (name: string) => boolean = () => false;
  export let setOverallDraft: (item: OverallConfigItem, value: string | boolean | number) => void = () => {};
  export let resetOverallConfigItem: (item: OverallConfigItem) => void = () => {};
  export let sourceLabel: (source: OverallConfigItem['source']) => string = (source) => source;
</script>

    {#if overallConfigOpen}
      <div class="mobile-dialog-root fixed inset-0 z-[70] flex items-center justify-center bg-black/70 p-4" in:overlayIn out:overlayOut>
        <button class="absolute inset-0" type="button" tabindex="-1" aria-label={$t.settings.closeOverallConfig} on:click={closeOverallConfigModal}></button>
        <div
          class="mobile-dvh-dialog overlay-panel relative flex max-h-[90vh] w-full max-w-5xl flex-col rounded-xl border border-stone-200 bg-white dark:border-zinc-800 dark:bg-zinc-900" in:dialogIn out:dialogOut
          aria-labelledby="overall-config-dialog-title"
          use:dialog={{ open: overallConfigOpen, onClose: closeOverallConfigModal }}
        >
          <div class="flex items-start justify-between gap-4 border-b border-stone-200 p-5 dark:border-zinc-800">
            <div class="min-w-0">
              <h2 id="overall-config-dialog-title" class="text-base font-semibold text-stone-900 dark:text-zinc-100">{$t.settings.overallConfig}</h2>
              <p class="mt-1 text-xs leading-5 text-stone-500 dark:text-zinc-500">{$t.settings.overallConfigHint}</p>
            </div>
            <button type="button" class="mobile-touch-target control-focus rounded-lg p-1.5 text-stone-500 hover:bg-stone-100 hover:text-stone-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100" aria-label={$t.settings.closeOverallConfig} on:click={closeOverallConfigModal}>
              x
            </button>
          </div>

          <div class="min-h-0 flex-1 overflow-y-auto p-5">
            {#if overallConfigLoading}
              <div class="rounded-lg border border-stone-200 bg-stone-50/90 p-4 text-sm text-stone-600 dark:border-zinc-800 dark:bg-zinc-950/60 dark:text-zinc-400">{$t.settings.overallConfigLoading}</div>
            {:else}
              <div class="space-y-6">
                {#each overallConfigGroupNames as group}
                  <section>
                    <h3 class="mb-3 text-sm font-semibold text-stone-800 dark:text-zinc-200">{group}</h3>
                    <div class="space-y-3">
                      {#each overallConfigGroups[group] as item}
                        <div class="rounded-lg border border-stone-200 bg-stone-50/80 p-3 dark:border-zinc-800 dark:bg-zinc-950/50" data-testid={`overall-config-${item.name}`}>
                          <div class="mb-2 flex flex-wrap items-start justify-between gap-2">
                            <div class="min-w-0">
                              <div class="font-mono text-xs font-semibold text-stone-900 dark:text-zinc-100">{item.name}</div>
                              <div class="mt-1 text-xs leading-5 text-stone-500 dark:text-zinc-500">{item.description}</div>
                            </div>
                            <div class="flex shrink-0 flex-wrap justify-end gap-1.5">
                              <span class="rounded border border-stone-300 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-stone-600 dark:border-zinc-700 dark:text-zinc-400">{sourceLabel(item.source)}</span>
                              {#if item.restart_required}
                                <span class="rounded border border-amber-500/40 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-amber-700 dark:text-amber-300">{$t.settings.restartRequired}</span>
                              {/if}
                              {#if item.build_only}
                                <span class="rounded border border-sky-500/40 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-sky-700 dark:text-sky-300">{$t.settings.buildOnly}</span>
                              {/if}
                            </div>
                          </div>

                          <div class="grid grid-cols-1 gap-2 sm:grid-cols-[1fr_auto]">
                            {#if item.type === 'bool'}
                              <label class="flex items-center gap-2 rounded-lg border border-stone-200 bg-white px-3 py-2.5 text-xs text-stone-700 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-300">
                                <input
                                  type="checkbox"
                                  class="control-focus accent-emerald-500"
                                  checked={Boolean(overallDraftValue(item))}
                                  on:change={(event) => setOverallDraft(item, event.currentTarget.checked)}
                                />
                                {$t.common.active}
                              </label>
                            {:else}
                              <input
                                value={String(overallDraftValue(item) ?? '')}
                                type={item.type === 'int' || item.type === 'float' ? 'number' : item.secret ? 'password' : 'text'}
                                step={item.type === 'float' ? 'any' : '1'}
                                class="control-focus w-full rounded-lg border border-stone-300 bg-white px-3 py-2.5 font-mono text-sm text-stone-900 focus:border-emerald-500 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                                on:input={(event) => setOverallDraft(item, event.currentTarget.value)}
                              />
                            {/if}
                            <button
                              type="button"
                              class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-xs font-semibold text-stone-700 hover:bg-stone-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
                              disabled={!item.has_override && !hasOverallDraft(item.name)}
                              on:click={() => resetOverallConfigItem(item)}
                            >
                              {$t.settings.resetToEnv}
                            </button>
                          </div>
                          {#if item.has_override || item.is_env_set}
                            <div class="mt-2 grid grid-cols-1 gap-1 text-[11px] text-stone-500 dark:text-zinc-500 sm:grid-cols-2">
                              <div class="truncate">env: <span class="font-mono">{item.env_value_masked || '-'}</span></div>
                              <div class="truncate">override: <span class="font-mono">{item.override_value_masked || '-'}</span></div>
                            </div>
                          {/if}
                        </div>
                      {/each}
                    </div>
                  </section>
                {/each}
              </div>
            {/if}
            {#if overallConfigError}
              <div class="mt-3 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-700 dark:text-red-200">{overallConfigError}</div>
            {/if}
          </div>

          <div class="flex justify-end gap-3 border-t border-stone-200 p-5 dark:border-zinc-800">
            <button type="button" class="control-focus rounded-lg border border-stone-300 px-4 py-2 text-sm text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" on:click={closeOverallConfigModal}>
              {$t.common.close}
            </button>
            <button
              type="button"
              disabled={overallConfigLoading || overallConfigSaving}
              class="control-focus rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
              on:click={saveOverallConfigModal}
            >
              {overallConfigSaving ? $t.settings.saving : $t.settings.saveOverallConfig}
            </button>
          </div>
        </div>
      </div>
    {/if}
