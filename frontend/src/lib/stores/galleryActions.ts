import type {
  DeferredGalleryActions,
  GalleryActionDeps
} from '$lib/stores/galleryDeferredActions';

export function createGalleryActions(deps: GalleryActionDeps) {
  let deferredActions: Promise<DeferredGalleryActions> | null = null;

  function loadDeferredActions() {
    deferredActions ??= import('$lib/stores/galleryDeferredActions').then(({ createDeferredGalleryActions }) =>
      createDeferredGalleryActions(deps)
    );
    return deferredActions;
  }

  return {
    async batchFavorite(...args: Parameters<DeferredGalleryActions['batchFavorite']>) {
      return (await loadDeferredActions()).batchFavorite(...args);
    },
    async batchDelete(...args: Parameters<DeferredGalleryActions['batchDelete']>) {
      return (await loadDeferredActions()).batchDelete(...args);
    },
    async batchDownload(...args: Parameters<DeferredGalleryActions['batchDownload']>) {
      return (await loadDeferredActions()).batchDownload(...args);
    },
    async exportArchive(...args: Parameters<DeferredGalleryActions['exportArchive']>) {
      return (await loadDeferredActions()).exportArchive(...args);
    },
    async syncGallery(...args: Parameters<DeferredGalleryActions['syncGallery']>) {
      return (await loadDeferredActions()).syncGallery(...args);
    },
    async deleteAll(...args: Parameters<DeferredGalleryActions['deleteAll']>) {
      return (await loadDeferredActions()).deleteAll(...args);
    },
    async importArchive(...args: Parameters<DeferredGalleryActions['importArchive']>) {
      return (await loadDeferredActions()).importArchive(...args);
    }
  };
}

