import { get, writable } from 'svelte/store';
import { apiFetch } from '$lib/api/client';
import { t } from '$lib/i18n';
import { confirmStore } from '$lib/stores/confirm';
import { createGalleryActions } from '$lib/stores/galleryActions';
import type { ToastOptions, ToastVariant } from '$lib/stores/ui';
import { formatBytes } from '$lib/utils/format';
import type { GalleryEntry, GalleryResponse } from '$lib/api/types';

export type GalleryFilters = {
  prompt: string;
  model: string;
  preset: string;
  size: string;
  dateFrom: string;
  dateTo: string;
  favorite: boolean;
};

export type GalleryState = {
  gallery: GalleryResponse | null;
  loading: boolean;
  operationStatus: GalleryOperationStatus | null;
  page: number;
  filters: GalleryFilters;
  selectionMode: boolean;
  selectedIds: Set<string>;
};

export type GalleryOperationStatus = {
  kind: 'import' | 'export' | 'download' | 'sync';
  label: string;
  detail: string;
  progress: number | null;
};

export const defaultGalleryFilters: GalleryFilters = {
  prompt: '',
  model: '',
  preset: '',
  size: '',
  dateFrom: '',
  dateTo: '',
  favorite: false
};

const initialGalleryState: GalleryState = {
  gallery: null,
  loading: false,
  operationStatus: null,
  page: 1,
  filters: { ...defaultGalleryFilters },
  selectionMode: false,
  selectedIds: new Set()
};

function buildGalleryParams(page: number, filters: GalleryFilters, includeTotalBytes = false) {
  const params = new URLSearchParams({ page: String(page), page_size: '9' });
  if (filters.prompt.trim()) params.set('prompt', filters.prompt.trim());
  if (filters.model) params.set('model', filters.model);
  if (filters.preset) params.set('preset', filters.preset);
  if (filters.size) params.set('size', filters.size);
  if (filters.dateFrom) params.set('date_from', filters.dateFrom);
  if (filters.dateTo) params.set('date_to', filters.dateTo);
  if (filters.favorite) params.set('favorite', 'true');
  if (includeTotalBytes) params.set('include_total_bytes', 'true');
  return params;
}

function isAbortError(error: unknown) {
  return error instanceof Error && error.name === 'AbortError';
}

