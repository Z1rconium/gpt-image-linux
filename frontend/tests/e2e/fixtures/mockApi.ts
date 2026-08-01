import { expect, type Page } from '@playwright/test';

const PNG_BYTES = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4//8/AwAI/AL+X1N6AAAAAElFTkSuQmCC',
  'base64'
);

type GalleryImageFixture = {
  id: string;
  prompt: string;
  size: string;
  filename: string;
  image_url?: string;
  thumbnail_url: string;
  thumbnail_status?: 'ready' | 'queued' | 'missing';
  created_at: string;
  completed_at: string;
  image_width: number;
  image_height: number;
  model: string;
  quality?: string;
  output_format?: string;
  output_compression?: number | null;
  response_format?: string | null;
  n?: number | null;
  api_path?: string | null;
  api_preset_name: string;
  duration: string;
  favorite: boolean;
  bytes: number;
};

type PromptSnippetFixture = {
  id: string;
  title: string;
  prompt: string;
  favorite: boolean;
  created_at: string;
  updated_at: string;
};

type MockOptions = {
  authenticated?: boolean;
  editUploadFailure?: boolean;
  galleryImages?: GalleryImageFixture[];
  promptSnippets?: PromptSnippetFixture[];
  settings?: Record<string, unknown>;
  generatedJob?: unknown;
  runningJobs?: unknown[];
  historyJobs?: unknown[];
  language?: 'en' | 'zh-CN' | null;
  reversePrompt?: string;
  optimizedPrompts?: string[];
  optimizeFailureAt?: number;
  optimizeDelayMs?: number;
};

const baseGalleryImages: GalleryImageFixture[] = [
  {
    id: 'img-1',
    prompt: 'First gallery image',
    size: '1024x1024',
    filename: 'img-1.png',
    thumbnail_url: '/api/thumb/img-1.png',
    created_at: '2026-05-18T12:00:00Z',
    completed_at: '2026-05-18T20:00:01+08:00',
    image_width: 1,
    image_height: 1,
    model: 'gpt-image-2',
    quality: 'high',
    output_format: 'webp',
    output_compression: 80,
    response_format: 'url',
    n: 2,
    api_path: '/v1/responses',
    api_preset_name: 'Default',
    duration: '1.00s',
    favorite: false,
    bytes: 68
  },
  {
    id: 'img-2',
    prompt: 'Second gallery image',
    size: '1536x1024',
    filename: 'img-2.png',
    thumbnail_url: '/api/thumb/img-2.png',
    created_at: '2026-05-18T12:01:00Z',
    completed_at: '2026-05-18T20:01:01+08:00',
    image_width: 1,
    image_height: 1,
    model: 'gpt-image-2',
    quality: 'auto',
    output_format: 'png',
    output_compression: null,
    response_format: 'url',
    n: 1,
    api_path: '/v1/images/edits',
    api_preset_name: 'Default',
    duration: '1.10s',
    favorite: true,
    bytes: 68
  }
];

const settingsResponse = {
  active_preset_id: 'default',
  api_url: 'https://api.example.com',
  api_key_masked: '********',
  has_api_key: true,
  api_key_source: 'stored',
  api_path: '/v1/images/generations',
  default_model: 'preset-default-model',
  default_response_format: 'url',
  has_upstream_socks5_proxy: false,
  upstream_socks5_proxy_masked: '',
  has_webhook_url: true,
  webhook_url_masked: 'https://hooks.example.com/***',
  prompt_optimizer: {
    enabled: true,
    api_url: 'https://example.com/v1/chat/completions',
    model: 'gpt-4o-mini',
    timeout_seconds: 60,
    api_key_masked: '********',
    has_api_key: true,
    api_key_source: 'stored',
    api_key_env_var: null
  },
  ai_assistant: {
    enabled: true,
    api_url: 'https://example.com',
    model: 'gpt-4o-mini',
    vision_model: 'gpt-4o-mini',
    timeout_seconds: 60,
    api_path: '/v1/chat/completions',
    api_key_masked: '********',
    has_api_key: true,
    api_key_source: 'stored',
    api_key_env_var: null
  },
  r2_backup: {
    enabled: false,
    endpoint_url: '',
    bucket_name: '',
    region: 'auto',
    key_prefix: 'gallery/',
    sync_interval_hours: 0,
    access_key_id_masked: '********',
    has_access_key_id: false,
    access_key_id_source: 'empty',
    access_key_id_env_var: null,
    secret_access_key_masked: '********',
    has_secret_access_key: false,
    secret_access_key_source: 'empty',
    secret_access_key_env_var: null
  },
  presets: [
    {
      id: 'default',
      name: 'Default',
      api_url: 'https://api.example.com',
      api_key_masked: '********',
      has_api_key: true,
      api_key_source: 'stored',
      api_path: '/v1/images/generations',
      default_model: 'preset-default-model',
      default_response_format: 'url'
    }
  ]
};

