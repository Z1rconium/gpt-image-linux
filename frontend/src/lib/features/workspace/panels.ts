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
  settings: createLazyComponent(() => import('$lib/components/SettingsDrawer.svelte')),
  jobs: createLazyComponent(() => import('$lib/components/JobHistoryDrawer.svelte')),
  snippets: createLazyComponent(() => import('$lib/components/PromptSnippetsDrawer.svelte')),
  imagePrompt: createLazyComponent(() => import('$lib/components/ImagePromptDialog.svelte')),
  lightbox: createLazyComponent(() => import('$lib/components/Lightbox.svelte')),
  size: createLazyComponent(() => import('$lib/components/SizeDialog.svelte')),
  editPreview: createLazyComponent(() => import('$lib/components/EditPreviewModal.svelte')),
  optimizer: createLazyComponent(() => import('$lib/components/PromptOptimizerAssistant.svelte'))
} satisfies Record<LazyPanel, ReturnType<typeof createLazyComponent>>;

export const settingsPanel = lazyPanels.settings;
export const jobsPanel = lazyPanels.jobs;
export const snippetsPanel = lazyPanels.snippets;
export const imagePromptPanel = lazyPanels.imagePrompt;
export const lightboxPanel = lazyPanels.lightbox;
export const sizePanel = lazyPanels.size;
export const editPreviewPanel = lazyPanels.editPreview;
export const optimizerPanel = lazyPanels.optimizer;
