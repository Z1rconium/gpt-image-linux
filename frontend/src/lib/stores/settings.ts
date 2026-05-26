import { derived, get, writable } from 'svelte/store';
import { apiFetch } from '$lib/api/client';
import { t } from '$lib/i18n';
import { confirmStore } from '$lib/stores/confirm';
import type { PresetHealthResponse, PromptOptimizerSystemPromptResponse, SettingsInput, SettingsResponse } from '$lib/api/types';
import type { ToastVariant } from '$lib/stores/ui';

type ShowToast = (message: string, variant?: ToastVariant) => void;

type SettingsState = {
  settings: SettingsResponse | null;
  saving: boolean;
  healthChecking: boolean;
  health: PresetHealthResponse | null;
};

const initialSettingsState: SettingsState = {
  settings: null,
  saving: false,
  healthChecking: false,
  health: null
};

function createSettingsStore() {
  const { subscribe, update } = writable<SettingsState>(initialSettingsState);

  async function loadSettings() {
    const settings = await apiFetch<SettingsResponse>('/api/settings', {}, 'loading settings');
    update((state) => ({ ...state, settings }));
  }

  async function saveSettings(body: SettingsInput, showToast: ShowToast) {
    if (!String(body.api_url || '').trim()) {
      showToast(get(t).messages.apiUrlRequired, 'error');
      return;
    }
    if (body.api_key !== null && !String(body.api_key || '').trim()) {
      showToast(get(t).messages.apiKeyRequired, 'error');
      return;
    }

    update((state) => ({ ...state, saving: true }));
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
      update((state) => ({ ...state, settings, health: null }));
      showToast(get(t).messages.presetSaved);
    } finally {
      update((state) => ({ ...state, saving: false }));
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
    update((state) => ({ ...state, settings, health: null }));
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
    update((state) => ({ ...state, settings, health: null }));
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
    update((state) => ({ ...state, settings, health: null }));
    showToast(get(t).messages.presetDeleted);
  }

  async function checkPresetHealth(presetId: string) {
    if (!presetId) return;
    update((state) => ({ ...state, healthChecking: true }));
    try {
      const health = await apiFetch<PresetHealthResponse>(
        `/api/settings/presets/${encodeURIComponent(presetId)}/health`,
        { method: 'POST' },
        'checking preset health'
      );
      update((state) => ({ ...state, health }));
    } finally {
      update((state) => ({ ...state, healthChecking: false }));
    }
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

  return {
    subscribe,
    loadSettings,
    saveSettings,
    createPreset,
    activatePreset,
    deletePreset,
    checkPresetHealth,
    loadPromptOptimizerSystemPrompt,
    savePromptOptimizerSystemPrompt
  };
}

export const settingsStore = createSettingsStore();
export const responsesMode = derived(settingsStore, ($state) => $state.settings?.api_path === '/v1/responses');
