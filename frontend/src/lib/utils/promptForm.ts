import type { ApiPath, ResponseFormatDefault } from '$lib/api/types/common';
import type { GalleryEntry } from '$lib/api/types/gallery';
import type { GenerateJobStatus } from '$lib/api/types/jobs';
import { DEFAULT_PROMPT_MODEL, DEFAULT_QUANTITY, initialPromptFormState, type PromptFormState } from '$lib/stores/preview';
import { displayImageSize } from '$lib/utils/format';

const GENERATION_API_PATHS: ApiPath[] = ['/v1/images/generations', '/v1/responses', '/v1/chat/completions'];
export const RESPONSE_FORMAT_OPTIONS: ResponseFormatDefault[] = ['', 'url', 'b64_json'];

export function normalizeApiPath(value: string | null | undefined, fallback: ApiPath = initialPromptFormState.apiPath): ApiPath {
  return GENERATION_API_PATHS.includes(value as ApiPath) ? (value as ApiPath) : fallback;
}

function normalizeJobQuality(value: string | null | undefined): PromptFormState['quality'] {
  if (value === 'auto' || value === 'low' || value === 'medium' || value === 'high') return value;
  return initialPromptFormState.quality;
}

function normalizeJobOutputFormat(value: string | null | undefined): PromptFormState['outputFormat'] {
  if (value === 'png' || value === 'jpeg' || value === 'webp') return value;
  return initialPromptFormState.outputFormat;
}

function normalizeBackground(value: string | null | undefined): PromptFormState['background'] {
  if (value === 'opaque' || value === 'transparent') return value;
  return 'auto';
}

export function normalizeResponseFormat(value: string | null | undefined, fallback: ResponseFormatDefault = ''): ResponseFormatDefault {
  return RESPONSE_FORMAT_OPTIONS.includes(value as ResponseFormatDefault) ? (value as ResponseFormatDefault) : fallback;
}

function normalizeJobResponseFormat(value: string | null | undefined): PromptFormState['responseFormat'] {
  return normalizeResponseFormat(value);
}

function clampQuantity(value: number | string | null | undefined): number {
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
    background: normalizeBackground(job.background),
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
    background: normalizeBackground(image.background),
    outputCompression: image.output_compression === null || image.output_compression === undefined ? '' : String(image.output_compression),
    quantity: clampQuantity(image.n),
    responseFormat: normalizeJobResponseFormat(image.response_format)
  };
}

export function galleryEntryToEditForm(
  image: GalleryEntry,
  fallbackModel = DEFAULT_PROMPT_MODEL,
  currentApiPath: ApiPath = initialPromptFormState.apiPath
): PromptFormState {
  const size = image.size || displayImageSize(image);
  return {
    prompt: image.prompt || '',
    apiPath: currentApiPath,
    size: size === '-' ? initialPromptFormState.size : size,
    model: image.model || fallbackModel || initialPromptFormState.model,
    quality: normalizeJobQuality(image.quality),
    outputFormat: normalizeJobOutputFormat(image.output_format),
    background: normalizeBackground(image.background),
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
