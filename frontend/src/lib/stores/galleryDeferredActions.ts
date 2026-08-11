import { get } from 'svelte/store';
import { apiFetch } from '$lib/api/client';
import { openJsonEventSource } from '$lib/api/events';
import { t } from '$lib/i18n';
import { confirmStore } from '$lib/stores/confirm';
import type { ToastOptions, ToastVariant } from '$lib/stores/ui';
import { formatBytes } from '$lib/utils/format';
import type { GalleryBatchResponse, GalleryEntry, GalleryExportJobStatus, GalleryImportJobStatus, GalleryResponse, GallerySyncJobStatus } from '$lib/api/types/gallery';
import type { GalleryLoadOptions, GalleryNavigation, GalleryOperationStatus, GalleryState } from '$lib/stores/gallery';

const STREAMING_ZIP_DOWNLOAD_BYTES_THRESHOLD = 64 * 1024 * 1024;
const GALLERY_JOB_EVENT_NETWORK_TIMEOUT_MS = 30_000;

export type GalleryActionDeps = {
  getState: () => GalleryState;
  loadGallery: (
    page?: number,
    includeTotalBytes?: boolean | GalleryNavigation,
    navigation?: GalleryNavigation,
    options?: GalleryLoadOptions
  ) => Promise<void>;
  patchGalleryEntries: (ids: Iterable<string>, updater: (image: GalleryEntry) => GalleryEntry | null) => void;
  clearSelection: () => void;
  setOperationStatus: (operationStatus: GalleryOperationStatus | null) => void;
  clearPendingSingleDeletes: () => void;
  registerAbortController: (controller: AbortController) => () => void;
};

export type GalleryWaitOptions = {
  eventsUrl: string;
  eventNames: string[];
  signal?: AbortSignal;
};

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function startNativeDownload(url: string, filename?: string) {
  const link = document.createElement('a');
  link.href = url;
  if (filename) link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function canUseFileSystemAccess() {
  return typeof window !== 'undefined' && typeof (window as unknown as { showSaveFilePicker?: unknown }).showSaveFilePicker === 'function';
}

type FileSystemWritableLike = {
  write: (chunk: Uint8Array) => Promise<void>;
  close: () => Promise<void>;
  abort?: () => Promise<void>;
};

type SaveFilePicker = (options?: {
  suggestedName?: string;
  types?: Array<{ description: string; accept: Record<string, string[]> }>;
}) => Promise<{ createWritable: () => Promise<FileSystemWritableLike> }>;

function parseHeaderInt(headers: Headers, name: string) {
  const parsed = Number.parseInt(headers.get(name) || '', 10);
  return Number.isFinite(parsed) ? parsed : 0;
}

function filenameFromContentDisposition(header: string | null, fallback: string) {
  const match = header?.match(/filename="?([^";]+)"?/i);
  return match?.[1] || fallback;
}

function operationProgressDetail(loaded: number, total: number) {
  if (total > 0) return `${formatBytes(loaded)} / ${formatBytes(total)}`;
  return loaded > 0 ? formatBytes(loaded) : '';
}

function operationProgress(progress: number, start = 0, end = 100) {
  const bounded = Math.max(0, Math.min(100, progress));
  return Math.min(end, Math.max(start, start + Math.round((bounded / 100) * (end - start))));
}

function exportJobDetail(job: GalleryExportJobStatus) {
  const labels = get(t).gallery;
  if (job.status === 'success') return labels.exportDownloadReady;
  if (job.stage === 'streaming') {
    return labels.exportStreamingArchive(formatBytes(job.bytes_written), formatBytes(job.bytes_total));
  }
  if (job.stage === 'packing') {
    return labels.exportPackingArchive(formatBytes(job.bytes_written), formatBytes(job.bytes_total));
  }
  if (job.requested_count > 0 && job.processed_count > 0) {
    return labels.exportPreparingEntries(Math.min(job.processed_count, job.requested_count), job.requested_count);
  }
  return job.message || labels.exportPreparing;
}

