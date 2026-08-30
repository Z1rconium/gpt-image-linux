<script lang="ts">
  import { dialogIn, dialogOut, overlayIn, overlayOut } from '$lib/motion';
  import { dialog } from '$lib/actions/dialog';
  import { plainTextInput } from '$lib/actions/plainTextInput';
  import { t } from '$lib/i18n';

  export let systemPromptOpen = false;
  export let systemPromptLoading = false;
  export let systemPromptSaving = false;
  export let systemPromptText = '';
  export let systemPromptError = '';
  export let closeSystemPromptEditor: () => void = () => {};
  export let saveSystemPrompt: () => void = () => {};
</script>

    {#if systemPromptOpen}
      <div class="mobile-dialog-root fixed inset-0 z-[70] flex items-center justify-center bg-black/70 p-4" in:overlayIn out:overlayOut>
        <button class="absolute inset-0" type="button" tabindex="-1" aria-label={$t.settings.closeSystemPromptEditor} on:click={closeSystemPromptEditor}></button>
        <div
          class="mobile-dvh-dialog overlay-panel relative flex max-h-[88vh] w-full max-w-3xl flex-col rounded-xl border border-stone-200 bg-white dark:border-zinc-800 dark:bg-zinc-900" in:dialogIn out:dialogOut
          aria-labelledby="system-prompt-dialog-title"
          use:dialog={{ open: systemPromptOpen, onClose: closeSystemPromptEditor }}
        >
          <div class="flex items-start justify-between gap-4 border-b border-stone-200 p-5 dark:border-zinc-800">
            <div class="min-w-0">
              <h2 id="system-prompt-dialog-title" class="text-base font-semibold text-stone-900 dark:text-zinc-100">{$t.settings.systemPromptTitle}</h2>
              <p class="mt-1 text-xs leading-5 text-stone-500 dark:text-zinc-500">{$t.settings.systemPromptHint}</p>
            </div>
            <button type="button" class="mobile-touch-target control-focus rounded-lg p-1.5 text-stone-500 hover:bg-stone-100 hover:text-stone-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100" aria-label={$t.settings.closeSystemPromptEditor} on:click={closeSystemPromptEditor}>
              x
            </button>
          </div>

          <div class="min-h-0 flex-1 overflow-y-auto p-5">
            {#if systemPromptLoading}
              <div class="rounded-lg border border-stone-200 bg-stone-50/90 p-4 text-sm text-stone-600 dark:border-zinc-800 dark:bg-zinc-950/60 dark:text-zinc-400">{$t.settings.systemPromptLoading}</div>
            {:else}
              <label class="block">
                <span class="mb-2 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.systemPromptLabel}</span>
                <textarea
                  bind:value={systemPromptText}
                  class="system-prompt-textarea control-focus min-h-[420px] w-full resize-y rounded-lg border border-stone-300 bg-stone-50 px-3 py-3 font-mono text-xs leading-5 text-stone-900 focus:border-emerald-500 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                  spellcheck="false"
                  data-autofocus
                  use:plainTextInput
                ></textarea>
              </label>
            {/if}
            {#if systemPromptError}
              <div class="mt-3 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-700 dark:text-red-200">{systemPromptError}</div>
            {/if}
          </div>

          <div class="flex justify-end gap-3 border-t border-stone-200 p-5 dark:border-zinc-800">
            <button type="button" class="control-focus rounded-lg border border-stone-300 px-4 py-2 text-sm text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" on:click={closeSystemPromptEditor}>
              {$t.common.close}
            </button>
            <button
              type="button"
              disabled={systemPromptLoading || systemPromptSaving}
              class="control-focus rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
              on:click={saveSystemPrompt}
            >
              {systemPromptSaving ? $t.settings.systemPromptSaving : $t.settings.systemPromptSave}
            </button>
          </div>
        </div>
      </div>
    {/if}
