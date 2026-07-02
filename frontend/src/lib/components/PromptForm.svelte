<script lang="ts">
  import PromptHelperPanel from '$lib/components/PromptHelperPanel.svelte';
  import { plainTextInput } from '$lib/actions/plainTextInput';
  import { t } from '$lib/i18n';
  import type { PromptFormState } from '$lib/stores/preview';
  import { RESPONSE_FORMAT_OPTIONS, sanitizeQuantityInput } from '$lib/utils/promptForm';

  export let form: PromptFormState;
  export let loading = false;
  export let optimizing = false;
  export let optimizerEnabled = false;
  export let editPlannerEnabled = false;
  export let editPlanning = false;
  export let onGenerate: () => void = () => {};
  export let onEdit: () => void = () => {};
  export let onOpenSize: () => void = () => {};
  export let onOptimize: () => void = () => {};
  export let onPlanEdit: () => void = () => {};
  export let onAppendPromptTag: (value: string) => void = () => {};

  $: promptLen = form.prompt.length;
  $: promptOnlyMode = form.apiPath === '/v1/responses' || form.apiPath === '/v1/chat/completions';
  $: modeLabel = form.apiPath === '/v1/chat/completions' ? $t.promptForm.chatCompletionsMode : $t.promptForm.responsesMode;
  $: disabledModeLabel =
    form.apiPath === '/v1/chat/completions' ? $t.promptForm.disabledForChatCompletions : $t.promptForm.disabledForResponses;
  $: compressionPlaceholder = promptOnlyMode
    ? disabledModeLabel
    : form.outputFormat === 'png'
      ? $t.promptForm.disabledForPng
      : '0-100';
  $: optimizeDisabled = loading || optimizing || !optimizerEnabled || !form.prompt.trim();

  function handleQuantityInput() {
    form = { ...form, quantity: sanitizeQuantityInput(form.quantity) };
  }

  function clampCompression() {
    if (form.outputCompression === '') return;
    form = { ...form, outputCompression: String(Math.min(Math.max(Number(form.outputCompression) || 0, 0), 100)) };
  }

  $: if (form.outputFormat === 'png' && form.outputCompression !== '') form = { ...form, outputCompression: '' };
</script>

