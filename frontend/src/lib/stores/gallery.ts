import { get, writable } from 'svelte/store';
import { apiFetch } from '$lib/api/client';
import { t } from '$lib/i18n';
import { confirmStore } from '$lib/stores/confirm';
import { createGalleryActions } from '$lib/stores/galleryActions';
import type { ToastOptions, ToastVariant } from '$lib/stores/ui';
import type { GalleryEntry, GalleryResponse, GallerySelectionTokenResponse } from '$lib/api/types';

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
  selectionToken: GallerySelectionToken | null;
};

export type GalleryOperationStatus = {
  kind: 'import' | 'export' | 'download' | 'sync';
  label: string;
  detail: string;
  progress: number | null;
};

export type GalleryNavigation = 'next' | 'prev' | 'jump';

export type GallerySelectionToken = {
  token: string;
  count: number;
  expiresAt: string;
  filters: GalleryFilters;
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
  selectedIds: new Set(),
  selectionToken: null
};

const THUMBNAIL_REFRESH_DELAYS_MS = [1200, 2000, 3500, 5000, 8000];

function buildGalleryParams(
  page: number,
  filters: GalleryFilters,
  includeTotalBytes = false,
  cursor?: string | null,
  direction?: 'next' | 'prev',
  includeCounts = true,
  includeFilterOptions = true
) {
  const params = new URLSearchParams({ page: String(page), page_size: '9' });
  if (filters.prompt.trim()) params.set('prompt', filters.prompt.trim());
  if (filters.model) params.set('model', filters.model);
  if (filters.preset) params.set('preset', filters.preset);
  if (filters.size) params.set('size', filters.size);
  if (filters.dateFrom) params.set('date_from', filters.dateFrom);
  if (filters.dateTo) params.set('date_to', filters.dateTo);
  if (filters.favorite) params.set('favorite', 'true');
  if (includeTotalBytes) params.set('include_total_bytes', 'true');
  if (!includeCounts) params.set('include_counts', 'false');
  if (!includeFilterOptions) params.set('include_filter_options', 'false');
  if (cursor && direction) {
    params.set('cursor', cursor);
    params.set('direction', direction);
  }
  return params;
}

function isAbortError(error: unknown) {
  return error instanceof Error && error.name === 'AbortError';
}

function galleryFiltersToSelectionPayload(filters: GalleryFilters) {
  return {
    prompt: filters.prompt.trim(),
    model: filters.model,
    preset: filters.preset,
    size: filters.size,
    date_from: filters.dateFrom,
    date_to: filters.dateTo,
    favorite: filters.favorite ? true : null
  };
}

function sameGalleryFilters(left: GalleryFilters, right: GalleryFilters) {
  return (
    left.prompt === right.prompt &&
    left.model === right.model &&
    left.preset === right.preset &&
    left.size === right.size &&
    left.dateFrom === right.dateFrom &&
    left.dateTo === right.dateTo &&
    left.favorite === right.favorite
  );
}

