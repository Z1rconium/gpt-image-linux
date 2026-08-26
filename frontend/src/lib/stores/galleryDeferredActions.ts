import { get } from 'svelte/store';
import { apiFetch } from '$lib/api/client';
import { t } from '$lib/i18n';
import { confirmStore } from '$lib/stores/confirm';
import type { ToastOptions, ToastVariant } from '$lib/stores/ui';
import { formatBytes } from '$lib/utils/format';
import type {
  GalleryBatchResponse,
  GalleryEntry,
  GalleryExportJobStatus,
  GalleryImportJobStatus,
  GalleryResponse,
  GallerySyncJobStatus,
  NodeImageBatchUploadResponse,
  NodeImageUploadJobStatus,
  NodeImageUploadResponse
} from '$lib/api/types/gallery';
import type { GalleryLoadOptions, GalleryNavigation, GalleryOperationStatus, GalleryState } from '$lib/stores/gallery';
import { nodeImageResult, type NodeImageResultItem } from '$lib/stores/nodeImage';
import { abortError, waitForGalleryJob } from '$lib/stores/galleryJobEvents';

const STREAMING_ZIP_DOWNLOAD_BYTES_THRESHOLD = 64 * 1024 * 1024;
const NODE_IMAGE_CANCEL_POLL_INTERVAL_MS = 250;
const NODE_IMAGE_UPLOAD_TERMINAL_STATUSES = ['success', 'partial_failure', 'cancelled', 'error'];

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

function waitForAbortableDelay(delayMs: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError());
      return;
    }
    const handleAbort = () => {
      clearTimeout(timer);
      reject(abortError());
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', handleAbort);
      resolve();
    }, delayMs);
    signal?.addEventListener('abort', handleAbort, { once: true });
  });
}

