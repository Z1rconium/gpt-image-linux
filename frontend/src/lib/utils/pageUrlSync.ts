import type { GalleryFilters } from '$lib/stores/gallery';
import { readGalleryUrlState, writeGalleryUrlState } from '$lib/stores/galleryUrlState';

export type JobsTab = 'running' | 'history';
export type HistoryMode = 'replace' | 'push';

export function readPageUrl(url: URL) {
  const jobsValue = url.searchParams.get('jobs');
  return {
    gallery: readGalleryUrlState(url.searchParams),
    jobsTab: jobsValue === 'history' || jobsValue === 'running' ? jobsValue : null,
    imageId: url.searchParams.get('image')
  } as const;
}

export function writePageUrl(
  state: { page: number; filters: GalleryFilters; imageId: string | null; jobsTab: JobsTab | null },
  mode: HistoryMode
) {
  if (typeof window === 'undefined') return;
  const url = new URL(window.location.href);
  writeGalleryUrlState(url.searchParams, state.page, state.filters);
  if (state.imageId) url.searchParams.set('image', state.imageId);
  else url.searchParams.delete('image');
  if (state.jobsTab) url.searchParams.set('jobs', state.jobsTab);
  else url.searchParams.delete('jobs');

  const nextUrl = `${url.pathname}${url.search}${url.hash}`;
  const currentUrl = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  if (nextUrl !== currentUrl) window.history[mode === 'push' ? 'pushState' : 'replaceState']({}, '', nextUrl);
}

export function createUrlSyncScheduler(sync: (mode: HistoryMode) => void) {
  let ready = false;
  let applying = false;
  let queued = false;
  let queuedMode: HistoryMode = 'replace';
  let timer: ReturnType<typeof setTimeout> | null = null;

  function schedule(mode: HistoryMode = 'replace', debounceMs = 0) {
    if (timer) clearTimeout(timer);
    timer = null;
    if (debounceMs > 0 && mode !== 'push') {
      timer = setTimeout(() => {
        timer = null;
        schedule(mode);
      }, debounceMs);
      return;
    }
    if (mode === 'push') queuedMode = 'push';
    if (queued) return;
    queued = true;
    queueMicrotask(() => {
      queued = false;
      const nextMode = queuedMode;
      queuedMode = 'replace';
      if (ready && !applying) sync(nextMode);
    });
  }

  return {
    schedule,
    setReady(value = true) {
      ready = value;
    },
    setApplying(value: boolean) {
      applying = value;
    },
    flush(mode: HistoryMode = 'replace') {
      if (ready && !applying) sync(mode);
    },
    destroy() {
      if (timer) clearTimeout(timer);
      timer = null;
    }
  };
}
