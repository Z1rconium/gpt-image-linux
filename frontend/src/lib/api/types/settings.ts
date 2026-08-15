import type { ApiKeySource, ApiPath, AssistantApiPath, OverallConfigValueSource, OverallConfigValueType, PresetHealthStatus, ResponseFormatDefault } from './common';

export type ApiPreset = {
  id: string;
  name: string;
  api_url: string;
  api_path: ApiPath;
  default_model: string;
  default_response_format: ResponseFormatDefault;
  api_key_masked: string;
  has_api_key: boolean;
  api_key_source: ApiKeySource;
  api_key_env_var?: string | null;
  api_key_secret_id?: string | null;
};

export type SettingsResponse = {
  active_preset_id: string;
  api_url: string;
  api_key_masked: string;
  has_api_key: boolean;
  api_key_source: ApiKeySource;
  api_key_env_var?: string | null;
  api_key_secret_id?: string | null;
  api_path: ApiPath;
  default_model: string;
  default_response_format: ResponseFormatDefault;
  has_upstream_socks5_proxy: boolean;
  upstream_socks5_proxy_masked: string;
  has_webhook_url: boolean;
  webhook_url_masked: string;
  presets: ApiPreset[];
  prompt_optimizer: PromptOptimizerSettings;
  ai_assistant: AIAssistantSettings;
  r2_backup: R2BackupSettings;
  nodeimage: NodeImageSettings;
  image_upload_limits: ImageUploadLimits;
};

export type ImageUploadLimits = {
  max_file_size_bytes: number;
  max_image_pixels: number;
};

export type PromptOptimizerSettings = {
  enabled: boolean;
  api_url: string;
  model: string;
  timeout_seconds: number;
  api_key_masked: string;
  has_api_key: boolean;
  api_key_source: ApiKeySource;
  api_key_env_var?: string | null;
  api_key_secret_id?: string | null;
};

export type PromptOptimizerHealthResponse = {
  status: 'ok' | 'warning' | 'error';
  message: string;
  model: string;
  duration_ms: number;
  status_code?: number | null;
};

export type AssistantHealthResponse = {
  status: 'ok' | 'warning' | 'error';
  message: string;
  model: string;
  duration_ms: number;
  status_code?: number | null;
};

export type PromptOptimizerSettingsInput = {
  enabled?: boolean | null;
  api_url?: string | null;
  model?: string | null;
  timeout_seconds?: number | null;
  api_key?: string | null;
};

export type AIAssistantSettings = {
  enabled: boolean;
  api_url: string;
  model: string;
  vision_model: string;
  timeout_seconds: number;
  api_path: AssistantApiPath;
  api_key_masked: string;
  has_api_key: boolean;
  api_key_source: ApiKeySource;
  api_key_env_var?: string | null;
  api_key_secret_id?: string | null;
};

export type AIAssistantSettingsInput = {
  enabled?: boolean | null;
  vision_model?: string | null;
};

export type R2BackupSettings = {
  enabled: boolean;
  endpoint_url: string;
  bucket_name: string;
  region: string;
  key_prefix: string;
  sync_interval_hours: number;
  access_key_id_masked: string;
  has_access_key_id: boolean;
  access_key_id_source: ApiKeySource;
  access_key_id_env_var?: string | null;
  access_key_id_secret_id?: string | null;
  secret_access_key_masked: string;
  has_secret_access_key: boolean;
  secret_access_key_source: ApiKeySource;
  secret_access_key_env_var?: string | null;
  secret_access_key_secret_id?: string | null;
};

export type R2BackupSettingsInput = {
  enabled?: boolean | null;
  endpoint_url?: string | null;
  bucket_name?: string | null;
  region?: string | null;
  key_prefix?: string | null;
  sync_interval_hours?: number | null;
  access_key_id?: string | null;
  secret_access_key?: string | null;
};

export type NodeImageSettings = {
  enabled: boolean;
  api_key_masked: string;
  has_api_key: boolean;
  api_key_resolvable: boolean;
  api_key_source: ApiKeySource;
  api_key_env_var?: string | null;
  api_key_secret_id?: string | null;
};

export type NodeImageSettingsInput = {
  enabled?: boolean | null;
  api_key?: string | null;
};

export type PromptOptimizerSystemPromptResponse = {
  system_prompt: string;
  default_system_prompt: string;
  customized: boolean;
};

export type SettingsInput = {
  active_preset_id?: string | null;
  preset_name?: string | null;
  api_url: string;
  api_key?: string | null;
  api_path: ApiPath;
  default_model?: string | null;
  default_response_format?: ResponseFormatDefault | null;
  upstream_socks5_proxy?: string | null;
  webhook_url?: string | null;
  prompt_optimizer?: PromptOptimizerSettingsInput | null;
  ai_assistant?: AIAssistantSettingsInput | null;
  r2_backup?: R2BackupSettingsInput | null;
  nodeimage?: NodeImageSettingsInput | null;
};

export type PresetHealthCheck = {
  name: string;
  status: PresetHealthStatus;
  message: string;
};

export type PresetHealthResponse = {
  status: PresetHealthStatus;
  checks: PresetHealthCheck[];
};

export type R2HealthResponse = PresetHealthResponse;

export type OverallConfigItem = {
  name: string;
  type: OverallConfigValueType;
  group: string;
  description: string;
  value: string | boolean | number;
  value_masked: string;
  env_value_masked: string;
  override_value_masked?: string | null;
  source: OverallConfigValueSource;
  is_env_set: boolean;
  has_override: boolean;
  secret: boolean;
  hot_reload: boolean;
  restart_required: boolean;
  build_only: boolean;
  startup_only: boolean;
  updated_at?: string | null;
  override_updated_at?: string | null;
};

export type OverallConfigResponse = {
  items: OverallConfigItem[];
  restart_required_names: string[];
};

export type OverallConfigUpdateItem = {
  name: string;
  value?: string | boolean | number | null;
  clear_override?: boolean;
};

export type OverallConfigUpdateRequest = {
  updates: OverallConfigUpdateItem[];
};
