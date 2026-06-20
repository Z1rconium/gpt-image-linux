<script lang="ts">
  import { onMount } from 'svelte';
  import '../app.css';
  import { themeStore } from '$lib/stores/theme';

  onMount(() => {
    themeStore.init();
  });
</script>

<slot />