function createGalleryStore() {
  const { subscribe, update } = writable<GalleryState>(initialGalleryState);
  let state = initialGalleryState;
  let filterTimer: ReturnType<typeof setTimeout> | null = null;
  let requestSeq = 0;
  let abortController: AbortController | null = null;
  let thumbnailRefreshTimer: ReturnType<typeof setTimeout> | null = null;
  let thumbnailRefreshKey = '';
  let thumbnailRefreshAttempts = 0;
  const prefetchedPages = new Map<string, GalleryResponse>();
  const prefetchRequests = new Map<string, Promise<GalleryResponse | null>>();
  const pendingSingleDeletes = new Map<string, { image: GalleryEntry; timer: ReturnType<typeof setTimeout> }>();

  subscribe((value) => {
    state = value;
  });

  function setOperationStatus(operationStatus: GalleryOperationStatus | null) {
    update((current) => ({ ...current, operationStatus }));
  }

  function clearThumbnailRefresh() {
    if (thumbnailRefreshTimer) clearTimeout(thumbnailRefreshTimer);
    thumbnailRefreshTimer = null;
    thumbnailRefreshKey = '';
    thumbnailRefreshAttempts = 0;
  }

  function pendingThumbnailRefreshKey(gallery: GalleryResponse, filters: GalleryFilters) {
    const pendingIds = gallery.images
      .filter((image) => image.thumbnail_status && image.thumbnail_status !== 'ready')
      .map((image) => image.id);
    if (!pendingIds.length) return '';
    return `${gallery.page}:${JSON.stringify(filters)}:${pendingIds.join(',')}`;
  }

  async function refreshPendingThumbnails(expectedKey: string, page: number, filters: GalleryFilters) {
    if (!state.gallery || state.gallery.page !== page || !sameGalleryFilters(filters, state.filters)) return;
    if (expectedKey !== thumbnailRefreshKey) return;

    try {
      const params = buildGalleryParams(page, filters);
      const gallery = await apiFetch<GalleryResponse>(
        `/api/gallery?${params.toString()}`,
        {},
        'refreshing gallery thumbnails'
      );
      if (!state.gallery || state.gallery.page !== page || !sameGalleryFilters(filters, state.filters)) return;
      if (expectedKey !== thumbnailRefreshKey) return;

      const currentTotalBytes = state.gallery.total_bytes;
      const filteredGallery = filterPendingGallery(
        {
          ...gallery,
          total_bytes: currentTotalBytes || gallery.total_bytes
        },
        false,
        filters
      );
      update((current) => ({
        ...current,
        gallery: filteredGallery,
        page: filteredGallery.page
      }));
      scheduleThumbnailRefresh(filteredGallery);
    } catch {
      // A failed background thumbnail refresh should not disturb the visible gallery state.
    }
  }

  function scheduleThumbnailRefresh(gallery: GalleryResponse) {
    const key = pendingThumbnailRefreshKey(gallery, state.filters);
    if (!key) {
      clearThumbnailRefresh();
      return;
    }
    if (key !== thumbnailRefreshKey) {
      clearThumbnailRefresh();
      thumbnailRefreshKey = key;
    }
    if (thumbnailRefreshTimer || thumbnailRefreshAttempts >= THUMBNAIL_REFRESH_DELAYS_MS.length) return;

    const delay = THUMBNAIL_REFRESH_DELAYS_MS[thumbnailRefreshAttempts];
    thumbnailRefreshAttempts += 1;
    const page = gallery.page;
    const filters = { ...state.filters };
    thumbnailRefreshTimer = setTimeout(() => {
      thumbnailRefreshTimer = null;
      void refreshPendingThumbnails(key, page, filters);
    }, delay);
  }

  function setPageAndFilters(page: number, filters: GalleryFilters) {
    prefetchedPages.clear();
    clearThumbnailRefresh();
    update((current) => ({
      ...current,
      page,
      filters: { ...filters },
      selectedIds: new Set(),
      selectionToken: null,
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

  async function loadGallery(page = state.page, includeTotalBytes: boolean | GalleryNavigation = false, navigation: GalleryNavigation = 'jump') {
    if (typeof includeTotalBytes === 'string') {
      navigation = includeTotalBytes;
      includeTotalBytes = false;
    }
    clearThumbnailRefresh();
    const filters = { ...state.filters };
    const cursor =
      navigation === 'next' ? state.gallery?.next_cursor : navigation === 'prev' ? state.gallery?.prev_cursor : null;
    const direction = navigation === 'next' || navigation === 'prev' ? navigation : undefined;
    const lightweightCursorPage = Boolean(cursor && direction && !includeTotalBytes && state.gallery);
    const params = buildGalleryParams(
      page,
      filters,
      includeTotalBytes,
      cursor,
      direction,
      !lightweightCursorPage,
      !lightweightCursorPage
    );
    const requestKey = params.toString();
    const seq = ++requestSeq;
    const cachedGallery = prefetchedPages.get(requestKey);
    if (cachedGallery) {
      prefetchedPages.delete(requestKey);
      const mergedGallery =
        lightweightCursorPage && state.gallery
          ? {
              ...cachedGallery,
              total: state.gallery.total,
              total_bytes: state.gallery.total_bytes,
              total_pages: state.gallery.total_pages,
              filter_options: state.gallery.filter_options
            }
          : cachedGallery;
      const filteredGallery = filterPendingGallery(mergedGallery, includeTotalBytes, filters);
      update((current) => ({
        ...current,
        gallery: filteredGallery,
        page: filteredGallery.page
      }));
      scheduleThumbnailRefresh(filteredGallery);
      return;
    }
    const pendingPrefetch = prefetchRequests.get(requestKey);
    if (pendingPrefetch) {
      update((current) => ({ ...current, loading: true, page }));
      try {
        const gallery = await pendingPrefetch;
        if (seq !== requestSeq) return;
        if (gallery) {
          const mergedGallery =
            lightweightCursorPage && state.gallery
              ? {
                  ...gallery,
                  total: state.gallery.total,
                  total_bytes: state.gallery.total_bytes,
                  total_pages: state.gallery.total_pages,
                  filter_options: state.gallery.filter_options
                }
              : gallery;
          const filteredGallery = filterPendingGallery(mergedGallery, includeTotalBytes, filters);
          update((current) => ({
            ...current,
            gallery: filteredGallery,
            page: filteredGallery.page
          }));
          scheduleThumbnailRefresh(filteredGallery);
          return;
        }
      } finally {
        if (seq === requestSeq) update((current) => ({ ...current, loading: false }));
      }
    }
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
      const mergedGallery =
        lightweightCursorPage && state.gallery
          ? {
              ...gallery,
              total: state.gallery.total,
              total_bytes: state.gallery.total_bytes,
              total_pages: state.gallery.total_pages,
              filter_options: state.gallery.filter_options
            }
          : gallery;
      const filteredGallery = filterPendingGallery(mergedGallery, includeTotalBytes, filters);
      update((current) => ({
        ...current,
        gallery: filteredGallery,
        page: filteredGallery.page
      }));
      scheduleThumbnailRefresh(filteredGallery);
    } catch (error) {
      if (seq !== requestSeq) return;
      if (isAbortError(error)) return;
      throw error;
    } finally {
      if (seq === requestSeq) {
        abortController = null;
        update((current) => ({ ...current, loading: false }));
      }
    }
  }

  async function prefetchGalleryPage(page: number, navigation: GalleryNavigation = 'jump') {
    if (!state.gallery) return null;
    const filters = { ...state.filters };
    const cursor =
      navigation === 'next' ? state.gallery.next_cursor : navigation === 'prev' ? state.gallery.prev_cursor : null;
    const direction = navigation === 'next' || navigation === 'prev' ? navigation : undefined;
    if ((navigation === 'next' || navigation === 'prev') && !cursor) return null;

    const params = buildGalleryParams(page, filters, false, cursor, direction, !direction, !direction);
    const requestKey = params.toString();
    const cached = prefetchedPages.get(requestKey);
    if (cached) return cached;
    const pending = prefetchRequests.get(requestKey);
    if (pending) return pending;

    const request = apiFetch<GalleryResponse>(`/api/gallery?${requestKey}`, {}, 'prefetching gallery').then(
      (gallery) => {
        prefetchRequests.delete(requestKey);
        prefetchedPages.set(requestKey, gallery);
        while (prefetchedPages.size > 4) {
          const oldestKey = prefetchedPages.keys().next().value;
          if (!oldestKey) break;
          prefetchedPages.delete(oldestKey);
        }
        return gallery;
      },
      () => {
        prefetchRequests.delete(requestKey);
        return null;
      }
    );
    prefetchRequests.set(requestKey, request);
    return request;
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
    prefetchedPages.clear();
    clearThumbnailRefresh();
    update((current) => ({
      ...current,
      page: 1,
      filters: {
        ...current.filters,
        [key]: key === 'favorite' ? Boolean(value) : String(value || '')
      },
      selectedIds: new Set(),
      selectionToken: null,
      selectionMode: false
    }));
    if (filterTimer) clearTimeout(filterTimer);
    filterTimer = setTimeout(() => {
      void loadGallery(1);
    }, key === 'prompt' ? 250 : 0);
  }

  function resetFilters() {
    prefetchedPages.clear();
    clearThumbnailRefresh();
    update((current) => ({
      ...current,
      page: 1,
      filters: { ...defaultGalleryFilters },
      selectedIds: new Set(),
      selectionToken: null,
      selectionMode: false
    }));
    void loadGallery(1);
  }

  function setSelectionMode(selectionMode: boolean) {
    update((current) => ({
      ...current,
      selectionMode,
      selectedIds: selectionMode ? current.selectedIds : new Set(),
      selectionToken: selectionMode ? current.selectionToken : null
    }));
  }

  function toggleSelection(image: GalleryEntry) {
    const selectedIds = state.selectionToken
      ? new Set(state.gallery?.images.map((entry) => entry.id) || [])
      : new Set(state.selectedIds);
    if (selectedIds.has(image.id)) selectedIds.delete(image.id);
    else selectedIds.add(image.id);
    update((current) => ({ ...current, selectedIds, selectionToken: null }));
  }

  function selectPage() {
    update((current) => {
      const selectedIds = new Set(current.selectedIds);
      current.gallery?.images.forEach((image) => selectedIds.add(image.id));
      return { ...current, selectedIds, selectionToken: null, selectionMode: true };
    });
  }

  async function selectFiltered() {
    const filters = { ...state.filters };
    const response = await apiFetch<GallerySelectionTokenResponse>(
      '/api/gallery/batch/selection-tokens',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filters: galleryFiltersToSelectionPayload(filters) })
      },
      'creating gallery selection token'
    );
    if (!sameGalleryFilters(filters, state.filters)) return;
    update((current) => ({
      ...current,
      selectionMode: true,
      selectedIds: new Set(),
      selectionToken: {
        token: response.selection_token,
        count: response.count,
        expiresAt: response.expires_at,
        filters
      }
    }));
  }

  function clearSelection() {
    update((current) => ({ ...current, selectedIds: new Set(), selectionToken: null }));
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
    clearThumbnailRefresh();
    clearPendingSingleDeletes();
    requestSeq += 1;
    abortController?.abort();
    abortController = null;
    prefetchedPages.clear();
    prefetchRequests.clear();
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
    prefetchGalleryPage,
    setPageAndFilters,
    updateFilter,
    resetFilters,
    setSelectionMode,
    toggleSelection,
    selectPage,
    selectFiltered,
    clearSelection,
    ...galleryActions,
    toggleFavorite,
    deleteImage,
    cancelPendingSingleDelete,
    cleanup
  };
}

export const galleryStore = createGalleryStore();