const overallConfigResponse = {
  restart_required_names: [],
  items: [
    {
      name: 'ENABLE_METRICS',
      type: 'bool',
      group: 'Observability',
      description: 'Enable /api/metrics.',
      value: false,
      value_masked: 'false',
      env_value_masked: 'false',
      override_value_masked: null,
      source: 'env',
      is_env_set: true,
      has_override: false,
      secret: false,
      hot_reload: true,
      restart_required: false,
      build_only: false,
      updated_at: '2026-05-18T12:00:00Z',
      override_updated_at: null
    },
    {
      name: 'WEBHOOK_SIGNING_SECRET',
      type: 'secret',
      group: 'Webhooks',
      description: 'Webhook signing secret.',
      value: '********',
      value_masked: '********',
      env_value_masked: '',
      override_value_masked: '********',
      source: 'override',
      is_env_set: false,
      has_override: true,
      secret: true,
      hot_reload: true,
      restart_required: false,
      build_only: false,
      updated_at: '2026-05-18T12:00:00Z',
      override_updated_at: '2026-05-18T12:00:00Z'
    },
    {
      name: 'ACCESS_KEY_COOKIE_NAME',
      type: 'string',
      group: 'Access / Security',
      description: 'Access cookie name.',
      value: 'gpt_image_access',
      value_masked: 'gpt_image_access',
      env_value_masked: 'gpt_image_access',
      override_value_masked: 'custom_access',
      source: 'override',
      is_env_set: false,
      has_override: true,
      secret: false,
      hot_reload: false,
      restart_required: true,
      build_only: false,
      updated_at: '2026-05-18T12:00:00Z',
      override_updated_at: null
    },
    {
      name: 'PYTHON_BASE_IMAGE',
      type: 'string',
      group: 'Docker Build',
      description: 'Python base image for Docker builds.',
      value: 'python:3.11-slim',
      value_masked: 'python:3.11-slim',
      env_value_masked: 'python:3.11-slim',
      override_value_masked: null,
      source: 'default',
      is_env_set: false,
      has_override: false,
      secret: false,
      hot_reload: false,
      restart_required: false,
      build_only: true,
      updated_at: '2026-05-18T12:00:00Z',
      override_updated_at: null
    }
  ]
};

const basePromptSnippets: PromptSnippetFixture[] = [
  {
    id: 'snippet-1',
    title: 'Portrait base',
    prompt: 'cinematic portrait prompt',
    favorite: false,
    created_at: '2026-05-18T12:00:00Z',
    updated_at: '2026-05-18T12:00:00Z'
  },
  {
    id: 'snippet-2',
    title: 'Product hero',
    prompt: 'studio product photography',
    favorite: true,
    created_at: '2026-05-18T12:01:00Z',
    updated_at: '2026-05-18T12:01:00Z'
  }
];

function json(body: unknown, status = 200) {
  return {
    status,
    contentType: 'application/json',
    body: JSON.stringify(body)
  };
}

function galleryCursor(image: GalleryImageFixture | undefined) {
  if (!image) return null;
  return Buffer.from(JSON.stringify({ sort_seq: 1, id: image.id }), 'utf8').toString('base64url');
}

