<script lang="ts">
  import { onMount } from 'svelte';
  import AccessGate from '$lib/components/AccessGate.svelte';
  import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
  import EditGalleryDialog from '$lib/components/EditGalleryDialog.svelte';
  import EditSourcePicker from '$lib/components/EditSourcePicker.svelte';
  import GalleryGrid from '$lib/components/GalleryGrid.svelte';
  import NodeImageResultDialog from '$lib/components/NodeImageResultDialog.svelte';
  import Header from '$lib/components/Header.svelte';
  import PreviewPanel from '$lib/components/PreviewPanel.svelte';
  import AiAssistantPanel from '$lib/components/AiAssistantPanel.svelte';
  import PromptForm from '$lib/components/PromptForm.svelte';
  import ToastHost from '$lib/components/ToastHost.svelte';
  import { apiFetch } from '$lib/api/client';
  import { language, t } from '$lib/i18n';
import type { AssistantJobDiagnoseResponse, AssistantRecommendParamsResponse, PromptOptimizeResponse } from '$lib/api/types/assistant';
import type { ApiPath, ResponseFormatDefault } from '$lib/api/types/common';
import type { GalleryEntry } from '$lib/api/types/gallery';
import type { GenerateJobStatus } from '$lib/api/types/jobs';
import type { AIAssistantSettingsInput, OverallConfigUpdateRequest, SettingsInput, SettingsResponse } from '$lib/api/types/settings';
import type { PromptSnippet, PromptSnippetCreateInput, PromptSnippetUpdateInput } from '$lib/api/types/snippets';
  import { accessStore } from '$lib/stores/access';
  import { assistantStore, isAbortError } from '$lib/stores/assistant';
  import { confirmStore } from '$lib/stores/confirm';
  import { editSourceCount, editSourceStore, MAX_EDIT_SOURCE_IMAGES } from '$lib/stores/editSource';
  import { galleryActivityStore, galleryStore } from '$lib/stores/gallery';
  import { jobsStore } from '$lib/stores/jobs';
  import { lightboxStore } from '$lib/stores/lightbox';
  import { nodeImageResult } from '$lib/stores/nodeImage';
  import { DEFAULT_PROMPT_MODEL, initialPromptFormState, previewStore, type PromptFormState } from '$lib/stores/preview';
  import { promptSnippetsStore } from '$lib/stores/promptSnippets';
  import { settingsActivityStore, settingsStore } from '$lib/stores/settings';
  import { toastStore, uiStore, type ToastOptions } from '$lib/stores/ui';
  import { versionStore } from '$lib/stores/version';
  import { extractImageFilesFromClipboard } from '$lib/utils/clipboard';
  import { copyText, imageUrl } from '$lib/utils/format';
  import { canPrefetchNonCritical } from '$lib/utils/network';
  import { buildPromptOptimizeRequest } from '$lib/utils/promptOptimizer';
  import {
    editPreviewPanel,
    imagePromptPanel,
    jobsPanel,
    lazyPanels,
    lightboxPanel,
    optimizerPanel,
    settingsPanel,
    sizePanel,
    snippetsPanel,
    type LazyPanel
  } from '$lib/features/workspace/panels';
  import { createLightboxController } from '$lib/features/workspace/lightbox';
  import { installWorkspaceLifecycle } from '$lib/features/workspace/lifecycle';
  import { waitForGalleryAnalysis } from '$lib/features/workspace/gallery';
  import {
    activePreset,
    presetApiPath,
    presetDefaultModel,
    presetDefaultResponseFormat
  } from '$lib/features/workspace/prompt';
  import { createUrlSyncScheduler, readPageUrl, writePageUrl, type JobsTab } from '$lib/utils/pageUrlSync';
  import {
    galleryEntryToEditForm,
    galleryEntryToPromptForm,
    galleryEntryToPromptOnly,
    jobToPromptForm,
    normalizeApiPath,
    normalizeResponseFormat,
    normalizeSubmissionQuantity
  } from '$lib/utils/promptForm';

  let jobsTab: JobsTab = 'running';
  let form: PromptFormState = { ...initialPromptFormState };
  let editPicker: EditSourcePicker;
  let galleryEditImage: GalleryEntry | null = null;
  let galleryEditDialogOpen = false;
  let editPreviewUrl = '';
  let editPreviewLabel = '';
  let lastActivePresetId = '';
  let lastActivePresetDefaultModel = DEFAULT_PROMPT_MODEL;
  let lastActivePresetDefaultResponseFormat: ResponseFormatDefault = initialPromptFormState.responseFormat;
  let jobDiagnoses: Record<string, AssistantJobDiagnoseResponse> = {};
  let lastActivePresetApiPath: ApiPath = initialPromptFormState.apiPath;
  let optimizingPrompt = false;
  let loadingPanel: LazyPanel | null = null;
  let lazyLoadSequence = 0;
  let gallerySuccessRefreshTimer: ReturnType<typeof setTimeout> | null = null;
  const handledTerminalJobIds = new Set<string>();
  const panelFocusTargets: Partial<Record<LazyPanel, HTMLElement>> = {};
  $: hasEditSource = editSourceCount($editSourceStore) > 0;
  $: editSources = [
    ...($editSourceStore.selectedGalleryImageId
      ? [
          {
            id: $editSourceStore.selectedGalleryImageId,
            label: $editSourceStore.galleryLabel || $editSourceStore.galleryPreviewLabel,
            previewUrl: $editSourceStore.galleryPreviewUrl,
            kind: 'gallery' as const
          }
        ]
      : []),
    ...$editSourceStore.files.map((source) => ({
      id: source.id,
      label: source.label,
      previewUrl: source.previewUrl,
      kind: 'upload' as const
    }))
  ];
  const urlSync = createUrlSyncScheduler((mode) => {
    writePageUrl(
      {
        page: $galleryStore.page,
        filters: $galleryStore.filters,
        imageId: $lightboxStore.image?.id || null,
        jobsTab: $uiStore.jobsOpen ? jobsTab : null
      },
      mode
    );
  });
  const lightboxController = createLightboxController({
    getImage: () => $lightboxStore.image,
    getGallery: () => $galleryStore.gallery,
    isAiAvailable: () => aiAssistantAvailable,
    setImage: lightboxStore.setImage,
    loadGalleryPage: (page, direction) => galleryStore.loadGallery(page, false, direction),
    prefetchPage: (page) => galleryStore.prefetchGalleryPage(page, 'next'),
    loadAiMetadata: assistantStore.loadGalleryMetadata,
    describeImage: assistantStore.describeGalleryImage,
    analyzeImage: assistantStore.analyzeGalleryImage,
    isAbortError,
    onNavigate: () => urlSync.schedule('replace'),
    onImageNotFound: () => showToast($t.messages.galleryImageNotFound, 'error'),
    onAnalyzed: () => showToast($t.messages.aiAssistantGalleryAnalyzed),
    onError: showError
  });
  $: activeJobsCount = $jobsStore.jobs.length;
  $: optimizerSettings = $settingsStore.settings?.prompt_optimizer || null;
  $: promptOptimizerConfigAvailable = Boolean(
    optimizerSettings?.api_url.trim() &&
      optimizerSettings.model.trim() &&
      optimizerSettings.has_api_key
  );
  $: optimizerAvailable = Boolean(
    optimizerSettings?.enabled && promptOptimizerConfigAvailable
  );
  $: optimizerAssistantEnabled =
    optimizerAvailable &&
    !$uiStore.settingsOpen &&
    !$uiStore.promptSnippetsOpen &&
    !$uiStore.imagePromptOpen &&
    !$uiStore.jobsOpen &&
    !$uiStore.editPreviewOpen &&
    !$uiStore.sizeDialogOpen &&
    !$confirmStore.request &&
    !Boolean($lightboxStore.image);
  $: aiAssistantSettings = $settingsStore.settings?.ai_assistant || null;
  $: aiAssistantAvailable = Boolean(
    aiAssistantSettings?.enabled &&
      promptOptimizerConfigAvailable &&
      (aiAssistantSettings.vision_model.trim() || optimizerSettings?.model.trim())
  );
  $: lightboxController.sync($lightboxStore.image, $galleryStore.gallery, aiAssistantAvailable);
  $: r2BackupSettings = $settingsStore.settings?.r2_backup || null;
  $: r2BackupAvailable = Boolean(
    r2BackupSettings?.enabled &&
      r2BackupSettings.endpoint_url.trim() &&
      r2BackupSettings.bucket_name.trim() &&
      r2BackupSettings.has_access_key_id &&
      r2BackupSettings.has_secret_access_key
  );
  $: nodeImageSettings = $settingsStore.settings?.nodeimage || null;
  $: nodeImageAvailable = Boolean(
    nodeImageSettings?.enabled && nodeImageSettings.api_key_resolvable
  );
  $: syncFormDefaultsToActivePreset($settingsStore.settings);
  $: if (optimizerAssistantEnabled) void ensurePanel('optimizer', false);

  async function loadInitialData() {
    await Promise.all([settingsStore.loadSettings(), jobsStore.loadJobs(), applyUrlStateToApp()]);
    const activeJob = $jobsStore.jobs[0];
    if (activeJob) trackJob(activeJob.job_id);
    urlSync.setReady();
    urlSync.flush();
    jobsStore.startJobsEvents();
  }

  async function loadAuthenticatedData() {
    await Promise.all([versionStore.loadVersion(), loadInitialData()]);
  }

  function showToast(message: string, variant?: 'status' | 'error', options?: ToastOptions) {
    uiStore.showToast(message, variant, options);
  }

  function errorMessage(error: unknown, fallback = $t.messages.requestFailed) {
    const message = error instanceof Error ? error.message : fallback;
    return message || fallback;
  }

  function showError(error: unknown, fallback = $t.messages.requestFailed) {
    showToast(errorMessage(error, fallback), 'error');
  }

  async function ensurePanel(panel: LazyPanel, showLoading = true, onRetry?: () => void) {
    const sequence = ++lazyLoadSequence;
    if (showLoading) loadingPanel = panel;
    try {
      await lazyPanels[panel].load();
      return true;
    } catch {
      const reloadRequired = lazyPanels[panel].retryRequiresReload();
      lazyPanels[panel].reset();
      showToast($t.common.loadFeatureFailed, 'error', {
        actionLabel: $t.common.retry,
        onAction: reloadRequired
          ? () => window.location.reload()
          : onRetry || (() => void ensurePanel(panel))
      });
      return false;
    } finally {
      if (sequence === lazyLoadSequence && loadingPanel === panel) loadingPanel = null;
    }
  }

  function prefetchPanel(panel: LazyPanel) {
    if (!canPrefetchNonCritical()) return;
    void lazyPanels[panel].prefetch();
  }

  function rememberPanelFocus(panel: LazyPanel) {
    if (panelFocusTargets[panel] || typeof document === 'undefined') return;
    const active = document.activeElement;
    if (active instanceof HTMLElement) panelFocusTargets[panel] = active;
  }

  function restorePanelFocus(panel: LazyPanel) {
    const target = panelFocusTargets[panel];
    delete panelFocusTargets[panel];
    queueMicrotask(() => {
      if (target?.isConnected) target.focus();
    });
  }

  async function openUiPanel<K extends keyof typeof $uiStore>(panel: LazyPanel, key: K) {
    rememberPanelFocus(panel);
    if (await ensurePanel(panel, true, () => void openUiPanel(panel, key))) setUi(key, true as (typeof $uiStore)[K]);
  }

  function closeUiPanel<K extends keyof typeof $uiStore>(panel: LazyPanel, key: K) {
    setUi(key, false as (typeof $uiStore)[K]);
    restorePanelFocus(panel);
  }

  function syncAfterGalleryMutation(mode: 'replace' | 'push' = 'replace', debounceMs = 0) {
    urlSync.schedule(mode, debounceMs);
  }

  async function syncLightboxFromUrl(imageId: string | null | undefined) {
    const nextImageId = String(imageId || '').trim();
    if (!nextImageId) {
      lightboxController.close();
      return;
    }

    if (!(await ensurePanel('lightbox'))) return;
    await lightboxController.openFromId(nextImageId, $galleryStore.gallery?.images || []);
  }

  async function openLightbox(image: GalleryEntry) {
    rememberPanelFocus('lightbox');
    if (!(await ensurePanel('lightbox'))) return;
    lightboxController.open(image);
    urlSync.schedule('push');
  }

  function closeLightbox() {
    lightboxController.close();
    urlSync.schedule('replace');
    restorePanelFocus('lightbox');
  }
  function lightboxNavigationBlocked() {
    return Boolean(
      $confirmStore.request ||
        $uiStore.editPreviewOpen ||
        $uiStore.sizeDialogOpen ||
        galleryEditDialogOpen ||
        $uiStore.promptSnippetsOpen ||
        $uiStore.imagePromptOpen ||
        $uiStore.jobsOpen ||
        $uiStore.settingsOpen
    );
  }

  async function openJobsDrawer(tab: JobsTab = jobsTab) {
    rememberPanelFocus('jobs');
    if (!(await ensurePanel('jobs'))) return;
    jobsTab = tab;
    setUi('jobsOpen', true);
    if (tab === 'history' && !$jobsStore.historyLoaded && !$jobsStore.historyLoading) {
      void jobsStore.loadJobHistory();
    } else if (tab === 'history' && $jobsStore.historyNeedsRefresh && !$jobsStore.historyLoading) {
      void jobsStore.refreshHistoryIfLoaded();
    }
    urlSync.schedule();
  }

  function closeJobsDrawer() {
    closeUiPanel('jobs', 'jobsOpen');
    urlSync.schedule();
  }

  async function openPromptSnippetsDrawer() {
    rememberPanelFocus('snippets');
    if (!(await ensurePanel('snippets'))) return;
    setUi('promptSnippetsOpen', true);
    void loadPromptSnippets();
  }

  function closePromptSnippetsDrawer() {
    closeUiPanel('snippets', 'promptSnippetsOpen');
  }

  async function openImagePromptDialog() {
    await openUiPanel('imagePrompt', 'imagePromptOpen');
  }

  function closeImagePromptDialog() {
    closeUiPanel('imagePrompt', 'imagePromptOpen');
  }

  function setJobsTab(tab: JobsTab) {
    jobsTab = tab;
    if (tab === 'history' && !$jobsStore.historyLoaded && !$jobsStore.historyLoading) {
      void jobsStore.loadJobHistory();
    } else if (tab === 'history' && $jobsStore.historyNeedsRefresh && !$jobsStore.historyLoading) {
      void jobsStore.refreshHistoryIfLoaded();
    }
    urlSync.schedule();
  }

  async function applyUrlStateToApp() {
    if (typeof window === 'undefined') return;
    const url = new URL(window.location.href);
    const { gallery: state, jobsTab: nextJobsTab, imageId } = readPageUrl(url);

    urlSync.setApplying(true);
    try {
      galleryStore.setPageAndFilters(state.page, state.filters);
      jobsTab = nextJobsTab || 'running';
      if (nextJobsTab && (await ensurePanel('jobs'))) setUi('jobsOpen', true);
      else setUi('jobsOpen', false);

      if (nextJobsTab === 'history' && !$jobsStore.historyLoaded && !$jobsStore.historyLoading) {
        void jobsStore.loadJobHistory();
      }

      await galleryStore.loadGallery(state.page);
      await syncLightboxFromUrl(imageId);
    } finally {
      urlSync.setApplying(false);
    }
    urlSync.schedule();
  }

  function setUi<K extends keyof typeof $uiStore>(key: K, value: (typeof $uiStore)[K]) {
    uiStore.setKey(key, value);
  }

  function syncFormDefaultsToActivePreset(settings: SettingsResponse | null) {
    const preset = activePreset(settings);
    const nextPresetId = preset?.id || '';
    const nextDefaultModel = presetDefaultModel(settings);
    const nextApiPath = presetApiPath(settings);
    const nextDefaultResponseFormat = presetDefaultResponseFormat(settings);
    if (
      nextPresetId === lastActivePresetId &&
      nextDefaultModel === lastActivePresetDefaultModel &&
      nextApiPath === lastActivePresetApiPath &&
      nextDefaultResponseFormat === lastActivePresetDefaultResponseFormat
    ) {
      return;
    }

    const currentModel = form.model.trim();
    const updates: Partial<PromptFormState> = {};
    if (!currentModel || currentModel === lastActivePresetDefaultModel) {
      updates.model = nextDefaultModel;
    }
    if (!form.apiPath || form.apiPath === lastActivePresetApiPath) {
      updates.apiPath = nextApiPath;
    }
    if (nextPresetId !== lastActivePresetId || nextDefaultResponseFormat !== lastActivePresetDefaultResponseFormat) {
      updates.responseFormat = nextDefaultResponseFormat;
    }
    if (Object.keys(updates).length) {
      form = { ...form, ...updates };
    }
    lastActivePresetId = nextPresetId;
    lastActivePresetDefaultModel = nextDefaultModel;
    lastActivePresetApiPath = nextApiPath;
    lastActivePresetDefaultResponseFormat = nextDefaultResponseFormat;
  }

  function saveSettings(body: SettingsInput) {
    void settingsStore.saveSettings(body, showToast).then((saved) => {
      if (saved) setUi('settingsOpen', false);
    });
  }

  function createPreset() {
    void settingsStore.createPreset(showToast);
  }

  function activatePreset(presetId: string) {
    return settingsStore.activatePreset(presetId, showToast);
  }

  function deletePreset(presetId: string) {
    return settingsStore.deletePreset(presetId, showToast);
  }

  function checkPresetHealth(presetId: string) {
    void settingsStore.checkPresetHealth(presetId);
  }

  function checkR2Health(body: NonNullable<SettingsInput['r2_backup']>) {
    void settingsStore.checkR2Health(body);
  }

  function checkPromptOptimizerHealth() {
    void settingsStore.checkPromptOptimizerHealth();
  }

  function clearPromptOptimizerHealth() {
    settingsStore.clearPromptOptimizerHealth();
  }

  function checkAiAssistantHealth(body: AIAssistantSettingsInput) {
    void settingsStore.checkAiAssistantHealth(body);
  }

  function clearAiAssistantHealth() {
    settingsStore.clearAiAssistantHealth();
  }

  function clearPresetHealth() {
    settingsStore.clearPresetHealth();
  }

  function loadPromptOptimizerSystemPrompt() {
    return settingsStore.loadPromptOptimizerSystemPrompt();
  }

  function savePromptOptimizerSystemPrompt(systemPrompt: string) {
    return settingsStore.savePromptOptimizerSystemPrompt(systemPrompt, showToast);
  }

  function loadOverallConfig() {
    return settingsStore.loadOverallConfig();
  }

  function saveOverallConfig(body: OverallConfigUpdateRequest) {
    return settingsStore.saveOverallConfig(body, showToast);
  }

  function showNewGalleryImagesToast() {
    showToast($t.messages.newGalleryImagesAvailable, 'status', {
      actionLabel: $t.common.refresh,
      durationMs: 8000,
      onAction: () => {
        void galleryStore.loadGallery(1, false, 'jump', { lightweight: true });
        syncAfterGalleryMutation();
      }
    });
  }

  function scheduleGalleryRefreshAfterSuccess() {
    if (gallerySuccessRefreshTimer) clearTimeout(gallerySuccessRefreshTimer);
    gallerySuccessRefreshTimer = setTimeout(() => {
      gallerySuccessRefreshTimer = null;
      if ($galleryStore.page !== 1) {
        showNewGalleryImagesToast();
        return;
      }
      void galleryStore.loadGallery(1, false, 'jump', { lightweight: true });
    }, 350);
  }

  function updatePreviewFromJob(job: GenerateJobStatus) {
    previewStore.setPreview(jobsStore.previewFromJob(job, $previewStore));
    if (job.status !== 'queued' && job.status !== 'running') {
      if (handledTerminalJobIds.has(job.job_id)) return;
      handledTerminalJobIds.add(job.job_id);
      if (handledTerminalJobIds.size > 200) {
        const oldestJobId = handledTerminalJobIds.values().next().value;
        if (oldestJobId) handledTerminalJobIds.delete(oldestJobId);
      }
      if (jobsStore.shouldRefreshJobsAfterSubmit()) void jobsStore.loadJobs();
      jobsStore.markHistoryStale();
      if (job.status === 'success' || job.status === 'partial_failure') scheduleGalleryRefreshAfterSuccess();
    }
  }

  function trackJob(jobId: string) {
    jobsStore.trackJob(jobId, async (job) => updatePreviewFromJob(job), previewStore.setError);
  }

  function normalizeFormQuantityForSubmit() {
    const quantity = normalizeSubmissionQuantity(form.quantity);
    if (form.quantity === '' || Number(form.quantity) !== quantity) {
      form = { ...form, quantity };
    }
  }

  function generateImage() {
    normalizeFormQuantityForSubmit();
    void previewStore.generateImage(
      form,
      jobsStore.makeQueuedPreview,
      trackJob,
      jobsStore.shouldRefreshJobsAfterSubmit() ? jobsStore.loadJobs : undefined
    );
  }

  function editImage() {
    normalizeFormQuantityForSubmit();
    void previewStore.editImage(
      form,
      $editSourceStore,
      jobsStore.makeQueuedPreview,
      trackJob,
      jobsStore.shouldRefreshJobsAfterSubmit() ? jobsStore.loadJobs : undefined
    );
  }

  function submitPrompt() {
    if (hasEditSource) editImage();
    else generateImage();
  }

  async function planEdit() {
    const goal = form.prompt.trim();
    if (!goal) {
      previewStore.setError($t.messages.promptRequired);
      return;
    }
    try {
      const previousForm = { ...form };
      const plan = await assistantStore.planEdit({
        goal,
        source_count: $editSourceStore.files.length + ($editSourceStore.selectedGalleryImageId ? 1 : 0),
        current_prompt: form.prompt,
        target_size: form.size
      });
      if (plan.edit_prompt) {
        form = { ...form, prompt: plan.edit_prompt, size: plan.suggested_size || form.size };
      }
      showToast($t.messages.aiAssistantEditPlanReady, 'status', {
        actionLabel: $t.common.undo,
        onAction: () => {
          form = { ...form, prompt: previousForm.prompt, size: previousForm.size };
        },
        durationMs: 8000
      });
    } catch (error) {
      showError(error);
    }
  }

  function setGalleryFilter(key: Parameters<typeof galleryStore.updateFilter>[0], value: Parameters<typeof galleryStore.updateFilter>[1]) {
    galleryStore.updateFilter(key, value);
    syncAfterGalleryMutation('replace', key === 'prompt' ? 300 : 0);
  }

  function resetGalleryFilters() {
    galleryStore.resetFilters();
    syncAfterGalleryMutation();
  }

  function loadGalleryPage(page: number, direction?: 'next' | 'prev' | 'jump') {
    void galleryStore.loadGallery(page, false, direction);
    syncAfterGalleryMutation();
  }

  function loadGalleryStats() {
    void galleryStore.loadGallery($galleryStore.page, true);
  }

  function promptContainsTag(prompt: string, value: string) {
    const normalized = value.trim().toLowerCase();
    return prompt
      .split(',')
      .map((item) => item.trim().toLowerCase())
      .includes(normalized);
  }

  function appendPromptTag(value: string) {
    const tag = value.trim();
    if (!tag) return;
    if (promptContainsTag(form.prompt, tag)) {
      showToast($t.messages.promptTagExists);
      return;
    }
    const prefix = form.prompt.trim();
    form = { ...form, prompt: prefix ? `${prefix}, ${tag}` : tag };
  }

  async function loadPromptSnippets(query = '') {
    try {
      await promptSnippetsStore.loadSnippets(query);
    } catch (error) {
      showError(error);
    }
  }

  async function createPromptSnippet(input: PromptSnippetCreateInput) {
    try {
      await promptSnippetsStore.createSnippet(input);
      showToast($t.messages.promptSnippetSaved);
    } catch (error) {
      showError(error);
    }
  }

  async function updatePromptSnippet(snippetId: string, input: PromptSnippetUpdateInput) {
    try {
      await promptSnippetsStore.updateSnippet(snippetId, input);
      showToast($t.messages.promptSnippetUpdated);
    } catch (error) {
      showError(error);
    }
  }

  async function deletePromptSnippet(snippet: PromptSnippet) {
    const confirmed = await confirmStore.confirm({
      title: $t.confirm.deleteSnippetTitle,
      message: $t.confirm.deleteSnippetMessage(snippet.title),
      confirmLabel: $t.common.delete,
      cancelLabel: $t.confirm.cancel,
      closeLabel: $t.confirm.closeLabel,
      variant: 'danger'
    });
    if (!confirmed) return;
    try {
      await promptSnippetsStore.deleteSnippet(snippet.id);
      showToast($t.messages.promptSnippetDeleted);
    } catch (error) {
      showError(error);
    }
  }

  function usePromptSnippet(snippet: PromptSnippet) {
    form = { ...form, prompt: snippet.prompt };
    closePromptSnippetsDrawer();
    showToast($t.messages.promptSnippetLoaded);
  }

  async function copyPromptSnippet(snippet: PromptSnippet) {
    await copyText(snippet.prompt);
    showToast($t.messages.promptSnippetCopied);
  }

  function applyImagePrompt(prompt: string) {
    form = { ...form, prompt };
    showToast($t.messages.aiAssistantPromptApplied);
  }

  async function saveImagePrompt(prompt: string) {
    try {
      await promptSnippetsStore.createSnippet({
        title: $t.imagePrompt.snippetTitle,
        prompt,
        favorite: true
      });
      showToast($t.messages.promptSnippetSaved);
    } catch (error) {
      showError(error);
      throw error;
    }
  }

  async function copyImagePrompt(prompt: string) {
    try {
      await copyText(prompt);
      showToast($t.messages.promptSnippetCopied);
    } catch (error) {
      showError(error);
    }
  }

  async function optimizePrompt() {
    const originalPrompt = form.prompt;
    const prompt = originalPrompt.trim();
    if (!prompt || optimizingPrompt) return;
    if (!optimizerAvailable) {
      showToast($t.messages.promptOptimizerUnavailable, 'error');
      return;
    }

    optimizingPrompt = true;
    try {
      const response = await apiFetch<PromptOptimizeResponse>(
        '/api/prompt/optimize',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(
            buildPromptOptimizeRequest({
              prompt,
              targetLanguage: $language,
              apiPath: form.apiPath,
              model: form.model,
              size: form.size,
              quality: form.quality
            })
          )
        },
        'optimizing prompt'
      );
      form = { ...form, prompt: response.optimized_prompt };
      showToast($t.messages.promptOptimized, 'status', {
        actionLabel: $t.common.undo,
        onAction: () => {
          form = { ...form, prompt: originalPrompt };
        },
        durationMs: 6000
      });
    } catch (error) {
      showError(error, $t.messages.promptOptimizeFailed);
    } finally {
      optimizingPrompt = false;
    }
  }

  function applyOptimizedPrompt(prompt: string) {
    form = { ...form, prompt };
    showToast($t.messages.promptOptimized);
  }

  async function applyAssistantPrompt(prompt: string) {
    form = { ...form, prompt };
    showToast($t.messages.aiAssistantPromptApplied);
  }

  async function insertAssistantPrompt(prompt: string) {
    const currentPrompt = form.prompt.trimEnd();
    form = { ...form, prompt: currentPrompt ? `${currentPrompt}\n${prompt}` : prompt };
    showToast($t.messages.aiAssistantPromptInserted);
  }

  async function saveAssistantSnippet(prompt: string) {
    try {
      await promptSnippetsStore.createSnippet({
        title: $t.aiAssistant.snippetTitle,
        prompt,
        favorite: true
      });
      showToast($t.messages.promptSnippetSaved);
    } catch (error) {
      showError(error);
    }
  }

  function applyAssistantParams(recommendation: AssistantRecommendParamsResponse) {
    const updates: Partial<PromptFormState> = {};
    if (recommendation.model_name?.trim()) updates.model = recommendation.model_name.trim();
    if (form.apiPath === '/v1/images/generations') {
      if (recommendation.size?.trim()) updates.size = recommendation.size.trim();
      if (recommendation.quality) updates.quality = recommendation.quality;
      if (recommendation.output_format) updates.outputFormat = recommendation.output_format;
      if (recommendation.n) updates.quantity = recommendation.n;
    }
    if (!Object.keys(updates).length) return;
    form = { ...form, ...updates };
    showToast($t.messages.aiAssistantParamsApplied);
  }

  function regenerate() {
    previewStore.regenerate(
      (next) => (form = { ...next, model: next.model.trim() || lastActivePresetDefaultModel || initialPromptFormState.model }),
      generateImage,
      editImage
    );
  }

  function clearPreview() {
    previewStore.clearPreview(jobsStore.closeActiveJobSource);
  }

  function openGalleryEditDialog(image: GalleryEntry) {
    galleryEditImage = image;
    galleryEditDialogOpen = true;
  }

  function closeGalleryEditDialog() {
    galleryEditDialogOpen = false;
    galleryEditImage = null;
  }

  function focusPromptAfterGalleryEdit() {
    if (typeof window === 'undefined') return;
    window.setTimeout(() => {
      const prompt = document.getElementById('prompt');
      if (!(prompt instanceof HTMLElement)) return;
      prompt.scrollIntoView({ behavior: 'smooth', block: 'center' });
      prompt.focus();
    }, 0);
  }

  function applyGalleryEditChoice(reusePrompt: boolean) {
    const image = galleryEditImage;
    if (!image) return;
    const nextLabel = $t.messages.galleryEditLabel(image.filename);
    if (!editSourceStore.setGallerySource(image.id, nextLabel, imageUrl(image.filename, image.image_url), nextLabel, previewStore.setError)) {
      showToast($t.messages.editSourceLimit(MAX_EDIT_SOURCE_IMAGES), 'error');
      closeGalleryEditDialog();
      return;
    }
    const nextForm = galleryEntryToEditForm(image, lastActivePresetDefaultModel, form.apiPath);
    form = reusePrompt ? nextForm : { ...nextForm, prompt: '' };
    closeGalleryEditDialog();
    closeLightbox();
    focusPromptAfterGalleryEdit();
    showToast($t.messages.galleryImageReady);
  }

  function handleEditFile(event: Event) {
    editSourceStore.handleFile(event, previewStore.setError);
  }

  function handleEditFiles(files: File[]) {
    const addedCount = editSourceStore.addFiles(files, previewStore.setError);
    if (addedCount > 0) showToast($t.messages.editSourceFromDropAdded);
  }

  async function openEditPreview(sourceId: string) {
    rememberPanelFocus('editPreview');
    const upload = $editSourceStore.files.find((source) => source.id === sourceId);
    if (upload) {
      editPreviewUrl = upload.previewUrl;
      editPreviewLabel = upload.previewLabel;
      if (await ensurePanel('editPreview')) setUi('editPreviewOpen', true);
      return;
    }
    if ($editSourceStore.selectedGalleryImageId === sourceId && $editSourceStore.galleryPreviewUrl) {
      editPreviewUrl = $editSourceStore.galleryPreviewUrl;
      editPreviewLabel = $editSourceStore.galleryPreviewLabel || $editSourceStore.galleryLabel;
      if (await ensurePanel('editPreview')) setUi('editPreviewOpen', true);
    }
  }

  function closeEditPreview() {
    setUi('editPreviewOpen', false);
    editPreviewUrl = '';
    editPreviewLabel = '';
    restorePanelFocus('editPreview');
  }

  function clearEditSource() {
    editSourceStore.clear();
    editPicker?.reset();
    closeEditPreview();
  }

  function removeEditSource(sourceId: string) {
    const upload = $editSourceStore.files.find((source) => source.id === sourceId);
    const isGallery = $editSourceStore.selectedGalleryImageId === sourceId;
    const label = upload?.label || (isGallery ? $editSourceStore.galleryLabel || $editSourceStore.galleryPreviewLabel : '');
    const previewUrl = upload?.previewUrl || (isGallery ? $editSourceStore.galleryPreviewUrl : '');
    if (!editSourceStore.remove(sourceId)) return;
    if (previewUrl && editPreviewUrl === previewUrl) closeEditPreview();
    showToast($t.messages.editSourceRemoved(label));
  }

  async function batchFavoriteGallery(favorite: boolean) {
    await galleryStore.batchFavorite(favorite, showToast, (ids, nextFavorite) => {
      ids.forEach((id) => lightboxStore.updateFavorite(id, nextFavorite));
    });
  }

  async function batchDeleteGallery() {
    await galleryStore.batchDelete(showToast, (ids) => {
      if ($lightboxStore.image && ids.includes($lightboxStore.image.id)) closeLightbox();
      if ($editSourceStore.selectedGalleryImageId && ids.includes($editSourceStore.selectedGalleryImageId)) {
        editSourceStore.clearGallerySource($editSourceStore.selectedGalleryImageId);
        closeEditPreview();
      }
    });
  }

  async function batchAnalyzeGallery() {
    const batchBody = galleryStore.selectedBatchRequestBody();
    const selectedCount = $galleryStore.selectionToken?.count || $galleryStore.selectedIds.size;
    if (!selectedCount) return;
    galleryActivityStore.setOperationStatus({
      kind: 'ai_analyze',
      label: $t.gallery.aiAnalyzing,
      detail: $t.gallery.aiAnalyzePreparing(selectedCount),
      progress: 0
    });
    try {
      const job = await assistantStore.batchAnalyzeGallery({ ...batchBody, target_language: $language });
      const currentJob = await waitForGalleryAnalysis(
        job,
        (nextJob) => {
          galleryActivityStore.setOperationStatus({
            kind: 'ai_analyze',
            label: $t.gallery.aiAnalyzing,
            detail: $t.gallery.aiAnalyzeProgress(
              nextJob.analyzed_count,
              nextJob.requested_count,
              nextJob.failed_count,
              nextJob.missing_count
            ),
            progress: nextJob.progress
          });
        }
      );
      galleryActivityStore.setOperationStatus({
        kind: 'ai_analyze',
        label: $t.gallery.aiAnalyzing,
        detail: $t.gallery.aiAnalyzeComplete(currentJob.analyzed_count, currentJob.failed_count, currentJob.missing_count),
        progress: 100
      });
      galleryStore.clearSelection();
      showToast(
        $t.gallery.aiAnalyzeComplete(currentJob.analyzed_count, currentJob.failed_count, currentJob.missing_count),
        currentJob.failed_count || currentJob.missing_count ? 'error' : 'status'
      );
    } catch (error) {
      showError(error);
    } finally {
      galleryActivityStore.setOperationStatus(null);
    }
  }

  function uploadGalleryImageToNodeImage(image: GalleryEntry) {
    void galleryStore.uploadToNodeImage(image, showToast);
  }

  function batchUploadGalleryToNodeImage() {
    void galleryStore.batchUploadToNodeImage(showToast);
  }

  async function toggleFavorite(image: GalleryEntry) {
    await galleryStore.toggleFavorite(image, (next) => {
      if ($lightboxStore.image?.id === image.id) lightboxStore.open(next);
    });
  }

  async function deleteImage(image: GalleryEntry) {
    await galleryStore.deleteImage(
      image,
      showToast,
      () => {
        if ($lightboxStore.image?.id === image.id) closeLightbox();
      },
      () => {
        if ($editSourceStore.selectedGalleryImageId === image.id) {
          editSourceStore.clearGallerySource(image.id);
          closeEditPreview();
        }
      }
    );
  }

  async function deleteAllImages() {
    await galleryStore.deleteAll(showToast, () => {
      closeLightbox();
      editSourceStore.clearGallerySource($editSourceStore.selectedGalleryImageId);
      closeEditPreview();
      clearPreview();
    });
  }

  async function importArchive(file: File) {
    await galleryStore.importArchive(file, showToast);
  }

  async function exportArchive() {
    await galleryStore.exportArchive(showToast);
  }

  async function syncGallery() {
    if (!r2BackupAvailable) {
      showToast($t.messages.r2BackupUnavailable, 'error');
      return;
    }
    try {
      await galleryStore.syncGallery(showToast);
    } catch (error) {
      showError(error);
    }
  }

  async function copyPrompt(image: GalleryEntry) {
    await copyText(image.prompt);
    showToast($t.messages.promptCopied);
  }

  async function copyImageUrl(image: GalleryEntry) {
    await copyText(new URL(imageUrl(image.filename, image.image_url), window.location.origin).href);
    showToast($t.messages.imageUrlCopied);
  }

  function copyPromptBestEffort(prompt: string) {
    if (!prompt) return;
    void copyText(prompt).catch(() => {});
  }

  function useGalleryPrompt(image: GalleryEntry) {
    form = galleryEntryToPromptOnly(image, form);
    copyPromptBestEffort(image.prompt);
    closeLightbox();
    showToast($t.messages.galleryPromptLoaded);
  }

  function useGalleryParams(image: GalleryEntry) {
    const ignoredEditPath = image.api_path === '/v1/images/edits';
    form = galleryEntryToPromptForm(image, lastActivePresetDefaultModel, form.apiPath);
    copyPromptBestEffort(image.prompt);
    closeLightbox();
    showToast(ignoredEditPath ? $t.messages.galleryEditApiPathIgnored : $t.messages.galleryParamsLoaded);
  }

  function useJobAsPrompt(job: GenerateJobStatus) {
    form = jobToPromptForm(job, lastActivePresetDefaultModel);
    closeJobsDrawer();
    showToast($t.messages.jobLoadedIntoPrompt);
  }

  async function clearJobHistory() {
    const confirmed = await confirmStore.confirm({
      title: $t.confirm.clearJobHistoryTitle,
      message: $t.confirm.clearJobHistoryMessage,
      details: [$t.confirm.clearJobHistoryDetail],
      confirmLabel: $t.common.clear,
      cancelLabel: $t.confirm.cancel,
      closeLabel: $t.confirm.closeLabel,
      variant: 'danger'
    });
    if (!confirmed) return;

    try {
      await jobsStore.clearJobHistory();
      showToast($t.messages.jobHistoryCleared);
    } catch (error) {
      showError(error);
    }
  }

  function retryJob(job: GenerateJobStatus) {
    form = jobToPromptForm(job, lastActivePresetDefaultModel);
    closeJobsDrawer();
    if (job.operation === 'edit') {
      if (!$editSourceStore.files.length && !$editSourceStore.selectedGalleryImageId) {
        previewStore.setError($t.messages.editRetryNeedsSource);
        showToast($t.messages.editRetryNeedsSource, 'error');
        return;
      }
      editImage();
      return;
    }
    generateImage();
  }

  async function diagnoseJob(job: GenerateJobStatus) {
    try {
      const diagnosis = await assistantStore.diagnoseJob(job.job_id);
      jobDiagnoses = { ...jobDiagnoses, [job.job_id]: diagnosis };
    } catch (error) {
      showError(error);
    }
  }

  onMount(() => {
    accessStore.installUnauthorizedHandler();
    const initialData = loadAuthenticatedData();
    void initialData.catch(() => undefined);
    void accessStore.checkAccess(() => initialData);

    const popstate = () => {
      void applyUrlStateToApp();
    };

    const isEditableTarget = (target: EventTarget | null) => {
      const element = target instanceof HTMLElement ? target : null;
      return Boolean(element?.closest('input, textarea') || element?.isContentEditable);
    };

    const isBlockingDialogOpen = () =>
      $uiStore.settingsOpen ||
      $uiStore.promptSnippetsOpen ||
      $uiStore.imagePromptOpen ||
      $uiStore.sizeDialogOpen ||
      galleryEditDialogOpen ||
      Boolean($lightboxStore.image);

    const paste = (event: ClipboardEvent) => {
      if (isEditableTarget(event.target) || isBlockingDialogOpen()) return;

      const files = extractImageFilesFromClipboard(event.clipboardData);
      if (!files.length) return;

      event.preventDefault();
      const addedCount = editSourceStore.addFiles(files, previewStore.setError);
      if (addedCount > 0) showToast($t.messages.editSourceFromClipboardAdded);
    };

    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if ($uiStore.imagePromptOpen) closeImagePromptDialog();
        else if ($uiStore.editPreviewOpen) closeEditPreview();
        else if ($lightboxStore.image) closeLightbox();
        else if ($uiStore.sizeDialogOpen) setUi('sizeDialogOpen', false);
        return;
      }

      if (!$lightboxStore.image || lightboxNavigationBlocked()) return;
      if (event.key === 'ArrowLeft' && $lightboxController.canNavigatePrevious) {
        event.preventDefault();
        void lightboxController.navigate(-1);
      } else if (event.key === 'ArrowRight' && $lightboxController.canNavigateNext) {
        event.preventDefault();
        void lightboxController.navigate(1);
      }
    };
    return installWorkspaceLifecycle({
      onPopstate: popstate,
      onKeydown: keydown,
      onPaste: paste,
      shouldPrefetch: canPrefetchNonCritical,
      prefetchCommonPanels: () => {
        prefetchPanel('settings');
        prefetchPanel('jobs');
        prefetchPanel('snippets');
      },
      cleanup: () => {
        if (gallerySuccessRefreshTimer) clearTimeout(gallerySuccessRefreshTimer);
        gallerySuccessRefreshTimer = null;
        urlSync.destroy();
        jobsStore.cleanup();
        galleryStore.cleanup();
        nodeImageResult.clear();
        previewStore.cleanup();
        uiStore.cleanup();
        lightboxController.destroy();
        optimizingPrompt = false;
      }
    });
  });
