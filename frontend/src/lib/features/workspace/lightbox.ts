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
