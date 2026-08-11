import { get } from 'svelte/store';
import type { AssistantGalleryBatchJobStatus } from '$lib/api/types/assistant';
import { t } from '$lib/i18n';
import { waitForGalleryJob } from '$lib/stores/galleryDeferredActions';

export async function waitForGalleryAnalysis(
  initialJob: AssistantGalleryBatchJobStatus,
  onProgress: (job: AssistantGalleryBatchJobStatus) => void
) {
  onProgress(initialJob);
  if (initialJob.status === 'success') return initialJob;
  if (initialJob.status === 'error') {
    throw new Error(initialJob.error || initialJob.message || get(t).messages.requestFailed);
  }
  return waitForGalleryJob<AssistantGalleryBatchJobStatus>(
    {
      eventsUrl: `/api/assistant/gallery/batch/analyze/${encodeURIComponent(initialJob.job_id)}/events`,
      eventNames: ['analysis']
    },
    onProgress
  );
}
