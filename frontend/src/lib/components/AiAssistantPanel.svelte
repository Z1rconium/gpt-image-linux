<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import { apiFetch } from '$lib/api/client';
  import { language, t } from '$lib/i18n';
  import { assistantStore } from '$lib/stores/assistant';
  import { initialPromptFormState, type PromptFormState } from '$lib/stores/preview';
import type { AssistantPromptCheckResponse, AssistantPromptVariant, AssistantRecommendParamsResponse, PromptOptimizeResponse } from '$lib/api/types/assistant';
import type { ApiPath } from '$lib/api/types/common';
  import { buildPromptOptimizeRequest } from '$lib/utils/promptOptimizer';

  type ResultMode = 'rewrite' | 'quickOptimize' | 'check' | 'variants' | 'params';

  export let enabled = false;
  export let optimizerEnabled = false;
  export let currentPrompt = '';
  export let apiPath: ApiPath = initialPromptFormState.apiPath;
  export let model = initialPromptFormState.model;
  export let size = initialPromptFormState.size;
  export let quality: PromptFormState['quality'] = initialPromptFormState.quality;
  export let outputFormat: PromptFormState['outputFormat'] = initialPromptFormState.outputFormat;
  export let quantity: number = 1;
  export let loading = false;
  export let onApplyPrompt: (prompt: string) => void | Promise<void> = () => {};
  export let onInsertPrompt: (prompt: string) => void | Promise<void> = () => {};
  export let onSaveSnippet: (prompt: string) => void | Promise<void> = () => {};
  export let onApplyParams: (params: AssistantRecommendParamsResponse) => void | Promise<void> = () => {};

  const dispatch = createEventDispatcher<{ error: string }>();

  let instruction = '';
  let resultMode: ResultMode | null = null;
  let rewrittenPrompt = '';
  let checkResult: AssistantPromptCheckResponse | null = null;
  let variants: AssistantPromptVariant[] = [];
  let paramsResult: AssistantRecommendParamsResponse | null = null;
  let error = '';
  let quickOptimizing = false;
  let activeContextKey = '';
  let resultContextKey = '';

  $: unavailable = !enabled;
  $: effectivePrompt = currentPrompt.trim() || instruction.trim();
  $: effectiveInstruction = currentPrompt.trim() ? instruction.trim() : '';
  $: panelBusy = loading || quickOptimizing;
  $: actionDisabled = panelBusy || unavailable || !effectivePrompt;
  $: quickOptimizeDisabled = panelBusy || unavailable || !optimizerEnabled || !currentPrompt.trim();
  $: activeContextKey = JSON.stringify({
    prompt: effectivePrompt,
    instruction: effectiveInstruction,
    apiPath,
    model,
    size,
    quality,
    outputFormat,
    quantity
  });
  $: if (resultContextKey && activeContextKey !== resultContextKey) {
    clearResult();
  }

  function contextPayload() {
    return {
      prompt: effectivePrompt,
      instruction: effectiveInstruction || null,
      target_language: $language,
      api_path: apiPath,
      model,
      size,
      quality
    };
  }

  function clearResult() {
    resultMode = null;
    rewrittenPrompt = '';
    checkResult = null;
    variants = [];
    paramsResult = null;
    resultContextKey = '';
  }

  async function runRewrite() {
    if (actionDisabled) return;
    error = '';
    const contextKey = activeContextKey;
    try {
      const response = await assistantStore.rewritePrompt(contextPayload());
      if (contextKey !== activeContextKey) return;
      resultMode = 'rewrite';
      rewrittenPrompt = response.rewritten_prompt;
      checkResult = null;
      variants = [];
      paramsResult = null;
      resultContextKey = contextKey;
    } catch (caught) {
      showError(caught);
    }
  }

  async function runQuickOptimize() {
    const prompt = currentPrompt.trim();
    if (quickOptimizeDisabled || !prompt) return;
    error = '';
    quickOptimizing = true;
    const contextKey = activeContextKey;
    try {
      const response = await apiFetch<PromptOptimizeResponse>(
        '/api/prompt/optimize',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(
            buildPromptOptimizeRequest({
              prompt,
              intent: instruction.trim(),
              targetLanguage: $language,
              apiPath,
              model,
              size,
              quality
            })
          )
        },
        'quick optimizing prompt'
      );
      if (contextKey !== activeContextKey) return;
      resultMode = 'quickOptimize';
      rewrittenPrompt = response.optimized_prompt;
      checkResult = null;
      variants = [];
      paramsResult = null;
      resultContextKey = contextKey;
    } catch (caught) {
      showError(caught, $t.messages.promptOptimizeFailed);
    } finally {
      quickOptimizing = false;
    }
  }

  async function runCheck() {
    if (actionDisabled) return;
    error = '';
    const contextKey = activeContextKey;
    try {
      const response = await assistantStore.checkPrompt({
        prompt: effectivePrompt,
        api_path: apiPath,
        model,
        size,
        quality
      });
      if (contextKey !== activeContextKey) return;
      resultMode = 'check';
      checkResult = response;
      rewrittenPrompt = '';
      variants = [];
      paramsResult = null;
      resultContextKey = contextKey;
    } catch (caught) {
      showError(caught);
    }
  }

  async function runVariants() {
    if (actionDisabled) return;
    error = '';
    const contextKey = activeContextKey;
    try {
      const response = await assistantStore.promptVariants({ ...contextPayload(), count: 3 });
      if (contextKey !== activeContextKey) return;
      resultMode = 'variants';
      variants = response.variants;
      rewrittenPrompt = '';
      checkResult = null;
      paramsResult = null;
      resultContextKey = contextKey;
    } catch (caught) {
      showError(caught);
    }
  }

  async function runParams() {
    if (actionDisabled) return;
    error = '';
    const contextKey = activeContextKey;
    try {
      const response = await assistantStore.recommendParams({
        prompt: effectivePrompt,
        api_path: apiPath,
        current_model: model,
        current_size: size,
        current_quality: quality,
        current_output_format: outputFormat,
        current_n: quantity
      });
      if (contextKey !== activeContextKey) return;
      resultMode = 'params';
      paramsResult = response;
      rewrittenPrompt = '';
      checkResult = null;
      variants = [];
      resultContextKey = contextKey;
    } catch (caught) {
      showError(caught);
    }
  }

  function showError(caught: unknown, fallback = $t.messages.requestFailed) {
    const message = caught instanceof Error ? caught.message : fallback;
    error = message;
    dispatch('error', message);
  }

  function severityClass(severity: string) {
    if (severity === 'error') return 'border-red-500/35 text-red-300';
    if (severity === 'warning') return 'border-amber-500/35 text-amber-300';
    return 'border-cyan-500/30 text-cyan-300';
  }
