<script lang="ts">
  import { language, t, toggleLanguage } from '$lib/i18n';

  export let activeJobsCount = 0;
  export let version = '';
  export let latestVersion = '';
  export let hasVersionUpdate = false;
  export let releaseUrl: string | null = null;
  export let promptSnippetsOpen = false;
  export let imagePromptOpen = false;
  export let jobsOpen = false;
  export let settingsOpen = false;
  export let onOpenPromptSnippets: () => void = () => {};
  export let onOpenImagePrompt: () => void = () => {};
  export let onOpenJobs: () => void = () => {};
  export let onOpenSettings: () => void = () => {};
  export let onPrefetchPromptSnippets: () => void = () => {};
  export let onPrefetchImagePrompt: () => void = () => {};
  export let onPrefetchJobs: () => void = () => {};
  export let onPrefetchSettings: () => void = () => {};

  $: versionTitle = hasVersionUpdate
    ? $t.header.versionUpdateTitle(version, latestVersion)
    : $t.header.versionTitle(version);
  $: safeReleaseUrl = releaseUrl?.startsWith('https://github.com/') ? releaseUrl : null;
</script>

<header class="app-header sticky top-0 z-40 border-b border-stone-200/80 bg-stone-50/88 backdrop-blur-sm dark:border-zinc-800 dark:bg-zinc-950/80">
  <div class="mx-auto flex max-w-5xl flex-wrap items-center gap-3 px-4 py-3 sm:flex-nowrap sm:px-6 sm:py-4">
    <div class="flex min-w-0 flex-1 items-start gap-3">
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
      <div class="flex h-8 w-8 items-center justify-center rounded-md bg-emerald-600" aria-hidden="true">
        <span class="text-sm font-bold text-white">I</span>
      </div>
      <div class="min-w-0">
        <div class="flex flex-wrap items-center gap-2">
          <h1 class="min-w-0 text-base font-semibold text-stone-950 dark:text-zinc-100">GPT Image Panel</h1>
          {#if version}
            <a
              href={safeReleaseUrl || undefined}
              target="_blank"
              rel="noreferrer"
              title={versionTitle}
              class={hasVersionUpdate
                ? 'control-focus inline-flex items-center whitespace-nowrap rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[11px] font-semibold leading-5 text-amber-800 transition-colors hover:border-amber-500/70 hover:bg-amber-500/15 dark:text-amber-200'
                : 'control-focus inline-flex items-center whitespace-nowrap rounded-md border border-stone-300 px-2 py-0.5 text-[11px] font-semibold leading-5 text-stone-500 transition-colors hover:text-stone-900 dark:border-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-100'}
            >
              {version}
              {#if hasVersionUpdate}
                <span class="ml-1 rounded bg-amber-500/15 px-1 py-px text-[10px] text-amber-800 dark:bg-amber-400/20 dark:text-amber-300">{$t.header.newVersion}</span>
              {/if}
            </a>
          {/if}
        </div>
        <p class="hidden text-xs text-stone-500 sm:block dark:text-zinc-500">{$t.header.subtitle}</p>
      </div>
    </div>

    <div class="flex w-full flex-wrap items-center justify-end gap-2 sm:w-auto sm:flex-none">
      <button
        type="button"
        class="mobile-touch-target control-focus inline-flex h-10 min-w-10 items-center justify-center rounded-lg border border-emerald-500/35 px-2 text-emerald-700 transition-colors hover:bg-emerald-500/10 dark:text-emerald-200"
        title={$t.header.reversePrompt}
        aria-label={$t.header.reversePrompt}
        aria-controls="image-prompt-dialog"
        aria-expanded={imagePromptOpen}
        on:mouseenter={onPrefetchImagePrompt}
        on:focus={onPrefetchImagePrompt}
        on:click={() => onOpenImagePrompt()}
      >
        <span class="text-sm font-semibold leading-none">{$t.header.reversePromptShort}</span>
      </button>
      <button
        type="button"
        class="mobile-touch-target control-focus relative inline-flex h-10 min-w-10 items-center justify-center rounded-lg px-2 text-stone-500 transition-colors hover:bg-stone-100 hover:text-stone-950 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
        title={$t.header.promptSnippets}
        aria-label={$t.header.promptSnippets}
        aria-controls="prompt-snippets-drawer"
        aria-expanded={promptSnippetsOpen}
        on:mouseenter={onPrefetchPromptSnippets}
        on:focus={onPrefetchPromptSnippets}
        on:click={() => onOpenPromptSnippets()}
      >
        <span class="text-sm font-semibold leading-none">{$t.header.prompts}</span>
      </button>
      <button
        type="button"
        class="mobile-touch-target control-focus relative inline-flex h-10 min-w-10 items-center justify-center rounded-lg px-2 text-stone-500 transition-colors hover:bg-stone-100 hover:text-stone-950 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
        title={$t.header.jobHistory}
        aria-label={$t.header.jobHistory}
        aria-controls="jobs-drawer"
        aria-expanded={jobsOpen}
        on:mouseenter={onPrefetchJobs}
        on:focus={onPrefetchJobs}
        on:click={() => onOpenJobs()}
      >
        <span class="text-sm font-semibold leading-none">{$t.header.jobs}</span>
        {#if activeJobsCount}
          <span class="absolute -right-1 -top-1 h-4 min-w-4 rounded-full bg-emerald-700 px-1 text-[10px] font-semibold leading-4 text-white">
            {activeJobsCount}
          </span>
        {/if}
      </button>
      <button
        type="button"
        class="mobile-touch-target control-focus inline-flex h-10 min-w-10 items-center justify-center rounded-lg px-2 text-stone-500 transition-colors hover:bg-stone-100 hover:text-stone-950 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
        title={$t.common.settings}
        aria-label={$t.common.settings}
        aria-controls="settings-drawer"
        aria-expanded={settingsOpen}
        on:mouseenter={onPrefetchSettings}
        on:focus={onPrefetchSettings}
        on:click={() => onOpenSettings()}
      >
        <span class="text-sm font-semibold leading-none">{$t.header.settingsShort}</span>
      </button>
    </div>
  </div>
</header>
