<script lang="ts">
  import { browser } from '$app/environment';
  import { onDestroy, onMount } from 'svelte';
  import { dialog } from '$lib/actions/dialog';
  import { plainTextInput } from '$lib/actions/plainTextInput';
  import { apiFetch } from '$lib/api/client';
  import { language, t } from '$lib/i18n';
  import type { ApiPath, PromptOptimizeResponse } from '$lib/api/types';
  import { initialPromptFormState, type PromptFormState } from '$lib/stores/preview';
  import { buildPromptOptimizeRequest } from '$lib/utils/promptOptimizer';

  export let currentPrompt = '';
  export let apiPath: ApiPath = initialPromptFormState.apiPath;
  export let model = initialPromptFormState.model;
  export let size = initialPromptFormState.size;
  export let quality: PromptFormState['quality'] = initialPromptFormState.quality;
  export let onApplyPrompt: (prompt: string) => void = () => {};
  export let enabled = true;

  type Phase = 'draft' | 'review';
  type FloatingPosition = {
    x: number;
    y: number;
  };

  const STORAGE_KEY = 'gpt-image-panel-prompt-optimizer-position';
  const LONG_PRESS_MS = 260;
  const TAP_MOVE_THRESHOLD = 8;
  const VIEWPORT_EDGE_GAP = 12;

  let triggerButton: HTMLButtonElement | null = null;
  let open = false;
  let phase: Phase = 'draft';
  let intent = '';
  let error = '';
  let optimizing = false;
  let optimizedPrompt = '';
  let originalPrompt = '';
  let submittedIntent = '';
  let requestSeq = 0;
  let activeRequest: AbortController | null = null;
  let floatingPosition: FloatingPosition | null = browser ? readStoredPosition() : null;
  let pressTimer: ReturnType<typeof setTimeout> | null = null;
  let activePointerId: number | null = null;
  let pointerDownAt: FloatingPosition | null = null;
  let dragCenterOffset: FloatingPosition | null = null;
  let dragPending = false;
  let dragging = false;
  let suppressClick = false;
  let lastPointerPosition: FloatingPosition | null = null;
  let resizeObserver: ResizeObserver | null = null;
  let observedTriggerButton: HTMLButtonElement | null = null;
  let triggerSize: { width: number; height: number } | null = null;

  $: submitDisabled = optimizing || !intent.trim() || !currentPrompt.trim();
  $: if (!enabled && open) closeAssistant();
  $: syncTriggerObserver(enabled, triggerButton);

  function readStoredPosition(): FloatingPosition | null {
    if (!browser) return null;
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw) as Partial<FloatingPosition>;
      if (typeof parsed.x !== 'number' || typeof parsed.y !== 'number') return null;
      return { x: parsed.x, y: parsed.y };
    } catch {
      return null;
    }
  }

  function persistPosition(position: FloatingPosition) {
    if (!browser) return;
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(position));
    } catch {
      // Ignore storage failures; the capsule can still move for this session.
    }
  }

  function readTriggerSize() {
    if (!browser) return null;

    const rect = triggerButton?.getBoundingClientRect();
    if (rect && rect.width > 0 && rect.height > 0) {
      triggerSize = { width: rect.width, height: rect.height };
      return triggerSize;
    }

    return triggerSize;
  }

  function clampPosition(position: FloatingPosition) {
    const size = readTriggerSize();
    if (!browser || !size) return position;

    const maxX = Math.max(VIEWPORT_EDGE_GAP, window.innerWidth - size.width - VIEWPORT_EDGE_GAP);
    const maxY = Math.max(VIEWPORT_EDGE_GAP, window.innerHeight - size.height - VIEWPORT_EDGE_GAP);

    return {
      x: Math.min(Math.max(position.x, VIEWPORT_EDGE_GAP), maxX),
      y: Math.min(Math.max(position.y, VIEWPORT_EDGE_GAP), maxY)
    };
  }

  function setFloatingPosition(position: FloatingPosition, persist = true) {
    const nextPosition = clampPosition(position);
    floatingPosition = nextPosition;
    if (persist) persistPosition(nextPosition);
  }

  function clampToViewport() {
    if (!floatingPosition) return;
    floatingPosition = clampPosition(floatingPosition);
  }

  function disconnectTriggerObserver() {
    resizeObserver?.disconnect();
    resizeObserver = null;
    observedTriggerButton = null;
    triggerSize = null;
  }

  function syncTriggerObserver(isEnabled: boolean, button: HTMLButtonElement | null) {
    if (!browser) return;

    if (!isEnabled || !button) {
      if (resizeObserver || observedTriggerButton || triggerSize) disconnectTriggerObserver();
      return;
    }

    if (observedTriggerButton === button) {
      readTriggerSize();
      clampToViewport();
      return;
    }

    disconnectTriggerObserver();
    observedTriggerButton = button;
    readTriggerSize();
    clampToViewport();

    if ('ResizeObserver' in window) {
      resizeObserver = new ResizeObserver(() => {
        readTriggerSize();
        clampToViewport();
      });
      resizeObserver.observe(button);
    }
  }

  function clearPressTimer() {
    if (pressTimer) clearTimeout(pressTimer);
    pressTimer = null;
  }

  function resetPointerState() {
    clearPressTimer();
    activePointerId = null;
    pointerDownAt = null;
    dragCenterOffset = null;
    dragPending = false;
    dragging = false;
    lastPointerPosition = null;
  }

  function resetState() {
    phase = 'draft';
    intent = '';
    error = '';
    optimizing = false;
    optimizedPrompt = '';
    originalPrompt = '';
    submittedIntent = '';
  }

  function closeAssistant() {
    requestSeq += 1;
    activeRequest?.abort();
    activeRequest = null;
    open = false;
    resetState();
  }

  function openAssistant() {
    open = true;
    phase = 'draft';
    error = '';
    optimizedPrompt = '';
    originalPrompt = currentPrompt.trim();
    submittedIntent = '';
    intent = '';
  }

  async function submitOptimization() {
    const trimmedPrompt = currentPrompt.trim();
    const trimmedIntent = intent.trim();
    if (!trimmedPrompt || !trimmedIntent || optimizing) return;

    requestSeq += 1;
    const seq = requestSeq;
    activeRequest?.abort();
    activeRequest = new AbortController();
    optimizing = true;
    error = '';

    try {
      const response = await apiFetch<PromptOptimizeResponse>(
        '/api/prompt/optimize',
        {
          method: 'POST',
          signal: activeRequest.signal,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(
            buildPromptOptimizeRequest({
              prompt: trimmedPrompt,
              intent: trimmedIntent,
              targetLanguage: $language,
              apiPath,
              model,
              size,
              quality
            })
          )
        },
        'optimizing prompt'
      );

      if (seq !== requestSeq) return;

      originalPrompt = trimmedPrompt;
      submittedIntent = trimmedIntent;
      optimizedPrompt = response.optimized_prompt;
      phase = 'review';
    } catch (caught) {
      if (seq !== requestSeq) return;
      if (caught instanceof Error && caught.name === 'AbortError') return;
      error = caught instanceof Error ? caught.message : $t.messages.promptOptimizeFailed;
    } finally {
      if (seq === requestSeq) {
        optimizing = false;
        activeRequest = null;
      }
    }
  }

  function acceptOptimization() {
    if (!optimizedPrompt) return;
    onApplyPrompt(optimizedPrompt);
    closeAssistant();
  }

  function rejectOptimization() {
    closeAssistant();
  }

  function startDrag(event: PointerEvent) {
    if (!triggerButton || activePointerId !== event.pointerId || !pointerDownAt) return;

    const rect = triggerButton.getBoundingClientRect();
    const anchor = lastPointerPosition || pointerDownAt;
    dragCenterOffset = {
      x: rect.width / 2,
      y: rect.height / 2
    };
    dragging = true;
    dragPending = false;
    suppressClick = true;
    setFloatingPosition(
      {
        x: anchor.x - dragCenterOffset.x,
        y: anchor.y - dragCenterOffset.y
      },
      true
    );
  }

  function handlePointerDown(event: PointerEvent) {
    if (!enabled || event.button !== 0 || !event.isPrimary || open) return;

    activePointerId = event.pointerId;
    pointerDownAt = {
      x: event.clientX,
      y: event.clientY
    };
    lastPointerPosition = pointerDownAt;
    dragPending = true;
    suppressClick = false;
    clearPressTimer();

    try {
      triggerButton?.setPointerCapture(event.pointerId);
    } catch {
      // Pointer capture is best-effort on the floating trigger.
    }

    pressTimer = setTimeout(() => {
      pressTimer = null;
      if (dragPending) startDrag(event);
    }, LONG_PRESS_MS);
  }

  function handlePointerMove(event: PointerEvent) {
    if (activePointerId !== event.pointerId || !pointerDownAt) return;

    lastPointerPosition = {
      x: event.clientX,
      y: event.clientY
    };
    const deltaX = event.clientX - pointerDownAt.x;
    const deltaY = event.clientY - pointerDownAt.y;

    if (!dragging) {
      if (Math.hypot(deltaX, deltaY) > TAP_MOVE_THRESHOLD) suppressClick = true;
      return;
    }

    if (!dragCenterOffset) return;
    setFloatingPosition(
      {
        x: event.clientX - dragCenterOffset.x,
        y: event.clientY - dragCenterOffset.y
      },
      true
    );
  }

  function handlePointerUp(event: PointerEvent) {
    if (activePointerId !== event.pointerId) return;

    if (triggerButton?.hasPointerCapture(event.pointerId)) {
      try {
        triggerButton.releasePointerCapture(event.pointerId);
      } catch {
        // Ignore capture release failures.
      }
    }

    clearPressTimer();
    activePointerId = null;
    pointerDownAt = null;
    dragCenterOffset = null;
    dragPending = false;
    dragging = false;
    lastPointerPosition = null;
  }

  function handlePointerCancel(event: PointerEvent) {
    if (activePointerId !== event.pointerId) return;
    resetPointerState();
    suppressClick = false;
  }

  function handleClick(event: MouseEvent) {
    if (suppressClick) {
      event.preventDefault();
      suppressClick = false;
      return;
    }

    openAssistant();
  }

  onMount(() => {
    if (floatingPosition) {
      setFloatingPosition(floatingPosition, false);
    }

    window.addEventListener('resize', clampToViewport);

    return () => {
      window.removeEventListener('resize', clampToViewport);
      disconnectTriggerObserver();
      resetPointerState();
    };
  });

  onDestroy(() => {
    activeRequest?.abort();
    resetPointerState();
  });