function createGalleryStore() {
  const { subscribe, update } = writable<GalleryState>(initialGalleryState);
  let state = initialGalleryState;
  let filterTimer: ReturnType<typeof setTimeout> | null = null;
  let requestSeq = 0;
  let abortController: AbortController | null = null;
  let pendingRequestKey = '';
  const pendingSingleDeletes = new Map<string, { image: GalleryEntry; timer: ReturnType<typeof setTimeout> }>();

  subscribe((value) => {
    state = value;
  });

  function setOperationStatus(operationStatus: GalleryOperationStatus | null) {
    update((current) => ({ ...current, operationStatus }));
  }

  function setPageAndFilters(page: number, filters: GalleryFilters) {
    update((current) => ({
      ...current,
      page,
      filters: { ...filters },
      selectedIds: new Set(),
      selectionMode: false
    }));
  }

  function pendingImageMatchesFilters(image: GalleryEntry, filters: GalleryFilters) {
    if (filters.prompt.trim() && !image.prompt.toLowerCase().includes(filters.prompt.trim().toLowerCase())) return false;
    if (filters.model && image.model !== filters.model) return false;
    if (filters.preset && image.api_preset_name !== filters.preset) return false;
    if (filters.size && image.size !== filters.size) return false;
    if (filters.favorite && !image.favorite) return false;
    if (filters.dateFrom || filters.dateTo) {
      const timestamp = image.completed_at || image.created_at;
      if (filters.dateFrom && timestamp < `${filters.dateFrom}T00:00:00`) return false;
      if (filters.dateTo && timestamp > `${filters.dateTo}T23:59:59.999999`) return false;
    }
    return true;
  }

  function filterPendingGallery(gallery: GalleryResponse, includeTotalBytes: boolean, filters: GalleryFilters) {
    if (!pendingSingleDeletes.size) return gallery;

    const pendingIds = new Set(pendingSingleDeletes.keys());
    const matchingPending = [...pendingSingleDeletes.values()].filter((pending) => pendingImageMatchesFilters(pending.image, filters));
    const hiddenBytes = matchingPending.reduce((sum, pending) => sum + (pending.image.bytes || 0), 0);

    return {
      ...gallery,
      images: gallery.images.filter((image) => !pendingIds.has(image.id)),
      total: Math.max(0, gallery.total - matchingPending.length),
      total_bytes: includeTotalBytes ? Math.max(0, gallery.total_bytes - hiddenBytes) : gallery.total_bytes
    };
  }

  async function loadGallery(page = state.page, includeTotalBytes = false) {
    const filters = { ...state.filters };
    const params = buildGalleryParams(page, filters, includeTotalBytes);
    const requestKey = params.toString();
    const seq = ++requestSeq;
    pendingRequestKey = requestKey;
    abortController?.abort();
    abortController = new AbortController();
    update((current) => ({ ...current, loading: true, page }));
    try {
      const gallery = await apiFetch<GalleryResponse>(
        `/api/gallery?${requestKey}`,
        { signal: abortController.signal },
        'loading gallery'
      );
      if (seq !== requestSeq) return;
      const filteredGallery = filterPendingGallery(gallery, includeTotalBytes, filters);
      const visibleIds = new Set(filteredGallery.images.map((image) => image.id));
      const selectedIds = new Set([...state.selectedIds].filter((id) => visibleIds.has(id)));
      update((current) => ({
        ...current,
        gallery: filteredGallery,
        page: filteredGallery.page,
        selectedIds,
        selectionMode: selectedIds.size > 0 ? current.selectionMode : false
      }));
    } catch (error) {
      if (seq !== requestSeq) return;
      if (isAbortError(error)) return;
      throw error;
    } finally {
      if (seq === requestSeq) {
        abortController = null;
        pendingRequestKey = '';
        update((current) => ({ ...current, loading: false }));
      }
    }
  }

  function removeGalleryEntryFromCurrentPage(image: GalleryEntry) {
    update((current) => {
      if (!current.gallery) return current;
      if (!current.gallery.images.some((entry) => entry.id === image.id)) {
        return {
          ...current,
          selectedIds: new Set([...current.selectedIds].filter((id) => id !== image.id))
        };
      }
      return {
        ...current,
        gallery: {
          ...current.gallery,
          images: current.gallery.images.filter((entry) => entry.id !== image.id),
          total: Math.max(0, current.gallery.total - 1),
          total_bytes: Math.max(0, current.gallery.total_bytes - (image.bytes || 0))
        },
        selectedIds: new Set([...current.selectedIds].filter((id) => id !== image.id))
      };
    });
  }

  function updateFilter(key: keyof GalleryFilters, value: string | boolean) {
    update((current) => ({
      ...current,
      page: 1,
      filters: {
        ...current.filters,
        [key]: key === 'favorite' ? Boolean(value) : String(value || '')
      }
    }));
    if (filterTimer) clearTimeout(filterTimer);
    filterTimer = setTimeout(() => {
      void loadGallery(1);
    }, key === 'prompt' ? 250 : 0);
  }

  function resetFilters() {
    update((current) => ({ ...current, page: 1, filters: { ...defaultGalleryFilters } }));
    void loadGallery(1);
  }

  function setSelectionMode(selectionMode: boolean) {
    update((current) => ({ ...current, selectionMode, selectedIds: selectionMode ? current.selectedIds : new Set() }));
  }

  function toggleSelection(image: GalleryEntry) {
    const selectedIds = new Set(state.selectedIds);
    if (selectedIds.has(image.id)) selectedIds.delete(image.id);
    else selectedIds.add(image.id);
    update((current) => ({ ...current, selectedIds }));
  }

  function selectPage() {
    update((current) => ({ ...current, selectedIds: new Set(current.gallery?.images.map((image) => image.id) || []) }));
  }

  function clearSelection() {
    update((current) => ({ ...current, selectedIds: new Set() }));
  }

  async function toggleFavorite(image: GalleryEntry, onChanged?: (image: GalleryEntry) => void) {
    await apiFetch<GalleryEntry>(
      `/api/gallery/${encodeURIComponent(image.id)}/favorite`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ favorite: !image.favorite })
      },
      'updating favorite'
    );
    await loadGallery(state.page);
    onChanged?.({ ...image, favorite: !image.favorite });
  }

  function cancelPendingSingleDelete(imageId: string) {
    const pending = pendingSingleDeletes.get(imageId);
    if (!pending) return false;
    clearTimeout(pending.timer);
    pendingSingleDeletes.delete(imageId);
    return true;
  }

  function clearPendingSingleDeletes() {
    pendingSingleDeletes.forEach((pending) => clearTimeout(pending.timer));
    pendingSingleDeletes.clear();
  }

  async function deleteImage(
    image: GalleryEntry,
    showToast: (message: string, variant?: ToastVariant, options?: ToastOptions) => void,
    onPendingHidden?: (image: GalleryEntry) => void,
    onDeleted?: (image: GalleryEntry) => void
  ) {
    const confirmed = await confirmStore.confirm({
      title: get(t).confirm.deleteImageTitle,
      message: get(t).confirm.deleteImageMessage(image.filename),
      details: [get(t).confirm.deleteImageDetail],
      confirmLabel: get(t).common.delete,
      cancelLabel: get(t).confirm.cancel,
      closeLabel: get(t).confirm.closeLabel,
      variant: 'danger'
    });
    if (!confirmed) return;

    cancelPendingSingleDelete(image.id);
    pendingSingleDeletes.set(image.id, {
      image,
      timer: setTimeout(async () => {
        try {
          await apiFetch(`/api/gallery/${encodeURIComponent(image.id)}`, { method: 'DELETE' }, 'deleting image');
          try {
            await loadGallery(state.page);
          } catch {
            // The DELETE already succeeded; keep the optimistic deletion visible if refresh races or fails.
          }
          pendingSingleDeletes.delete(image.id);
          removeGalleryEntryFromCurrentPage(image);
          onDeleted?.(image);
          showToast(get(t).messages.imageDeleted);
        } catch (error) {
          if (isAbortError(error)) return;
          pendingSingleDeletes.delete(image.id);
          await loadGallery(state.page);
          showToast(get(t).messages.imageDeletionFailed, 'error');
        }
      }, 5000)
    });

    removeGalleryEntryFromCurrentPage(image);
    onPendingHidden?.(image);

    showToast(get(t).messages.imageDeletionPending, 'status', {
      actionLabel: get(t).common.undo,
      onAction: async () => {
        if (!cancelPendingSingleDelete(image.id)) return;
        await loadGallery(state.page);
        showToast(get(t).messages.imageDeletionUndone);
      },
      durationMs: 5000
    });
  }

  function cleanup() {
    if (filterTimer) clearTimeout(filterTimer);
    filterTimer = null;
    clearPendingSingleDeletes();
    requestSeq += 1;
    abortController?.abort();
    abortController = null;
    pendingRequestKey = '';
    setOperationStatus(null);
  }

  const galleryActions = createGalleryActions({
    getState: () => state,
    loadGallery,
    clearSelection,
    setOperationStatus,
    clearPendingSingleDeletes
  });

  return {
    subscribe,
    loadGallery,
    setPageAndFilters,
    updateFilter,
    resetFilters,
    setSelectionMode,
    toggleSelection,
    selectPage,
    clearSelection,
    ...galleryActions,
    toggleFavorite,
    deleteImage,
    cancelPendingSingleDelete,
    cleanup
  };
}

export const galleryStore = createGalleryStore();
