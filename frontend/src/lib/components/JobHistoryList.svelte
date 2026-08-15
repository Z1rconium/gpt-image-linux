<script lang="ts">
  import type { AssistantJobDiagnoseResponse } from '$lib/api/types/assistant';
  import type { GenerateJobStatus } from '$lib/api/types/jobs';
  import { t } from '$lib/i18n';
  import { formatBeijingTime, jobFailureMessage, operationLabel, stageLabel, statusLabel } from '$lib/utils/format';
  import { isActiveJobStatus, isFailureJobStatus } from '$lib/utils/jobs';

  type MaybePromise = void | Promise<void>;
  type Props = {
    historyJobs?: GenerateJobStatus[];
    historyLoading?: boolean;
    historyLoaded?: boolean;
    historyHasMore?: boolean;
    historyFailedOnly?: boolean;
    aiAssistantEnabled?: boolean;
    diagnosingJobId?: string | null;
    diagnoses?: Record<string, AssistantJobDiagnoseResponse>;
    onLoadMoreHistory?: () => MaybePromise;
    onUseJob?: (job: GenerateJobStatus) => void;
    onRetryJob?: (job: GenerateJobStatus) => void;
    onDiagnoseJob?: (job: GenerateJobStatus) => MaybePromise;
  };

  const ESTIMATED_ITEM_HEIGHT = 224;
  const ITEM_GAP = 12;
  const OVERSCAN_PX = 720;

  let {
    historyJobs = [],
    historyLoading = false,
    historyLoaded = false,
    historyHasMore = false,
    historyFailedOnly = false,
    aiAssistantEnabled = false,
    diagnosingJobId = null,
    diagnoses = {},
    onLoadMoreHistory = () => {},
    onUseJob = () => {},
    onRetryJob = () => {},
    onDiagnoseJob = () => {}
  }: Props = $props();

  let scrollEl = $state<HTMLDivElement | null>(null);
  let scrollTop = $state(0);
  let viewportHeight = $state(0);
  let measuredHeights = $state<Record<string, number>>({});
  let expandedErrorIds = $state(new Set<string>());
  let loadMoreRequest = false;
  let previousResetKey = '';

  const resetKey = $derived(`${historyFailedOnly}:${historyJobs[0]?.job_id || ''}`);
  const layout = $derived.by(() => {
    const offsets = new Array<number>(historyJobs.length + 1);
    offsets[0] = 0;
    for (let index = 0; index < historyJobs.length; index += 1) {
      const job = historyJobs[index];
      const height = measuredHeights[job.job_id] || ESTIMATED_ITEM_HEIGHT;
      offsets[index + 1] = offsets[index] + height + (index < historyJobs.length - 1 ? ITEM_GAP : 0);
    }
    return { offsets, totalHeight: offsets[historyJobs.length] || 0 };
  });
  const renderWindow = $derived.by(() => {
    if (!historyJobs.length) return { start: 0, end: 0 };
    const rangeStart = Math.max(0, scrollTop - OVERSCAN_PX);
    const rangeEnd = scrollTop + Math.max(viewportHeight, ESTIMATED_ITEM_HEIGHT) + OVERSCAN_PX;
    let start = 0;
    while (start < historyJobs.length - 1 && layout.offsets[start + 1] < rangeStart) start += 1;
    let end = start + 1;
    while (end < historyJobs.length && layout.offsets[end] < rangeEnd) end += 1;
    return { start, end: Math.min(historyJobs.length, end + 1) };
  });
  const renderedJobs = $derived(historyJobs.slice(renderWindow.start, renderWindow.end));
  const topSpacerHeight = $derived(layout.offsets[renderWindow.start] || 0);
  const renderedHeight = $derived(
    (layout.offsets[renderWindow.end] || 0) - topSpacerHeight - (renderWindow.end < historyJobs.length ? ITEM_GAP : 0)
  );
  const bottomSpacerHeight = $derived(Math.max(0, layout.totalHeight - topSpacerHeight - renderedHeight));

  $effect(() => {
    if (resetKey === previousResetKey) return;
    previousResetKey = resetKey;
    scrollTop = 0;
    measuredHeights = {};
    expandedErrorIds = new Set<string>();
    if (scrollEl) scrollEl.scrollTop = 0;
  });

  $effect(() => {
    const currentIds = new Set(historyJobs.map((job) => job.job_id));
    const nextIds = new Set([...expandedErrorIds].filter((jobId) => currentIds.has(jobId)));
    if (nextIds.size !== expandedErrorIds.size) expandedErrorIds = nextIds;
  });

  $effect(() => {
    if (
      historyLoaded &&
      historyHasMore &&
      !historyLoading &&
      viewportHeight > 0 &&
      layout.totalHeight <= viewportHeight + 160
    ) {
      void requestMoreHistory();
    }
  });

  function handleScroll() {
    if (scrollEl) scrollTop = scrollEl.scrollTop;
  }

  async function requestMoreHistory() {
    if (loadMoreRequest || historyLoading || !historyHasMore) return;
    loadMoreRequest = true;
    try {
      await onLoadMoreHistory();
    } finally {
      loadMoreRequest = false;
    }
  }

  function observeViewport(node: HTMLElement) {
    const update = () => {
      viewportHeight = node.clientHeight;
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(node);
    return {
      destroy() {
        observer.disconnect();
      }
    };
  }

  function measureItem(node: HTMLElement, jobId: string) {
    const update = () => {
      const nextHeight = Math.ceil(node.getBoundingClientRect().height);
      if (nextHeight > 0 && measuredHeights[jobId] !== nextHeight) {
        measuredHeights = { ...measuredHeights, [jobId]: nextHeight };
      }
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(node);
    return {
      destroy() {
        observer.disconnect();
      }
    };
  }

  function observeHistorySentinel(node: HTMLElement) {
    if (!scrollEl) return { destroy() {} };
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) void requestMoreHistory();
      },
      { root: scrollEl, rootMargin: '160px 0px' }
    );
    observer.observe(node);
    return {
      destroy() {
        observer.disconnect();
      }
    };
  }

  function statusClass(job: GenerateJobStatus) {
    if (job.status === 'success') return 'text-emerald-300';
    if (job.status === 'partial_failure') return 'text-amber-700 dark:text-amber-300';
    if (job.status === 'error' || job.status === 'upstream_error') return 'text-red-300';
    if (job.status === 'cancelled') return 'text-stone-500 dark:text-zinc-400';
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

  function jobErrorMessage(job: GenerateJobStatus, fallback: string) {
    return jobFailureMessage(job, fallback);
  }

  function failurePanelClass(job: GenerateJobStatus) {
    return job.status === 'partial_failure'
      ? 'border-amber-400/40 bg-amber-50 text-amber-900 dark:border-amber-500/30 dark:bg-amber-950/25 dark:text-amber-200'
      : 'border-red-400/40 bg-red-50 text-red-800 dark:border-red-500/25 dark:bg-red-950/30 dark:text-red-300';
  }

  function failureButtonClass(job: GenerateJobStatus) {
    return job.status === 'partial_failure'
      ? 'border-amber-500/40 text-amber-800 hover:bg-amber-500/10 dark:text-amber-200'
      : 'border-red-500/35 text-red-700 hover:bg-red-500/10 dark:text-red-200';
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
</script>

<div
  bind:this={scrollEl}
  class="mobile-drawer-scroll min-h-0 flex-1 overflow-y-auto p-5"
  onscroll={handleScroll}
  use:observeViewport
>
  {#if historyLoading && historyJobs.length === 0}
    <div class="rounded-xl border border-dashed border-stone-300 bg-stone-100/80 px-4 py-10 text-center dark:border-zinc-800 dark:bg-zinc-950/35">
      <p class="text-sm font-medium text-stone-700 dark:text-zinc-300">{$t.jobs.historyLoading}</p>
    </div>
  {:else if historyJobs.length === 0}
    <div class="rounded-xl border border-dashed border-stone-300 bg-stone-100/80 px-4 py-10 text-center dark:border-zinc-800 dark:bg-zinc-950/35">
      <p class="text-sm font-medium text-stone-700 dark:text-zinc-300">{historyFailedOnly ? $t.jobs.noErrorHistory : $t.jobs.noHistory}</p>
      <p class="mt-2 text-xs text-stone-500 dark:text-zinc-500">{historyFailedOnly ? $t.jobs.noErrorHistoryHint : $t.jobs.noHistoryHint}</p>
    </div>
  {:else}
    <div aria-busy={historyLoading}>
      <div style={`height: ${topSpacerHeight}px`} aria-hidden="true"></div>
      <div class="space-y-3">
        {#each renderedJobs as job, renderedIndex (job.job_id)}
          <article
            class="rounded-xl border border-stone-200 bg-stone-50/80 p-4 dark:border-zinc-800 dark:bg-zinc-950/45"
            use:measureItem={job.job_id}
            aria-posinset={renderWindow.start + renderedIndex + 1}
            aria-setsize={historyJobs.length}
          >
            <div class="flex items-center justify-between gap-3">
              <span class="rounded-md border border-stone-300 px-2 py-0.5 text-xs text-stone-500 dark:border-zinc-700 dark:text-zinc-400">{operationLabel(job.operation, $t.operations)}</span>
              <span class={`text-xs font-medium ${statusClass(job)}`}>{statusLabel(job.status, $t.statuses)}</span>
            </div>
            <p class="mt-2 line-clamp-2 text-sm text-stone-800 dark:text-zinc-200">{job.prompt || $t.common.untitledJob}</p>
            <p class="mt-1 truncate text-xs text-stone-500 dark:text-zinc-500">{historyStageLabel(job, $t.stages)}</p>
            {#if jobErrorMessage(job, $t.messages.jobFailed)}
              <div
                class:hidden={!isErrorExpanded(job.job_id)}
                class={`mt-2 rounded-lg border px-3 py-2 text-xs leading-relaxed ${failurePanelClass(job)}`}
                aria-hidden={!isErrorExpanded(job.job_id)}
              >
                {jobErrorMessage(job, $t.messages.jobFailed)}
              </div>
            {/if}
            <div class="mt-3 flex flex-wrap gap-x-3 gap-y-1 text-xs text-zinc-500">
              {#if jobMeta(job)}
                <span>{jobMeta(job)}</span>
              {/if}
              <span>{formatBeijingTime(job.completed_at || job.updated_at || job.created_at)}</span>
              {#if job.duration}
                <span>{$t.common.duration}: {job.duration}</span>
              {/if}
            </div>
            {#if jobErrorMessage(job, $t.messages.jobFailed)}
              <div class="mt-3">
                <button
                  type="button"
                  class={`control-focus rounded-lg border px-3 py-2 text-xs font-medium ${failureButtonClass(job)}`}
                  aria-expanded={isErrorExpanded(job.job_id)}
                  onclick={() => toggleError(job.job_id)}
                >
                  {isErrorExpanded(job.job_id) ? $t.jobs.hideError : $t.jobs.showError}
                </button>
              </div>
            {/if}
            {#if diagnoses[job.job_id]}
              {@const diagnosis = diagnoses[job.job_id]}
              <div class="mt-3 rounded-lg border border-cyan-500/25 bg-cyan-950/20 px-3 py-2 text-xs leading-5 text-cyan-100">
                <div class="font-semibold">{$t.jobs.aiDiagnosis}</div>
                <p class="mt-1 text-zinc-300">{diagnosis.summary}</p>
                {#if diagnosis.likely_causes.length}
                  <div class="mt-2 text-zinc-400">{$t.jobs.aiLikelyCauses}: {diagnosis.likely_causes.join('; ')}</div>
                {/if}
                {#if diagnosis.recommended_actions.length}
                  <div class="mt-1 text-zinc-400">{$t.jobs.aiRecommendedActions}: {diagnosis.recommended_actions.join('; ')}</div>
                {/if}
              </div>
            {/if}
            <div class="mt-4 flex flex-wrap justify-end gap-2">
              {#if aiAssistantEnabled && isFailureJobStatus(job.status)}
                <button
                  type="button"
                  class="control-focus rounded-lg border border-cyan-500/35 px-3 py-2 text-xs font-medium text-cyan-200 hover:bg-cyan-500/10 disabled:cursor-not-allowed disabled:opacity-40"
                  disabled={diagnosingJobId === job.job_id}
                  onclick={() => onDiagnoseJob(job)}
                >
                  {diagnosingJobId === job.job_id ? $t.jobs.diagnosing : $t.jobs.diagnose}
                </button>
              {/if}
              <button type="button" class="control-focus rounded-lg border border-zinc-700 px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800" onclick={() => onUseJob(job)}>
                {$t.jobs.useAsPrompt}
              </button>
              <button
                type="button"
                class="control-focus rounded-lg border border-emerald-500/40 px-3 py-2 text-xs font-medium text-emerald-200 hover:bg-emerald-500/10 disabled:cursor-not-allowed disabled:opacity-40"
                disabled={isActiveJobStatus(job.status)}
                title={isActiveJobStatus(job.status) ? $t.jobs.retryUnavailable : $t.jobs.retry}
                onclick={() => onRetryJob(job)}
              >
                {$t.jobs.retry}
              </button>
            </div>
          </article>
        {/each}
      </div>
      <div style={`height: ${bottomSpacerHeight}px`} aria-hidden="true"></div>
      {#if historyLoading}
        <div class="rounded-xl border border-zinc-800 bg-zinc-950/35 px-4 py-4 text-center text-xs text-zinc-400">
          {$t.jobs.historyLoading}
        </div>
      {/if}
      {#if historyJobs.length && historyHasMore}
        <div class="h-8" aria-hidden="true" use:observeHistorySentinel></div>
      {/if}
    </div>
  {/if}
</div>