</script>

{#if enabled}
  <button
    type="button"
    bind:this={triggerButton}
    data-testid="prompt-optimizer-assistant-trigger"
    class:cursor-grabbing={dragging}
    class="control-focus fixed bottom-4 right-4 z-40 inline-flex touch-none select-none items-center gap-2 rounded-full border border-emerald-500/30 bg-emerald-600 px-4 py-3 text-sm font-semibold text-white shadow-[0_18px_36px_-24px_rgba(16,185,129,0.6)] transition-transform hover:bg-emerald-500 active:scale-[0.98] sm:bottom-6 sm:right-6"
    aria-haspopup="dialog"
    aria-expanded={open}
    aria-label={$t.promptOptimizerAssistant.open}
    title={$t.promptOptimizerAssistant.open}
    style:left={floatingPosition ? `${floatingPosition.x}px` : null}
    style:top={floatingPosition ? `${floatingPosition.y}px` : null}
    style:right={floatingPosition ? 'auto' : null}
    style:bottom={floatingPosition ? 'auto' : null}
    on:pointerdown={handlePointerDown}
    on:pointermove={handlePointerMove}
    on:pointerup={handlePointerUp}
    on:pointercancel={handlePointerCancel}
    on:click={handleClick}
    on:contextmenu|preventDefault
  >
    <svg viewBox="0 0 24 24" class="h-4 w-4 shrink-0 fill-none stroke-current stroke-[1.8]" aria-hidden="true">
      <path d="M4 12h6"></path>
      <path d="M8 8.5 9.5 5 11 8.5 14.5 10 11 11.5 9.5 15 8 11.5 4.5 10 8 8.5Z"></path>
      <path d="M15.5 13.5 16.5 11l1 2.5 2.5 1-2.5 1-1 2.5-1-2.5-2.5-1 2.5-1Z"></path>
    </svg>
    <span class="whitespace-nowrap">{$t.promptOptimizerAssistant.open}</span>
  </button>
{/if}

{#if enabled && open}
  <div class="mobile-dialog-root fixed inset-0 z-[92] flex items-center justify-center bg-black/65 p-4">
    <button
      type="button"
      class="absolute inset-0"
      tabindex="-1"
      aria-label={$t.promptOptimizerAssistant.closeLabel}
      on:click={closeAssistant}
    ></button>
    <div
      class="mobile-dvh-dialog fade-in relative z-10 flex w-full max-w-4xl flex-col overflow-hidden rounded-2xl border border-stone-200 bg-white shadow-[0_24px_90px_-32px_rgba(15,23,42,0.45)] dark:border-zinc-800 dark:bg-zinc-950 dark:shadow-none"
      aria-labelledby="prompt-optimizer-assistant-title"
      use:dialog={{ open, onClose: closeAssistant }}
    >
      <div class="flex items-start justify-between gap-3 border-b border-stone-200 px-5 py-4 dark:border-zinc-800 sm:px-6">
        <div class="min-w-0">
          <h2 id="prompt-optimizer-assistant-title" class="text-base font-semibold text-stone-950 dark:text-zinc-100">
            {$t.promptOptimizerAssistant.title}
          </h2>
          <p class="mt-1 text-xs text-stone-500 dark:text-zinc-500">
            {phase === 'draft' ? $t.promptOptimizerAssistant.intentLabel : $t.promptOptimizerAssistant.reviewTitle}
          </p>
        </div>
        <button
          type="button"
          class="control-focus rounded-lg p-2 text-stone-500 hover:bg-stone-100 hover:text-stone-950 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
          aria-label={$t.promptOptimizerAssistant.closeLabel}
          title={$t.promptOptimizerAssistant.closeLabel}
          on:click={closeAssistant}
        >
          <svg viewBox="0 0 24 24" class="h-4 w-4 fill-none stroke-current stroke-[1.8]" aria-hidden="true">
            <path d="M6 6l12 12"></path>
            <path d="M18 6 6 18"></path>
          </svg>
        </button>
      </div>

      <div class="flex-1 overflow-y-auto px-5 py-5 sm:px-6">
        {#if phase === 'draft'}
          <div class="space-y-4">
            <div class="space-y-2">
              <label for="prompt-optimizer-intent" class="block text-xs font-medium text-stone-600 dark:text-zinc-400">
                {$t.promptOptimizerAssistant.intentLabel}
              </label>
              <textarea
                id="prompt-optimizer-intent"
                bind:value={intent}
                rows="5"
                maxlength="1200"
                placeholder={$t.promptOptimizerAssistant.intentPlaceholder}
                class="control-focus min-h-[9rem] w-full resize-y rounded-xl border border-stone-200 bg-stone-50 px-4 py-3 text-sm leading-6 text-stone-900 focus:border-emerald-500 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-100"
                use:plainTextInput
                data-autofocus
              ></textarea>
              <p class="text-xs leading-5 text-stone-500 dark:text-zinc-500">{$t.promptOptimizerAssistant.intentHint}</p>
              {#if !currentPrompt.trim()}
                <p class="text-xs leading-5 text-amber-700 dark:text-amber-200">{$t.promptOptimizerAssistant.emptyPrompt}</p>
              {/if}
            </div>

            <div class="space-y-2">
              <div class="flex items-center justify-between gap-3">
                <span class="text-xs font-medium text-stone-600 dark:text-zinc-400">
                  {$t.promptOptimizerAssistant.currentPromptLabel}
                </span>
                <span class="text-xs text-stone-500 dark:text-zinc-500">{currentPrompt.trim().length}/4000</span>
              </div>
              <div class="rounded-xl border border-stone-200 bg-stone-50 px-4 py-3 text-sm leading-6 text-stone-700 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300">
                {#if currentPrompt.trim()}
                  <pre class="whitespace-pre-wrap break-words font-[inherit]">{currentPrompt}</pre>
                {:else}
                  <p>{$t.promptOptimizerAssistant.emptyPrompt}</p>
                {/if}
              </div>
            </div>

            {#if error}
              <p class="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-200">{error}</p>
            {/if}

            <div class="flex items-center justify-end gap-3 border-t border-stone-200 pt-4 dark:border-zinc-800">
              <button
                type="button"
                class="control-focus rounded-lg border border-stone-300 px-4 py-2.5 text-sm font-medium text-stone-700 hover:bg-stone-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
                on:click={closeAssistant}
              >
                {$t.common.close}
              </button>
              <button
                type="button"
                disabled={submitDisabled}
                class="control-focus rounded-lg bg-emerald-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
                on:click={submitOptimization}
              >
                {optimizing ? $t.promptOptimizerAssistant.optimizing : $t.promptOptimizerAssistant.optimize}
              </button>
            </div>
          </div>
        {:else}
          <div class="space-y-4">
            <div class="rounded-xl border border-emerald-500/20 bg-emerald-500/[0.08] px-4 py-3 text-sm text-emerald-950 dark:bg-emerald-500/10 dark:text-emerald-50">
              <div class="text-xs font-medium uppercase tracking-wide text-emerald-700 dark:text-emerald-200">
                {$t.promptOptimizerAssistant.intentLabel}
              </div>
              <p class="mt-2 whitespace-pre-wrap break-words leading-6">{submittedIntent}</p>
            </div>

            <div class="grid gap-3 lg:grid-cols-2">
              <section class="min-w-0 rounded-xl border border-stone-200 bg-stone-50 p-4 dark:border-zinc-800 dark:bg-zinc-900" data-testid="prompt-optimizer-original">
                <div class="mb-3 flex items-center justify-between gap-3">
                  <h3 class="text-xs font-semibold uppercase tracking-wide text-stone-500 dark:text-zinc-500">
                    {$t.promptOptimizerAssistant.originalPrompt}
                  </h3>
                </div>
                <pre class="max-h-[20rem] overflow-auto whitespace-pre-wrap break-words text-sm leading-6 text-stone-800 dark:text-zinc-200">{originalPrompt}</pre>
              </section>

              <section class="min-w-0 rounded-xl border border-emerald-500/30 bg-emerald-500/[0.08] p-4 dark:bg-emerald-500/10" data-testid="prompt-optimizer-optimized">
                <div class="mb-3 flex items-center justify-between gap-3">
                  <h3 class="text-xs font-semibold uppercase tracking-wide text-emerald-700 dark:text-emerald-200">
                    {$t.promptOptimizerAssistant.optimizedPrompt}
                  </h3>
                </div>
                <pre class="max-h-[20rem] overflow-auto whitespace-pre-wrap break-words text-sm leading-6 text-emerald-950 dark:text-emerald-50">{optimizedPrompt}</pre>
              </section>
            </div>

            <div class="flex items-center justify-end gap-3 border-t border-stone-200 pt-4 dark:border-zinc-800">
              <button
                type="button"
                class="control-focus rounded-lg border border-stone-300 px-4 py-2.5 text-sm font-medium text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
                on:click={rejectOptimization}
              >
                {$t.common.reject}
              </button>
              <button
                type="button"
                class="control-focus rounded-lg bg-emerald-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-emerald-500"
                on:click={acceptOptimization}
              >
                {$t.common.accept}
              </button>
            </div>
          </div>
        {/if}
      </div>
    </div>
  </div>
{/if}
