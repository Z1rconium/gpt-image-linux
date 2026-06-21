import { get, writable } from 'svelte/store';
import { apiFetch } from '$lib/api/client';
import { openJsonEventSource } from '$lib/api/events';
import { t } from '$lib/i18n';
import { filenameFromImageUrl, jobFailureMessage } from '$lib/utils/format';
import { isActiveJobStatus } from '$lib/utils/jobs';
import type { GenerateJobResponse, GenerateJobStatus } from '$lib/api/types';
import type { PreviewState } from '$lib/stores/preview';

export type JobsState = {
  jobs: GenerateJobStatus[];
  historyJobs: GenerateJobStatus[];
  historyLoading: boolean;
  historyLoaded: boolean;
  historyNeedsRefresh: boolean;
  historyHasMore: boolean;
  historyFailedOnly: boolean;
  selectedIds: Set<string>;
};

const initialJobsState: JobsState = {
  jobs: [],
  historyJobs: [],
  historyLoading: false,
  historyLoaded: false,
  historyNeedsRefresh: false,
  historyHasMore: false,
  historyFailedOnly: false,
  selectedIds: new Set()
};

const HISTORY_PAGE_SIZE = 50;
const HISTORY_CACHE_LIMIT = 500;

function jobImageSignature(image: NonNullable<GenerateJobStatus['images']>[number]) {
  return [
    image.image_id,
    image.image_url || '',
    image.filename || '',
    image.image_width ?? '',
    image.image_height ?? ''
  ].join('|');
}

function jobSignature(job: GenerateJobStatus) {
  const imageSignature = job.images?.map((image) => jobImageSignature(image)).join(',') || '';
  const stageSignature = job.stage_timings
    ? Object.entries(job.stage_timings)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, value]) => `${key}:${value}`)
        .join(',')
    : '';
  return [
    job.job_id,
    job.status,
    job.stage || '',
    job.message || '',
    job.operation || '',
    job.updated_at || '',
    job.completed_at || '',
    job.image_id || '',
    job.image_url || '',
    job.prompt || '',
    job.size || '',
    job.model || '',
    job.duration || '',
    job.error || '',
    imageSignature,
    stageSignature
  ].join('::');
}

function sameJobList(left: GenerateJobStatus[], right: GenerateJobStatus[]) {
  if (left.length !== right.length) return false;
  for (let index = 0; index < left.length; index += 1) {
    const current = left[index];
    const next = right[index];
    if (!next || current.job_id !== next.job_id || jobSignature(current) !== jobSignature(next)) return false;
  }
  return true;
}

function sameStringSet(left: Set<string>, right: Set<string>) {
  if (left.size !== right.size) return false;
  for (const value of left) {
    if (!right.has(value)) return false;
  }
  return true;
}

function mergeHistoryJobs(currentJobs: GenerateJobStatus[], nextJobs: GenerateJobStatus[]) {
  const merged: GenerateJobStatus[] = [];
  const seen = new Set<string>();
  for (const job of [...currentJobs, ...nextJobs]) {
    if (seen.has(job.job_id)) continue;
    seen.add(job.job_id);
    merged.push(job);
    if (merged.length >= HISTORY_CACHE_LIMIT) break;
  }
  return merged;
}