function syncJobDetail(job: GallerySyncJobStatus) {
  const labels = get(t).gallery;
  if (job.status === 'success' && job.dry_run) {
    return labels.syncDryRunCompleteDetail(job.total_count, job.pending_upload_count, job.skipped_existing_count, job.missing_local_count);
  }
  if (job.status === 'success') return labels.syncCompleteDetail(job.uploaded_count, job.skipped_existing_count);
  if (job.dry_run && job.total_count > 0) {
    return labels.syncDryRunProgress(
      Math.min(job.compared_count, job.total_count),
      job.total_count,
      job.pending_upload_count,
      job.skipped_existing_count,
      job.missing_local_count
    );
  }
  if (job.stage === 'listing_remote') return labels.syncListingRemote;
  if (job.total_count > 0) {
    return labels.syncProgress(
      Math.min(job.compared_count, job.total_count),
      job.total_count,
      job.uploaded_count,
      job.skipped_existing_count,
      formatBytes(job.bytes_uploaded)
    );
  }
  return job.message || labels.syncPreparing;
}

function importJobDetail(job: GalleryImportJobStatus) {
  const labels = get(t).gallery;
  if (job.status === 'success') return labels.importCompleteDetail(job.imported_count, job.skipped_count);
  if (job.requested_count > 0 && job.processed_count > 0) {
    return labels.importValidatingEntries(Math.min(job.processed_count, job.requested_count), job.requested_count, job.imported_count, job.skipped_count);
  }
  return job.message || labels.importingArchive;
}

function batchToastMessage(action: 'delete' | 'favorite', result: GalleryBatchResponse) {
  const updatedCount = result.updated_count ?? result.count;
  const missingCount = result.missing_count ?? Math.max(0, (result.requested_count || 0) - updatedCount);
  if (action === 'delete') return get(t).messages.selectedImagesDeleted(updatedCount, missingCount);
  return get(t).messages.selectedImagesFavorited(updatedCount, missingCount);
}

function selectedCount(state: GalleryState) {
  return state.selectionToken?.count || state.selectedIds.size;
}

function selectedVisibleIds(state: GalleryState) {
  if (state.selectionToken) return state.gallery?.images.map((image) => image.id) || [];
  return state.gallery?.images.filter((image) => state.selectedIds.has(image.id)).map((image) => image.id) || [];
}

function selectedVisibleEntries(state: GalleryState) {
  const visibleIds = selectedVisibleIds(state);
  if (!visibleIds.length) return { visibleIds, visibleIdSet: new Set<string>(), entries: [] };
  const visibleIdSet = new Set(visibleIds);
  return {
    visibleIds,
    visibleIdSet,
    entries: state.gallery?.images.filter((image) => visibleIdSet.has(image.id)) || []
  };
}

function batchRequestBody(state: GalleryState) {
  if (state.selectionToken) return { selection_token: state.selectionToken.token };
  return { ids: [...state.selectedIds] };
}

async function refreshGalleryPageBestEffort(
  deps: GalleryActionDeps,
  page: number
) {
  try {
    await deps.loadGallery(page);
  } catch {
    // Keep the optimistic batch update visible if the follow-up refresh fails.
  }
}

function abortError() {
  return new DOMException('Gallery operation cancelled', 'AbortError');
}

function isAbortError(error: unknown) {
  return error instanceof Error && error.name === 'AbortError';
}

function networkTimeoutError() {
  return new Error(get(t).messages.requestFailed);
}

export function waitForGalleryJob<T extends { status: string; error?: string | null; message?: string | null }>(
  options: GalleryWaitOptions,
  onJob: (job: T) => void
): Promise<T> {
  return new Promise((resolve, reject) => {
    let settled = false;
    let source: EventSource | null = null;
    let networkTimer: ReturnType<typeof setTimeout> | null = null;

    const clearNetworkTimer = () => {
      if (networkTimer) clearTimeout(networkTimer);
      networkTimer = null;
    };

    const settle = (callback: () => void) => {
      if (settled) return;
      settled = true;
      clearNetworkTimer();
      source?.close();
      options.signal?.removeEventListener('abort', handleAbort);
      callback();
    };

    const handleAbort = () => {
      settle(() => reject(abortError()));
    };

    if (options.signal?.aborted) {
      reject(abortError());
      return;
    }

    options.signal?.addEventListener('abort', handleAbort, { once: true });
    source = openJsonEventSource<T>(
      options.eventsUrl,
      {
        onEvent: ({ data }) => {
          clearNetworkTimer();
          onJob(data);
          if (data.status === 'success') {
            settle(() => resolve(data));
          } else if (data.status === 'error') {
            settle(() => reject(new Error(data.error || data.message || get(t).messages.requestFailed)));
          }
        },
        onNetworkError: () => {
          if (settled || networkTimer) return;
          networkTimer = setTimeout(() => {
            settle(() => reject(networkTimeoutError()));
          }, GALLERY_JOB_EVENT_NETWORK_TIMEOUT_MS);
        },
        onError: (error) => {
          settle(() => reject(error instanceof Error ? error : new Error(get(t).messages.requestFailed)));
        }
      },
      options.eventNames
    );
    source.onopen = () => {
      clearNetworkTimer();
    };
  });
}

