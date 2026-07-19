import type { ApiPath, AssistantGalleryBatchJobStatusValue } from './common';

export type PromptOptimizeRequest = {
  prompt: string;
  intent?: string | null;
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

export type AssistantBaseResponse = {
  model: string;
  duration_ms: number;
  warnings: string[];
};

export type AssistantPromptRewriteRequest = {
  prompt: string;
  instruction?: string | null;
  target_language?: 'en' | 'zh-CN' | 'same';
  api_path?: ApiPath | null;
  model?: string | null;
  size?: string | null;
  quality?: 'auto' | 'low' | 'medium' | 'high' | null;
};

export type AssistantPromptRewriteResponse = AssistantBaseResponse & {
  rewritten_prompt: string;
};

export type AssistantPromptCheckRequest = {
  prompt: string;
  api_path?: ApiPath | null;
  model?: string | null;
  size?: string | null;
  quality?: 'auto' | 'low' | 'medium' | 'high' | null;
};

export type AssistantPromptIssue = {
  severity: 'info' | 'warning' | 'error';
  message: string;
  suggestion?: string | null;
};

export type AssistantPromptCheckResponse = AssistantBaseResponse & {
  score: number;
  summary: string;
  issues: AssistantPromptIssue[];
};

export type AssistantPromptVariantsRequest = AssistantPromptRewriteRequest & {
  count?: number;
};

export type AssistantPromptVariant = {
  title: string;
  prompt: string;
  angle?: string | null;
};

export type AssistantPromptVariantsResponse = AssistantBaseResponse & {
  variants: AssistantPromptVariant[];
};

export type AssistantRecommendParamsRequest = {
  prompt: string;
  api_path: ApiPath;
  current_model?: string | null;
  current_size?: string | null;
  current_quality?: 'auto' | 'low' | 'medium' | 'high' | null;
  current_output_format?: 'png' | 'jpeg' | 'webp' | null;
  current_n?: number | null;
};

export type AssistantRecommendParamsResponse = AssistantBaseResponse & {
  model_name?: string | null;
  size?: string | null;
  quality?: 'auto' | 'low' | 'medium' | 'high' | null;
  output_format?: 'png' | 'jpeg' | 'webp' | null;
  n?: number | null;
  rationale: string;
};

export type AssistantJobDiagnoseRequest = {
  include_prompt?: boolean;
};

export type AssistantJobDiagnoseResponse = AssistantBaseResponse & {
  summary: string;
  likely_causes: string[];
  recommended_actions: string[];
  safe_job: Record<string, unknown>;
};

export type AssistantEditPlanRequest = {
  goal: string;
  source_count?: number;
  current_prompt?: string | null;
  target_size?: string | null;
};

export type AssistantEditPlanResponse = AssistantBaseResponse & {
  edit_prompt: string;
  source_requirements: string[];
  suggested_size?: string | null;
  confidence: number;
  next_action: 'confirm' | 'revise' | 'add_sources';
};

export type AssistantImagePromptResponse = AssistantBaseResponse & {
  prompt: string;
};

export type AssistantTemporaryImage = {
  b64: string;
  mime_type: string;
  width: number;
  height: number;
  model: string;
  duration_ms: number;
};

export type AssistantImagePromptOptimizeResponse = AssistantBaseResponse & {
  prompt: string;
  comparison_summary: string;
  temporary_image: AssistantTemporaryImage;
};

export type AssistantGalleryImageResponse = AssistantBaseResponse & {
  image_id: string;
  description: string;
  prompt: string;
  analysis: Record<string, unknown>;
};

export type AssistantGalleryMetadataResponse = {
  image_id: string;
  description: string;
  prompt: string;
  analysis: Record<string, unknown>;
  model: string;
  created_at?: string | null;
  updated_at?: string | null;
};

export type AssistantGalleryBatchRequest = {
  ids?: string[] | null;
  selection_token?: string | null;
  target_language?: 'en' | 'zh-CN';
};

export type AssistantGalleryBatchJobStatus = {
  job_id: string;
  status: AssistantGalleryBatchJobStatusValue;
  stage?: string | null;
  message?: string | null;
  progress: number;
  requested_count: number;
  processed_count: number;
  analyzed_count: number;
  missing_count: number;
  failed_count: number;
  created_at?: string | null;
  updated_at?: string | null;
  error?: string | null;
};

