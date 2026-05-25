<script lang="ts">
  import { tick } from 'svelte';
  import type { GenerateJobStatus } from '$lib/api/types';
  import { t } from '$lib/i18n';
  import { formatBeijingTime, operationLabel, stageLabel, statusLabel } from '$lib/utils/format';
  import { isActiveJobStatus, isFailureJobStatus } from '$lib/utils/jobs';
  import { dialog } from '$lib/actions/dialog';

  type JobsTab = 'running' | 'history';
  type MaybePromise = void | Promise<void>;

  export let open = false;
  export let activeTab: JobsTab = 'running';
  export let jobs: GenerateJobStatus[] = [];
  export let historyJobs: GenerateJobStatus[] = [];
  export let historyLoading = false;
  export let historyLoaded = false;
  export let historyHasMore = false;
  export let selectedIds: Set<string> = new Set();
  export let onClose: () => void = () => {};
  export let onTabChange: (tab: JobsTab) => void = () => {};
  export let onRefresh: () => MaybePromise = () => {};
  export let onRefreshHistory: () => MaybePromise = () => {};
  export let onLoadMoreHistory: () => MaybePromise = () => {};
  export let onToggle: (jobId: string) => void = () => {};
  export let onToggleAll: () => void = () => {};
  export let onCancelSelected: () => MaybePromise = () => {};
  export let onUseJob: (job: GenerateJobStatus) => void = () => {};
  export let onRetryJob: (job: GenerateJobStatus) => void = () => {};

  let internalActiveTab: JobsTab = 'running';
  let historyScrollEl: HTMLDivElement | null = null;
  let historyLoadMoreRequest = false;
  let expandedErrorIds = new Set<string>();

  $: if (!open && internalActiveTab !== 'running') internalActiveTab = 'running';
  $: if (!open && expandedErrorIds.size) expandedErrorIds = new Set<string>();
  $: if (open && internalActiveTab !== activeTab) internalActiveTab = activeTab;
  $: if (open && internalActiveTab === 'history' && historyLoaded && historyHasMore && !historyLoading) void fillHistoryViewportIfNeeded();
  $: if (expandedErrorIds.size) {
    const currentIds = new Set(historyJobs.map((job) => job.job_id));
    const nextIds = new Set([...expandedErrorIds].filter((jobId) => currentIds.has(jobId)));
    if (nextIds.size !== expandedErrorIds.size) expandedErrorIds = nextIds;
  }

  function selectTab(tab: JobsTab) {
    internalActiveTab = tab;
    onTabChange(tab);
    if (tab === 'history' && !historyLoaded && !historyLoading) void onRefreshHistory();
  }

  function refreshCurrentTab() {
    if (internalActiveTab === 'history') void onRefreshHistory();
    else void onRefresh();
  }

  async function requestMoreHistory() {
    if (historyLoadMoreRequest || internalActiveTab !== 'history' || historyLoading || !historyHasMore) return;
    historyLoadMoreRequest = true;
    try {
      await onLoadMoreHistory();
    } finally {
      historyLoadMoreRequest = false;
    }
  }

  function handleHistoryScroll(event: Event) {
    const element = event.currentTarget as HTMLDivElement;
    if (element.scrollHeight - element.scrollTop - element.clientHeight <= 160) void requestMoreHistory();
  }

  async function fillHistoryViewportIfNeeded() {
    await tick();
    if (!historyScrollEl || internalActiveTab !== 'history') return;
    if (historyScrollEl.scrollHeight <= historyScrollEl.clientHeight + 160) await requestMoreHistory();
  }

  function isActiveJob(job: GenerateJobStatus) {
    return isActiveJobStatus(job.status);
  }

  function statusClass(job: GenerateJobStatus) {
    if (job.status === 'success') return 'text-emerald-300';
    if (job.status === 'error' || job.status === 'upstream_error') return 'text-red-300';
    if (job.status === 'cancelled') return 'text-zinc-400';
    if (job.status === 'interrupted') return 'text-amber-300';
    if (job.status === 'running') return 'text-cyan-300';
    return 'text-amber-300';
  }

  function jobMeta(job: GenerateJobStatus) {
    return [job.model, job.size, job.api_preset_name].filter(Boolean).join(' / ');
  }

  function historyStageLabel(job: GenerateJobStatus, labels: Record<string, string>) {
    if (!job.stage) return '';
    if (!isFailureJobStatus(job.status)) return stageLabel(job, labels);
    return labels[job.stage] || job.stage.replaceAll('_', ' ');
  }

  function jobErrorMessage(job: GenerateJobStatus) {
    if (!isFailureJobStatus(job.status)) return '';
    return (job.error || job.message || '').trim();
  }

  function isErrorExpanded(jobId: string) {
    return expandedErrorIds.has(jobId);
  }

  function toggleError(jobId: string) {
    const nextIds = new Set(expandedErrorIds);
    if (nextIds.has(jobId)) nextIds.delete(jobId);
    else nextIds.add(jobId);
    expandedErrorIds = nextIds;
  }

  let visibleJobIds = new Set<string>();

  $: if (!open) {
    visibleJobIds = new Set<string>();
  }

  function lazyRender(node: HTMLElement, jobId: string) {
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) {
          visibleJobIds.add(jobId);
          visibleJobIds = visibleJobIds;
          observer.disconnect();
        }
      },
      { rootMargin: '300px' }
    );
    observer.observe(node);
    return {
      destroy() {
        observer.disconnect();
      }
    };
  }