function waitForGalleryExportJob(
  jobId: string,
  onJob: (job: GalleryExportJobStatus) => void,
  options: { eventsUrl?: string; signal?: AbortSignal } = {}
): Promise<GalleryExportJobStatus> {
  return waitForGalleryJob<GalleryExportJobStatus>(
    {
      eventsUrl: options.eventsUrl || `/api/gallery/export-jobs/${encodeURIComponent(jobId)}/events`,
      eventNames: ['export'],
      signal: options.signal
    },
    onJob
  );
}

function waitForGallerySyncJob(
  jobId: string,
  onJob: (job: GallerySyncJobStatus) => void,
  options: { signal?: AbortSignal } = {}
): Promise<GallerySyncJobStatus> {
  return waitForGalleryJob<GallerySyncJobStatus>(
    {
      eventsUrl: `/api/gallery/sync-jobs/${encodeURIComponent(jobId)}/events`,
      eventNames: ['sync'],
      signal: options.signal
    },
    onJob
  );
}

function waitForGalleryImportJob(
  jobId: string,
  onJob: (job: GalleryImportJobStatus) => void,
  options: { signal?: AbortSignal } = {}
): Promise<GalleryImportJobStatus> {
  return waitForGalleryJob<GalleryImportJobStatus>(
    {
      eventsUrl: `/api/gallery/import-jobs/${encodeURIComponent(jobId)}/events`,
      eventNames: ['import'],
      signal: options.signal
    },
    onJob
  );
}

