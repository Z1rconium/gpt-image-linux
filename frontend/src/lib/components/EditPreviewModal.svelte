<script lang="ts">
  import { t } from '$lib/i18n';
  import { dialog } from '$lib/actions/dialog';

  export let open: boolean;
  export let url: string;
  export let label: string;
  export let onClose: () => void;
</script>

{#if open && url}
  <div class="mobile-dialog-root fixed inset-0 z-[75] flex items-center justify-center bg-black/75 p-4">
    <button class="absolute inset-0" type="button" tabindex="-1" aria-label={$t.promptForm.closeEditPreview} on:click={onClose}></button>
    <div
      class="mobile-dvh-dialog relative flex max-h-[calc(100vh-32px)] w-full max-w-4xl flex-col overflow-hidden rounded-2xl border border-zinc-800 bg-zinc-950 shadow-2xl"
      aria-labelledby="edit-preview-title"
      use:dialog={{ open, onClose }}
    >
      <div class="flex items-center justify-between gap-3 border-b border-zinc-800 px-4 py-3">
        <div class="min-w-0">
          <h2 id="edit-preview-title" class="text-sm font-semibold text-zinc-100">{$t.promptForm.editSourcePreview}</h2>
          <p class="mt-1 truncate text-xs text-zinc-500">{label}</p>
        </div>
        <button type="button" class="mobile-touch-target control-focus rounded-lg px-2 py-1 text-sm text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100" aria-label={$t.promptForm.closeEditPreview} on:click={onClose}>x</button>
      </div>
      <div class="flex min-h-0 flex-1 items-center justify-center bg-zinc-950 p-4">
        <img src={url} alt={label} class="mobile-dvh-image max-h-[calc(100vh-140px)] max-w-full rounded-lg object-contain" decoding="async" />
      </div>
    </div>
  </div>
{/if}
