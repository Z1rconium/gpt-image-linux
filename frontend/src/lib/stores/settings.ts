import { get, writable } from 'svelte/store';
import { apiFetch } from '$lib/api/client';
import { t } from '$lib/i18n';
import { confirmStore } from '$lib/stores/confirm';
import type {
  AIAssistantSettingsInput,
  AssistantHealthResponse,
  OverallConfigResponse,
  OverallConfigUpdateRequest,
  PresetHealthResponse,
  PromptOptimizerHealthResponse,
  PromptOptimizerSystemPromptResponse,
  R2BackupSettingsInput,
  R2HealthResponse,
  SettingsInput,
  SettingsResponse
} from '$lib/api/types';
import type { ToastVariant } from '$lib/stores/ui';

type ShowToast = (message: string, variant?: ToastVariant) => void;

type SettingsState = {
  settings: SettingsResponse | null;
};

export type SettingsActivityState = {
  saving: boolean;
  healthChecking: boolean;
  health: PresetHealthResponse | null;
  r2HealthChecking: boolean;
  r2Health: R2HealthResponse | null;
  promptOptimizerHealthChecking: boolean;
  promptOptimizerHealth: PromptOptimizerHealthResponse | null;
  aiAssistantHealthChecking: boolean;
  aiAssistantHealth: AssistantHealthResponse | null;
};

const initialSettingsState: SettingsState = {
  settings: null,
};

const initialSettingsActivityState: SettingsActivityState = {
  saving: false,
  healthChecking: false,
  health: null,
  r2HealthChecking: false,
  r2Health: null,
  promptOptimizerHealthChecking: false,
  promptOptimizerHealth: null,
  aiAssistantHealthChecking: false,
  aiAssistantHealth: null
};

function createSettingsActivityStore() {
  const { subscribe, update } = writable<SettingsActivityState>(initialSettingsActivityState);

  function setSaving(saving: boolean) {
    update((state) => ({ ...state, saving }));
  }

  function setHealthChecking(healthChecking: boolean) {
    update((state) => ({ ...state, healthChecking }));
  }

  function setHealth(health: PresetHealthResponse | null) {
    update((state) => ({ ...state, health }));
  }

  function setR2HealthChecking(r2HealthChecking: boolean) {
    update((state) => ({ ...state, r2HealthChecking }));
  }

  function setR2Health(r2Health: R2HealthResponse | null) {
    update((state) => ({ ...state, r2Health }));
  }

  function setPromptOptimizerHealthChecking(promptOptimizerHealthChecking: boolean) {
    update((state) => ({ ...state, promptOptimizerHealthChecking }));
  }

  function setPromptOptimizerHealth(promptOptimizerHealth: PromptOptimizerHealthResponse | null) {
    update((state) => ({ ...state, promptOptimizerHealth }));
  }

  function setAiAssistantHealthChecking(aiAssistantHealthChecking: boolean) {
    update((state) => ({ ...state, aiAssistantHealthChecking }));
  }

  function setAiAssistantHealth(aiAssistantHealth: AssistantHealthResponse | null) {
    update((state) => ({ ...state, aiAssistantHealth }));
  }

  function clearHealth() {
    update((state) => ({ ...state, health: null }));
  }

  function clearPromptOptimizerHealth() {
    update((state) => ({ ...state, promptOptimizerHealth: null }));
  }

  function clearAiAssistantHealth() {
    update((state) => ({ ...state, aiAssistantHealth: null }));
  }

  function reset() {
    update(() => ({ ...initialSettingsActivityState }));
  }

  return {
    subscribe,
    setSaving,
    setHealthChecking,
    setHealth,
    setR2HealthChecking,
    setR2Health,
    setPromptOptimizerHealthChecking,
    setPromptOptimizerHealth,
    setAiAssistantHealthChecking,
    setAiAssistantHealth,
    clearHealth,
    clearPromptOptimizerHealth,
    clearAiAssistantHealth,
    reset
  };
}

export const settingsActivityStore = createSettingsActivityStore();