function isAbortError(error: unknown) {
  return error instanceof Error && error.name === 'AbortError';
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

function waitForNodeImageUploadJob(
  jobId: string,
  onJob: (job: NodeImageUploadJobStatus) => void,
  options: { eventsUrl?: string; signal?: AbortSignal } = {}
): Promise<NodeImageUploadJobStatus> {
  return waitForGalleryJob<NodeImageUploadJobStatus>(
    {
      eventsUrl: options.eventsUrl || `/api/gallery/nodeimage-upload-jobs/${encodeURIComponent(jobId)}/events`,
      eventNames: ['nodeimage_upload'],
      terminalStatuses: NODE_IMAGE_UPLOAD_TERMINAL_STATUSES,
      resolveErrorStatuses: true,
      signal: options.signal
    },
    onJob
  );
}

function isNodeImageUploadTerminal(job: NodeImageUploadJobStatus) {
  return NODE_IMAGE_UPLOAD_TERMINAL_STATUSES.includes(job.status);
}

async function waitForNodeImageUploadTerminal(
  initialJob: NodeImageUploadJobStatus,
  statusUrl: string,
  signal?: AbortSignal
) {
  let job = initialJob;
  while (!isNodeImageUploadTerminal(job)) {
    await waitForAbortableDelay(NODE_IMAGE_CANCEL_POLL_INTERVAL_MS, signal);
    job = await apiFetch<NodeImageUploadJobStatus>(
      statusUrl,
      { signal },
      'checking NodeImage upload cancellation'
    );
  }
  return job;
}

function nodeImageJobDetail(job: NodeImageUploadJobStatus, fallbackCount: number) {
  const labels = get(t).gallery;
  const total = job.requested_count || fallbackCount;
  if (job.status === 'cancelled') return labels.nodeImageUploadCancelled(job.uploaded_count, job.failed_count);
  if (job.stage === 'cancelling') return labels.nodeImageUploadCancelling;
  if (job.status === 'success') return labels.nodeImageUploadProgress(total, total, job.uploaded_count, job.failed_count);
  return labels.nodeImageUploadProgress(
    Math.min(job.processed_count, total),
    total,
    job.uploaded_count,
    job.failed_count
  );
}

export function createDeferredGalleryActions(deps: GalleryActionDeps) {
  let nodeImageUploadInProgress = false;

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

  async function uploadToNodeImage(
    image: GalleryEntry,
    showToast: (message: string, variant?: ToastVariant) => void
  ) {
    if (nodeImageUploadInProgress) return;
    nodeImageUploadInProgress = true;
    const controller = new AbortController();
    const unregister = deps.registerAbortController(controller);
    deps.setOperationStatus({
      kind: 'nodeimage_upload',
      label: get(t).gallery.uploadingToNodeImage,
      detail: get(t).gallery.nodeImageUploadPreparing(1),
      progress: null
    });
    try {
      const result = await apiFetch<NodeImageUploadResponse>(
        `/api/gallery/${encodeURIComponent(image.id)}/nodeimage-upload`,
        { method: 'POST', signal: controller.signal },
        'uploading to NodeImage'
      );
      nodeImageResult.show({
        items: [
          {
            imageId: image.id,
            label: image.filename,
            status: 'ok',
            url: result.url,
            markdown: result.markdown,
            error: ''
          }
        ],
        uploadedCount: 1,
        failedCount: 0,
        cancelledCount: 0
      });
      showToast(get(t).messages.nodeImageUploadComplete);
    } catch (error) {
      if (isAbortError(error)) return;
      const reason = error instanceof Error ? error.message : get(t).messages.requestFailed;
      showToast(get(t).messages.nodeImageUploadFailed(reason), 'error');
    } finally {
      unregister();
      deps.setOperationStatus(null);
      nodeImageUploadInProgress = false;
    }
  }

  async function batchUploadToNodeImage(
    showToast: (message: string, variant?: ToastVariant) => void
  ) {
    const state = deps.getState();
    const count = selectedCount(state);
    if (!count || nodeImageUploadInProgress) return;
    nodeImageUploadInProgress = true;
    const controller = new AbortController();
    const unregister = deps.registerAbortController(controller);
    let jobId: string | null = null;
    let cancelRequested = false;
    let cancelUrl: string | null = null;
    let statusUrl: string | null = null;
    let cancellationPromise: Promise<void> | null = null;
    let latestJob: NodeImageUploadJobStatus | null = null;
    let terminalHandled = false;
    const entryLabels = new Map(
      (state.gallery?.images || []).map((image) => [image.id, image.filename])
    );

    const showResult = (result: NodeImageUploadJobStatus | NodeImageBatchUploadResponse) => {
      const jobResult = 'job_id' in result ? result : null;
      const items: NodeImageResultItem[] = result.results.map((item) => ({
        imageId: item.image_id,
        label: item.filename || entryLabels.get(item.image_id) || item.image_id,
        status: item.status,
        url: item.url || '',
        markdown: item.markdown || '',
        error: item.error || ''
      }));
      nodeImageResult.show({
        items,
        uploadedCount: result.uploaded_count,
        failedCount: result.failed_count,
        cancelledCount: jobResult?.cancelled_count || 0
      });
    };

    const showTerminalResult = (job: NodeImageUploadJobStatus) => {
      if (terminalHandled) return;
      terminalHandled = true;
      showResult(job);
      deps.clearSelection();
      if (job.status === 'error') {
        showToast(
          get(t).messages.nodeImageUploadFailed(job.error || job.message || get(t).gallery.nodeImageUnknownError),
          'error'
        );
        return;
      }
      showToast(
        job.status === 'cancelled'
          ? get(t).messages.nodeImageBatchCancelled(job.uploaded_count, job.failed_count)
          : get(t).messages.nodeImageBatchComplete(job.uploaded_count, job.failed_count),
        job.failed_count || job.status === 'cancelled' ? 'error' : 'status'
      );
    };

    const cancelUpload = () => {
      if (cancellationPromise) return cancellationPromise;
      cancellationPromise = (async () => {
        cancelRequested = true;
        deps.setOperationStatus({
          kind: 'nodeimage_upload',
          label: get(t).gallery.uploadingToNodeImage,
          detail: get(t).gallery.nodeImageUploadCancelling,
          progress: latestJob?.progress ?? null,
          cancel: cancelUpload,
          cancelPending: true
        });
        if (!jobId) {
          controller.abort();
          return;
        }

        const cancellationController = new AbortController();
        const unregisterCancellation = deps.registerAbortController(cancellationController);
        try {
          const fallbackStatusUrl = `/api/gallery/nodeimage-upload-jobs/${encodeURIComponent(jobId)}`;
          const cancellationRequestUrl = cancelUrl || fallbackStatusUrl;
          const requested = await apiFetch<NodeImageUploadJobStatus>(
            cancellationRequestUrl,
            {
              method: cancelUrl ? 'POST' : 'DELETE',
              signal: cancellationController.signal
            },
            'cancelling NodeImage upload'
          );
          const cancelled = await waitForNodeImageUploadTerminal(
            requested,
            statusUrl || requested.status_url || fallbackStatusUrl,
            cancellationController.signal
          );
          latestJob = cancelled;
          showTerminalResult(cancelled);
          controller.abort();
        } catch (error) {
          if (isAbortError(error)) return;
          cancelRequested = false;
          cancellationPromise = null;
          if (latestJob && !isNodeImageUploadTerminal(latestJob)) {
            deps.setOperationStatus({
              kind: 'nodeimage_upload',
              label: get(t).gallery.uploadingToNodeImage,
              detail: nodeImageJobDetail(latestJob, count),
              progress: Math.round((Math.min(latestJob.processed_count, latestJob.requested_count || count) / Math.max(1, latestJob.requested_count || count)) * 100),
              cancel: cancelUpload,
              cancelPending: false
            });
          }
          const reason = error instanceof Error ? error.message : get(t).messages.requestFailed;
          showToast(get(t).messages.nodeImageUploadFailed(reason), 'error');
        } finally {
          unregisterCancellation();
        }
      })();
      return cancellationPromise;
    };

    deps.setOperationStatus({
      kind: 'nodeimage_upload',
      label: get(t).gallery.uploadingToNodeImage,
      detail: get(t).gallery.nodeImageUploadPreparing(count),
      progress: 0
    });
    try {
      const result = await apiFetch<NodeImageUploadJobStatus | NodeImageBatchUploadResponse>(
        '/api/gallery/batch/nodeimage-upload',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(batchRequestBody(state)),
          signal: controller.signal
        },
        'uploading selected images to NodeImage'
      );
      if (!('job_id' in result)) {
        showResult(result);
        deps.clearSelection();
        showToast(
          get(t).messages.nodeImageBatchComplete(result.uploaded_count, result.failed_count),
          result.failed_count ? 'error' : 'status'
        );
        return;
      }

      jobId = result.job_id;
      cancelUrl = result.cancel_url || null;
      statusUrl = result.status_url || null;
      latestJob = result;
      deps.setOperationStatus({
        kind: 'nodeimage_upload',
        label: get(t).gallery.uploadingToNodeImage,
        detail: nodeImageJobDetail(result, count),
        progress: Math.round((Math.min(result.processed_count, count) / Math.max(1, count)) * 100),
        cancel: cancelUpload
      });
      const completedJob = await waitForNodeImageUploadJob(
        result.job_id,
        (nextJob) => {
          latestJob = nextJob;
          deps.setOperationStatus({
            kind: 'nodeimage_upload',
            label: get(t).gallery.uploadingToNodeImage,
            detail: nodeImageJobDetail(nextJob, count),
            progress: Math.round((Math.min(nextJob.processed_count, nextJob.requested_count || count) / Math.max(1, nextJob.requested_count || count)) * 100),
            cancel: cancelUpload,
            cancelPending: cancelRequested
          });
        },
        { eventsUrl: result.events_url || undefined, signal: controller.signal }
      );
      if (cancelRequested && cancellationPromise) await cancellationPromise;
      showTerminalResult(completedJob);
    } catch (error) {
      if (isAbortError(error)) return;
      if (cancelRequested && cancellationPromise) await cancellationPromise;
      if (terminalHandled) return;
      const reason = error instanceof Error ? error.message : get(t).messages.requestFailed;
      showToast(get(t).messages.nodeImageUploadFailed(reason), 'error');
    } finally {
      if (cancellationPromise) await cancellationPromise;
      unregister();
      deps.setOperationStatus(null);
      nodeImageUploadInProgress = false;
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
    uploadToNodeImage,
    batchUploadToNodeImage,
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