</script>

<svelte:head>
  <title>GPT Image Panel</title>
</svelte:head>

<a class="skip-link control-focus" href="#main-content">{$t.common.skipToMain}</a>

<AccessGate visible={$accessStore.gateVisible} error={$accessStore.error} loading={$accessStore.loading} onUnlock={(key) => accessStore.unlockAccess(key, loadAuthenticatedData)} />
<Header
  version={$versionStore.version}
  latestVersion={$versionStore.latestVersion}
  hasVersionUpdate={$versionStore.hasUpdate}
  releaseUrl={$versionStore.releaseUrl}
  {activeJobsCount}
  promptSnippetsOpen={$uiStore.promptSnippetsOpen}
  imagePromptOpen={$uiStore.imagePromptOpen}
  jobsOpen={$uiStore.jobsOpen}
  settingsOpen={$uiStore.settingsOpen}
  onOpenPromptSnippets={openPromptSnippetsDrawer}
  onOpenImagePrompt={openImagePromptDialog}
  onOpenJobs={openJobsDrawer}
  onOpenSettings={() => void openUiPanel('settings', 'settingsOpen')}
  onPrefetchPromptSnippets={() => prefetchPanel('snippets')}
  onPrefetchImagePrompt={() => prefetchPanel('imagePrompt')}
  onPrefetchJobs={() => prefetchPanel('jobs')}
  onPrefetchSettings={() => prefetchPanel('settings')}
