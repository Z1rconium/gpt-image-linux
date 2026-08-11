import { writable } from 'svelte/store';
import { apiFetch } from '$lib/api/client';
import type { AssistantGalleryImageResponse, AssistantGalleryMetadataResponse } from '$lib/api/types/assistant';
import type { GalleryEntry, GalleryResponse } from '$lib/api/types/gallery';
import { imageUrl } from '$lib/utils/format';
import { canPrefetchLargeMedia } from '$lib/utils/network';

type PrefetchPage = (page: number) => Promise<GalleryResponse | null>;

export function createLightboxPrefetch(prefetchPage: PrefetchPage) {
  const prefetchedImageUrls = new Set<string>();
  let pendingPrefetch: ReturnType<typeof setTimeout> | number | null = null;

  function prefetchImage(image: GalleryEntry | null | undefined) {
    if (!image || typeof window === 'undefined' || !canPrefetchLargeMedia()) return;
    const url = imageUrl(image.filename, image.image_url);
    if (prefetchedImageUrls.has(url)) return;
    prefetchedImageUrls.add(url);
    if (prefetchedImageUrls.size > 24) {
      const oldestUrl = prefetchedImageUrls.values().next().value;
      if (oldestUrl) prefetchedImageUrls.delete(oldestUrl);
    }
    const img = new Image();
    img.decoding = 'async';
    img.fetchPriority = 'low';
    img.src = url;
  }

  function clear() {
    if (pendingPrefetch === null || typeof window === 'undefined') return;
    if (typeof pendingPrefetch === 'number' && 'cancelIdleCallback' in window) {
      window.cancelIdleCallback(pendingPrefetch);
    } else {
      window.clearTimeout(pendingPrefetch as ReturnType<typeof setTimeout>);
    }
    pendingPrefetch = null;
  }

  function prefetchNeighbors(image: GalleryEntry | null, gallery: GalleryResponse | null) {
    if (!image || !gallery || typeof window === 'undefined' || !canPrefetchLargeMedia()) return;
    const currentIndex = gallery.images.findIndex((candidate) => candidate.id === image.id);
    if (currentIndex < 0) return;

    prefetchImage(gallery.images[currentIndex + 1]);
    if (currentIndex < gallery.images.length - 2 || !gallery.has_next) return;

    clear();
    const runPrefetch = () => {
      pendingPrefetch = null;
      void prefetchPage(gallery.page + 1).then((nextGallery) => {
        prefetchImage(nextGallery?.images[0]);
      });
    };
    pendingPrefetch =
      typeof window.requestIdleCallback === 'function'
        ? window.requestIdleCallback(runPrefetch, { timeout: 1500 })
        : window.setTimeout(runPrefetch, 250);
  }

  return { clear, prefetchNeighbors };
}

type LightboxControllerState = {
  navigating: boolean;
  canNavigatePrevious: boolean;
  canNavigateNext: boolean;
  aiMetadata: AssistantGalleryMetadataResponse | null;
};

type LightboxControllerOptions = {
  getImage: () => GalleryEntry | null;
  getGallery: () => GalleryResponse | null;
  isAiAvailable: () => boolean;
  setImage: (image: GalleryEntry | null) => void;
  loadGalleryPage: (page: number, direction: 'next' | 'prev') => Promise<unknown>;
  prefetchPage: PrefetchPage;
  loadAiMetadata: (imageId: string) => Promise<AssistantGalleryMetadataResponse>;
  describeImage: (imageId: string, signal: AbortSignal) => Promise<AssistantGalleryImageResponse>;
  analyzeImage: (imageId: string, signal: AbortSignal) => Promise<AssistantGalleryImageResponse>;
  isAbortError: (error: unknown) => boolean;
  onNavigate: () => void;
  onImageNotFound: () => void;
  onAnalyzed: () => void;
  onError: (error: unknown) => void;
};

const initialControllerState: LightboxControllerState = {
  navigating: false,
  canNavigatePrevious: false,
  canNavigateNext: false,
  aiMetadata: null
};

