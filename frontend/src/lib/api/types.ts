export type ApiPath = '/v1/images/generations' | '/v1/responses' | '/v1/chat/completions';
export type ApiKeySource = 'empty' | 'stored' | 'env';
export type OverallConfigValueType = 'string' | 'secret' | 'bool' | 'int' | 'float';
export type OverallConfigValueSource = 'override' | 'env' | 'default';
export type ResponseFormatDefault = '' | 'url' | 'b64_json';
export type PresetHealthStatus = 'ok' | 'warning' | 'error';
export type GenerateJobStatusValue = 'queued' | 'running' | 'success' | 'error' | 'cancelled' | 'interrupted' | 'upstream_error';
export type GalleryExportJobStatusValue = 'queued' | 'running' | 'success' | 'error';
export type GallerySyncJobStatusValue = 'queued' | 'running' | 'success' | 'error';
export type GalleryImportJobStatusValue = 'queued' | 'running' | 'success' | 'error';

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
};

export type SettingsResponse = {
  active_preset_id: string;
  api_url: string;
  api_key_masked: string;
  has_api_key: boolean;
  api_key_source: ApiKeySource;
  api_key_env_var?: string | null;
  api_path: ApiPath;
  default_model: string;
  default_response_format: ResponseFormatDefault;
  has_upstream_socks5_proxy: boolean;
  upstream_socks5_proxy_masked: string;
  has_webhook_url: boolean;
  webhook_url_masked: string;
  presets: ApiPreset[];
  prompt_optimizer: PromptOptimizerSettings;
  r2_backup: R2BackupSettings;
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
};

export type PromptOptimizerSettingsInput = {
  enabled?: boolean | null;
  api_url?: string | null;
  model?: string | null;
  timeout_seconds?: number | null;
  api_key?: string | null;
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
  secret_access_key_masked: string;
  has_secret_access_key: boolean;
  secret_access_key_source: ApiKeySource;
  secret_access_key_env_var?: string | null;
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
  r2_backup?: R2BackupSettingsInput | null;
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

export type AccessStatus = {
  authenticated: boolean;
  expires_at?: string | null;
};

export type GenerateRequestBody = {
  prompt: string;
  size: string;
  model: string;
  n: number;
  quality: 'auto' | 'low' | 'medium' | 'high';
  output_format: 'png' | 'jpeg' | 'webp';
  output_compression?: number | null;
  response_format?: 'url' | 'b64_json' | null;
  api_path?: ApiPath | null;
};

export type PromptOptimizeRequest = {
  prompt: string;
  target_language?: 'en' | 'zh-CN' | 'same';
  api_path?: ApiPath | null;
  model?: string | null;
  size?: string | null;
  quality?: 'auto' | 'low' | 'medium' | 'high' | null;
};

export type PromptOptimizeResponse = {
  optimized_prompt: string;
  model: string;
  duration_ms: number;
};

export type PromptSnippet = {
  id: string;
  title: string;
  prompt: string;
  favorite: boolean;
  created_at: string;
  updated_at: string;
};

export type PromptSnippetListResponse = {
  snippets: PromptSnippet[];
};

export type PromptSnippetCreateInput = {
  title: string;
  prompt: string;
  favorite?: boolean;
};

export type PromptSnippetUpdateInput = {
  title?: string;
  prompt?: string;
  favorite?: boolean;
};

export type GenerateJobResponse = {
  job_id: string;
  status: GenerateJobStatusValue;
  message?: string | null;
  stage?: string | null;
  operation?: 'generation' | 'edit' | null;
};

export type GenerateJobImage = {
  image_id: string;
  image_url: string;
  filename: string;
  image_width?: number | null;
  image_height?: number | null;
};

export type GenerateJobStatus = GenerateJobResponse & {
  id?: string | null;
  image_id?: string | null;
  image_url?: string | null;
  images?: GenerateJobImage[];
  prompt?: string | null;
  size?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  updated_at?: string | null;
  image_width?: number | null;
  image_height?: number | null;
  model?: string | null;
  quality?: string | null;
  output_format?: string | null;
  output_compression?: number | null;
  response_format?: string | null;
  n?: number | null;
  api_path?: string | null;
  api_preset_name?: string | null;
  duration?: string | null;
  stage_timings?: Record<string, number>;
  error?: string | null;
};

export type GalleryEntry = {
  id: string;
  prompt: string;
  size: string;
  filename: string;
  image_url?: string | null;
  thumbnail_filename?: string | null;
  thumbnail_url?: string | null;
  thumbnail_status?: 'ready' | 'queued' | 'missing';
  created_at: string;
  completed_at?: string | null;
  image_width?: number | null;
  image_height?: number | null;
  model?: string | null;
  quality?: string | null;
  output_format?: string | null;
  output_compression?: number | null;
  response_format?: string | null;
  n?: number | null;
  api_path?: string | null;
  api_preset_name?: string | null;
  duration?: string | null;
  favorite: boolean;
  bytes?: number | null;
};

export type GalleryResponse = {
  total: number;
  total_bytes: number;
  page: number;
  page_size: number;
  total_pages: number;
  has_prev: boolean;
  has_next: boolean;
  next_cursor?: string | null;
  prev_cursor?: string | null;
  images: GalleryEntry[];
  filter_options: {
    models: string[];
    presets: string[];
    sizes: string[];
  };
};

export type MessageResponse = {
  status: string;
  message: string;
};

export type GalleryBatchResponse = {
  status: string;
  count: number;
  file_count?: number;
  requested_count?: number;
  updated_count?: number;
  missing_count?: number;
  missing_ids?: string[];
};

export type GallerySelectionTokenResponse = {
  selection_token: string;
  count: number;
  expires_at: string;
};

export type GalleryExportJobStatus = {
  job_id: string;
  status: GalleryExportJobStatusValue;
  stage?: string | null;
  message?: string | null;
  progress: number;
  filename?: string | null;
  download_url?: string | null;
  requested_count: number;
  processed_count: number;
  exported_count: number;
  missing_count: number;
  bytes_total: number;
  bytes_written: number;
  created_at?: string | null;
  updated_at?: string | null;
  error?: string | null;
};

export type GallerySyncJobStatus = {
  job_id: string;
  status: GallerySyncJobStatusValue;
  stage?: string | null;
  message?: string | null;
  progress: number;
  created_at?: string | null;
  updated_at?: string | null;
  error?: string | null;
  total_count: number;
  compared_count: number;
  uploaded_count: number;
  pending_upload_count: number;
  skipped_existing_count: number;
  missing_local_count: number;
  failed_count: number;
  bytes_total: number;
  bytes_uploaded: number;
  dry_run: boolean;
  checkpoint_filename?: string | null;
};

export type GalleryImportJobStatus = {
  job_id: string;
  status: GalleryImportJobStatusValue;
  stage?: string | null;
  message?: string | null;
  progress: number;
  requested_count: number;
  processed_count: number;
  imported_count: number;
  skipped_count: number;
  created_at?: string | null;
  updated_at?: string | null;
  error?: string | null;
};
