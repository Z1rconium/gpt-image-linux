import { get, writable } from 'svelte/store';
import { apiFetch } from '$lib/api/client';
import { t } from '$lib/i18n';
import { MAX_EDIT_SOURCE_IMAGES, editSourceCount, editSourceStore, type EditSourceState } from '$lib/stores/editSource';
import type { ApiPath, ResponseFormatDefault } from '$lib/api/types/common';
import type { GenerateRequestBody } from '$lib/api/types/generation';
import type { GenerateJobResponse, GenerateJobStatus } from '$lib/api/types/jobs';

export type PreviewState = {
  loading: boolean;
  error: string;
  job: GenerateJobStatus | null;
  imageUrl: string;
  filename: string;
  prompt: string;
};

export type PromptFormState = {
  prompt: string;
  apiPath: ApiPath;
  size: string;
  model: string;
  quality: GenerateRequestBody['quality'];
  outputFormat: GenerateRequestBody['output_format'];
  outputCompression: string;
  quantity: number | string;
  responseFormat: ResponseFormatDefault;
};

export const DEFAULT_QUANTITY = 1;

const initialPreviewState: PreviewState = {
  loading: false,
  error: '',
  job: null,
  imageUrl: '',
  filename: '',
  prompt: ''
};

export const DEFAULT_PROMPT_MODEL = 'gpt-image-2';

export const initialPromptFormState: PromptFormState = {
  prompt: '',
  apiPath: '/v1/images/generations',
  size: 'auto',
  model: DEFAULT_PROMPT_MODEL,
  quality: 'auto',
  outputFormat: 'png',
  outputCompression: '',
  quantity: DEFAULT_QUANTITY,
  responseFormat: 'url'
};

function buildRequestBody(form: PromptFormState): GenerateRequestBody {
  const quantity = form.quantity === '' ? DEFAULT_QUANTITY : Math.min(Math.max(Number(form.quantity) || DEFAULT_QUANTITY, 1), 10);
  const body: GenerateRequestBody = {
    prompt: form.prompt.trim(),
    size: form.size,
    model: form.model.trim(),
    n: quantity,
    quality: form.quality,
    output_format: form.outputFormat,
    output_compression: null,
    response_format: form.responseFormat ? (form.responseFormat as 'url' | 'b64_json') : null,
    api_path: form.apiPath
  };

  if (form.outputFormat !== 'png' && form.outputCompression !== '') {
    body.output_compression = Math.min(Math.max(Number(form.outputCompression), 0), 100);
  }

  return body;
}

function createPreviewStore() {
  const { subscribe, set, update } = writable<PreviewState>(initialPreviewState);
  let lastRequest: GenerateRequestBody | null = null;
  let lastAction: 'generate' | 'edit' = 'generate';

  function setPreview(next: PreviewState) {
    set(next);
  }

  function setError(message: string) {
    update((current) => ({ ...current, loading: false, error: message }));
  }

  function setSubmissionError(error: unknown) {
    const message = error instanceof Error ? error.message : get(t).messages.requestFailed;
    update((current) => ({ ...current, loading: false, error: message, job: null }));
  }

  function clearPreview(closeActiveJobSource?: () => void) {
    closeActiveJobSource?.();
    set(initialPreviewState);
  }

  async function generateImage(
    form: PromptFormState,
    makeQueuedPreview: (prompt: string, operation: NonNullable<GenerateJobResponse['operation']>) => PreviewState,
    trackJob: (jobId: string) => void,
    loadJobs?: () => Promise<void>
  ) {
    const body = buildRequestBody(form);
    if (!body.prompt) {
      setError(get(t).messages.promptRequired);
      return;
    }
    lastRequest = body;
    lastAction = 'generate';
    set(makeQueuedPreview(body.prompt, 'generation'));

    try {
      const job = await apiFetch<GenerateJobResponse>(
        '/api/generate',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        },
        'starting image generation'
      );
      trackJob(job.job_id);
      if (loadJobs) await loadJobs();
    } catch (error) {
      setSubmissionError(error);
    }
  }

  async function editImage(
    form: PromptFormState,
    editSource: EditSourceState,
    makeQueuedPreview: (prompt: string, operation: NonNullable<GenerateJobResponse['operation']>) => PreviewState,
    trackJob: (jobId: string) => void,
    loadJobs?: () => Promise<void>
  ) {
    const sourceCount = editSourceCount(editSource);
    if (sourceCount === 0) {
      setError(get(t).messages.editSourceRequired);
      return;
    }
    if (sourceCount > MAX_EDIT_SOURCE_IMAGES) {
      setError(get(t).messages.editSourceLimit(MAX_EDIT_SOURCE_IMAGES));
      return;
    }

    const body = buildRequestBody(form);
    if (!body.prompt) {
      setError(get(t).messages.promptRequired);
      return;
    }
    lastRequest = body;
    lastAction = 'edit';
    set(makeQueuedPreview(body.prompt, 'edit'));

    const formData = new FormData();
    Object.entries(body).forEach(([key, value]) => {
      if (key === 'api_path') return;
      if (value !== null && value !== undefined && value !== '') {
        formData.append(key, String(value));
      }
    });

    let endpoint = '/api/edits';
    if (editSource.selectedGalleryImageId) {
      endpoint = `/api/edits/from-gallery/${encodeURIComponent(editSource.selectedGalleryImageId)}`;
    }
    const uploadFieldName = sourceCount > 1 ? 'image[]' : 'image';
    editSource.files.forEach((source) => {
      formData.append(uploadFieldName, source.file, source.file.name);
    });

    try {
      const job = await apiFetch<GenerateJobResponse>(
        endpoint,
        {
          method: 'POST',
          body: formData
        },
        'starting image edit'
      );
      trackJob(job.job_id);
      if (loadJobs) await loadJobs();
    } catch (error) {
      setSubmissionError(error);
    }
  }

  function regenerate(setForm: (form: PromptFormState) => void, generate: () => void, edit: () => void) {
    if (!lastRequest) return;
    setForm({
      prompt: lastRequest.prompt,
      apiPath: lastRequest.api_path || initialPromptFormState.apiPath,
      size: lastRequest.size,
      model: lastRequest.model,
      quantity: lastRequest.n,
      quality: lastRequest.quality,
      outputFormat: lastRequest.output_format,
      outputCompression: lastRequest.output_compression === null || lastRequest.output_compression === undefined ? '' : String(lastRequest.output_compression),
      responseFormat: lastRequest.response_format || ''
    });
    if (lastAction === 'edit') edit();
    else generate();
  }

  function cleanup() {
    editSourceStore.cleanup();
  }

  return {
    subscribe,
    setPreview,
    setError,
    clearPreview,
    generateImage,
    editImage,
    regenerate,
    cleanup
  };
}

export const previewStore = createPreviewStore();