/>

<ConfirmDialog request={$confirmStore.request} />
<NodeImageResultDialog />

{#if $imagePromptPanel.component}
  <svelte:component
    this={$imagePromptPanel.component}
    open={$uiStore.imagePromptOpen}
    available={aiAssistantAvailable}
    onClose={closeImagePromptDialog}
    onApply={applyImagePrompt}
    onSave={saveImagePrompt}
    onCopy={copyImagePrompt}
  />
{/if}

{#if $settingsPanel.component}
<svelte:component
  this={$settingsPanel.component}
  open={$uiStore.settingsOpen}
  settings={$settingsStore.settings}
  saving={$settingsActivityStore.saving}
  health={$settingsActivityStore.health}
  healthChecking={$settingsActivityStore.healthChecking}
  r2Health={$settingsActivityStore.r2Health}
  r2HealthChecking={$settingsActivityStore.r2HealthChecking}
  promptOptimizerHealth={$settingsActivityStore.promptOptimizerHealth}
  promptOptimizerHealthChecking={$settingsActivityStore.promptOptimizerHealthChecking}
  aiAssistantHealth={$settingsActivityStore.aiAssistantHealth}
  aiAssistantHealthChecking={$settingsActivityStore.aiAssistantHealthChecking}
  onClose={() => closeUiPanel('settings', 'settingsOpen')}
  onSave={saveSettings}
  onCreate={createPreset}
  onActivate={activatePreset}
  onDelete={deletePreset}
  onHealthCheck={checkPresetHealth}
  onClearPresetHealth={clearPresetHealth}
  onR2HealthCheck={checkR2Health}
  onPromptOptimizerHealthCheck={checkPromptOptimizerHealth}
  onClearPromptOptimizerHealth={clearPromptOptimizerHealth}
  onAiAssistantHealthCheck={checkAiAssistantHealth}
  onClearAiAssistantHealth={clearAiAssistantHealth}
  onLoadPromptOptimizerSystemPrompt={loadPromptOptimizerSystemPrompt}
  onSavePromptOptimizerSystemPrompt={savePromptOptimizerSystemPrompt}
  onLoadOverallConfig={loadOverallConfig}
  onSaveOverallConfig={saveOverallConfig}
/>
{/if}

{#if $snippetsPanel.component}
<svelte:component
  this={$snippetsPanel.component}
  open={$uiStore.promptSnippetsOpen}
  snippets={$promptSnippetsStore.snippets}
  loading={$promptSnippetsStore.loading}
  saving={$promptSnippetsStore.saving}
  currentPrompt={form.prompt}
  onClose={closePromptSnippetsDrawer}
  onSearch={loadPromptSnippets}
  onCreate={createPromptSnippet}
  onUpdate={updatePromptSnippet}
  onDelete={deletePromptSnippet}
  onUse={usePromptSnippet}
  onCopy={copyPromptSnippet}
/>
{/if}

{#if $jobsPanel.component}
<svelte:component
  this={$jobsPanel.component}
  open={$uiStore.jobsOpen}
  activeTab={jobsTab}
  jobs={$jobsStore.jobs}
  historyJobs={$jobsStore.historyJobs}
  historyLoading={$jobsStore.historyLoading}
  historyLoaded={$jobsStore.historyLoaded}
  historyHasMore={$jobsStore.historyHasMore}
  historyFailedOnly={$jobsStore.historyFailedOnly}
  selectedIds={$jobsStore.selectedIds}
  onClose={closeJobsDrawer}
  onTabChange={setJobsTab}
  onRefresh={jobsStore.loadJobs}
  onRefreshHistory={jobsStore.loadJobHistory}
  onLoadMoreHistory={jobsStore.loadMoreJobHistory}
  onHistoryFailedOnlyChange={jobsStore.setHistoryFailedOnly}
  onClearHistory={clearJobHistory}
  onToggle={jobsStore.toggleSelection}
  onToggleAll={jobsStore.toggleAll}
  onCancelSelected={jobsStore.cancelSelected}
  onUseJob={useJobAsPrompt}
  onRetryJob={retryJob}
  aiAssistantEnabled={aiAssistantAvailable}
  diagnosingJobId={$assistantStore.diagnoseLoadingJobId}
  diagnoses={jobDiagnoses}
  onDiagnoseJob={diagnoseJob}
/>
{/if}

<main id="main-content" tabindex="-1" class:optimizer-gutter={optimizerAssistantEnabled} class="mx-auto max-w-5xl space-y-6 px-4 py-6 pb-28 sm:px-6 sm:pb-32">
  <ToastHost toast={$toastStore} />

  <PromptForm
    bind:form
    loading={$previewStore.loading}
    optimizing={optimizingPrompt}
    optimizerEnabled={optimizerAvailable}
    editPlannerEnabled={aiAssistantAvailable}
    editPlanning={$assistantStore.editPlanLoading}
    onSubmit={submitPrompt}
    {hasEditSource}
    onPlanEdit={planEdit}
    onOptimize={optimizePrompt}
    onAppendPromptTag={appendPromptTag}
    onOpenSize={() => void openUiPanel('size', 'sizeDialogOpen')}
  >
    <EditSourcePicker
      slot="edit-source"
      bind:this={editPicker}
      sources={editSources}
      onChange={handleEditFile}
      onDropFiles={handleEditFiles}
      onPreview={openEditPreview}
      onRemove={removeEditSource}
      onClear={clearEditSource}
    />
  </PromptForm>

  <AiAssistantPanel
    enabled={aiAssistantAvailable}
    optimizerEnabled={optimizerAvailable}
    currentPrompt={form.prompt}
    apiPath={form.apiPath}
    model={form.model}
    size={form.size}
    quality={form.quality}
    outputFormat={form.outputFormat}
    quantity={normalizeSubmissionQuantity(form.quantity)}
    loading={$assistantStore.promptLoading || $assistantStore.paramsLoading}
    onApplyPrompt={applyAssistantPrompt}
    onInsertPrompt={insertAssistantPrompt}
    onSaveSnippet={saveAssistantSnippet}
    onApplyParams={applyAssistantParams}
  />

  <PreviewPanel
    loading={$previewStore.loading}
    error={$previewStore.error}
    job={$previewStore.job}
    imageUrl={$previewStore.imageUrl}
    filename={$previewStore.filename}
    prompt={$previewStore.prompt}
    onRegenerate={regenerate}
    onClear={clearPreview}
  />

  <GalleryGrid
    gallery={$galleryStore.gallery}
    filters={$galleryStore.filters}
    loading={$galleryStore.loading}
    operationStatus={$galleryActivityStore.operationStatus}
    canSyncR2={r2BackupAvailable}
    canNodeImageUpload={nodeImageAvailable}
    onFilter={setGalleryFilter}
    onResetFilters={resetGalleryFilters}
    onPage={loadGalleryPage}
    onLoadStats={loadGalleryStats}
    onFavorite={toggleFavorite}
    onDelete={deleteImage}
    onDeleteAll={deleteAllImages}
    onImport={importArchive}
    onExport={exportArchive}
    onSync={syncGallery}
    onOpen={openLightbox}
    onEdit={openGalleryEditDialog}
    onUsePrompt={useGalleryPrompt}
    onUseAll={useGalleryParams}
    onNodeImageUpload={uploadGalleryImageToNodeImage}
    selectionMode={$galleryStore.selectionMode}
    selectedIds={$galleryStore.selectedIds}
    selectionTokenCount={$galleryStore.selectionToken?.count || 0}
    onSelectionMode={galleryStore.setSelectionMode}
    onToggleSelection={galleryStore.toggleSelection}
    onSelectPage={galleryStore.selectPage}
    onSelectFiltered={galleryStore.selectFiltered}
    onClearSelection={galleryStore.clearSelection}
    onBatchDelete={batchDeleteGallery}
    onBatchFavorite={batchFavoriteGallery}
    onBatchDownload={() => galleryStore.batchDownload(showToast)}
    onBatchNodeImageUpload={batchUploadGalleryToNodeImage}
    canAiAnalyze={aiAssistantAvailable}
    onBatchAiAnalyze={batchAnalyzeGallery}
  />
</main>

{#if $optimizerPanel.component}
<svelte:component
  this={$optimizerPanel.component}
  enabled={optimizerAssistantEnabled}
  currentPrompt={form.prompt}
  apiPath={form.apiPath}
  model={form.model}
  size={form.size}
  quality={form.quality}
  onApplyPrompt={applyOptimizedPrompt}
/>
{/if}

{#if $lightboxPanel.component}
<svelte:component
  this={$lightboxPanel.component}
  open={Boolean($lightboxStore.image)}
  image={$lightboxStore.image}
  onClose={closeLightbox}
  onEdit={openGalleryEditDialog}
  onFavorite={toggleFavorite}
  onDelete={deleteImage}
  onCopyPrompt={copyPrompt}
  onCopyUrl={copyImageUrl}
  onUsePrompt={useGalleryPrompt}
  onUseAll={useGalleryParams}
  canNavigatePrevious={$lightboxController.canNavigatePrevious}
  canNavigateNext={$lightboxController.canNavigateNext}
  navigating={$lightboxController.navigating}
  aiAssistantEnabled={aiAssistantAvailable}
  aiMetadata={$lightboxController.aiMetadata}
  aiLoadingImageId={$assistantStore.galleryLoadingImageId}
  onAiDescribe={lightboxController.describe}
  onAiAnalyze={lightboxController.analyze}
  onNavigatePrevious={() => lightboxController.navigate(-1)}
  onNavigateNext={() => lightboxController.navigate(1)}
/>
{/if}

{#if $editPreviewPanel.component}
<svelte:component
  this={$editPreviewPanel.component}
  open={$uiStore.editPreviewOpen}
  url={editPreviewUrl}
  label={editPreviewLabel}
  onClose={closeEditPreview}
/>
{/if}

<EditGalleryDialog
  open={galleryEditDialogOpen}
  image={galleryEditImage}
  onChoose={applyGalleryEditChoice}
  onClose={closeGalleryEditDialog}
/>

{#if $sizePanel.component}
  <svelte:component this={$sizePanel.component} open={$uiStore.sizeDialogOpen} value={form.size} onApply={(nextSize: string) => (form = { ...form, size: nextSize })} onClose={() => closeUiPanel('size', 'sizeDialogOpen')} />
{/if}

{#if loadingPanel}
  <div class="lazy-panel-status" role="status" aria-live="polite">
    <span class="spinner" aria-hidden="true"></span>
    <span>{$t.common.loadingFeature}</span>
  </div>
{/if}
