import type { ApiPath } from './common';

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


