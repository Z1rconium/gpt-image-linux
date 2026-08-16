<script lang="ts">
  import { onDestroy } from 'svelte';
  import { dialog } from '$lib/actions/dialog';
  import { language, t, toggleLanguage } from '$lib/i18n';

  export let visible = false;
  export let error = '';
  export let loading = false;
  export let credential: 'access' | 'admin' = 'access';
  export let onUnlock: (accessKey: string) => Promise<boolean> = async () => false;
  export let onCancel: (() => void) | undefined = undefined;

  let accessKey = '';
  let localError = '';
  $: accessInputId = `${credential}-gate-credential`;
  $: accessInputName = `${credential}_gate_credential`;
  $: accessErrorId = `${credential}-gate-error`;

  $: combinedError = error || localError;
  $: if (!visible) clearLocalCredential();

  function clearLocalCredential() {
    accessKey = '';
    localError = '';
  }

  onDestroy(clearLocalCredential);

  async function submit() {
    const value = accessKey.trim();
    if (!value) {
      localError = $t.access.required;
      return;
    }
    localError = '';
    if (await onUnlock(value)) clearLocalCredential();
  }
</script>

{#if visible}
  <div
    class="mobile-dialog-root fixed inset-0 z-[100] flex items-center justify-center bg-stone-100 px-4 dark:bg-zinc-950"
    aria-label={$t.access.dialogLabel}
    use:dialog={{ open: visible, onClose: onCancel }}
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
          name={accessInputName}
          type="password"
          autocomplete="off"
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
        <div class={`grid gap-2 ${onCancel ? 'grid-cols-2' : 'grid-cols-1'}`}>
          {#if onCancel}
            <button
              type="button"
              disabled={loading}
              class="control-focus rounded-xl border border-stone-300 bg-white px-4 py-3 text-sm font-semibold text-stone-700 transition-colors hover:bg-stone-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
              on:click={onCancel}
            >
              {$t.confirm.cancel}
            </button>
          {/if}
          <button
            type="submit"
            disabled={loading}
            class="control-focus rounded-xl bg-emerald-600 px-4 py-3 text-sm font-semibold text-white transition-colors hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loading ? $t.access.unlocking : $t.access.unlock}
          </button>
        </div>
      </form>
      {#if combinedError}
        <p id={accessErrorId} class="mt-3 text-sm text-red-400" role="alert" aria-live="assertive">
          {combinedError}
        </p>
      {/if}
    </div>
  </div>
{/if}