function createJobsStore() {
  const { subscribe, update } = writable<JobsState>(initialJobsState);
  let state = initialJobsState;
  let jobsSource: EventSource | null = null;
  let jobsPollingTimer: ReturnType<typeof setInterval> | null = null;
  let jobsFeedHealthy = false;
  let activeJobSource: EventSource | null = null;
  let activeJobPollingTimer: ReturnType<typeof setTimeout> | null = null;
  let trackedJobId: string | null = null;
  let historyRequestSeq = 0;

  subscribe((value) => {
    state = value;
  });

  function applyActiveJobs(jobs: GenerateJobStatus[]) {
    const activeIds = new Set(jobs.map((job) => job.job_id));
    const selectedIds = new Set([...state.selectedIds].filter((id) => activeIds.has(id)));
    update((current) => {
      if (sameJobList(current.jobs, jobs) && sameStringSet(current.selectedIds, selectedIds)) return current;
      return { ...current, jobs, selectedIds };
    });
  }

  async function loadJobs() {
    try {
      const jobs = await apiFetch<GenerateJobStatus[]>('/api/generate/jobs', {}, 'loading jobs');
      applyActiveJobs(jobs);
    } catch {
      // Keep the last successful snapshot on transient failures.
    }
  }

  async function loadJobHistory(options: { append?: boolean; failedOnly?: boolean } = {}) {
    if (state.historyLoading) return;
    const failedOnly = options.failedOnly ?? state.historyFailedOnly;
    const append = Boolean(options.append) && state.historyFailedOnly === failedOnly;
    const filterChanged = state.historyFailedOnly !== failedOnly;
    const cursorJob = append ? state.historyJobs[state.historyJobs.length - 1] : null;
    const seq = ++historyRequestSeq;
    update((current) => ({
      ...current,
      historyFailedOnly: failedOnly,
      historyLoading: true,
      ...(filterChanged ? { historyJobs: [], historyLoaded: false, historyHasMore: false } : {})
    }));
    try {
      const params = new URLSearchParams({
        include_finished: 'true',
        limit: String(HISTORY_PAGE_SIZE)
      });
      if (append && cursorJob?.updated_at && cursorJob.job_id) {
        params.set('before_updated_at', cursorJob.updated_at);
        params.set('before_job_id', cursorJob.job_id);
      } else if (append) {
        params.set('offset', String(state.historyJobs.length));
      }
      if (failedOnly) params.set('failed_only', 'true');
      const historyJobs = await apiFetch<GenerateJobStatus[]>(`/api/generate/jobs?${params.toString()}`, {}, 'loading job history');
      if (seq !== historyRequestSeq) return;
      update((current) => {
        const mergedJobs = append ? mergeHistoryJobs(current.historyJobs, historyJobs) : historyJobs;
        if (
          sameJobList(current.historyJobs, mergedJobs) &&
          current.historyLoaded &&
          current.historyFailedOnly === failedOnly &&
          current.historyHasMore === (historyJobs.length === HISTORY_PAGE_SIZE) &&
          !filterChanged
        ) {
          return { ...current, historyLoading: false, historyNeedsRefresh: false };
        }
        return {
          ...current,
          historyJobs: mergedJobs,
          historyLoaded: true,
          historyFailedOnly: failedOnly,
          historyHasMore: historyJobs.length === HISTORY_PAGE_SIZE,
          historyNeedsRefresh: append ? current.historyNeedsRefresh : false
        };
      });
    } catch {
      if (seq !== historyRequestSeq) return;
      update((current) => ({
        ...current,
        historyJobs: append ? current.historyJobs : [],
        historyLoaded: true,
        historyFailedOnly: failedOnly,
        historyHasMore: false
      }));
    } finally {
      if (seq === historyRequestSeq) update((current) => ({ ...current, historyLoading: false }));
    }
  }

  async function loadMoreJobHistory() {
    if (!state.historyHasMore || state.historyLoading) return;
    await loadJobHistory({ append: true, failedOnly: state.historyFailedOnly });
  }

  async function refreshHistoryIfLoaded() {
    if (!state.historyLoaded || !state.historyNeedsRefresh) return;
    await loadJobHistory({ failedOnly: state.historyFailedOnly });
  }

  async function setHistoryFailedOnly(failedOnly: boolean) {
    if (state.historyFailedOnly === failedOnly && state.historyLoaded) {
      if (state.historyNeedsRefresh) await refreshHistoryIfLoaded();
      return;
    }
    await loadJobHistory({ failedOnly });
  }

  function markHistoryStale() {
    if (state.historyNeedsRefresh) return;
    update((current) => ({ ...current, historyNeedsRefresh: true }));
  }

  function shouldRefreshJobsAfterSubmit() {
    return !jobsFeedHealthy;
  }

  async function clearJobHistory() {
    const seq = ++historyRequestSeq;
    update((current) => ({
      ...current,
      historyLoading: true,
      historyNeedsRefresh: false
    }));
    try {
      await apiFetch('/api/generate/jobs/history', { method: 'DELETE' }, 'clearing job history');
      if (seq !== historyRequestSeq) return;
      update((current) => ({
        ...current,
        historyJobs: [],
        historyLoaded: true,
        historyHasMore: false,
        historyLoading: false,
        historyNeedsRefresh: false
      }));
    } catch (error) {
      if (seq === historyRequestSeq) update((current) => ({ ...current, historyLoading: false }));
      throw error;
    }
  }

  function startJobsPolling() {
    if (jobsPollingTimer) return;
    void loadJobs();
    jobsPollingTimer = setInterval(() => {
      void loadJobs();
    }, 5000);
  }

  function stopJobsPolling() {
    if (jobsPollingTimer) clearInterval(jobsPollingTimer);
    jobsPollingTimer = null;
  }

  function startJobsEvents() {
    jobsSource?.close();
    jobsSource = openJsonEventSource<GenerateJobStatus[]>('/api/generate/jobs/events', {
      onEvent: ({ data }) => {
        stopJobsPolling();
        jobsFeedHealthy = true;
        if (Array.isArray(data)) applyActiveJobs(data);
      },
      onNetworkError: () => {
        jobsFeedHealthy = false;
        startJobsPolling();
      },
      onError: () => {
        jobsFeedHealthy = false;
        startJobsPolling();
      }
    });
    jobsFeedHealthy = true;
  }

  function toggleSelection(jobId: string) {
    const selectedIds = new Set(state.selectedIds);
    if (selectedIds.has(jobId)) selectedIds.delete(jobId);
    else selectedIds.add(jobId);
    update((current) => ({ ...current, selectedIds }));
  }

  function toggleAll() {
    const selectedIds = state.selectedIds.size === state.jobs.length ? new Set<string>() : new Set(state.jobs.map((job) => job.job_id));
    update((current) => ({ ...current, selectedIds }));
  }

  async function cancelSelected() {
    const ids = [...state.selectedIds];
    await Promise.all(
      ids.map((jobId) =>
        apiFetch(`/api/generate/${encodeURIComponent(jobId)}`, { method: 'DELETE' }, 'cancelling job').catch(() => null)
      )
    );
    update((current) => ({ ...current, selectedIds: new Set() }));
    await loadJobs();
    await refreshHistoryIfLoaded();
  }

  function trackJob(
    jobId: string,
    updatePreviewFromJob: (job: GenerateJobStatus) => Promise<void>,
    setPreviewError: (message: string) => void
  ) {
    if (!jobId) return;
    let terminal = false;
    closeActiveJobSource();
    trackedJobId = jobId;
    activeJobSource = openJsonEventSource<GenerateJobStatus>(`/api/generate/${encodeURIComponent(jobId)}/events`, {
      onEvent: ({ data }) => {
        if (trackedJobId !== jobId) return;
        void updatePreviewFromJob(data);
        if (!isActiveJobStatus(data.status)) {
          terminal = true;
          closeActiveJobSource();
        }
      },
      onNetworkError: () => {
        if (!terminal && trackedJobId === jobId) void pollJob(jobId, updatePreviewFromJob, setPreviewError);
      },
      onError: () => {
        closeActiveJobEventSource();
        if (!terminal && trackedJobId === jobId) void pollJob(jobId, updatePreviewFromJob, setPreviewError);
      }
    });
  }

  async function pollJob(
    jobId: string,
    updatePreviewFromJob: (job: GenerateJobStatus) => Promise<void>,
    setPreviewError: (message: string) => void
  ) {
    if (trackedJobId !== jobId) return;
    clearActiveJobPollingTimer();
    try {
      const job = await apiFetch<GenerateJobStatus>(`/api/generate/${encodeURIComponent(jobId)}`, {}, 'loading job');
      if (trackedJobId !== jobId) return;
      await updatePreviewFromJob(job);
      if (trackedJobId !== jobId) return;
      if (isActiveJobStatus(job.status)) {
        activeJobPollingTimer = setTimeout(() => {
          activeJobPollingTimer = null;
          if (trackedJobId === jobId) void pollJob(jobId, updatePreviewFromJob, setPreviewError);
        }, 1200);
      } else {
        closeActiveJobSource();
      }
    } catch (error) {
      if (trackedJobId !== jobId) return;
      setPreviewError(error instanceof Error ? error.message : get(t).messages.jobLoadFailed);
      closeActiveJobSource();
    }
  }

  function makeQueuedPreview(currentPrompt: string, operation: NonNullable<GenerateJobResponse['operation']>): PreviewState {
    closeActiveJobSource();
    return {
      loading: true,
      error: '',
      imageUrl: '',
      filename: '',
      prompt: currentPrompt,
      job: {
        job_id: '',
        status: 'queued',
        stage: 'queued',
        message: operation === 'edit' ? get(t).messages.queuedEdit : get(t).messages.queuedGeneration,
        operation
      }
    };
  }

  function previewFromJob(job: GenerateJobStatus, preview: PreviewState): PreviewState {
    const primaryImage = job.images?.[0];
    const image = primaryImage?.image_url || job.image_url || '';
    return {
      loading: isActiveJobStatus(job.status),
      error: jobFailureMessage(job, get(t).messages.jobFailed),
      job,
      imageUrl: image || preview.imageUrl,
      filename: primaryImage?.filename || (image ? filenameFromImageUrl(image) : preview.filename),
      prompt: job.prompt || preview.prompt
    };
  }

  function closeActiveJobEventSource() {
    activeJobSource?.close();
    activeJobSource = null;
  }

  function clearActiveJobPollingTimer() {
    if (activeJobPollingTimer) clearTimeout(activeJobPollingTimer);
    activeJobPollingTimer = null;
  }

  function closeActiveJobSource() {
    closeActiveJobEventSource();
    clearActiveJobPollingTimer();
    trackedJobId = null;
  }

  function cleanup() {
    closeActiveJobSource();
    jobsSource?.close();
    jobsSource = null;
    stopJobsPolling();
    jobsFeedHealthy = false;
    historyRequestSeq += 1;
  }

  return {
    subscribe,
    loadJobs,
    loadJobHistory,
    loadMoreJobHistory,
    refreshHistoryIfLoaded,
    setHistoryFailedOnly,
    markHistoryStale,
    shouldRefreshJobsAfterSubmit,
    clearJobHistory,
    startJobsEvents,
    toggleSelection,
    toggleAll,
    cancelSelected,
    trackJob,
    makeQueuedPreview,
    previewFromJob,
    closeActiveJobSource,
    cleanup
  };
}

export const jobsStore = createJobsStore();
