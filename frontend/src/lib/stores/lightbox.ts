import { writable } from 'svelte/store';
import type { GalleryEntry } from '$lib/api/types/gallery';

export type LightboxState = {
  image: GalleryEntry | null;
};

function createLightboxStore() {
  const { subscribe, update, set } = writable<LightboxState>({ image: null });

  return {
    subscribe,
    open(image: GalleryEntry) {
      set({ image });
    },
    close() {
      set({ image: null });
    },
    setImage(image: GalleryEntry | null) {
      set({ image });
    },
    updateFavorite(imageId: string, favorite: boolean) {
      update((current) => ({
        image: current.image?.id === imageId ? { ...current.image, favorite } : current.image
      }));
    },
    closeIfId(imageId: string) {
      update((current) => ({ image: current.image?.id === imageId ? null : current.image }));
    },
    closeIfAny(ids: string[]) {
      const selectedIds = new Set(ids);
      update((current) => ({ image: current.image && selectedIds.has(current.image.id) ? null : current.image }));
    }
  };
}

export const lightboxStore = createLightboxStore();
