<script lang="ts">
  import { language, t, toggleLanguage } from '$lib/i18n';

  export let visible = false;
  export let error = '';
  export let loading = false;
  export let onUnlock: (accessKey: string) => Promise<void> | void = () => {};

  let accessKey = '';
  let localError = '';

  async function submit() {
    const value = accessKey.trim();
    if (!value) {
      localError = $t.access.required;
      return;
    }
    localError = '';
    await onUnlock(value);
  }
</script>

{#if visible}
  <div class="mobile-dialog-root fixed inset-0 z-[100] flex items-center justify-center bg-stone-100 px-4 dark:bg-zinc-950">
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
      <h2 class="text-lg font-semibold text-stone-950 dark:text-zinc-100">{$t.access.title}</h2>
      <form class="mt-5 space-y-4" on:submit|preventDefault={submit}>
        <input
          bind:value={accessKey}
          name="access_key"
          type="password"
          autocomplete="current-password"
          aria-label={$t.access.title}
          placeholder={$t.access.placeholder}
          class="control-focus w-full rounded-xl border border-stone-300 bg-stone-50 px-4 py-3 font-mono text-sm text-stone-900 transition-colors placeholder-stone-400 focus:border-emerald-500 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-100 dark:placeholder-zinc-500"
        />
        <button
          type="submit"
          disabled={loading}
          class="control-focus w-full rounded-xl bg-emerald-600 px-4 py-3 text-sm font-semibold text-white transition-colors hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {loading ? $t.access.unlocking : $t.access.unlock}
        </button>
      </form>
      {#if error || localError}
        <p class="mt-3 text-sm text-red-400">{error || localError}</p>
      {/if}
    </div>
  </div>
{/if}