</script>

<section data-testid="ai-assistant-panel" class="app-section px-1 py-1 sm:px-0">
  <div class="mb-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
    <div>
      <h2 class="text-sm font-semibold text-stone-950 dark:text-zinc-100">{$t.aiAssistant.title}</h2>
      <p class="mt-1 text-xs text-stone-500 dark:text-zinc-500">{$t.aiAssistant.subtitle}</p>
    </div>
    {#if unavailable}
      <span class="w-fit rounded-md border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-xs font-medium text-amber-700 dark:text-amber-200">
        {$t.aiAssistant.unavailable}
      </span>
    {/if}
  </div>

  <div class="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto]">
    <label class="block">
      <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.aiAssistant.instruction}</span>
      <input
        bind:value={instruction}
        class="control-focus w-full rounded-lg border border-stone-200 bg-stone-50 px-3 py-2.5 text-sm text-stone-900 focus:border-emerald-500 disabled:opacity-50 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-100"
        placeholder={$t.aiAssistant.instructionPlaceholder}
        disabled={unavailable}
      />
    </label>
    <div class="grid grid-cols-2 gap-2 self-end sm:grid-cols-5">
      <button type="button" disabled={actionDisabled} class="control-focus rounded-lg border border-emerald-500/40 px-3 py-2 text-xs font-semibold text-emerald-700 hover:bg-emerald-500/10 disabled:cursor-not-allowed disabled:border-stone-300 disabled:text-stone-400 disabled:opacity-60 dark:text-emerald-200 dark:disabled:border-zinc-700 dark:disabled:text-zinc-500" on:click={runRewrite}>
        {$t.aiAssistant.rewrite}
      </button>
      <button
        type="button"
        disabled={quickOptimizeDisabled}
        title={!optimizerEnabled ? $t.aiAssistant.quickOptimizeUnavailable : undefined}
        class="control-focus rounded-lg border border-emerald-500/40 px-3 py-2 text-xs font-semibold text-emerald-700 hover:bg-emerald-500/10 disabled:cursor-not-allowed disabled:border-stone-300 disabled:text-stone-400 disabled:opacity-60 dark:text-emerald-200 dark:disabled:border-zinc-700 dark:disabled:text-zinc-500"
        on:click={runQuickOptimize}
      >
        {$t.aiAssistant.quickOptimize}
      </button>
      <button type="button" disabled={actionDisabled} class="control-focus rounded-lg border border-cyan-500/35 px-3 py-2 text-xs font-semibold text-cyan-700 hover:bg-cyan-500/10 disabled:cursor-not-allowed disabled:border-stone-300 disabled:text-stone-400 disabled:opacity-60 dark:text-cyan-200 dark:disabled:border-zinc-700 dark:disabled:text-zinc-500" on:click={runCheck}>
        {$t.aiAssistant.check}
      </button>
      <button type="button" disabled={actionDisabled} class="control-focus rounded-lg border border-violet-500/35 px-3 py-2 text-xs font-semibold text-violet-700 hover:bg-violet-500/10 disabled:cursor-not-allowed disabled:border-stone-300 disabled:text-stone-400 disabled:opacity-60 dark:text-violet-200 dark:disabled:border-zinc-700 dark:disabled:text-zinc-500" on:click={runVariants}>
        {$t.aiAssistant.variants}
      </button>
      <button type="button" disabled={actionDisabled} class="control-focus rounded-lg border border-stone-300 px-3 py-2 text-xs font-semibold text-stone-700 hover:bg-stone-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" on:click={runParams}>
        {$t.aiAssistant.params}
      </button>
    </div>
  </div>

  {#if panelBusy}
    <div class="mt-4 rounded-lg border border-zinc-800 bg-zinc-950/35 px-3 py-2 text-xs text-zinc-400">{$t.aiAssistant.working}</div>
  {/if}

  {#if error}
    <div class="mt-4 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-200">{error}</div>
  {/if}

  {#if (resultMode === 'rewrite' || resultMode === 'quickOptimize') && rewrittenPrompt}
    <div class="mt-4 rounded-xl border border-stone-200 bg-stone-50 p-3 dark:border-zinc-800 dark:bg-zinc-950/50">
      <div class="mb-2 text-xs font-semibold text-stone-500 dark:text-zinc-400">{resultMode === 'quickOptimize' ? $t.aiAssistant.quickOptimizeResult : $t.aiAssistant.rewriteResult}</div>
      <p class="whitespace-pre-wrap text-sm leading-6 text-stone-900 dark:text-zinc-100">{rewrittenPrompt}</p>
      <div class="mt-3 flex flex-wrap justify-end gap-2">
        <button type="button" class="control-focus rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800" on:click={() => onInsertPrompt(rewrittenPrompt)}>{$t.aiAssistant.insert}</button>
        <button type="button" class="control-focus rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800" on:click={() => onSaveSnippet(rewrittenPrompt)}>{$t.aiAssistant.saveSnippet}</button>
        <button type="button" class="control-focus rounded-lg bg-emerald-600 px-3 py-2 text-xs font-semibold text-white hover:bg-emerald-500" on:click={() => onApplyPrompt(rewrittenPrompt)}>{$t.common.apply}</button>
      </div>
    </div>
  {:else if resultMode === 'check' && checkResult}
    <div class="mt-4 rounded-xl border border-stone-200 bg-stone-50 p-3 dark:border-zinc-800 dark:bg-zinc-950/50">
      <div class="flex items-center justify-between gap-3">
        <span class="text-xs font-semibold text-stone-500 dark:text-zinc-400">{$t.aiAssistant.checkResult}</span>
        <span class="rounded-md border border-zinc-700 px-2 py-0.5 font-mono text-[11px] text-zinc-300">{checkResult.score}/100</span>
      </div>
      <p class="mt-2 text-sm leading-6 text-stone-900 dark:text-zinc-100">{checkResult.summary}</p>
      {#if checkResult.issues.length}
        <div class="mt-3 space-y-2">
          {#each checkResult.issues as issue}
            <div class={`rounded-lg border px-3 py-2 text-xs ${severityClass(issue.severity)}`}>
              <div class="font-semibold">{issue.message}</div>
              {#if issue.suggestion}
                <div class="mt-1 text-zinc-400">{issue.suggestion}</div>
              {/if}
            </div>
          {/each}
        </div>
      {/if}
    </div>
  {:else if resultMode === 'variants' && variants.length}
    <div class="mt-4 grid gap-3">
      {#each variants as variant}
        <article class="rounded-xl border border-stone-200 bg-stone-50 p-3 dark:border-zinc-800 dark:bg-zinc-950/50">
          <div class="mb-2 flex items-start justify-between gap-3">
            <div>
              <h3 class="text-xs font-semibold text-stone-900 dark:text-zinc-100">{variant.title}</h3>
              {#if variant.angle}
                <p class="mt-1 text-xs text-stone-500 dark:text-zinc-500">{variant.angle}</p>
              {/if}
            </div>
          </div>
          <p class="whitespace-pre-wrap text-sm leading-6 text-stone-800 dark:text-zinc-200">{variant.prompt}</p>
          <div class="mt-3 flex flex-wrap justify-end gap-2">
            <button type="button" class="control-focus rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800" on:click={() => onInsertPrompt(variant.prompt)}>{$t.aiAssistant.insert}</button>
            <button type="button" class="control-focus rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800" on:click={() => onSaveSnippet(variant.prompt)}>{$t.aiAssistant.saveSnippet}</button>
            <button type="button" class="control-focus rounded-lg bg-emerald-600 px-3 py-2 text-xs font-semibold text-white hover:bg-emerald-500" on:click={() => onApplyPrompt(variant.prompt)}>{$t.common.apply}</button>
          </div>
        </article>
      {/each}
    </div>
  {:else if resultMode === 'params' && paramsResult}
    <div class="mt-4 rounded-xl border border-stone-200 bg-stone-50 p-3 dark:border-zinc-800 dark:bg-zinc-950/50">
      <div class="mb-2 text-xs font-semibold text-stone-500 dark:text-zinc-400">{$t.aiAssistant.paramsResult}</div>
      <p class="text-sm leading-6 text-stone-900 dark:text-zinc-100">{paramsResult.rationale}</p>
      <div class="mt-3 flex flex-wrap gap-2 text-[11px] text-zinc-400">
        {#if paramsResult.model_name}<span class="rounded border border-zinc-700 px-2 py-1">model: {paramsResult.model_name}</span>{/if}
        {#if paramsResult.size}<span class="rounded border border-zinc-700 px-2 py-1">size: {paramsResult.size}</span>{/if}
        {#if paramsResult.quality}<span class="rounded border border-zinc-700 px-2 py-1">quality: {paramsResult.quality}</span>{/if}
        {#if paramsResult.output_format}<span class="rounded border border-zinc-700 px-2 py-1">format: {paramsResult.output_format}</span>{/if}
        {#if paramsResult.n}<span class="rounded border border-zinc-700 px-2 py-1">n: {paramsResult.n}</span>{/if}
      </div>
      {#if paramsResult.warnings.length}
        <div class="mt-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">{paramsResult.warnings.join(' ')}</div>
      {/if}
      <div class="mt-3 flex justify-end">
        <button type="button" class="control-focus rounded-lg bg-emerald-600 px-3 py-2 text-xs font-semibold text-white hover:bg-emerald-500" on:click={() => paramsResult && onApplyParams(paramsResult)}>{$t.aiAssistant.applyParams}</button>
      </div>
    </div>
  {/if}
</section>
