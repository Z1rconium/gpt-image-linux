<script lang="ts">
  import { language, t, toggleLanguage } from '$lib/i18n';
  import { themeStore } from '$lib/stores/theme';

  export let activeJobsCount = 0;
  export let version = '';
  export let latestVersion = '';
  export let hasVersionUpdate = false;
  export let releaseUrl: string | null = null;
  export let onOpenPromptSnippets: () => void = () => {};
  export let onOpenJobs: () => void = () => {};
  export let onOpenSettings: () => void = () => {};

  $: versionTitle = hasVersionUpdate
    ? $t.header.versionUpdateTitle(version, latestVersion)
    : $t.header.versionTitle(version);
  $: safeReleaseUrl = releaseUrl?.startsWith('https://github.com/') ? releaseUrl : null;
  $: themeToggleTitle = $themeStore === 'dark' ? $t.header.themeToggleToLight : $t.header.themeToggleToDark;
</script>

<header class="app-header sticky top-0 z-40 border-b border-stone-200/80 bg-stone-50/88 backdrop-blur-md dark:border-zinc-800 dark:bg-zinc-950/80">
  <div class="mx-auto flex max-w-5xl items-center justify-between px-4 py-4 sm:px-6">
    <div class="flex items-center gap-3">
      <button
        type="button"
        class="mobile-touch-target control-focus h-8 min-w-12 rounded-lg border border-stone-300 px-2 text-xs font-semibold text-stone-600 transition-colors hover:border-emerald-500/60 hover:bg-stone-100 hover:text-stone-950 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
        title={$t.language.toggleTitle}
        aria-label={$t.language.toggleTitle}
        aria-pressed={$language === 'zh-CN'}
        on:click={toggleLanguage}
      >
        {$t.language.button}
      </button>
      <div class="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-emerald-400 to-cyan-500">
        <span class="text-sm font-black text-zinc-950">I</span>
      </div>
      <div>
        <div class="flex flex-wrap items-center gap-2">
          <h1 class="text-base font-semibold text-stone-950 dark:text-zinc-100">GPT Image Panel</h1>
          {#if version}
            <a
              href={safeReleaseUrl || undefined}
              target="_blank"
              rel="noreferrer"
              title={versionTitle}
              class={hasVersionUpdate
                ? 'control-focus inline-flex items-center rounded-md border border-amber-400/40 bg-amber-400/10 px-2 py-0.5 text-[11px] font-semibold leading-5 text-amber-200 transition-colors hover:border-amber-300/70 hover:bg-amber-400/15'
                : 'control-focus rounded-md border border-stone-300 px-2 py-0.5 text-[11px] font-semibold leading-5 text-stone-500 transition-colors hover:text-stone-900 dark:border-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-100'}
            >
              {version}
              {#if hasVersionUpdate}
                <span class="ml-1 rounded bg-amber-400/20 px-1 py-px text-[10px] text-amber-300">{$t.header.newVersion}</span>
              {/if}
            </a>
          {/if}
        </div>
        <p class="hidden text-xs text-stone-500 sm:block dark:text-zinc-500">{$t.header.subtitle}</p>
      </div>
    </div>

    <div class="flex items-center gap-2">
      <button
        type="button"
        class="mobile-touch-target control-focus inline-flex h-10 min-w-10 items-center justify-center rounded-lg border border-stone-300 px-2 text-stone-500 transition-colors hover:bg-stone-100 hover:text-stone-950 dark:border-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
        title={themeToggleTitle}
        aria-label={themeToggleTitle}
        aria-pressed={$themeStore === 'dark'}
        on:click={() => themeStore.toggle()}
      >
        {#if $themeStore === 'dark'}
          <svg viewBox="0 0 24 24" class="h-4 w-4 fill-none stroke-current stroke-[1.8]">
            <circle cx="12" cy="12" r="4.5"></circle>
            <path d="M12 2.5v2.5M12 19v2.5M4.93 4.93l1.77 1.77M17.3 17.3l1.77 1.77M2.5 12H5m14 0h2.5M4.93 19.07l1.77-1.77M17.3 6.7l1.77-1.77"></path>
          </svg>
        {:else}
          <svg viewBox="0 0 24 24" class="h-4 w-4 fill-none stroke-current stroke-[1.8]">
            <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"></path>
          </svg>
        {/if}
      </button>
      <button
        type="button"
        class="mobile-touch-target control-focus relative inline-flex h-10 min-w-10 items-center justify-center rounded-lg px-2 text-stone-500 transition-colors hover:bg-stone-100 hover:text-stone-950 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
        title={$t.header.promptSnippets}
        aria-label={$t.header.promptSnippets}
        on:click={() => onOpenPromptSnippets()}
      >
        <span class="text-sm font-semibold leading-none">{$t.header.prompts}</span>
      </button>
      <button
        type="button"
        class="mobile-touch-target control-focus relative inline-flex h-10 min-w-10 items-center justify-center rounded-lg px-2 text-stone-500 transition-colors hover:bg-stone-100 hover:text-stone-950 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
        title={$t.header.jobHistory}
        aria-label={$t.header.jobHistory}
        on:click={() => onOpenJobs()}
      >
        <span class="text-sm font-semibold leading-none">{$t.header.jobs}</span>
        {#if activeJobsCount}
          <span class="absolute -right-1 -top-1 h-4 min-w-4 rounded-full bg-emerald-500 px-1 text-[10px] font-semibold leading-4 text-zinc-950">
            {activeJobsCount}
          </span>
        {/if}
      </button>
      <button
        type="button"
        class="mobile-touch-target control-focus inline-flex h-10 min-w-10 items-center justify-center rounded-lg px-2 text-stone-500 transition-colors hover:bg-stone-100 hover:text-stone-950 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
        title={$t.common.settings}
        aria-label={$t.common.settings}
        on:click={() => onOpenSettings()}
      >
        <span class="text-sm font-semibold leading-none">{$t.header.settingsShort}</span>
      </button>
    </div>
  </div>
</header>
