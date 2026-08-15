import type { GenerateJobStatus } from '$lib/api/types/jobs';
import { isFailureJobStatus } from '$lib/utils/jobs';

function safeAssetUrl(url?: string | null) {
  if (!url) return null;
  return url.startsWith('/api/') || /^https?:\/\//.test(url) ? url : null;
}

export function imageUrl(filename: string, url?: string | null) {
  const safeUrl = safeAssetUrl(url);
  if (safeUrl) return safeUrl;
  return `/api/image/${encodeURIComponent(filename)}`;
}

export function thumbnailUrl(filename: string, url?: string | null) {
  const safeUrl = safeAssetUrl(url);
  return safeUrl || `/api/thumb/${encodeURIComponent(filename)}`;
}

export function downloadUrl(filename: string) {
  return `/api/download/${encodeURIComponent(filename)}`;
}

export function filenameFromImageUrl(url: string) {
  return decodeURIComponent(url.split('/').pop() || '');
}

export function formatBytes(totalBytes: number) {
  if (!Number.isFinite(totalBytes) || totalBytes <= 0) return '';
  return `${(totalBytes / (1024 * 1024)).toFixed(1)} MB`;
}

const dateTimeFormatters = new Map<string, Intl.DateTimeFormat>();

function getLocalDateTimeFormatter(): Intl.DateTimeFormat {
  const locale = typeof navigator !== 'undefined' && navigator.language ? navigator.language : undefined;
  const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  const cacheKey = `${locale || 'default'}|${timeZone}`;
  const existing = dateTimeFormatters.get(cacheKey);
  if (existing) return existing;

  const formatter = new Intl.DateTimeFormat(locale, {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false
    });
  dateTimeFormatters.set(cacheKey, formatter);
  return formatter;
}

export function formatLocalTime(value: string | null | undefined) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '-';

  const parts = getLocalDateTimeFormatter().formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value || '';

  return `${part('year')}-${part('month')}-${part('day')} ${part('hour')}:${part('minute')}:${part('second')}`;
}

export const formatBeijingTime = formatLocalTime;

type ImageSizeLike = {
  size?: string | null;
  image_width?: number | null;
  image_height?: number | null;
};

export function displayImageSize(image: ImageSizeLike | null | undefined) {
  if (image?.image_width && image?.image_height) return `${image.image_width}x${image.image_height}`;
  return image?.size || '-';
}

export function stageLabel(job: GenerateJobStatus | null, labels?: Record<string, string>) {
  if (!job?.stage) return '';
  const translated = labels?.[job.stage];
  const legacyProgressMatch = job.message?.match(/\((\d+\/\d+)(?: completed)?\)\s*$/);
  const messageWithoutProgress = legacyProgressMatch
    ? job.message?.slice(0, legacyProgressMatch.index).trim()
    : job.message;
  const baseLabel = translated || messageWithoutProgress || job.stage.replaceAll('_', ' ');
  const completedCount = job.completed_count;
  const totalCount = job.n;
  if (
    Number.isInteger(completedCount) &&
    Number.isInteger(totalCount) &&
    Number(completedCount) >= 0 &&
    Number(totalCount) > 0
  ) {
    return `${baseLabel} (${completedCount}/${totalCount})`;
  }

  const legacyProgress = legacyProgressMatch?.[1];
  if (legacyProgress) return `${baseLabel} (${legacyProgress})`;

  const failureMessage = jobFailureMessage(job);
  return failureMessage || baseLabel;
}

export function jobFailureMessage(job: GenerateJobStatus | null | undefined, fallback = '') {
  if (!job || !isFailureJobStatus(job.status)) return '';
  return String(job.error || job.message || fallback || '').trim();
}

export function statusLabel(status: string | null | undefined, labels?: Record<string, string>) {
  if (!status) return '';
  return labels?.[status] || status;
}

export function operationLabel(operation: string | null | undefined, labels?: Record<string, string>) {
  if (!operation) return labels?.generation || 'generation';
  return labels?.[operation] || operation;
}

export async function copyText(text: string) {
  if (!text) return;
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      // Fall through for browsers/test contexts without clipboard permission.
    }
  }

  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', '');
  textarea.style.position = 'fixed';
  textarea.style.left = '-9999px';
  textarea.style.top = '0';
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand('copy');
  textarea.remove();
}
