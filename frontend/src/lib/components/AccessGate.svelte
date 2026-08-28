<script lang="ts">
  import { onDestroy, tick } from 'svelte';
  import { browser } from '$app/environment';
  import { dialog } from '$lib/actions/dialog';
  import { language, t, toggleLanguage } from '$lib/i18n';

  export let visible = false;
  export let error = '';
  export let loading = false;
  export let turnstileEnabled = false;
  export let turnstileSiteKey = '';
  export let onUnlock: (accessKey: string, turnstileToken: string) => Promise<void> | void = () => {};

  type TurnstileApi = {
    render: (el: HTMLElement, options: Record<string, unknown>) => string;
    reset: (widgetId?: string) => void;
    remove: (widgetId: string) => void;
  };

  const TURNSTILE_SRC = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';

  let accessKey = '';
  let localError = '';
  let turnstileToken = '';
  let widgetContainer: HTMLElement | null = null;
  let widgetId: string | null = null;
  let wasLoading = false;
  const accessInputId = 'access-gate-access-key';
  const accessErrorId = 'access-gate-error';

  $: combinedError = error || localError;

  function getTurnstile(): TurnstileApi | undefined {
    return (window as unknown as { turnstile?: TurnstileApi }).turnstile;
  }

  let scriptPromise: Promise<TurnstileApi> | null = null;

  function loadTurnstile(): Promise<TurnstileApi> {
    const existing = getTurnstile();
    if (existing) return Promise.resolve(existing);
    if (!scriptPromise) {
      scriptPromise = new Promise<TurnstileApi>((resolve, reject) => {
        const script = document.createElement('script');
        script.src = TURNSTILE_SRC;
        script.async = true;
        script.onload = () => {
          const api = getTurnstile();
          if (api) resolve(api);
          else reject(new Error('turnstile-missing'));
        };
        script.onerror = () => {
          scriptPromise = null;
          reject(new Error('turnstile-load-failed'));
        };
        document.head.appendChild(script);
      });
    }
    return scriptPromise;
  }

  async function renderWidget() {
    if (!browser || !widgetContainer || !turnstileSiteKey || widgetId !== null) return;
    try {
      const api = await loadTurnstile();
      if (!widgetContainer || widgetId !== null) return;
      widgetId = api.render(widgetContainer, {
        sitekey: turnstileSiteKey,
        theme: 'auto',
        callback: (token: string) => {
          turnstileToken = token;
          localError = '';
        },
        'expired-callback': () => {
          turnstileToken = '';
        },
        'error-callback': () => {
          turnstileToken = '';
        }
      });
    } catch {
      localError = $t.access.turnstileUnavailable;
    }
  }

  function resetWidget() {
    turnstileToken = '';
    if (widgetId !== null) getTurnstile()?.reset(widgetId);
  }

  function destroyWidget() {
    if (widgetId !== null) {
      try {
        getTurnstile()?.remove(widgetId);
      } catch {
        // Widget already gone.
      }
      widgetId = null;
    }
    turnstileToken = '';
  }

  $: if (visible && turnstileEnabled && turnstileSiteKey && browser) {
    // Wait for the conditional widget container to be mounted before rendering.
    // Without this tick, the reactive block can run while widgetContainer is
    // still null and never run again after bind:this completes.
    void tick().then(() => renderWidget());
  }

  $: {
    if (wasLoading && !loading && visible) {
      resetWidget();
    }
    wasLoading = loading;
  }

  onDestroy(() => {
    destroyWidget();
  });

  async function submit() {
    const value = accessKey.trim();
    if (!value) {
      localError = $t.access.required;
      return;
    }
    if (turnstileEnabled && !turnstileToken.trim()) {
      localError = $t.access.turnstileRequired;
      return;
    }
    localError = '';
    await onUnlock(value, turnstileToken);
  }
</script>

{#if visible}
  <div
    class="mobile-dialog-root fixed inset-0 z-[100] flex items-center justify-center bg-stone-100 px-4 dark:bg-zinc-950"
    aria-label={$t.access.dialogLabel}
    use:dialog={{ open: visible }}
  >
    <button
      type="button"
      class="control-focus absolute left-4 top-4 h-8 min-w-12 rounded-lg border border-stone-300 px-2 text-xs font-semibold text-stone-600 transition-colors hover:border-emerald-500/60 hover:bg-stone-200 hover:text-stone-950 sm:left-6 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
      title={$t.language.toggleTitle}
      aria-label={$t.language.toggleTitle}
      aria-pressed={$language === 'zh-CN'}
      on:click={toggleLanguage}
    >
      {$t.language.button}
    </button>
    <div class="mobile-dvh-dialog fade-in w-full max-w-sm overflow-y-auto rounded-2xl border border-stone-200 bg-white/90 p-5 shadow-2xl shadow-stone-300/50 dark:border-zinc-800 dark:bg-zinc-900/80 dark:shadow-none sm:p-6">
      <div class="mb-5 flex h-10 w-10 items-center justify-center rounded-xl bg-emerald-500/10">
        <span class="text-lg text-emerald-400">#</span>
      </div>
      <h2 id="access-gate-title" class="text-lg font-semibold text-stone-950 dark:text-zinc-100">{$t.access.title}</h2>
      <form class="mt-5 space-y-4" on:submit|preventDefault={submit}>
        <input
          bind:value={accessKey}
          id={accessInputId}
          name="access_key"
          type="password"
          autocomplete="current-password"
          aria-describedby={combinedError ? accessErrorId : undefined}
          aria-invalid={combinedError ? 'true' : undefined}
          aria-label={$t.access.title}
          data-autofocus
          placeholder={$t.access.placeholder}
          class="control-focus w-full rounded-xl border border-stone-300 bg-stone-50 px-4 py-3 font-mono text-sm text-stone-900 transition-colors placeholder-stone-400 focus:border-emerald-500 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-100 dark:placeholder-zinc-500"
          on:input={() => {
            localError = '';
          }}
        />
        <button
          type="submit"
          disabled={loading}
          class="control-focus w-full rounded-xl bg-emerald-600 px-4 py-3 text-sm font-semibold text-white transition-colors hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {loading ? $t.access.unlocking : $t.access.unlock}
        </button>
        {#if turnstileEnabled && turnstileSiteKey}
          <div class="flex justify-center pt-1">
            <div bind:this={widgetContainer} class="min-h-[65px]" role="presentation"></div>
          </div>
        {/if}
      </form>
      {#if combinedError}
        <p id={accessErrorId} class="mt-3 text-sm text-red-400" role="alert" aria-live="assertive">
          {combinedError}
        </p>
      {/if}
    </div>
  </div>
{/if}
