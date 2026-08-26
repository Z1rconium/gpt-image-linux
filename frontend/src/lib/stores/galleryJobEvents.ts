import { get } from 'svelte/store';
import { openJsonEventSource } from '$lib/api/events';
import { t } from '$lib/i18n';

const GALLERY_JOB_EVENT_NETWORK_TIMEOUT_MS = 30_000;

export type GalleryWaitOptions = {
  eventsUrl: string;
  eventNames: string[];
  signal?: AbortSignal;
  terminalStatuses?: string[];
  resolveErrorStatuses?: boolean;
};

export function abortError() {
  return new DOMException('Gallery operation cancelled', 'AbortError');
}

export function waitForGalleryJob<T extends { status: string; error?: string | null; message?: string | null }>(
  options: GalleryWaitOptions,
  onJob: (job: T) => void
): Promise<T> {
  return new Promise((resolve, reject) => {
    const terminalStatuses = new Set(options.terminalStatuses || ['success', 'error']);
    let settled = false;
    let source: EventSource | null = null;
    let networkTimer: ReturnType<typeof setTimeout> | null = null;

    const clearNetworkTimer = () => {
      if (networkTimer) clearTimeout(networkTimer);
      networkTimer = null;
    };

    const settle = (callback: () => void) => {
      if (settled) return;
      settled = true;
      clearNetworkTimer();
      source?.close();
      options.signal?.removeEventListener('abort', handleAbort);
      callback();
    };

    const handleAbort = () => {
      settle(() => reject(abortError()));
    };

    if (options.signal?.aborted) {
      reject(abortError());
      return;
    }

    options.signal?.addEventListener('abort', handleAbort, { once: true });
    source = openJsonEventSource<T>(
      options.eventsUrl,
      {
        onEvent: ({ data }) => {
          clearNetworkTimer();
          onJob(data);
          if (terminalStatuses.has(data.status) && (data.status !== 'error' || options.resolveErrorStatuses)) {
            settle(() => resolve(data));
          } else if (data.status === 'error') {
            settle(() => reject(new Error(data.error || data.message || get(t).messages.requestFailed)));
          }
        },
        onNetworkError: () => {
          if (settled || networkTimer) return;
          networkTimer = setTimeout(() => {
            settle(() => reject(networkTimeoutError()));
          }, GALLERY_JOB_EVENT_NETWORK_TIMEOUT_MS);
        },
        onError: (error) => {
          settle(() => reject(error instanceof Error ? error : new Error(get(t).messages.requestFailed)));
        }
      },
      options.eventNames
    );
    source.onopen = () => {
      clearNetworkTimer();
    };
  });
}

function networkTimeoutError() {
  return new Error(get(t).messages.requestFailed);
}
