import { writable } from 'svelte/store';
import { apiFetch } from '$lib/api/client';
import type {
  AssistantEditPlanRequest,
  AssistantEditPlanResponse,
  AssistantGalleryBatchJobStatus,
  AssistantGalleryBatchRequest,
  AssistantGalleryImageResponse,
  AssistantGalleryMetadataResponse,
  AssistantJobDiagnoseRequest,
  AssistantJobDiagnoseResponse,
  AssistantPromptCheckRequest,
  AssistantPromptCheckResponse,
  AssistantPromptRewriteRequest,
  AssistantPromptRewriteResponse,
  AssistantPromptVariantsRequest,
  AssistantPromptVariantsResponse,
  AssistantRecommendParamsRequest,
  AssistantRecommendParamsResponse
} from '$lib/api/types';

export type AssistantState = {
  promptLoading: boolean;
  paramsLoading: boolean;
  diagnoseLoadingJobId: string | null;
  editPlanLoading: boolean;
  galleryLoadingImageId: string | null;
  batchJob: AssistantGalleryBatchJobStatus | null;
};

const initialAssistantState: AssistantState = {
  promptLoading: false,
  paramsLoading: false,
  diagnoseLoadingJobId: null,
  editPlanLoading: false,
  galleryLoadingImageId: null,
  batchJob: null
};

function createAssistantStore() {
  const { subscribe, update } = writable<AssistantState>(initialAssistantState);

  async function rewritePrompt(body: AssistantPromptRewriteRequest, signal?: AbortSignal) {
    update((state) => ({ ...state, promptLoading: true }));
    try {
      return await apiFetch<AssistantPromptRewriteResponse>(
        '/api/assistant/prompt/rewrite',
        {
          method: 'POST',
          signal,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        },
        'rewriting prompt with AI Assistant'
      );
    } finally {
      update((state) => ({ ...state, promptLoading: false }));
    }
  }

  async function checkPrompt(body: AssistantPromptCheckRequest, signal?: AbortSignal) {
    update((state) => ({ ...state, promptLoading: true }));
    try {
      return await apiFetch<AssistantPromptCheckResponse>(
        '/api/assistant/prompt/check',
        {
          method: 'POST',
          signal,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        },
        'checking prompt with AI Assistant'
      );
    } finally {
      update((state) => ({ ...state, promptLoading: false }));
    }
  }

  async function promptVariants(body: AssistantPromptVariantsRequest, signal?: AbortSignal) {
    update((state) => ({ ...state, promptLoading: true }));
    try {
      return await apiFetch<AssistantPromptVariantsResponse>(
        '/api/assistant/prompt/variants',
        {
          method: 'POST',
          signal,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        },
        'creating prompt variants with AI Assistant'
      );
    } finally {
      update((state) => ({ ...state, promptLoading: false }));
    }
  }

  async function recommendParams(body: AssistantRecommendParamsRequest, signal?: AbortSignal) {
    update((state) => ({ ...state, paramsLoading: true }));
    try {
      return await apiFetch<AssistantRecommendParamsResponse>(
        '/api/assistant/generate/recommend-params',
        {
          method: 'POST',
          signal,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        },
        'recommending generation parameters with AI Assistant'
      );
    } finally {
      update((state) => ({ ...state, paramsLoading: false }));
    }
  }

  async function diagnoseJob(jobId: string, body: AssistantJobDiagnoseRequest = { include_prompt: true }) {
    update((state) => ({ ...state, diagnoseLoadingJobId: jobId }));
    try {
      return await apiFetch<AssistantJobDiagnoseResponse>(
        `/api/assistant/jobs/${encodeURIComponent(jobId)}/diagnose`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        },
        'diagnosing job with AI Assistant'
      );
    } finally {
      update((state) => ({ ...state, diagnoseLoadingJobId: null }));
    }
  }

  async function planEdit(body: AssistantEditPlanRequest, signal?: AbortSignal) {
    update((state) => ({ ...state, editPlanLoading: true }));
    try {
      return await apiFetch<AssistantEditPlanResponse>(
        '/api/assistant/edit/plan',
        {
          method: 'POST',
          signal,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        },
        'planning edit with AI Assistant'
      );
    } finally {
      update((state) => ({ ...state, editPlanLoading: false }));
    }
  }

  async function describeGalleryImage(imageId: string) {
    update((state) => ({ ...state, galleryLoadingImageId: imageId }));
    try {
      return await apiFetch<AssistantGalleryImageResponse>(
        `/api/assistant/gallery/${encodeURIComponent(imageId)}/describe`,
        { method: 'POST' },
        'describing gallery image with AI Assistant'
      );
    } finally {
      update((state) => ({ ...state, galleryLoadingImageId: null }));
    }
  }

  async function promptFromGalleryImage(imageId: string) {
    update((state) => ({ ...state, galleryLoadingImageId: imageId }));
    try {
      return await apiFetch<AssistantGalleryImageResponse>(
        `/api/assistant/gallery/${encodeURIComponent(imageId)}/prompt`,
        { method: 'POST' },
        'reverse prompting gallery image with AI Assistant'
      );
    } finally {
      update((state) => ({ ...state, galleryLoadingImageId: null }));
    }
  }

  async function analyzeGalleryImage(imageId: string) {
    update((state) => ({ ...state, galleryLoadingImageId: imageId }));
    try {
      return await apiFetch<AssistantGalleryImageResponse>(
        `/api/assistant/gallery/${encodeURIComponent(imageId)}/analyze`,
        { method: 'POST' },
        'analyzing gallery image with AI Assistant'
      );
    } finally {
      update((state) => ({ ...state, galleryLoadingImageId: null }));
    }
  }

  async function loadGalleryMetadata(imageId: string) {
    return apiFetch<AssistantGalleryMetadataResponse>(
      `/api/assistant/gallery/${encodeURIComponent(imageId)}/metadata`,
      {},
      'loading AI gallery metadata'
    );
  }

  async function batchAnalyzeGallery(body: AssistantGalleryBatchRequest) {
    const job = await apiFetch<AssistantGalleryBatchJobStatus>(
      '/api/assistant/gallery/batch/analyze',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      },
      'starting gallery AI analysis'
    );
    update((state) => ({ ...state, batchJob: job }));
    return job;
  }

  async function loadBatchAnalyzeJob(jobId: string) {
    const job = await apiFetch<AssistantGalleryBatchJobStatus>(
      `/api/assistant/gallery/batch/analyze/${encodeURIComponent(jobId)}`,
      {},
      'loading gallery AI analysis job'
    );
    update((state) => ({ ...state, batchJob: job }));
    return job;
  }

  function reset() {
    update(() => ({ ...initialAssistantState }));
  }

  return {
    subscribe,
    rewritePrompt,
    checkPrompt,
    promptVariants,
    recommendParams,
    diagnoseJob,
    planEdit,
    describeGalleryImage,
    promptFromGalleryImage,
    analyzeGalleryImage,
    loadGalleryMetadata,
    batchAnalyzeGallery,
    loadBatchAnalyzeJob,
    reset
  };
}

export const assistantStore = createAssistantStore();
