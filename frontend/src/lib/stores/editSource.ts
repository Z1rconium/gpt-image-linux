import { get, writable } from 'svelte/store';
import { t } from '$lib/i18n';

export const MAX_EDIT_SOURCE_IMAGES = 16;

export type EditUploadSource = {
  id: string;
  file: File;
  label: string;
  previewUrl: string;
  previewLabel: string;
};

export type EditSourceState = {
  files: EditUploadSource[];
  selectedGalleryImageId: string;
  galleryLabel: string;
  galleryPreviewUrl: string;
  galleryPreviewLabel: string;
};

const initialEditSourceState: EditSourceState = {
  files: [],
  selectedGalleryImageId: '',
  galleryLabel: '',
  galleryPreviewUrl: '',
  galleryPreviewLabel: ''
};

let nextEditSourceId = 0;

function isImageFile(file: File) {
  if (file.type.startsWith('image/') && file.type !== 'image/svg+xml') return true;
  return /\.(avif|bmp|gif|heic|heif|ico|jpe?g|png|tiff?|webp)$/i.test(file.name);
}

function revokeEditSourceUrls(source: EditSourceState) {
  source.files.forEach((upload) => URL.revokeObjectURL(upload.previewUrl));
}

function makeUploadSource(file: File): EditUploadSource {
  const objectUrl = URL.createObjectURL(file);
  nextEditSourceId += 1;
  return {
    id: `upload-${Date.now()}-${nextEditSourceId}`,
    file,
    label: file.name,
    previewUrl: objectUrl,
    previewLabel: file.name
  };
}

export function editSourceCount(source: EditSourceState) {
  return source.files.length + (source.selectedGalleryImageId ? 1 : 0);
}

function createEditSourceStore() {
  const { subscribe, set, update } = writable<EditSourceState>(initialEditSourceState);
  let state = initialEditSourceState;

  subscribe((value) => {
    state = value;
  });

  function addFiles(files: File[], setError: (message: string) => void) {
    const validFiles = files.filter(isImageFile);
    const invalidCount = files.length - validFiles.length;
    if (!validFiles.length) {
      if (files.length) setError(get(t).messages.imageUploadRequired);
      return 0;
    }

    const current = state;
    const availableSlots = MAX_EDIT_SOURCE_IMAGES - editSourceCount(current);
    if (availableSlots <= 0) {
      setError(get(t).messages.editSourceLimit(MAX_EDIT_SOURCE_IMAGES));
      return 0;
    }

    const acceptedFiles = validFiles.slice(0, availableSlots);
    const overLimitCount = validFiles.length - acceptedFiles.length;
    if (invalidCount > 0) setError(get(t).messages.imageUploadRequired);
    if (overLimitCount > 0) setError(get(t).messages.editSourceSomeSkipped(MAX_EDIT_SOURCE_IMAGES));

    const nextUploads = acceptedFiles.map(makeUploadSource);
    update((source) => ({
      ...source,
      files: [...source.files, ...nextUploads]
    }));
    return nextUploads.length;
  }

  return {
    subscribe,
    set,
    update,
    addFiles,
    clear(input?: HTMLInputElement) {
      revokeEditSourceUrls(state);
      set({ ...initialEditSourceState, files: [] });
      if (input) input.value = '';
    },
    handleFile(event: Event, setError: (message: string) => void, input?: HTMLInputElement) {
      const target = event.currentTarget as HTMLInputElement;
      const selectedFiles = Array.from(target.files || []);
      addFiles(selectedFiles, setError);
      if (input) input.value = '';
      else target.value = '';
    },
    setGallerySource(
      imageId: string,
      label: string,
      previewUrl: string,
      previewLabel: string,
      setError?: (message: string) => void
    ) {
      const current = state;
      if (!current.selectedGalleryImageId && editSourceCount(current) >= MAX_EDIT_SOURCE_IMAGES) {
        setError?.(get(t).messages.editSourceLimit(MAX_EDIT_SOURCE_IMAGES));
        return false;
      }

      update((source) => ({
        ...source,
        selectedGalleryImageId: imageId,
        galleryLabel: label,
        galleryPreviewUrl: previewUrl,
        galleryPreviewLabel: previewLabel
      }));
      return true;
    },
    clearGallerySource(imageId?: string) {
      update((source) => {
        if (imageId && source.selectedGalleryImageId !== imageId) return source;
        return {
          ...source,
          selectedGalleryImageId: '',
          galleryLabel: '',
          galleryPreviewUrl: '',
          galleryPreviewLabel: ''
        };
      });
    },
    remove(sourceId: string) {
      const upload = state.files.find((source) => source.id === sourceId);
      if (upload) {
        URL.revokeObjectURL(upload.previewUrl);
        update((source) => ({
          ...source,
          files: source.files.filter((item) => item.id !== sourceId)
        }));
        return true;
      }
      if (state.selectedGalleryImageId === sourceId) {
        update((source) => ({
          ...source,
          selectedGalleryImageId: '',
          galleryLabel: '',
          galleryPreviewUrl: '',
          galleryPreviewLabel: ''
        }));
        return true;
      }
      return false;
    },
    cleanup() {
      revokeEditSourceUrls(state);
    }
  };
}

export const editSourceStore = createEditSourceStore();