function galleryResponse(
  images = baseGalleryImages,
  includeTotalBytes = false,
  requestedPage = 1,
  includeCounts = true,
  includeFilterOptions = true
) {
  const pageSize = 9;
  const totalPages = Math.max(Math.ceil(images.length / pageSize), 1);
  const parsedPage = Number.isFinite(requestedPage) ? requestedPage : 1;
  const page = Math.min(Math.max(parsedPage, 1), totalPages);
  const pageImages = images.slice((page - 1) * pageSize, page * pageSize);

  return {
    total: includeCounts ? images.length : 0,
    total_bytes: includeTotalBytes ? images.reduce((sum, image) => sum + image.bytes, 0) : 0,
    page,
    page_size: pageSize,
    total_pages: includeCounts ? totalPages : Math.max(page + (page < totalPages ? 1 : 0), 1),
    has_prev: page > 1,
    has_next: page < totalPages,
    next_cursor: page < totalPages ? galleryCursor(pageImages[pageImages.length - 1]) : null,
    prev_cursor: page > 1 ? galleryCursor(pageImages[0]) : null,
    images: pageImages,
    filter_options: includeFilterOptions
      ? {
          models: ['gpt-image-2'],
          presets: ['Default'],
          sizes: ['1024x1024', '1536x1024']
        }
      : {
          models: [],
          presets: [],
          sizes: []
        }
  };
}

type JobStatus = 'queued' | 'running' | 'success' | 'error' | 'cancelled' | 'interrupted' | 'upstream_error';

function job(jobId: string, prompt: string, status: JobStatus = 'success') {
  const stage =
    status === 'success'
      ? 'completed'
      : status === 'running' || status === 'queued'
        ? 'waiting_for_api'
        : status === 'upstream_error'
          ? 'generation_failed'
          : status;
  const message =
    status === 'success'
      ? 'Image generation completed'
      : status === 'running' || status === 'queued'
        ? 'Waiting for upstream API response'
        : status === 'upstream_error'
          ? 'Upstream API error'
          : status === 'interrupted'
            ? 'Job interrupted by server restart'
            : 'Generation job cancelled';
  const terminal = status !== 'running' && status !== 'queued';

  return {
    job_id: jobId,
    status,
    stage,
    message,
    operation: 'generation',
    image_id: 'img-1',
    image_url: '/api/image/img-1.png',
    images: [
      {
        image_id: 'img-1',
        image_url: '/api/image/img-1.png',
        filename: 'img-1.png',
        image_width: 1,
        image_height: 1
      }
    ],
    prompt,
    size: '1024x1024',
    created_at: '2026-05-18T12:00:00Z',
    updated_at: '2026-05-18T12:00:01Z',
    completed_at: terminal ? '2026-05-18T20:00:01+08:00' : null,
    image_width: 1,
    image_height: 1,
    model: 'gpt-image-2',
    duration: terminal ? '1.00s' : null,
    error: status === 'success' || status === 'running' || status === 'queued' ? null : message,
    stage_timings: {
      upstream_wait: 1.2,
      download_decode: 0.4,
      validate: 0.1,
      thumbnail: 0.2,
      db_insert: 0.3
    }
  };
}

function manyJobs(count: number) {
  return Array.from({ length: count }, (_, index) => job(`job-${index}`, `history prompt ${index}`, 'running'));
}

function isErrorJob(candidate: unknown) {
  if (!candidate || typeof candidate !== 'object') return false;
  const status = (candidate as { status?: unknown }).status;
  return status === 'error' || status === 'upstream_error';
}

function cloneSettings(settings: Record<string, unknown>) {
  return structuredClone(settings) as typeof settingsResponse;
}

function applyActivePresetFields(settings: typeof settingsResponse) {
  const active = settings.presets.find((preset) => preset.id === settings.active_preset_id) || settings.presets[0];
  if (!active) return settings;
  return {
    ...settings,
    active_preset_id: active.id,
    api_url: active.api_url,
    api_key_masked: active.api_key_masked,
    has_api_key: active.has_api_key,
    api_key_source: active.api_key_source,
    api_path: active.api_path,
    default_model: active.default_model,
    default_response_format: active.default_response_format
  };
}

