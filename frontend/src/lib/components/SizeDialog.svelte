<script lang="ts">
  import { dialogIn, dialogOut, overlayIn, overlayOut } from '$lib/motion';
  import { t } from '$lib/i18n';
  import { dialog } from '$lib/actions/dialog';

  export let open = false;
  export let value = 'auto';
  export let onApply: (size: string) => void = () => {};
  export let onClose: () => void = () => {};

  const presets = ['auto', '1024x1024', '1024x1536', '1536x1024', '2048x2048', '2048x3072', '3072x2048'];
  let custom = value;

  $: if (open) custom = value;

  function apply(size = custom.trim()) {
    if (!size) return;
    onApply(size);
    onClose();
  }
</script>

{#if open}
  <div class="fixed inset-0 z-[80] flex items-center justify-center bg-stone-950/60 px-4 backdrop-blur dark:bg-zinc-950/75" in:overlayIn out:overlayOut>
    <button class="absolute inset-0" type="button" tabindex="-1" aria-label={$t.common.close} on:click={onClose}></button>
    <div
      class="overlay-panel relative w-full max-w-lg rounded-2xl border border-stone-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900" in:dialogIn out:dialogOut
      aria-labelledby="size-dialog-title"
      use:dialog={{ open, onClose }}
    >
      <div class="mb-5 flex items-center justify-between">
        <div>
          <h2 id="size-dialog-title" class="text-lg font-semibold text-stone-950 dark:text-zinc-100">{$t.sizeDialog.title}</h2>
          <p class="mt-1 text-xs text-stone-500 dark:text-zinc-500">{$t.sizeDialog.subtitle}</p>
        </div>
        <button type="button" class="control-focus rounded-lg p-1.5 text-stone-500 hover:bg-stone-100 hover:text-stone-950 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100" aria-label={$t.common.close} on:click={onClose}>x</button>
      </div>

      <div class="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {#each presets as size}
          <button
            type="button"
            class={`control-focus rounded-lg border px-3 py-3 text-sm transition-colors ${
              value === size
                ? 'border-emerald-500 bg-emerald-500/10 text-emerald-700 dark:text-emerald-100'
                : 'border-stone-200 bg-stone-50 text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-300 dark:hover:bg-zinc-800'
            }`}
            on:click={() => apply(size)}
          >
            {size}
          </button>
        {/each}
      </div>

      <div class="mt-5 flex gap-2">
        <label for="custom-size" class="sr-only">{$t.common.size}</label>
        <input
          bind:value={custom}
          id="custom-size"
          name="custom_size"
          inputmode="numeric"
          autocomplete="off"
          aria-label={$t.common.size}
          class="control-focus min-w-0 flex-1 rounded-lg border border-stone-200 bg-stone-50 px-3 py-2.5 font-mono text-sm text-stone-900 focus:border-emerald-500 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
          placeholder="1024x1024"
        />
        <button type="button" class="control-focus rounded-lg bg-emerald-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-emerald-500" on:click={() => apply()}>{$t.common.apply}</button>
      </div>
    </div>
  </div>
{/if}