</script>

{#if open}
  <div class="fixed inset-0 z-50">
    <button class="drawer-backdrop absolute inset-0" type="button" tabindex="-1" aria-label={$t.jobs.closeLabel} on:click={onClose}></button>
    <aside
      class="fade-in absolute right-0 top-0 flex h-full w-full max-w-lg flex-col border-l border-zinc-800 bg-zinc-900 shadow-2xl"
      aria-labelledby="jobs-drawer-title"
      use:dialog={{ open, onClose }}
    >
      <div class="flex items-center justify-between border-b border-zinc-800 p-5">
        <div class="min-w-0">
          <h2 id="jobs-drawer-title" class="text-lg font-semibold text-zinc-100">{$t.jobs.title}</h2>
          <p class="mt-1 text-xs text-zinc-500">{$t.jobs.subtitle}</p>
        </div>
        <button type="button" class="control-focus rounded-lg p-1.5 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100" aria-label={$t.jobs.closeLabel} on:click={onClose}>x</button>
      </div>

      <div class="flex flex-col gap-3 border-b border-zinc-800 p-5 sm:flex-row sm:items-center sm:justify-between">
        <div class="grid grid-cols-2 rounded-lg border border-zinc-800 bg-zinc-950 p-1 text-xs font-medium">
          <button type="button" class={`control-focus rounded-md px-3 py-1.5 ${internalActiveTab === 'running' ? 'bg-zinc-800 text-zinc-100' : 'text-zinc-500 hover:text-zinc-200'}`} on:click={() => selectTab('running')}>
            {$t.jobs.runningTab}
          </button>
          <button type="button" class={`control-focus rounded-md px-3 py-1.5 ${internalActiveTab === 'history' ? 'bg-zinc-800 text-zinc-100' : 'text-zinc-500 hover:text-zinc-200'}`} on:click={() => selectTab('history')}>
            {$t.jobs.historyTab}
          </button>
        </div>
        <div class="flex justify-end gap-3">
          {#if internalActiveTab === 'running'}
            <button type="button" class="control-focus rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800 disabled:opacity-40" disabled={!jobs.length} on:click={onToggleAll}>
              {$t.jobs.selectAll}
            </button>
          {/if}
          <button type="button" class="control-focus rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800 disabled:opacity-40" disabled={internalActiveTab === 'history' && historyLoading} on:click={refreshCurrentTab}>
            {$t.jobs.refresh}
          </button>
        </div>
      </div>

      <div bind:this={historyScrollEl} class="min-h-0 flex-1 overflow-y-auto p-5" on:scroll={handleHistoryScroll}>
        {#if internalActiveTab === 'running' && jobs.length === 0}
          <div class="rounded-xl border border-dashed border-zinc-800 bg-zinc-950/35 px-4 py-10 text-center">
            <p class="text-sm font-medium text-zinc-300">{$t.jobs.noRunning}</p>
            <p class="mt-2 text-xs text-zinc-500">{$t.jobs.noRunningHint}</p>
          </div>
        {:else if internalActiveTab === 'running'}
          <div class="space-y-3">
            {#each jobs as job (job.job_id)}
              <div class="flex gap-3 rounded-xl border border-zinc-800 bg-zinc-950/45 p-4">
                <input type="checkbox" class="control-focus mt-1 accent-emerald-500" checked={selectedIds.has(job.job_id)} on:change={() => onToggle(job.job_id)} />
                <div class="min-w-0 flex-1">
                  <div class="flex items-center justify-between gap-3">
                    <span class="rounded-md border border-zinc-700 px-2 py-0.5 text-[11px] text-zinc-400">{operationLabel(job.operation, $t.operations)}</span>
                    <span class={`text-xs font-medium ${statusClass(job)}`}>{statusLabel(job.status, $t.statuses)}</span>
                  </div>
                  <p class="mt-2 truncate text-sm text-zinc-200">{job.prompt || $t.common.untitledJob}</p>
                  <p class="mt-1 truncate text-xs text-zinc-500">{stageLabel(job, $t.stages)}</p>
                </div>
              </div>
            {/each}
          </div>
        {:else if historyLoading && historyJobs.length === 0}
          <div class="rounded-xl border border-dashed border-zinc-800 bg-zinc-950/35 px-4 py-10 text-center">
            <p class="text-sm font-medium text-zinc-300">{$t.jobs.historyLoading}</p>
          </div>
        {:else if historyJobs.length === 0}
          <div class="rounded-xl border border-dashed border-zinc-800 bg-zinc-950/35 px-4 py-10 text-center">
            <p class="text-sm font-medium text-zinc-300">{$t.jobs.noHistory}</p>
            <p class="mt-2 text-xs text-zinc-500">{$t.jobs.noHistoryHint}</p>
          </div>
        {:else}
          <div class="space-y-3" aria-busy={historyLoading}>
            {#each historyJobs as job (job.job_id)}
              <div class="min-h-[175px]" use:lazyRender={job.job_id}>
                {#if visibleJobIds.has(job.job_id)}
                  <article class="deferred-list-item rounded-xl border border-zinc-800 bg-zinc-950/45 p-4">
                    <div class="flex items-center justify-between gap-3">
                      <span class="rounded-md border border-zinc-700 px-2 py-0.5 text-[11px] text-zinc-400">{operationLabel(job.operation, $t.operations)}</span>
                      <span class={`text-xs font-medium ${statusClass(job)}`}>{statusLabel(job.status, $t.statuses)}</span>
                    </div>
                    <p class="mt-2 line-clamp-2 text-sm text-zinc-200">{job.prompt || $t.common.untitledJob}</p>
                    <p class="mt-1 truncate text-xs text-zinc-500">{historyStageLabel(job, $t.stages)}</p>
                    <div class="mt-3 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-zinc-500">
                      {#if jobMeta(job)}
                        <span>{jobMeta(job)}</span>
                      {/if}
                      <span>{formatBeijingTime(job.completed_at || job.updated_at || job.created_at)}</span>
                      {#if job.duration}
                        <span>{$t.common.duration}: {job.duration}</span>
                      {/if}
                    </div>
                    {#if jobErrorMessage(job)}
                      <div class="mt-3">
                        <button
                          type="button"
                          class="control-focus rounded-lg border border-red-500/35 px-3 py-2 text-xs font-medium text-red-200 hover:bg-red-500/10"
                          aria-expanded={isErrorExpanded(job.job_id)}
                          on:click={() => toggleError(job.job_id)}
                        >
                          {isErrorExpanded(job.job_id) ? $t.jobs.hideError : $t.jobs.showError}
                        </button>
                        {#if isErrorExpanded(job.job_id)}
                          <pre class="mt-3 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-red-500/25 bg-red-950/20 p-3 text-xs leading-relaxed text-red-100">{jobErrorMessage(job)}</pre>
                        {/if}
                      </div>
                    {/if}
                    <div class="mt-4 flex flex-wrap justify-end gap-2">
                      <button type="button" class="control-focus rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800" on:click={() => onUseJob(job)}>
                        {$t.jobs.useAsPrompt}
                      </button>
                      <button
                        type="button"
                        class="control-focus rounded-lg border border-emerald-500/40 px-3 py-2 text-xs font-medium text-emerald-200 hover:bg-emerald-500/10 disabled:cursor-not-allowed disabled:opacity-40"
                        disabled={isActiveJob(job)}
                        title={isActiveJob(job) ? $t.jobs.retryUnavailable : $t.jobs.retry}
                        on:click={() => onRetryJob(job)}
                      >
                        {$t.jobs.retry}
                      </button>
                    </div>
                  </article>
                {:else}
                  <div class="h-[175px] rounded-xl border border-zinc-800/40 bg-zinc-950/20"></div>
                {/if}
              </div>
            {/each}
            {#if historyLoading}
              <div class="rounded-xl border border-zinc-800 bg-zinc-950/35 px-4 py-4 text-center text-xs text-zinc-400">
                {$t.jobs.historyLoading}
              </div>
            {/if}
          </div>
        {/if}
      </div>

      {#if internalActiveTab === 'running'}
        <div class="border-t border-zinc-800 p-5">
          <button type="button" disabled={!selectedIds.size} class="control-focus w-full rounded-xl bg-red-600 px-4 py-3 text-sm font-semibold text-white hover:bg-red-500 disabled:cursor-not-allowed disabled:opacity-40" on:click={onCancelSelected}>
            {$t.jobs.cancelSelected}
          </button>
        </div>
      {/if}
    </aside>
  </div>
{/if}
