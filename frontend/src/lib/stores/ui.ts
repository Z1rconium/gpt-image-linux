import { writable } from 'svelte/store';

export type ToastVariant = 'status' | 'error';

export type ToastMessage = {
  message: string;
  variant: ToastVariant;
  actionLabel?: string;
  onAction?: () => void;
};

export type ToastOptions = {
  actionLabel?: string;
  onAction?: () => void;
  durationMs?: number;
};

export type UiState = {
  settingsOpen: boolean;
  jobsOpen: boolean;
  promptSnippetsOpen: boolean;
  imagePromptOpen: boolean;
  sizeDialogOpen: boolean;
  editPreviewOpen: boolean;
};

const initialUiState: UiState = {
  settingsOpen: false,
  jobsOpen: false,
  promptSnippetsOpen: false,
  imagePromptOpen: false,
  sizeDialogOpen: false,
  editPreviewOpen: false
};

function createToastStore() {
  const { subscribe, set } = writable<ToastMessage | null>(null);
  let toastTimer: ReturnType<typeof setTimeout> | null = null;

  function showToast(message: string, variant: ToastVariant = 'status', options: ToastOptions = {}) {
    set({
      message,
      variant,
      actionLabel: options.actionLabel,
      onAction: options.onAction
    });
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      set(null);
    }, options.durationMs ?? 2500);
  }

  function cleanup() {
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = null;
    set(null);
  }

  return {
    subscribe,
    showToast,
    cleanup
  };
}

export const toastStore = createToastStore();

function createUiStore() {
  const { subscribe, update } = writable<UiState>(initialUiState);

  function setKey<K extends keyof UiState>(key: K, value: UiState[K]) {
    update((state) => ({ ...state, [key]: value }));
  }

  function showToast(message: string, variant: ToastVariant = 'status', options: ToastOptions = {}) {
    toastStore.showToast(message, variant, options);
  }

  function cleanup() {
    toastStore.cleanup();
  }

  return {
    subscribe,
    setKey,
    showToast,
    cleanup
  };
}

export const uiStore = createUiStore();
