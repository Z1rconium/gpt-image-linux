import type { ApiPath, GalleryEntry, GenerateJobStatus } from '$lib/api/types';
import { DEFAULT_PROMPT_MODEL, DEFAULT_QUANTITY, initialPromptFormState, type PromptFormState } from '$lib/stores/preview';

const GENERATION_API_PATHS: ApiPath[] = ['/v1/images/generations', '/v1/responses', '/v1/chat/completions'];

export function normalizeApiPath(value: string | null | undefined, fallback: ApiPath = initialPromptFormState.apiPath): ApiPath {
  return GENERATION_API_PATHS.includes(value as ApiPath) ? (value as ApiPath) : fallback;
}

export function normalizeJobQuality(value: string | null | undefined): PromptFormState['quality'] {
  if (value === 'auto' || value === 'low' || value === 'medium' || value === 'high') return value;
  return initialPromptFormState.quality;
}

export function normalizeJobOutputFormat(value: string | null | undefined): PromptFormState['outputFormat'] {
  if (value === 'png' || value === 'jpeg' || value === 'webp') return value;
  return initialPromptFormState.outputFormat;
}

export function normalizeJobResponseFormat(value: string | null | undefined): PromptFormState['responseFormat'] {
  return value === 'url' || value === 'b64_json' ? value : '';
}

export function clampQuantity(value: number | string | null | undefined): number {
  return Math.min(Math.max(Number(value) || DEFAULT_QUANTITY, 1), 10);
}

export function sanitizeQuantityInput(value: number | string | null | undefined): string {
  return String(value ?? '').replace(/\D+/g, '');
}

export function normalizeSubmissionQuantity(value: number | string | null | undefined): number {
  if (value === '' || value === null || value === undefined) return DEFAULT_QUANTITY;
  return clampQuantity(value);
}

export function jobToPromptForm(job: GenerateJobStatus, fallbackModel = DEFAULT_PROMPT_MODEL): PromptFormState {
  return {
    prompt: job.prompt || '',
    apiPath: normalizeApiPath(job.api_path),
    size: job.size || initialPromptFormState.size,
    model: job.model || fallbackModel || initialPromptFormState.model,
    quality: normalizeJobQuality(job.quality),
    outputFormat: normalizeJobOutputFormat(job.output_format),
    outputCompression: job.output_compression === null || job.output_compression === undefined ? '' : String(job.output_compression),
    quantity: clampQuantity(job.n),
    responseFormat: normalizeJobResponseFormat(job.response_format)
  };
}

export function galleryEntryToPromptForm(
  image: GalleryEntry,
  fallbackModel = DEFAULT_PROMPT_MODEL,
  currentApiPath: ApiPath = initialPromptFormState.apiPath
): PromptFormState {
  return {
    prompt: image.prompt || '',
    apiPath: normalizeApiPath(image.api_path, currentApiPath),
    size: image.size || initialPromptFormState.size,
    model: image.model || fallbackModel || initialPromptFormState.model,
    quality: normalizeJobQuality(image.quality),
    outputFormat: normalizeJobOutputFormat(image.output_format),
    outputCompression: image.output_compression === null || image.output_compression === undefined ? '' : String(image.output_compression),
    quantity: clampQuantity(image.n),
    responseFormat: normalizeJobResponseFormat(image.response_format)
  };
}

export function galleryEntryToPromptOnly(image: GalleryEntry, current: PromptFormState): PromptFormState {
  return {
    ...current,
    prompt: image.prompt || ''
  };
}
