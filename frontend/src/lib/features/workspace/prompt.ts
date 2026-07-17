import type { ApiPath, ResponseFormatDefault } from '$lib/api/types/common';
import type { SettingsResponse } from '$lib/api/types/settings';
import { DEFAULT_PROMPT_MODEL, initialPromptFormState } from '$lib/stores/preview';
import { normalizeApiPath, normalizeResponseFormat } from '$lib/utils/promptForm';

export function activePreset(settings: SettingsResponse | null) {
  return settings?.presets.find((preset) => preset.id === settings.active_preset_id) || settings?.presets[0] || null;
}

export function presetDefaultModel(settings: SettingsResponse | null) {
  const preset = activePreset(settings);
  return (preset?.default_model || settings?.default_model || DEFAULT_PROMPT_MODEL).trim() || DEFAULT_PROMPT_MODEL;
}

export function presetApiPath(settings: SettingsResponse | null): ApiPath {
  const preset = activePreset(settings);
  return normalizeApiPath(preset?.api_path || settings?.api_path, initialPromptFormState.apiPath);
}

export function presetDefaultResponseFormat(settings: SettingsResponse | null): ResponseFormatDefault {
  const preset = activePreset(settings);
  return normalizeResponseFormat(preset?.default_response_format ?? settings?.default_response_format, initialPromptFormState.responseFormat);
}

