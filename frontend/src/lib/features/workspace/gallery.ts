import type { AssistantGalleryBatchJobStatus } from '$lib/api/types/assistant';

export async function waitForGalleryAnalysis(
  initialJob: AssistantGalleryBatchJobStatus,
  loadJob: (jobId: string) => Promise<AssistantGalleryBatchJobStatus>,
  onProgress: (job: AssistantGalleryBatchJobStatus) => void,
  pollIntervalMs = 1200
) {
  let currentJob = initialJob;
  while (currentJob.status === 'queued' || currentJob.status === 'running') {
    onProgress(currentJob);
    await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
    currentJob = await loadJob(initialJob.job_id);
  }
  return currentJob;
}