export function createLightboxController(options: LightboxControllerOptions) {
  const { subscribe, update, set } = writable<LightboxControllerState>(initialControllerState);
  let state = initialControllerState;
  let imageLookupSeq = 0;
  let aiMetadataSeq = 0;
  let aiController: AbortController | null = null;
  let lastMetadataKey = '';
  const prefetch = createLightboxPrefetch(options.prefetchPage);

  subscribe((value) => {
    state = value;
  });

  function updateNavigation(image: GalleryEntry | null, gallery: GalleryResponse | null) {
    const imageIndex = image && gallery ? gallery.images.findIndex((candidate) => candidate.id === image.id) : -1;
    const canNavigatePrevious = Boolean(image && gallery && imageIndex >= 0 && (imageIndex > 0 || gallery.has_prev));
    const canNavigateNext = Boolean(
      image && gallery && imageIndex >= 0 && (imageIndex < gallery.images.length - 1 || gallery.has_next)
    );
    if (
      state.canNavigatePrevious !== canNavigatePrevious ||
      state.canNavigateNext !== canNavigateNext
    ) {
      update((current) => ({ ...current, canNavigatePrevious, canNavigateNext }));
    }
  }

  function cancelAiRequest() {
    aiController?.abort();
    aiController = null;
  }

  function nextAiSignal() {
    cancelAiRequest();
    aiController = new AbortController();
    return aiController.signal;
  }

  function isCurrentRequest(seq: number, imageId: string) {
    return seq === aiMetadataSeq && options.getImage()?.id === imageId;
  }

  async function loadAiMetadata(imageId: string) {
    const seq = ++aiMetadataSeq;
    cancelAiRequest();
    if (!options.isAiAvailable()) {
      if (isCurrentRequest(seq, imageId)) update((current) => ({ ...current, aiMetadata: null }));
      return;
    }
    try {
      const aiMetadata = await options.loadAiMetadata(imageId);
      if (isCurrentRequest(seq, imageId)) update((current) => ({ ...current, aiMetadata }));
    } catch {
      if (isCurrentRequest(seq, imageId)) update((current) => ({ ...current, aiMetadata: null }));
    }
  }

  function selectImage(image: GalleryEntry, notifyNavigation = false) {
    options.setImage(image);
    sync(image, options.getGallery(), options.isAiAvailable());
    if (notifyNavigation) options.onNavigate();
  }

  function open(image: GalleryEntry) {
    imageLookupSeq += 1;
    selectImage(image);
  }

  async function openFromId(imageId: string | null | undefined, currentImages: GalleryEntry[]) {
    const nextImageId = String(imageId || '').trim();
    if (!nextImageId) {
      close();
      return;
    }

    const existing = currentImages.find((image) => image.id === nextImageId);
    if (existing) {
      imageLookupSeq += 1;
      selectImage(existing);
      return;
    }

    const seq = ++imageLookupSeq;
    try {
      const image = await apiFetch<GalleryEntry>(`/api/gallery/${encodeURIComponent(nextImageId)}`, {}, 'loading gallery image');
      if (seq === imageLookupSeq) selectImage(image);
    } catch {
      if (seq !== imageLookupSeq) return;
      close();
      options.onImageNotFound();
    }
  }

  function close() {
    imageLookupSeq += 1;
    aiMetadataSeq += 1;
    lastMetadataKey = '';
    cancelAiRequest();
    prefetch.clear();
    options.setImage(null);
    set(initialControllerState);
  }

  function sync(image: GalleryEntry | null, gallery: GalleryResponse | null, aiAvailable: boolean) {
    updateNavigation(image, gallery);
    if (image && gallery) prefetch.prefetchNeighbors(image, gallery);
    const metadataKey = image ? `${image.id}:${aiAvailable}` : '';
    if (metadataKey === lastMetadataKey) return;
    lastMetadataKey = metadataKey;
    if (image) void loadAiMetadata(image.id);
    else if (state.aiMetadata) update((current) => ({ ...current, aiMetadata: null }));
  }

  async function navigate(direction: -1 | 1) {
    if (state.navigating) return;
    const image = options.getImage();
    const gallery = options.getGallery();
    if (!image || !gallery) return;

    const currentIndex = gallery.images.findIndex((candidate) => candidate.id === image.id);
    if (currentIndex < 0) return;
    const nextIndex = currentIndex + direction;
    if (nextIndex >= 0 && nextIndex < gallery.images.length) {
      selectImage(gallery.images[nextIndex], true);
      return;
    }

    if ((direction < 0 && !gallery.has_prev) || (direction > 0 && !gallery.has_next)) return;
    update((current) => ({ ...current, navigating: true }));
    try {
      await options.loadGalleryPage(gallery.page + direction, direction > 0 ? 'next' : 'prev');
      const nextImages = options.getGallery()?.images || [];
      const nextImage = direction < 0 ? nextImages.at(-1) : nextImages[0];
      if (nextImage) selectImage(nextImage, true);
    } catch (error) {
      options.onError(error);
    } finally {
      update((current) => ({ ...current, navigating: false }));
    }
  }

  async function describe(image: GalleryEntry) {
    const seq = ++aiMetadataSeq;
    const signal = nextAiSignal();
    try {
      const result = await options.describeImage(image.id, signal);
      if (!isCurrentRequest(seq, image.id)) return;
      const currentMetadata = state.aiMetadata?.image_id === image.id ? state.aiMetadata : null;
      update((current) => ({
        ...current,
        aiMetadata: {
          image_id: image.id,
          description: result.description,
          prompt: currentMetadata?.prompt || '',
          analysis: currentMetadata?.analysis || {},
          model: result.model,
          created_at: currentMetadata?.created_at || null,
          updated_at: null
        }
      }));
    } catch (error) {
      if (!options.isAbortError(error) && isCurrentRequest(seq, image.id)) options.onError(error);
    }
  }

  async function analyze(image: GalleryEntry) {
    const seq = ++aiMetadataSeq;
    const signal = nextAiSignal();
    try {
      const result = await options.analyzeImage(image.id, signal);
      if (!isCurrentRequest(seq, image.id)) return;
      const currentMetadata = state.aiMetadata?.image_id === image.id ? state.aiMetadata : null;
      update((current) => ({
        ...current,
        aiMetadata: {
          image_id: image.id,
          description: result.description,
          prompt: result.prompt,
          analysis: result.analysis,
          model: result.model,
          created_at: currentMetadata?.created_at || null,
          updated_at: null
        }
      }));
      options.onAnalyzed();
    } catch (error) {
      if (!options.isAbortError(error) && isCurrentRequest(seq, image.id)) options.onError(error);
    }
  }

  function destroy() {
    imageLookupSeq += 1;
    aiMetadataSeq += 1;
    cancelAiRequest();
    prefetch.clear();
  }

  return { subscribe, open, openFromId, close, sync, navigate, describe, analyze, destroy };
}
