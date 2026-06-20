<script lang="ts">
  import type { ToastMessage } from '$lib/stores/ui';

  export let toast: ToastMessage | null = null;

  let role: 'alert' | 'status' = 'status';
  let ariaLive: 'assertive' | 'polite' = 'polite';

  $: isError = toast?.variant === 'error';
  $: {
    role = isError ? 'alert' : 'status';
    ariaLive = isError ? 'assertive' : 'polite';
  }
  $: toastClass = isError
    ? 'border-red-500/40 bg-red-950/90 text-red-100'
    : 'border-stone-300 bg-white/95 text-stone-800 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100';

  function handleAction() {
    toast?.onAction?.();
  }
</script>

{#if toast?.message}
  <div
    class={`fixed bottom-5 right-5 z-[90] flex max-w-[min(28rem,calc(100vw-2.5rem))] items-center gap-3 rounded-xl border px-4 py-3 text-sm shadow-2xl ${toastClass}`}
    {role}
    aria-live={ariaLive}
    aria-atomic="true"
  >
    <span class="min-w-0">{toast.message}</span>
    {#if toast.actionLabel && toast.onAction}
      <button type="button" class="control-focus shrink-0 rounded-lg border border-stone-300 px-3 py-1.5 text-xs font-semibold text-stone-800 hover:bg-stone-100 dark:border-zinc-600 dark:text-zinc-100 dark:hover:bg-zinc-800" on:click={handleAction}>
        {toast.actionLabel}
      </button>
    {/if}
  </div>
{/if}
