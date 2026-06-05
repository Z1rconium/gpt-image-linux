import { get } from 'svelte/store';
import { apiFetch } from '$lib/api/client';
import { openJsonEventSource } from '$lib/api/events';
import { t } from '$lib/i18n';
import { confirmStore } from '$lib/stores/confirm';
import type { ToastOptions, ToastVariant } from '$lib/stores/ui';
import { formatBytes } from '$lib/utils/format';
import type { GalleryBatchResponse, GalleryExportJobStatus, GalleryResponse, GallerySyncJobStatus } from '$lib/api/types';
import type { GalleryOperationStatus, GalleryState } from '$lib/stores/gallery';

const STREAMING_ZIP_DOWNLOAD_BYTES_THRESHOLD = 64 * 1024 * 1024;

type GalleryActionDeps = {
  getState: () => GalleryState;
  loadGallery: (page?: number, includeTotalBytes?: boolean) => Promise<void>;
  clearSelection: () => void;
  setOperationStatus: (operationStatus: GalleryOperationStatus | null) => void;
  clearPendingSingleDeletes: () => void;
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
  if (job.status === 'success') return labels.syncCompleteDetail(job.uploaded_count, job.skipped_existing_count);
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

function batchToastMessage(action: 'delete' | 'favorite', result: GalleryBatchResponse) {
  const updatedCount = result.updated_count ?? result.count;
  const missingCount = result.missing_count ?? Math.max(0, (result.requested_count || 0) - updatedCount);
  if (action === 'delete') return get(t).messages.selectedImagesDeleted(updatedCount, missingCount);
  return get(t).messages.selectedImagesFavorited(updatedCount, missingCount);
}

function waitForGalleryExportJob(jobId: string, onJob: (job: GalleryExportJobStatus) => void): Promise<GalleryExportJobStatus> {
  return new Promise((resolve, reject) => {
    let settled = false;
    let source: EventSource | null = null;
    source = openJsonEventSource<GalleryExportJobStatus>(
      `/api/gallery/export-jobs/${encodeURIComponent(jobId)}/events`,
      {
        onEvent: ({ data }) => {
          onJob(data);
          if (data.status === 'success') {
            settled = true;
            source?.close();
            resolve(data);
          } else if (data.status === 'error') {
            settled = true;
            source?.close();
            reject(new Error(data.error || data.message || get(t).messages.requestFailed));
          }
        },
        onError: (error) => {
          if (settled) return;
          settled = true;
          source?.close();
          reject(error instanceof Error ? error : new Error(get(t).messages.requestFailed));
        }
      },
      ['export']
    );
  });
}

function waitForGallerySyncJob(jobId: string, onJob: (job: GallerySyncJobStatus) => void): Promise<GallerySyncJobStatus> {
  return new Promise((resolve, reject) => {
    let settled = false;
    let source: EventSource | null = null;
    source = openJsonEventSource<GallerySyncJobStatus>(
      `/api/gallery/sync-jobs/${encodeURIComponent(jobId)}/events`,
      {
        onEvent: ({ data }) => {
          onJob(data);
          if (data.status === 'success') {
            settled = true;
            source?.close();
            resolve(data);
          } else if (data.status === 'error') {
            settled = true;
            source?.close();
            reject(new Error(data.error || data.message || get(t).messages.requestFailed));
          }
        },
        onError: (error) => {
          if (settled) return;
          settled = true;
          source?.close();
          reject(error instanceof Error ? error : new Error(get(t).messages.requestFailed));
        }
      },
      ['sync']
    );
  });
}

export function createGalleryActions(deps: GalleryActionDeps) {
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
    const ids = [...state.selectedIds];
    if (!ids.length) return;
    const result = await apiFetch<GalleryBatchResponse>(
      '/api/gallery/batch/favorite',
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids, favorite })
      },
      'updating selected favorites'
    );
    await deps.loadGallery(state.page);
    onAffected?.(ids, favorite);
    showToast(batchToastMessage('favorite', result));
  }

  async function batchDelete(
    showToast: (message: string, variant?: ToastVariant, options?: ToastOptions) => void,
    onDeleted?: (ids: string[]) => void
  ) {
    const state = deps.getState();
    const ids = [...state.selectedIds];
    if (!ids.length) return;
    const selectedEntries = state.gallery?.images.filter((image) => ids.includes(image.id)) || [];
    const selectedBytes = selectedEntries.reduce((sum, image) => sum + (image.bytes || 0), 0);
    const details = [
      get(t).confirm.deleteSelectedDetail(ids.length),
      selectedEntries.length ? get(t).confirm.deleteSelectedSize(formatBytes(selectedBytes)) : ''
    ].filter(Boolean);
    const confirmed = await confirmStore.confirm({
      title: get(t).confirm.deleteSelectedTitle(ids.length),
      message: get(t).confirm.deleteSelectedMessage(ids.length),
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
        body: JSON.stringify({ ids })
      },
      'deleting selected images'
    );
    onDeleted?.(ids);
    deps.clearSelection();
    await deps.loadGallery(state.page);
    showToast(batchToastMessage('delete', result));
  }

  async function batchDownload(showToast?: (message: string) => void) {
    const state = deps.getState();
    const ids = [...state.selectedIds];
    if (!ids.length) return;
    const label = get(t).gallery.downloadingSelected;
    const selectedEntries = state.gallery?.images.filter((image) => ids.includes(image.id)) || [];
    const selectedBytes = selectedEntries.reduce((sum, image) => sum + (image.bytes || 0), 0);
    deps.setOperationStatus({
      kind: 'download',
      label,
      detail: get(t).gallery.downloadPreparing(ids.length),
      progress: 0
    });
    try {
      if (selectedBytes >= STREAMING_ZIP_DOWNLOAD_BYTES_THRESHOLD && canUseFileSystemAccess()) {
        const response = await fetch('/api/gallery/batch/download', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', Accept: 'application/zip' },
          body: JSON.stringify({ ids })
        });
        if (!response.ok) throw new Error(get(t).messages.requestFailed);
        await saveResponseStreamToFile(response, 'download', label, get(t).gallery.browserSavingDownload, 'gpt-images-selected.zip');
        const requestedCount = parseHeaderInt(response.headers, 'X-Gallery-Requested-Count') || ids.length;
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
          body: JSON.stringify({ ids })
        },
        'preparing selected image download'
      );
      const readyJob = await waitForGalleryExportJob(job.job_id, (nextJob) => {
        deps.setOperationStatus({
          kind: 'download',
          label,
          detail: exportJobDetail(nextJob),
          progress: operationProgress(nextJob.progress, 0, 50)
        });
      });
      deps.setOperationStatus({
        kind: 'download',
        label,
        detail: get(t).gallery.browserSavingDownload,
        progress: 50
      });
      const downloadUrl = readyJob.download_url || `/api/gallery/export-jobs/${encodeURIComponent(readyJob.job_id)}/download`;
      if (readyJob.bytes_total >= STREAMING_ZIP_DOWNLOAD_BYTES_THRESHOLD) {
        startNativeDownload(downloadUrl, 'gpt-images-selected.zip');
        const requestedCount = readyJob.requested_count || ids.length;
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
      const requestedCount = readyJob.requested_count || parseHeaderInt(response.headers, 'X-Gallery-Requested-Count') || ids.length;
      const exportedCount = readyJob.exported_count || parseHeaderInt(response.headers, 'X-Gallery-Exported-Count') || requestedCount;
      const missingCount = readyJob.missing_count || parseHeaderInt(response.headers, 'X-Gallery-Missing-Count');
      showToast?.(get(t).messages.selectedImagesDownloaded(exportedCount, missingCount));
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
      let totalBytes = deps.getState().gallery?.total_bytes || 0;
      if (totalBytes <= 0) {
        const stats = await apiFetch<GalleryResponse>(
          '/api/gallery?page=1&page_size=1&include_total_bytes=true',
          {},
          'loading gallery export size'
        );
        totalBytes = stats.total_bytes || 0;
      }
      if (totalBytes >= STREAMING_ZIP_DOWNLOAD_BYTES_THRESHOLD) {
        deps.setOperationStatus({
          kind: 'export',
          label,
          detail: get(t).gallery.browserSavingDownload,
          progress: null
        });
        startNativeDownload('/api/download-all', 'gpt-images.zip');
        showToast?.(get(t).messages.exportReady);
        return;
      }

      const job = await apiFetch<GalleryExportJobStatus>('/api/gallery/export-jobs', { method: 'POST' }, 'preparing gallery export');
      const readyJob = await waitForGalleryExportJob(job.job_id, (nextJob) => {
        deps.setOperationStatus({
          kind: 'export',
          label,
          detail: exportJobDetail(nextJob),
          progress: operationProgress(nextJob.progress, 0, 50)
        });
      });
      deps.setOperationStatus({
        kind: 'export',
        label,
        detail: get(t).gallery.browserSavingDownload,
        progress: 50
      });
      const downloadUrl = readyJob.download_url || `/api/gallery/export-jobs/${encodeURIComponent(readyJob.job_id)}/download`;
      if (readyJob.bytes_total >= STREAMING_ZIP_DOWNLOAD_BYTES_THRESHOLD) {
        startNativeDownload(downloadUrl, 'gpt-images.zip');
        showToast?.(get(t).messages.exportReady);
        return;
      }

      const response = await fetch(downloadUrl, {
        method: 'GET',
        credentials: 'same-origin',
        headers: { Accept: 'application/zip' }
      });
      if (!response.ok) throw new Error(get(t).messages.requestFailed);
      const blob = await downloadResponseBlob(response, 'export', label, get(t).gallery.browserSavingDownload, { start: 50, end: 100 });
      downloadBlob(blob, filenameFromContentDisposition(response.headers.get('Content-Disposition'), 'gpt-images.zip'));
      showToast?.(get(t).messages.exportReady);
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
      const job = await apiFetch<GallerySyncJobStatus>('/api/gallery/sync-jobs', { method: 'POST' }, 'starting R2 gallery sync');
      const finished = await waitForGallerySyncJob(job.job_id, (nextJob) => {
        deps.setOperationStatus({
          kind: 'sync',
          label,
          detail: syncJobDetail(nextJob),
          progress: nextJob.progress
        });
      });
      showToast?.(get(t).messages.r2SyncComplete(finished.uploaded_count, finished.skipped_existing_count, finished.missing_local_count));
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
      const result = await apiFetch<{ status: string; imported: number }>(
        '/api/import',
        {
          method: 'POST',
          body: formData
        },
        'importing archive'
      );
      deps.setOperationStatus({
        kind: 'import',
        label: get(t).gallery.importingArchive,
        detail: get(t).gallery.refreshingAfterImport,
        progress: null
      });
      await deps.loadGallery(1);
      showToast(get(t).messages.imported(result.imported));
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