function createSettingsStore() {
  const { subscribe, update } = writable<SettingsState>(initialSettingsState);

  async function loadSettings() {
    const settings = await apiFetch<SettingsResponse>('/api/settings', {}, 'loading settings');
    update((state) => ({ ...state, settings }));
  }

  async function saveSettings(body: SettingsInput, showToast: ShowToast) {
    if (!String(body.api_url || '').trim()) {
      showToast(get(t).messages.apiUrlRequired, 'error');
      return false;
    }
    if (body.api_key !== null && !String(body.api_key || '').trim()) {
      showToast(get(t).messages.apiKeyRequired, 'error');
      return false;
    }

    settingsActivityStore.setSaving(true);
    try {
      const settings = await apiFetch<SettingsResponse>(
        '/api/settings',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        },
        'saving settings'
      );
      update((state) => ({ ...state, settings }));
      settingsActivityStore.clearHealth();
      settingsActivityStore.setR2Health(null);
      settingsActivityStore.clearPromptOptimizerHealth();
      settingsActivityStore.clearAiAssistantHealth();
      showToast(get(t).messages.presetSaved);
      return true;
    } finally {
      settingsActivityStore.setSaving(false);
    }
  }

  async function createPreset(showToast: ShowToast) {
    const activePresetId = get(settingsStore).settings?.active_preset_id;
    const settings = await apiFetch<SettingsResponse>(
      '/api/settings/presets',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source_preset_id: activePresetId })
      },
      'creating preset'
    );
    update((state) => ({ ...state, settings }));
    settingsActivityStore.clearHealth();
    settingsActivityStore.setR2Health(null);
    settingsActivityStore.clearPromptOptimizerHealth();
    settingsActivityStore.clearAiAssistantHealth();
    showToast(get(t).messages.presetCreated);
  }

  async function activatePreset(presetId: string, showToast: ShowToast) {
    const current = get(settingsStore).settings;
    if (!presetId || presetId === current?.active_preset_id) return;
    const settings = await apiFetch<SettingsResponse>(
      `/api/settings/presets/${encodeURIComponent(presetId)}/activate`,
      { method: 'POST' },
      'switching preset'
    );
    update((state) => ({ ...state, settings }));
    settingsActivityStore.clearHealth();
    settingsActivityStore.setR2Health(null);
    settingsActivityStore.clearPromptOptimizerHealth();
    settingsActivityStore.clearAiAssistantHealth();
    showToast(get(t).messages.presetSwitched);
  }

  async function deletePreset(presetId: string, showToast: ShowToast) {
    const current = get(settingsStore).settings;
    if (!current || current.presets.length <= 1) return;
    const target = current.presets.find((preset) => preset.id === presetId) || current.presets.find((preset) => preset.id === current.active_preset_id);
    if (!target) return;
    const confirmed = await confirmStore.confirm({
      title: get(t).confirm.deletePresetTitle,
      message: get(t).confirm.deletePresetMessage(target.name || get(t).common.untitledPreset),
      confirmLabel: get(t).settings.deletePreset,
      cancelLabel: get(t).confirm.cancel,
      closeLabel: get(t).confirm.closeLabel,
      variant: 'danger'
    });
    if (!confirmed) return;
    const settings = await apiFetch<SettingsResponse>(
      `/api/settings/presets/${encodeURIComponent(target.id)}`,
      { method: 'DELETE' },
      'deleting preset'
    );
    update((state) => ({ ...state, settings }));
    settingsActivityStore.clearHealth();
    settingsActivityStore.setR2Health(null);
    settingsActivityStore.clearPromptOptimizerHealth();
    settingsActivityStore.clearAiAssistantHealth();
    showToast(get(t).messages.presetDeleted);
  }

  async function checkPresetHealth(presetId: string) {
    if (!presetId) return;
    settingsActivityStore.setHealthChecking(true);
    try {
      const health = await apiFetch<PresetHealthResponse>(
        `/api/settings/presets/${encodeURIComponent(presetId)}/health`,
        { method: 'POST' },
        'checking preset health'
      );
      settingsActivityStore.setHealth(health);
    } finally {
      settingsActivityStore.setHealthChecking(false);
    }
  }

  function clearPresetHealth() {
    settingsActivityStore.clearHealth();
  }

  async function checkR2Health(body: R2BackupSettingsInput) {
    settingsActivityStore.setR2HealthChecking(true);
    try {
      const r2Health = await apiFetch<R2HealthResponse>(
        '/api/settings/r2/health',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        },
        'checking R2 health'
      );
      settingsActivityStore.setR2Health(r2Health);
    } finally {
      settingsActivityStore.setR2HealthChecking(false);
    }
  }

  async function checkPromptOptimizerHealth() {
    settingsActivityStore.setPromptOptimizerHealthChecking(true);
    try {
      const promptOptimizerHealth = await apiFetch<PromptOptimizerHealthResponse>(
        '/api/prompt/optimizer-health',
        { method: 'POST' },
        'checking prompt optimizer health'
      );
      settingsActivityStore.setPromptOptimizerHealth(promptOptimizerHealth);
    } finally {
      settingsActivityStore.setPromptOptimizerHealthChecking(false);
    }
  }

  function clearPromptOptimizerHealth() {
    settingsActivityStore.clearPromptOptimizerHealth();
  }

  async function checkAiAssistantHealth(body?: AIAssistantSettingsInput) {
    settingsActivityStore.setAiAssistantHealthChecking(true);
    try {
      const aiAssistantHealth = await apiFetch<AssistantHealthResponse>(
        '/api/assistant/health',
        {
          method: 'POST',
          headers: body ? { 'Content-Type': 'application/json' } : undefined,
          body: body ? JSON.stringify(body) : undefined
        },
        'checking AI Assistant health'
      );
      settingsActivityStore.setAiAssistantHealth(aiAssistantHealth);
    } finally {
      settingsActivityStore.setAiAssistantHealthChecking(false);
    }
  }

  function clearAiAssistantHealth() {
    settingsActivityStore.clearAiAssistantHealth();
  }

  async function loadPromptOptimizerSystemPrompt() {
    return apiFetch<PromptOptimizerSystemPromptResponse>(
      '/api/prompt/optimizer-system-prompt',
      {},
      'loading prompt optimizer system prompt'
    );
  }

  async function savePromptOptimizerSystemPrompt(systemPrompt: string, showToast: ShowToast) {
    const response = await apiFetch<PromptOptimizerSystemPromptResponse>(
      '/api/prompt/optimizer-system-prompt',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ system_prompt: systemPrompt })
      },
      'saving prompt optimizer system prompt'
    );
    showToast(get(t).messages.promptOptimizerSystemPromptSaved);
    return response;
  }

  async function loadOverallConfig() {
    return apiFetch<OverallConfigResponse>(
      '/api/settings/overall-config',
      {},
      'loading overall config'
    );
  }

  async function saveOverallConfig(body: OverallConfigUpdateRequest, showToast: ShowToast) {
    const response = await apiFetch<OverallConfigResponse>(
      '/api/settings/overall-config',
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      },
      'saving overall config'
    );
    if (response.restart_required_names.length) {
      showToast(get(t).messages.overallConfigRestartRequired(response.restart_required_names.length));
    } else {
      showToast(get(t).messages.overallConfigSaved);
    }
    return response;
  }

  return {
    subscribe,
    loadSettings,
    saveSettings,
    createPreset,
    activatePreset,
    deletePreset,
    checkPresetHealth,
    clearPresetHealth,
    checkR2Health,
    checkPromptOptimizerHealth,
    clearPromptOptimizerHealth,
    checkAiAssistantHealth,
    clearAiAssistantHealth,
    loadPromptOptimizerSystemPrompt,
    savePromptOptimizerSystemPrompt,
    loadOverallConfig,
    saveOverallConfig
  };
}

export const settingsStore = createSettingsStore();
