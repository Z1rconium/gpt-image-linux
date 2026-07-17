import { createLazyComponent } from '$lib/utils/lazyComponent';

export type LazyPanel =
  | 'settings'
  | 'jobs'
  | 'snippets'
  | 'imagePrompt'
  | 'lightbox'
  | 'size'
  | 'editPreview'
  | 'optimizer';

export const lazyPanels = {
  settings: createLazyComponent(
    () => import('$lib/components/SettingsDrawer.svelte'),
    () => import('$lib/components/SettingsDrawer.svelte?lazy-retry')
  ),
  jobs: createLazyComponent(
    () => import('$lib/components/JobHistoryDrawer.svelte'),
    () => import('$lib/components/JobHistoryDrawer.svelte?lazy-retry')
  ),
  snippets: createLazyComponent(
    () => import('$lib/components/PromptSnippetsDrawer.svelte'),
    () => import('$lib/components/PromptSnippetsDrawer.svelte?lazy-retry')
  ),
  imagePrompt: createLazyComponent(
    () => import('$lib/components/ImagePromptDialog.svelte'),
    () => import('$lib/components/ImagePromptDialog.svelte?lazy-retry')
  ),
  lightbox: createLazyComponent(
    () => import('$lib/components/Lightbox.svelte'),
    () => import('$lib/components/Lightbox.svelte?lazy-retry')
  ),
  size: createLazyComponent(
    () => import('$lib/components/SizeDialog.svelte'),
    () => import('$lib/components/SizeDialog.svelte?lazy-retry')
  ),
  editPreview: createLazyComponent(
    () => import('$lib/components/EditPreviewModal.svelte'),
    () => import('$lib/components/EditPreviewModal.svelte?lazy-retry')
  ),
  optimizer: createLazyComponent(
    () => import('$lib/components/PromptOptimizerAssistant.svelte'),
    () => import('$lib/components/PromptOptimizerAssistant.svelte?lazy-retry')
  )
} satisfies Record<LazyPanel, ReturnType<typeof createLazyComponent>>;

export const settingsPanel = lazyPanels.settings;
export const jobsPanel = lazyPanels.jobs;
export const snippetsPanel = lazyPanels.snippets;
export const imagePromptPanel = lazyPanels.imagePrompt;
export const lightboxPanel = lazyPanels.lightbox;
export const sizePanel = lazyPanels.size;
export const editPreviewPanel = lazyPanels.editPreview;
export const optimizerPanel = lazyPanels.optimizer;
