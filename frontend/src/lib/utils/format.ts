import type { GenerateJobStatus } from '$lib/api/types';
import type { GalleryEntry } from '$lib/api/types';
import { isFailureJobStatus } from '$lib/utils/jobs';

export function imageUrl(filename: string) {
  return `/api/image/${encodeURIComponent(filename)}`;
}

export function thumbnailUrl(filename: string, url?: string | null) {
  return url || `/api/thumb/${encodeURIComponent(filename)}`;
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

let beijingTimeFormatter: Intl.DateTimeFormat | null = null;

function getBeijingTimeFormatter(): Intl.DateTimeFormat {
  if (!beijingTimeFormatter) {
    beijingTimeFormatter = new Intl.DateTimeFormat('zh-CN', {
      timeZone: 'Asia/Shanghai',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false
    });
  }
  return beijingTimeFormatter;
}

export function formatBeijingTime(value: string | null | undefined) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '-';

  const parts = getBeijingTimeFormatter().formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value || '';

  return `${part('year')}-${part('month')}-${part('day')} ${part('hour')}:${part('minute')}:${part('second')}`;
}

type ImageSizeLike = {
  size?: string | null;
  image_width?: number | null;
  image_height?: number | null;
};

export function displayImageSize(image: ImageSizeLike | null | undefined) {
  if (image?.image_width && image?.image_height) return `${image.image_width}x${image.image_height}`;
  return image?.size || '-';
}

export function galleryImageSize(image: ImageSizeLike) {
  return displayImageSize(image);
}

export function stageLabel(job: GenerateJobStatus | null, labels?: Record<string, string>) {
  if (!job?.stage) return '';
  const failureMessage = jobFailureMessage(job);
  if (failureMessage) return failureMessage;

  const translated = labels?.[job.stage];
  if (!translated) return job.message || job.stage.replaceAll('_', ' ');

  const progressSuffix = job.message?.match(/\(\d+\/\d+\)$/)?.[0];
  return progressSuffix ? `${translated} ${progressSuffix}` : translated;
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
