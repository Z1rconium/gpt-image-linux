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
  promptLoadingCount: number;
  paramsLoadingCount: number;
  editPlanLoadingCount: number;
  galleryLoadingImageIds: string[];
  batchJob: AssistantGalleryBatchJobStatus | null;
};

const initialAssistantState: AssistantState = {
  promptLoading: false,
  paramsLoading: false,
  diagnoseLoadingJobId: null,
  editPlanLoading: false,
  galleryLoadingImageId: null,
  promptLoadingCount: 0,
  paramsLoadingCount: 0,
  editPlanLoadingCount: 0,
  galleryLoadingImageIds: [],
  batchJob: null
};

export function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError';
}

function createAssistantStore() {
  const { subscribe, update } = writable<AssistantState>(initialAssistantState);
  const operationControllers = new Map<string, AbortController>();

  function beginCounter(key: 'prompt' | 'params' | 'editPlan') {
    update((state) => {
      if (key === 'prompt') {
        const count = state.promptLoadingCount + 1;
        return { ...state, promptLoadingCount: count, promptLoading: count > 0 };
      }
      if (key === 'params') {
        const count = state.paramsLoadingCount + 1;
        return { ...state, paramsLoadingCount: count, paramsLoading: count > 0 };
      }
      const count = state.editPlanLoadingCount + 1;
      return { ...state, editPlanLoadingCount: count, editPlanLoading: count > 0 };
    });
  }

  function endCounter(key: 'prompt' | 'params' | 'editPlan') {
    update((state) => {
      if (key === 'prompt') {
        const count = Math.max(0, state.promptLoadingCount - 1);
        return { ...state, promptLoadingCount: count, promptLoading: count > 0 };
      }
      if (key === 'params') {
        const count = Math.max(0, state.paramsLoadingCount - 1);
        return { ...state, paramsLoadingCount: count, paramsLoading: count > 0 };
      }
      const count = Math.max(0, state.editPlanLoadingCount - 1);
      return { ...state, editPlanLoadingCount: count, editPlanLoading: count > 0 };
    });
  }

  function beginGalleryLoading(imageId: string) {
    update((state) => {
      const ids = [...state.galleryLoadingImageIds, imageId];
      return { ...state, galleryLoadingImageIds: ids, galleryLoadingImageId: ids.at(-1) ?? null };
    });
  }

  function endGalleryLoading(imageId: string) {
    update((state) => {
      const index = state.galleryLoadingImageIds.indexOf(imageId);
      const ids =
        index >= 0
          ? [
              ...state.galleryLoadingImageIds.slice(0, index),
              ...state.galleryLoadingImageIds.slice(index + 1)
            ]
          : state.galleryLoadingImageIds;
      return { ...state, galleryLoadingImageIds: ids, galleryLoadingImageId: ids.at(-1) ?? null };
    });
  }

  function operationSignal(key: string, signal?: AbortSignal): AbortSignal | undefined {
    if (signal) return signal;
    operationControllers.get(key)?.abort();
    const controller = new AbortController();
    operationControllers.set(key, controller);
    return controller.signal;
  }

  function clearOperationSignal(key: string, signal?: AbortSignal) {
    const controller = operationControllers.get(key);
    if (controller && controller.signal === signal) operationControllers.delete(key);
  }

  async function rewritePrompt(body: AssistantPromptRewriteRequest, signal?: AbortSignal) {
    const requestSignal = operationSignal('prompt', signal);
    beginCounter('prompt');
    try {
      return await apiFetch<AssistantPromptRewriteResponse>(
        '/api/assistant/prompt/rewrite',
        {
          method: 'POST',
          signal: requestSignal,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        },
        'rewriting prompt with AI Assistant'
      );
    } finally {
      endCounter('prompt');
      clearOperationSignal('prompt', requestSignal);
    }
  }

  async function checkPrompt(body: AssistantPromptCheckRequest, signal?: AbortSignal) {
    const requestSignal = operationSignal('prompt', signal);
    beginCounter('prompt');
    try {
      return await apiFetch<AssistantPromptCheckResponse>(
        '/api/assistant/prompt/check',
        {
          method: 'POST',
          signal: requestSignal,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        },
        'checking prompt with AI Assistant'
      );
    } finally {
      endCounter('prompt');
      clearOperationSignal('prompt', requestSignal);
    }
  }

  async function promptVariants(body: AssistantPromptVariantsRequest, signal?: AbortSignal) {
    const requestSignal = operationSignal('prompt', signal);
    beginCounter('prompt');
    try {
      return await apiFetch<AssistantPromptVariantsResponse>(
        '/api/assistant/prompt/variants',
        {
          method: 'POST',
          signal: requestSignal,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        },
        'creating prompt variants with AI Assistant'
      );
    } finally {
      endCounter('prompt');
      clearOperationSignal('prompt', requestSignal);
    }
  }

  async function recommendParams(body: AssistantRecommendParamsRequest, signal?: AbortSignal) {
    const requestSignal = operationSignal('params', signal);
    beginCounter('params');
    try {
      return await apiFetch<AssistantRecommendParamsResponse>(
        '/api/assistant/generate/recommend-params',
        {
          method: 'POST',
          signal: requestSignal,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        },
        'recommending generation parameters with AI Assistant'
      );
    } finally {
      endCounter('params');
      clearOperationSignal('params', requestSignal);
    }
  }

  async function diagnoseJob(jobId: string, body: AssistantJobDiagnoseRequest = { include_prompt: false }, signal?: AbortSignal) {
    const requestSignal = operationSignal(`diagnose:${jobId}`, signal);
    update((state) => ({ ...state, diagnoseLoadingJobId: jobId }));
    try {
      return await apiFetch<AssistantJobDiagnoseResponse>(
        `/api/assistant/jobs/${encodeURIComponent(jobId)}/diagnose`,
        {
          method: 'POST',
          signal: requestSignal,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        },
        'diagnosing job with AI Assistant'
      );
    } finally {
      update((state) => ({ ...state, diagnoseLoadingJobId: null }));
      clearOperationSignal(`diagnose:${jobId}`, requestSignal);
    }
  }

  async function planEdit(body: AssistantEditPlanRequest, signal?: AbortSignal) {
    const requestSignal = operationSignal('editPlan', signal);
    beginCounter('editPlan');
    try {
      return await apiFetch<AssistantEditPlanResponse>(
        '/api/assistant/edit/plan',
        {
          method: 'POST',
          signal: requestSignal,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        },
        'planning edit with AI Assistant'
      );
    } finally {
      endCounter('editPlan');
      clearOperationSignal('editPlan', requestSignal);
    }
  }

  async function describeGalleryImage(imageId: string, signal?: AbortSignal) {
    const requestSignal = operationSignal(`gallery:describe:${imageId}`, signal);
    beginGalleryLoading(imageId);
    try {
      return await apiFetch<AssistantGalleryImageResponse>(
        `/api/assistant/gallery/${encodeURIComponent(imageId)}/describe`,
        { method: 'POST', signal: requestSignal },
        'describing gallery image with AI Assistant'
      );
    } finally {
      endGalleryLoading(imageId);
      clearOperationSignal(`gallery:describe:${imageId}`, requestSignal);
    }
  }

  async function promptFromGalleryImage(imageId: string, signal?: AbortSignal) {
    const requestSignal = operationSignal(`gallery:prompt:${imageId}`, signal);
    beginGalleryLoading(imageId);
    try {
      return await apiFetch<AssistantGalleryImageResponse>(
        `/api/assistant/gallery/${encodeURIComponent(imageId)}/prompt`,
        { method: 'POST', signal: requestSignal },
        'reverse prompting gallery image with AI Assistant'
      );
    } finally {
      endGalleryLoading(imageId);
      clearOperationSignal(`gallery:prompt:${imageId}`, requestSignal);
    }
  }

  async function analyzeGalleryImage(imageId: string, signal?: AbortSignal) {
    const requestSignal = operationSignal(`gallery:analyze:${imageId}`, signal);
    beginGalleryLoading(imageId);
    try {
      return await apiFetch<AssistantGalleryImageResponse>(
        `/api/assistant/gallery/${encodeURIComponent(imageId)}/analyze`,
        { method: 'POST', signal: requestSignal },
        'analyzing gallery image with AI Assistant'
      );
    } finally {
      endGalleryLoading(imageId);
      clearOperationSignal(`gallery:analyze:${imageId}`, requestSignal);
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
    for (const controller of operationControllers.values()) controller.abort();
    operationControllers.clear();
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
