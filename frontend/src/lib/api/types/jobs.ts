import type { GenerateJobStatusValue } from './common';

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