<section class="rounded-2xl border border-stone-200 bg-white/80 p-4 shadow-sm shadow-stone-200/60 sm:p-5 dark:border-zinc-800 dark:bg-zinc-900/60 dark:shadow-none">
  <div class="mb-4 flex items-start justify-between gap-4">
    <div>
      <h2 class="text-sm font-semibold text-stone-950 dark:text-zinc-100">{$t.promptForm.title}</h2>
      <p class="mt-1 text-xs text-stone-500 dark:text-zinc-500">{$t.promptForm.subtitle}</p>
    </div>
    {#if promptOnlyMode}
      <span class="rounded-md border border-cyan-500/30 bg-cyan-500/10 px-2 py-1 text-xs font-medium text-cyan-200">{modeLabel}</span>
    {/if}
  </div>

  <div class="grid gap-4 lg:grid-cols-[minmax(0,1fr)_260px] lg:items-stretch">
    <div class="min-w-0 flex h-full flex-col">
      <div class="mb-2 flex items-center justify-between gap-3">
        <label for="prompt" class="text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.common.prompt}</label>
        <button
          type="button"
          disabled={optimizeDisabled}
          class="control-focus rounded-lg border border-emerald-500/40 px-3 py-1.5 text-xs font-semibold text-emerald-700 hover:bg-emerald-500/10 disabled:cursor-not-allowed disabled:border-stone-300 disabled:text-stone-400 disabled:opacity-60 dark:text-emerald-200 dark:disabled:border-zinc-700 dark:disabled:text-zinc-500"
          title={optimizerEnabled ? $t.promptForm.optimize : $t.promptForm.optimizerUnavailable}
          on:click={onOptimize}
        >
          {optimizing ? $t.promptForm.optimizing : $t.promptForm.optimize}
        </button>
      </div>
      <div class="relative flex min-h-[13rem] flex-1">
        <textarea
          id="prompt"
          name="prompt"
          bind:value={form.prompt}
          maxlength="4000"
          rows="8"
          autocomplete="off"
          spellcheck="false"
          aria-label={$t.common.prompt}
          placeholder={$t.promptForm.placeholder}
          class="control-focus h-full min-h-[13rem] w-full flex-1 resize-y rounded-xl border border-stone-200 bg-stone-50 px-4 py-3 pb-8 text-sm leading-6 text-stone-900 focus:border-emerald-500 lg:resize-none dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-100"
          use:plainTextInput
        ></textarea>
        <div class="pointer-events-none absolute bottom-3 right-4 text-xs text-stone-500 dark:text-zinc-500">{promptLen}/4000</div>
      </div>
    </div>

    <PromptHelperPanel onAppend={onAppendPromptTag} />
  </div>

  <div class="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
    <label class="block">
      <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.promptForm.apiPath}</span>
      <select bind:value={form.apiPath} disabled={loading} class="control-focus form-select font-mono focus:border-emerald-500">
        <option value="/v1/images/generations">/v1/images/generations</option>
        <option value="/v1/responses">/v1/responses</option>
        <option value="/v1/chat/completions">/v1/chat/completions</option>
      </select>
    </label>

    <label class="block">
      <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.common.model}</span>
      <input bind:value={form.model} class="control-focus w-full rounded-lg border border-stone-200 bg-stone-50 px-3 py-2.5 font-mono text-sm text-stone-900 focus:border-emerald-500 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-100" />
    </label>

    <label class="block">
      <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.common.size}</span>
      <button
        type="button"
        disabled={promptOnlyMode || loading}
        class="control-focus w-full rounded-lg border border-stone-200 bg-stone-50 px-3 py-2.5 text-left font-mono text-sm text-stone-900 hover:bg-stone-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-100 dark:hover:bg-zinc-900"
        on:click={onOpenSize}
      >
        {promptOnlyMode ? disabledModeLabel : form.size}
      </button>
    </label>

    <label class="block">
      <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.promptForm.quality}</span>
      <select bind:value={form.quality} disabled={promptOnlyMode || loading} class="control-focus form-select focus:border-emerald-500">
        <option value="auto">auto</option>
        <option value="low">low</option>
        <option value="medium">medium</option>
        <option value="high">high</option>
      </select>
    </label>

    <label class="block">
      <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.promptForm.quantity}</span>
      <input
        bind:value={form.quantity}
        disabled={promptOnlyMode || loading}
        type="text"
        inputmode="numeric"
        pattern="[0-9]*"
        class="control-focus w-full rounded-lg border border-stone-200 bg-stone-50 px-3 py-2.5 text-sm text-stone-900 focus:border-emerald-500 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-100"
        on:input={handleQuantityInput}
      />
    </label>

    <label class="block">
      <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.promptForm.format}</span>
      <select bind:value={form.outputFormat} disabled={promptOnlyMode || loading} class="control-focus form-select focus:border-emerald-500">
        <option value="png">png</option>
        <option value="jpeg">jpeg</option>
        <option value="webp">webp</option>
      </select>
    </label>

    <label class="block">
      <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.promptForm.compression}</span>
      <input bind:value={form.outputCompression} disabled={promptOnlyMode || loading || form.outputFormat === 'png'} type="number" min="0" max="100" placeholder={compressionPlaceholder} class="control-focus w-full rounded-lg border border-stone-200 bg-stone-50 px-3 py-2.5 text-sm text-stone-900 focus:border-emerald-500 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-100" on:input={clampCompression} />
    </label>

    <label class="block">
      <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.promptForm.responseFormat}</span>
      <select bind:value={form.responseFormat} disabled={promptOnlyMode || loading} class="control-focus form-select focus:border-emerald-500">
        {#each RESPONSE_FORMAT_OPTIONS as responseFormat}
          <option value={responseFormat}>{responseFormat || $t.promptForm.defaultResponseFormat}</option>
        {/each}
      </select>
    </label>

  </div>

  <div class="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
    <slot name="edit-source" />
    <div class="flex gap-2">
      <button type="button" disabled={loading} class="control-focus rounded-xl bg-stone-900 px-4 py-3 text-sm font-semibold text-white hover:bg-stone-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-zinc-700 dark:hover:bg-zinc-600" on:click={onEdit}>
        {$t.promptForm.edits}
      </button>
      <button
        type="button"
        disabled={loading || editPlanning || !editPlannerEnabled || !form.prompt.trim()}
        class="control-focus rounded-xl border border-cyan-500/35 px-4 py-3 text-sm font-semibold text-cyan-700 hover:bg-cyan-500/10 disabled:cursor-not-allowed disabled:border-stone-300 disabled:text-stone-400 disabled:opacity-60 dark:text-cyan-200 dark:disabled:border-zinc-700 dark:disabled:text-zinc-500"
        on:click={onPlanEdit}
      >
        {editPlanning ? $t.promptForm.planningEdit : $t.promptForm.planEdit}
      </button>
      <button type="button" disabled={loading} class="control-focus rounded-xl bg-emerald-600 px-4 py-3 text-sm font-semibold text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50" on:click={onGenerate}>
        {$t.promptForm.generate}
      </button>
    </div>
  </div>
</section>