export function createDeferredGalleryActions(deps: GalleryActionDeps) {
  async function waitWithAbort<T>(wait: (signal: AbortSignal) => Promise<T>): Promise<T> {
    const controller = new AbortController();
    const unregister = deps.registerAbortController(controller);
    try {
      return await wait(controller.signal);
    } finally {
      unregister();
    }
  }

  async function downloadResponseBlob(
    response: Response,
    kind: GalleryOperationStatus['kind'],
    label: string,
    initialDetail: string,
    progressRange: { start: number; end: number } = { start: 0, end: 100 }
  ) {
    const total = Number.parseInt(response.headers.get('Content-Length') || '0', 10);
    if (!response.body) {
      const blob = await response.blob();
      deps.setOperationStatus({ kind, label, detail: initialDetail, progress: progressRange.end });
      return blob;
    }

    const reader = response.body.getReader();
    const chunks: BlobPart[] = [];
    let loaded = 0;
    deps.setOperationStatus({ kind, label, detail: initialDetail, progress: total > 0 ? progressRange.start : null });

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (!value) continue;
      const chunk = new Uint8Array(value.byteLength);
      chunk.set(value);
      chunks.push(chunk);
      loaded += value.byteLength;
      deps.setOperationStatus({
        kind,
        label,
        detail: operationProgressDetail(loaded, total) || initialDetail,
        progress: total > 0 ? operationProgress((loaded / total) * 100, progressRange.start, progressRange.end) : null
      });
    }

    return new Blob(chunks, { type: response.headers.get('Content-Type') || 'application/zip' });
  }

  async function saveResponseStreamToFile(
    response: Response,
    kind: GalleryOperationStatus['kind'],
    label: string,
    initialDetail: string,
    fallbackFilename: string,
    progressRange: { start: number; end: number } = { start: 0, end: 100 }
  ) {
    const picker = (window as unknown as { showSaveFilePicker?: SaveFilePicker }).showSaveFilePicker;
    if (!picker || !response.body) return false;

    const filename = filenameFromContentDisposition(response.headers.get('Content-Disposition'), fallbackFilename);
    const handle = await picker({
      suggestedName: filename,
      types: [{ description: 'ZIP archive', accept: { 'application/zip': ['.zip'] } }]
    });
    const writable = await handle.createWritable();
    const reader = response.body.getReader();
    const total = Number.parseInt(response.headers.get('Content-Length') || '0', 10);
    let loaded = 0;
    deps.setOperationStatus({ kind, label, detail: initialDetail, progress: total > 0 ? progressRange.start : null });

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        if (!value) continue;
        await writable.write(value);
        loaded += value.byteLength;
        deps.setOperationStatus({
          kind,
          label,
          detail: operationProgressDetail(loaded, total) || initialDetail,
          progress: total > 0 ? operationProgress((loaded / total) * 100, progressRange.start, progressRange.end) : null
        });
      }
      await writable.close();
      deps.setOperationStatus({ kind, label, detail: operationProgressDetail(loaded, total) || initialDetail, progress: progressRange.end });
      return true;
    } catch (error) {
      await writable.abort?.();
      throw error;
    }
  }

  async function batchFavorite(favorite: boolean, showToast: (message: string) => void, onAffected?: (ids: string[], favorite: boolean) => void) {
    const state = deps.getState();
    const count = selectedCount(state);
    if (!count) return;
    const { visibleIds } = selectedVisibleEntries(state);
    const result = await apiFetch<GalleryBatchResponse>(
      '/api/gallery/batch/favorite',
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...batchRequestBody(state), favorite })
      },
      'updating selected favorites'
    );
    const refreshRequired = (!favorite && state.filters.favorite) || result.count !== visibleIds.length;
    deps.patchGalleryEntries(
      visibleIds,
      (image) => (state.filters.favorite && !favorite ? null : { ...image, favorite })
    );
    deps.clearSelection();
    onAffected?.(visibleIds, favorite);
    showToast(batchToastMessage('favorite', result));
    if (refreshRequired) {
      await refreshGalleryPageBestEffort(deps, state.page);
    }
  }

  async function batchDelete(
    showToast: (message: string, variant?: ToastVariant, options?: ToastOptions) => void,
    onDeleted?: (ids: string[]) => void
  ) {
    const state = deps.getState();
    const count = selectedCount(state);
    if (!count) return;
    const { visibleIds, entries: selectedEntries } = selectedVisibleEntries(state);
    const selectedBytes = selectedEntries.reduce((sum, image) => sum + (image.bytes || 0), 0);
    const details = [
      get(t).confirm.deleteSelectedDetail(count),
      selectedEntries.length ? get(t).confirm.deleteSelectedSize(formatBytes(selectedBytes)) : ''
    ].filter(Boolean);
    const confirmed = await confirmStore.confirm({
      title: get(t).confirm.deleteSelectedTitle(count),
      message: get(t).confirm.deleteSelectedMessage(count),
      details,
      confirmLabel: get(t).gallery.deleteSelected,
      cancelLabel: get(t).confirm.cancel,
      closeLabel: get(t).confirm.closeLabel,
      variant: 'danger'
    });
    if (!confirmed) return;
    const result = await apiFetch<GalleryBatchResponse>(
      '/api/gallery/batch/delete',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(batchRequestBody(state))
      },
      'deleting selected images'
    );
    deps.patchGalleryEntries(visibleIds, () => null);
    onDeleted?.(visibleIds);
    deps.clearSelection();
    showToast(batchToastMessage('delete', result));
    await refreshGalleryPageBestEffort(deps, state.page);
  }

  async function batchDownload(showToast?: (message: string) => void) {
    const state = deps.getState();
    const count = selectedCount(state);
    if (!count) return;
    const label = get(t).gallery.downloadingSelected;
    const { entries: selectedEntries } = selectedVisibleEntries(state);
    const selectedBytes = selectedEntries.reduce((sum, image) => sum + (image.bytes || 0), 0);
    deps.setOperationStatus({
      kind: 'download',
      label,
      detail: get(t).gallery.downloadPreparing(count),
      progress: 0
    });
    try {
      if (!state.selectionToken && selectedBytes >= STREAMING_ZIP_DOWNLOAD_BYTES_THRESHOLD && canUseFileSystemAccess()) {
        const response = await fetch('/api/gallery/batch/download', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', Accept: 'application/zip' },
          body: JSON.stringify(batchRequestBody(state))
        });
        if (!response.ok) throw new Error(get(t).messages.requestFailed);
        await saveResponseStreamToFile(response, 'download', label, get(t).gallery.browserSavingDownload, 'gpt-images-selected.zip');
        const requestedCount = parseHeaderInt(response.headers, 'X-Gallery-Requested-Count') || count;
        const exportedCount = parseHeaderInt(response.headers, 'X-Gallery-Exported-Count') || requestedCount;
        const missingCount = parseHeaderInt(response.headers, 'X-Gallery-Missing-Count');
        showToast?.(get(t).messages.selectedImagesDownloaded(exportedCount, missingCount));
        return;
      }

      const job = await apiFetch<GalleryExportJobStatus>(
        '/api/gallery/export-jobs',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(batchRequestBody(state))
        },
        'preparing selected image download'
      );
      const readyJob = await waitWithAbort((signal) =>
        waitForGalleryExportJob(
          job.job_id,
          (nextJob) => {
            deps.setOperationStatus({
              kind: 'download',
              label,
              detail: exportJobDetail(nextJob),
              progress: operationProgress(nextJob.progress, 0, 50)
            });
          },
          { signal }
        )
      );
      deps.setOperationStatus({
        kind: 'download',
        label,
        detail: get(t).gallery.browserSavingDownload,
        progress: 50
      });
      const downloadUrl = readyJob.download_url || `/api/gallery/export-jobs/${encodeURIComponent(readyJob.job_id)}/download`;
      if (readyJob.bytes_total >= STREAMING_ZIP_DOWNLOAD_BYTES_THRESHOLD) {
        startNativeDownload(downloadUrl, 'gpt-images-selected.zip');
        const requestedCount = readyJob.requested_count || count;
        const exportedCount = readyJob.exported_count || requestedCount;
        const missingCount = readyJob.missing_count || 0;
        showToast?.(get(t).messages.selectedImagesDownloaded(exportedCount, missingCount));
        return;
      }

      const response = await fetch(downloadUrl, {
        method: 'GET',
        credentials: 'same-origin',
        headers: { Accept: 'application/zip' }
      });
      if (!response.ok) throw new Error(get(t).messages.requestFailed);

      const blob = await downloadResponseBlob(
        response,
        'download',
        label,
        get(t).gallery.browserSavingDownload,
        { start: 50, end: 100 }
      );
      downloadBlob(blob, filenameFromContentDisposition(response.headers.get('Content-Disposition'), 'gpt-images-selected.zip'));
      const requestedCount = readyJob.requested_count || parseHeaderInt(response.headers, 'X-Gallery-Requested-Count') || count;
      const exportedCount = readyJob.exported_count || parseHeaderInt(response.headers, 'X-Gallery-Exported-Count') || requestedCount;
      const missingCount = readyJob.missing_count || parseHeaderInt(response.headers, 'X-Gallery-Missing-Count');
      showToast?.(get(t).messages.selectedImagesDownloaded(exportedCount, missingCount));
    } catch (error) {
      if (isAbortError(error)) return;
      throw error;
    } finally {
      deps.setOperationStatus(null);
    }
  }

  async function exportArchive(showToast?: (message: string) => void) {
    const label = get(t).gallery.exportingArchive;
    deps.setOperationStatus({
      kind: 'export',
      label,
      detail: get(t).gallery.exportPreparing,
      progress: 0
    });
    try {
      const job = await apiFetch<GalleryExportJobStatus>('/api/gallery/direct-export-jobs', { method: 'POST' }, 'preparing direct gallery export');
      deps.setOperationStatus({
        kind: 'export',
        label,
        detail: exportJobDetail(job),
        progress: operationProgress(job.progress, 0, 100)
      });
      const readyJobPromise = waitWithAbort((signal) =>
        waitForGalleryExportJob(
          job.job_id,
          (nextJob) => {
            deps.setOperationStatus({
              kind: 'export',
              label,
              detail: exportJobDetail(nextJob),
              progress: operationProgress(nextJob.progress, 0, 100)
            });
          },
          {
            eventsUrl: `/api/gallery/direct-export-jobs/${encodeURIComponent(job.job_id)}/events`,
            signal
          }
        )
      );
      const downloadUrl = job.download_url || `/api/download-all?export_job_id=${encodeURIComponent(job.job_id)}`;
      startNativeDownload(downloadUrl, job.filename || 'gpt-images.zip');
      await readyJobPromise;
      showToast?.(get(t).messages.exportReady);
    } catch (error) {
      if (isAbortError(error)) return;
      throw error;
    } finally {
      deps.setOperationStatus(null);
    }
  }

  async function syncGallery(showToast?: (message: string) => void) {
    const label = get(t).gallery.syncingR2;
    deps.setOperationStatus({
      kind: 'sync',
      label,
      detail: get(t).gallery.syncPreparing,
      progress: 0
    });
    try {
      const dryRunJob = await apiFetch<GallerySyncJobStatus>(
        '/api/gallery/sync-jobs',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ dry_run: true })
        },
        'preflighting R2 gallery sync'
      );
      const dryRunFinished = await waitWithAbort((signal) =>
        waitForGallerySyncJob(
          dryRunJob.job_id,
          (nextJob) => {
            deps.setOperationStatus({
              kind: 'sync',
              label,
              detail: syncJobDetail(nextJob),
              progress: nextJob.progress
            });
          },
          { signal }
        )
      );
      deps.setOperationStatus({
        kind: 'sync',
        label,
        detail: syncJobDetail(dryRunFinished),
        progress: 100
      });
      if (dryRunFinished.total_count <= 0 || dryRunFinished.pending_upload_count <= 0) {
        showToast?.(
          get(t).messages.r2SyncComplete(
            0,
            dryRunFinished.skipped_existing_count,
            dryRunFinished.missing_local_count
          )
        );
        return;
      }

      const job = await apiFetch<GallerySyncJobStatus>('/api/gallery/sync-jobs', { method: 'POST' }, 'starting R2 gallery sync');
      const finished = await waitWithAbort((signal) =>
        waitForGallerySyncJob(
          job.job_id,
          (nextJob) => {
            deps.setOperationStatus({
              kind: 'sync',
              label,
              detail: syncJobDetail(nextJob),
              progress: nextJob.progress
            });
          },
          { signal }
        )
      );
      showToast?.(get(t).messages.r2SyncComplete(finished.uploaded_count, finished.skipped_existing_count, finished.missing_local_count));
    } catch (error) {
      if (isAbortError(error)) return;
      throw error;
    } finally {
      deps.setOperationStatus(null);
    }
  }

  async function deleteAll(
    showToast: (message: string, variant?: ToastVariant, options?: ToastOptions) => void,
    onDeleted?: () => void
  ) {
    const stats = await apiFetch<GalleryResponse>(
      '/api/gallery?page=1&page_size=1&include_total_bytes=true',
      {},
      'loading gallery delete impact'
    );
    const confirmed = await confirmStore.confirm({
      title: get(t).confirm.deleteAllTitle,
      message: get(t).confirm.deleteAllMessage(stats.total),
      details: [get(t).confirm.deleteAllDetail(formatBytes(stats.total_bytes)), get(t).confirm.deleteAllConfirmHint],
      confirmLabel: get(t).confirm.deleteAllConfirmLabel,
      cancelLabel: get(t).confirm.cancel,
      closeLabel: get(t).confirm.closeLabel,
      requiredText: get(t).confirm.deleteAllConfirmLabel,
      requiredTextLabel: get(t).confirm.deleteAllConfirmHint,
      variant: 'danger'
    });
    if (!confirmed) return;

    deps.clearPendingSingleDeletes();
    await apiFetch('/api/gallery', { method: 'DELETE' }, 'deleting all images');
    onDeleted?.();
    await deps.loadGallery(1);
    showToast(get(t).messages.allImagesDeleted);
  }

  async function importArchive(file: File, showToast: (message: string) => void) {
    const formData = new FormData();
    formData.append('archive', file, file.name);
    deps.setOperationStatus({
      kind: 'import',
      label: get(t).gallery.importingArchive,
      detail: get(t).gallery.importingArchiveDetail(formatBytes(file.size)),
      progress: null
    });
    try {
      const job = await apiFetch<GalleryImportJobStatus>(
        '/api/import?async_job=true',
        {
          method: 'POST',
          body: formData
        },
        'importing archive'
      );
      const finished = await waitWithAbort((signal) =>
        waitForGalleryImportJob(
          job.job_id,
          (nextJob) => {
            deps.setOperationStatus({
              kind: 'import',
              label: get(t).gallery.importingArchive,
              detail: importJobDetail(nextJob),
              progress: nextJob.progress
            });
          },
          { signal }
        )
      );
      deps.setOperationStatus({
        kind: 'import',
        label: get(t).gallery.importingArchive,
        detail: get(t).gallery.refreshingAfterImport,
        progress: 100
      });
      await deps.loadGallery(1);
      showToast(get(t).messages.imported(finished.imported_count));
    } catch (error) {
      if (isAbortError(error)) return;
      throw error;
    } finally {
      deps.setOperationStatus(null);
    }
  }

  return {
    batchFavorite,
    batchDelete,
    batchDownload,
    exportArchive,
    syncGallery,
    deleteAll,
    importArchive
  };
}

export type DeferredGalleryActions = ReturnType<typeof createDeferredGalleryActions>;