function manyGalleryImages(count: number) {
  return Array.from({ length: count }, (_, index) => ({
    ...baseGalleryImages[index % baseGalleryImages.length],
    id: `paged-img-${index + 1}`,
    prompt: `Paged gallery image ${index + 1}`,
    filename: `paged-img-${index + 1}.png`,
    thumbnail_url: `/api/thumb/paged-img-${index + 1}.png`,
    thumbnail_status: 'ready' as const
  }));
}

async function mockApi(page: Page, options: MockOptions = {}) {
  let authenticated = options.authenticated ?? true;
  let galleryImages = [...(options.galleryImages ?? baseGalleryImages)];
  let promptSnippets = [...(options.promptSnippets ?? basePromptSnippets)];
  let promptSnippetCounter = promptSnippets.length + 1;
  let mockedSettings = cloneSettings(options.settings ?? settingsResponse);
  let mockedOverallConfig: any = structuredClone(overallConfigResponse);
  let optimizerSystemPrompt = 'Default optimizer system prompt';
  let selectionTokenSeq = 0;
  let imagePromptOptimizeCount = 0;
  const selectionTokens = new Map<string, { prompt: string; favorite?: boolean | null }>();
  const runningJobs = options.runningJobs ?? [];
  let historyJobs = options.historyJobs ?? [job('history-1', 'saved prompt')];
  const initialLanguage = options.language === undefined ? 'en' : options.language;

  function matchesGalleryFilters(image: GalleryImageFixture, filters: { prompt?: string; favorite?: boolean | null }) {
    const prompt = String(filters.prompt || '').trim().toLowerCase();
    if (prompt && !image.prompt.toLowerCase().includes(prompt)) return false;
    if (filters.favorite === true && !image.favorite) return false;
    if (filters.favorite === false && image.favorite) return false;
    return true;
  }

  function resolveBatchIds(body: { ids?: string[]; selection_token?: string }) {
    if (body.selection_token) {
      const filters = selectionTokens.get(body.selection_token) || { prompt: '' };
      return galleryImages.filter((image) => matchesGalleryFilters(image, filters)).map((image) => image.id);
    }
    return body.ids || [];
  }

  await page.addInitScript((languageValue: 'en' | 'zh-CN' | null) => {
    if (languageValue) {
      localStorage.setItem('gpt-image-panel-language', languageValue);
    } else {
      localStorage.removeItem('gpt-image-panel-language');
    }
    if (!sessionStorage.getItem('gpt-image-panel-theme-init')) {
      localStorage.removeItem('gpt-image-panel-theme');
      sessionStorage.setItem('gpt-image-panel-theme-init', '1');
    }
  }, initialLanguage);

  await page.route('**/*', async (route) => {
    const request = route.request();
    const url = new URL(request.url());

    if (url.pathname === '/api/access/status') {
      await route.fulfill(json({ authenticated, expires_at: authenticated ? '2026-05-18T14:00:00Z' : null }));
      return;
    }
    if (url.pathname === '/api/access/admin/status') {
      await route.fulfill(json({ authenticated: true, expires_at: '2026-05-18T14:00:00Z' }));
      return;
    }
    if (url.pathname === '/api/access') {
      const body = JSON.parse(request.postData() || '{}');
      authenticated = body.access_key === 'open-sesame';
      await route.fulfill(json({ authenticated, expires_at: authenticated ? '2026-05-18T14:00:00Z' : null }));
      return;
    }
    if (url.pathname === '/api/version') {
      await route.fulfill(json({ version: 'v0.test', github_repo: 'test/repo', release_url: null }));
      return;
    }
    if (url.pathname === '/api/version/latest') {
      await route.fulfill(json({ latest_version: null, has_update: false, checked_at: null }));
      return;
    }
    if (url.pathname === '/api/settings/overall-config' && request.method() === 'GET') {
      await route.fulfill(json(mockedOverallConfig));
      return;
    }
    if (url.pathname === '/api/settings/overall-config' && request.method() === 'PUT') {
      const body = JSON.parse(request.postData() || '{}');
      const restartNames: string[] = [];
      mockedOverallConfig = {
        ...mockedOverallConfig,
        restart_required_names: restartNames,
        items: mockedOverallConfig.items.map((item: any) => {
          const update = body.updates?.find((candidate: { name?: string }) => candidate.name === item.name);
          if (!update) return item;
          if (update.clear_override) {
            return { ...item, has_override: false, override_value_masked: null, source: item.is_env_set ? 'env' : 'default' };
          }
          if (item.restart_required || item.build_only) restartNames.push(item.name);
          const masked = item.secret ? '********' : String(update.value);
          return {
            ...item,
            value: item.secret ? '********' : update.value,
            value_masked: masked,
            override_value_masked: masked,
            source: 'override',
            has_override: true
          };
        })
      };
      await route.fulfill(json(mockedOverallConfig));
      return;
    }
    if (url.pathname === '/api/settings') {
      await route.fulfill(json(mockedSettings));
      return;
    }
    if (url.pathname.match(/^\/api\/settings\/presets\/[^/]+\/activate$/) && request.method() === 'POST') {
      const id = decodeURIComponent(url.pathname.split('/').at(-2) || '');
      if (!mockedSettings.presets.some((preset) => preset.id === id)) {
        await route.fulfill(json({ detail: 'Preset not found' }, 404));
        return;
      }
      mockedSettings = applyActivePresetFields({ ...mockedSettings, active_preset_id: id });
      await route.fulfill(json(mockedSettings));
      return;
    }
    if (url.pathname.match(/^\/api\/settings\/presets\/[^/]+$/) && request.method() === 'DELETE') {
      const id = decodeURIComponent(url.pathname.split('/').pop() || '');
      if (mockedSettings.presets.length <= 1) {
        await route.fulfill(json({ detail: 'At least one preset is required' }, 400));
        return;
      }
      const deleteIndex = mockedSettings.presets.findIndex((preset) => preset.id === id);
      if (deleteIndex < 0) {
        await route.fulfill(json({ detail: 'Preset not found' }, 404));
        return;
      }
      const nextPresets = mockedSettings.presets.filter((preset) => preset.id !== id);
      const nextActiveId =
        mockedSettings.active_preset_id === id
          ? nextPresets[Math.min(deleteIndex, nextPresets.length - 1)].id
          : mockedSettings.active_preset_id;
      mockedSettings = applyActivePresetFields({
        ...mockedSettings,
        active_preset_id: nextActiveId,
        presets: nextPresets
      });
      await route.fulfill(json(mockedSettings));
      return;
    }
    if (url.pathname === '/api/prompt-snippets' && request.method() === 'GET') {
      const query = (url.searchParams.get('query') || '').toLowerCase();
      const snippets = (query
        ? promptSnippets.filter(
            (snippet) => snippet.title.toLowerCase().includes(query) || snippet.prompt.toLowerCase().includes(query)
          )
        : promptSnippets
      ).sort((a, b) => Number(b.favorite) - Number(a.favorite) || b.updated_at.localeCompare(a.updated_at));
      await route.fulfill(json({ snippets }));
      return;
    }
    if (url.pathname === '/api/prompt-snippets' && request.method() === 'POST') {
      const body = JSON.parse(request.postData() || '{}');
      const now = `2026-05-18T12:${String(promptSnippetCounter).padStart(2, '0')}:00Z`;
      const snippet = {
        id: `snippet-${promptSnippetCounter}`,
        title: body.title,
        prompt: body.prompt,
        favorite: Boolean(body.favorite),
        created_at: now,
        updated_at: now
      };
      promptSnippetCounter += 1;
      promptSnippets = [snippet, ...promptSnippets];
      await route.fulfill(json(snippet));
      return;
    }
    if (url.pathname.match(/^\/api\/prompt-snippets\/[^/]+$/) && request.method() === 'PATCH') {
      const id = decodeURIComponent(url.pathname.split('/').pop() || '');
      const body = JSON.parse(request.postData() || '{}');
      const existing = promptSnippets.find((snippet) => snippet.id === id);
      if (!existing) {
        await route.fulfill(json({ detail: 'Prompt snippet not found' }, 404));
        return;
      }
      const updated = { ...existing, ...body, updated_at: '2026-05-18T13:00:00Z' };
      promptSnippets = promptSnippets.map((snippet) => (snippet.id === id ? updated : snippet));
      await route.fulfill(json(updated));
      return;
    }
    if (url.pathname.match(/^\/api\/prompt-snippets\/[^/]+$/) && request.method() === 'DELETE') {
      const id = decodeURIComponent(url.pathname.split('/').pop() || '');
      promptSnippets = promptSnippets.filter((snippet) => snippet.id !== id);
      await route.fulfill(json({ status: 'ok', message: 'Deleted prompt snippet' }));
      return;
    }
    if (url.pathname === '/api/prompt/optimize' && request.method() === 'POST') {
      const body = JSON.parse(request.postData() || '{}');
      await route.fulfill(
        json({
          optimized_prompt: `Optimized ${body.prompt}`,
          model: 'gpt-4o-mini',
          duration_ms: 12
        })
      );
      return;
    }
    if (url.pathname === '/api/prompt/optimizer-health' && request.method() === 'POST') {
      await route.fulfill(
        json({
          status: 'ok',
          message: 'Prompt optimizer responded successfully with model gpt-4o-mini',
          model: 'gpt-4o-mini',
          duration_ms: 13,
          status_code: 200
        })
      );
      return;
    }
    if (url.pathname === '/api/prompt/optimizer-system-prompt' && request.method() === 'GET') {
      await route.fulfill(
        json({
          system_prompt: optimizerSystemPrompt,
          default_system_prompt: 'Default optimizer system prompt',
          customized: optimizerSystemPrompt !== 'Default optimizer system prompt'
        })
      );
      return;
    }
    if (url.pathname === '/api/prompt/optimizer-system-prompt' && request.method() === 'POST') {
      const body = JSON.parse(request.postData() || '{}');
      optimizerSystemPrompt = String(body.system_prompt || '').trim();
      await route.fulfill(
        json({
          system_prompt: optimizerSystemPrompt,
          default_system_prompt: 'Default optimizer system prompt',
          customized: true
        })
      );
      return;
    }
    if (url.pathname === '/api/assistant/image/prompt/optimize' && request.method() === 'POST') {
      imagePromptOptimizeCount += 1;
      if (options.optimizeDelayMs) await new Promise((resolve) => setTimeout(resolve, options.optimizeDelayMs));
      if (options.optimizeFailureAt === imagePromptOptimizeCount) {
        await route.fulfill(json({ detail: 'Trial generation failed: custom size unsupported' }, 502));
        return;
      }
      const prompt = options.optimizedPrompts?.[imagePromptOptimizeCount - 1] ?? `Refined reverse prompt ${imagePromptOptimizeCount}`;
      await route.fulfill(
        json({
          prompt,
          comparison_summary: `Comparison summary ${imagePromptOptimizeCount}`,
          model: 'assistant-vision-model',
          duration_ms: 29,
          warnings: [],
          temporary_image: {
            b64: PNG_BYTES.toString('base64'),
            mime_type: 'image/png',
            width: 896,
            height: 896,
            model: 'preset-default-model',
            duration_ms: 140
          }
        })
      );
      return;
    }
    if (url.pathname === '/api/assistant/image/prompt' && request.method() === 'POST') {
      await route.fulfill(
        json({
          prompt: options.reversePrompt ?? 'A bright red square centered on a clean white background',
          model: 'assistant-vision-model',
          duration_ms: 18,
          warnings: []
        })
      );
      return;
    }
    if (url.pathname.match(/^\/api\/assistant\/gallery\/[^/]+\/metadata$/) && request.method() === 'GET') {
      const imageId = decodeURIComponent(url.pathname.split('/').at(-2) || '');
      await route.fulfill(
        json({
          image_id: imageId,
          description: 'Stored AI description',
          prompt: 'Stored AI prompt',
          analysis: { subjects: ['square'] },
          model: 'assistant-vision-model',
          created_at: '2026-05-18T12:00:00Z',
          updated_at: '2026-05-18T12:00:00Z'
        })
      );
      return;
    }
    if (url.pathname === '/api/settings/r2/health') {
      await route.fulfill(json({ status: 'ok', checks: [{ name: 'configuration', status: 'ok', message: 'ok' }] }));
      return;
    }
    if (url.pathname.endsWith('/health') && url.pathname.startsWith('/api/settings/presets/')) {
      await route.fulfill(json({ status: 'ok', checks: [{ name: 'api_url', status: 'ok', message: 'ok' }] }));
      return;
    }
    if (url.pathname === '/api/gallery' && request.method() === 'GET') {
      const requestedPage = Number.parseInt(url.searchParams.get('page') || '1', 10);
      await route.fulfill(
        json(
          galleryResponse(
            galleryImages,
            url.searchParams.get('include_total_bytes') === 'true',
            requestedPage
          )
        )
      );
      return;
    }
    if (url.pathname === '/api/gallery/search' && request.method() === 'POST') {
      const body = request.postDataJSON() as Record<string, unknown>;
      const prompt = String(body.prompt || '');
      const favoriteParam = body.favorite;
      const requestedPage = Number(body.page || 1);
      const images = galleryImages.filter((image) =>
        matchesGalleryFilters(image, {
          prompt,
          favorite: typeof favoriteParam === 'boolean' ? favoriteParam : null
        })
      );
      await route.fulfill(
        json(
          galleryResponse(
            images,
            body.include_total_bytes === true,
            requestedPage,
            body.include_counts !== false,
            body.include_filter_options !== false
          )
        )
      );
      return;
    }
    if (url.pathname === '/api/gallery/batch/selection-tokens' && request.method() === 'POST') {
      const body = JSON.parse(request.postData() || '{}');
      const filters = body.filters || {};
      const token = `sel-${++selectionTokenSeq}`;
      selectionTokens.set(token, {
        prompt: String(filters.prompt || ''),
        favorite: filters.favorite ?? null
      });
      const count = galleryImages.filter((image) => matchesGalleryFilters(image, selectionTokens.get(token) || {})).length;
      await route.fulfill(
        json(
          {
            selection_token: token,
            count,
            expires_at: '2026-05-18T13:00:00Z'
          },
          201
        )
      );
      return;
    }
    if (url.pathname.match(/^\/api\/gallery\/[^/]+$/) && request.method() === 'GET') {
      const id = decodeURIComponent(url.pathname.split('/').pop() || '');
      const image = galleryImages.find((entry) => entry.id === id);
      await route.fulfill(image ? json(image) : json({ detail: 'Gallery entry not found' }, 404));
      return;
    }
    if (url.pathname.match(/^\/api\/gallery\/[^/]+$/) && request.method() === 'DELETE') {
      const id = decodeURIComponent(url.pathname.split('/').pop() || '');
      galleryImages = galleryImages.filter((entry) => entry.id !== id);
      await route.fulfill(json({ status: 'ok', message: 'Deleted gallery entry and 1 image file(s)' }));
      return;
    }
    if (url.pathname === '/api/gallery' && request.method() === 'DELETE') {
      galleryImages = [];
      await route.fulfill(json({ status: 'ok', message: 'Deleted all images' }));
      return;
    }
    if (url.pathname === '/api/gallery/batch/delete') {
      const body = JSON.parse(request.postData() || '{}');
      const ids = new Set<string>(resolveBatchIds(body));
      galleryImages = galleryImages.filter((entry) => !ids.has(entry.id));
      await route.fulfill(json({ status: 'ok', count: ids.size, file_count: ids.size, requested_count: ids.size, updated_count: ids.size }));
      return;
    }
    if (url.pathname === '/api/gallery/batch/favorite') {
      const body = JSON.parse(request.postData() || '{}');
      const ids = new Set<string>(resolveBatchIds(body));
      galleryImages = galleryImages.map((entry) => (ids.has(entry.id) ? { ...entry, favorite: Boolean(body.favorite) } : entry));
      await route.fulfill(json({ status: 'ok', count: ids.size, file_count: 0, requested_count: ids.size, updated_count: ids.size }));
      return;
    }
    if (url.pathname.match(/^\/api\/gallery\/[^/]+\/favorite$/)) {
      const id = decodeURIComponent(url.pathname.split('/').at(-2) || '');
      const body = JSON.parse(request.postData() || '{}');
      const image = galleryImages.find((entry) => entry.id === id) || galleryImages[0];
      if (image) {
        galleryImages = galleryImages.map((entry) => (entry.id === image.id ? { ...entry, favorite: body.favorite ?? true } : entry));
      }
      await route.fulfill(json(image ? { ...image, favorite: body.favorite ?? true } : { ...baseGalleryImages[0], favorite: true }));
      return;
    }
    if (url.pathname.startsWith('/api/thumb/') || url.pathname.startsWith('/api/image/')) {
      await route.fulfill({ status: 200, contentType: 'image/png', body: PNG_BYTES });
      return;
    }
    if (url.pathname === '/api/generate' && request.method() === 'POST') {
      await route.fulfill(json({ job_id: 'job-generated', status: 'queued', stage: 'queued', operation: 'generation' }, 202));
      return;
    }
    if (url.pathname === '/api/edits/from-gallery/img-1' && request.method() === 'POST') {
      await route.fulfill(json({ job_id: 'job-edited', status: 'queued', stage: 'queued', operation: 'edit' }, 202));
      return;
    }
    if (url.pathname === '/api/edits' && request.method() === 'POST') {
      if (options.editUploadFailure) {
        await route.fulfill(json({ detail: 'Upload image is required.' }, 422));
        return;
      }
      await route.fulfill(json({ job_id: 'job-upload-edited', status: 'queued', stage: 'queued', operation: 'edit' }, 202));
      return;
    }
    if (url.pathname === '/api/generate/jobs') {
      const includeFinished = url.searchParams.get('include_finished') === 'true';
      const failedOnly = url.searchParams.get('failed_only') === 'true';
      await route.fulfill(json(includeFinished ? (failedOnly ? historyJobs.filter(isErrorJob) : historyJobs) : runningJobs));
      return;
    }
    if (url.pathname === '/api/generate/jobs/history' && request.method() === 'DELETE') {
      historyJobs = [];
      await route.fulfill(json({ status: 'success', message: 'Deleted job history' }));
      return;
    }
    if (url.pathname === '/api/generate/jobs/events') {
      await route.fulfill({ status: 204 });
      return;
    }
    if (url.pathname === '/api/generate/job-generated/events') {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `event: job\ndata: ${JSON.stringify(options.generatedJob ?? job('job-generated', 'browser smoke prompt'))}\n\n`
      });
      return;
    }
    if (url.pathname === '/api/generate/job-edited/events') {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `event: job\ndata: ${JSON.stringify({ ...job('job-edited', 'browser edit prompt'), operation: 'edit' })}\n\n`
      });
      return;
    }
    if (url.pathname === '/api/generate/job-upload-edited/events') {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `event: job\ndata: ${JSON.stringify({ ...job('job-upload-edited', 'browser upload edit prompt'), operation: 'edit' })}\n\n`
      });
      return;
    }
    if (url.pathname.startsWith('/api/generate/job-')) {
      const id = url.pathname.split('/').pop() || 'job-generated';
      await route.fulfill(json(job(id, 'polled prompt')));
      return;
    }

    await route.continue();
  });
}

async function loadApp(page: Page, options: MockOptions = {}) {
  await mockApi(page, options);
  await page.goto('/');
  await expect(page.getByRole('heading', { name: options.language === 'zh-CN' ? '提示词' : 'Prompt', exact: true })).toBeVisible();
}

export {
  PNG_BYTES,
  baseGalleryImages,
  settingsResponse,
  overallConfigResponse,
  basePromptSnippets,
  json,
  galleryCursor,
  galleryResponse,
  job,
  manyJobs,
  isErrorJob,
  cloneSettings,
  applyActivePresetFields,
  manyGalleryImages,
  mockApi,
  loadApp
};
export type { GalleryImageFixture, PromptSnippetFixture, MockOptions, JobStatus };
