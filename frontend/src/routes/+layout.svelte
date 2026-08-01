<script lang="ts">
  import { onMount } from 'svelte';
  import '../app.css';
  import { i18nReady, initI18n } from '$lib/i18n';
  import { themeStore } from '$lib/stores/theme';

  onMount(() => {
    const cleanupTheme = themeStore.init();
    void initI18n();
    return cleanupTheme;
  });
</script>

{#if $i18nReady}
  <slot />
{:else}
  <div class="grid min-h-screen place-items-center" aria-busy="true">
    <span class="h-6 w-6 animate-spin rounded-full border-2 border-slate-300 border-t-sky-500" aria-hidden="true"></span>
  </div>
{/if}
