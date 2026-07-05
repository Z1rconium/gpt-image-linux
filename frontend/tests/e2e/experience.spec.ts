import { expect, type Page, test } from '@playwright/test';

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
  language?: 'en' | 'zh-CN';
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
  const selectionTokens = new Map<string, { prompt: string; favorite?: boolean | null }>();
  const runningJobs = options.runningJobs ?? [];
  let historyJobs = options.historyJobs ?? [job('history-1', 'saved prompt')];
  const initialLanguage = options.language ?? 'en';

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

  await page.addInitScript((languageValue: string) => {
    localStorage.setItem('gpt-image-panel-language', languageValue);
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
    if (url.pathname === '/api/settings/r2/health') {
      await route.fulfill(json({ status: 'ok', checks: [{ name: 'configuration', status: 'ok', message: 'ok' }] }));
      return;
    }
    if (url.pathname.endsWith('/health') && url.pathname.startsWith('/api/settings/presets/')) {
      await route.fulfill(json({ status: 'ok', checks: [{ name: 'api_url', status: 'ok', message: 'ok' }] }));
      return;
    }
    if (url.pathname === '/api/gallery' && request.method() === 'GET') {
      const prompt = url.searchParams.get('prompt') || '';
      const favoriteParam = url.searchParams.get('favorite');
      const requestedPage = Number.parseInt(url.searchParams.get('page') || '1', 10);
      const images = galleryImages.filter((image) =>
        matchesGalleryFilters(image, {
          prompt,
          favorite: favoriteParam === 'true' ? true : favoriteParam === 'false' ? false : null
        })
      );
      await route.fulfill(
        json(
          galleryResponse(
            images,
            url.searchParams.get('include_total_bytes') === 'true',
            requestedPage,
            url.searchParams.get('include_counts') !== 'false',
            url.searchParams.get('include_filter_options') !== 'false'
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

test('access gate unlocks before loading the app', async ({ page }) => {
  await mockApi(page, { authenticated: false });
  await page.goto('/');

  await expect(page.getByRole('heading', { name: 'Access Key' })).toBeVisible();
  await page.getByLabel('Access Key').fill('open-sesame');
  await page.getByRole('button', { name: 'Unlock' }).click();

  await expect(page.getByRole('heading', { name: 'Prompt', exact: true })).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Prompt', exact: true })).toBeVisible();
});

test('theme follows system preference, toggles, and persists after reload', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await loadApp(page);

  const root = page.locator('html');
  const themeButton = page.getByRole('button', { name: 'Switch to light mode' });

  await expect(root).toHaveAttribute('data-theme', 'dark');
  await expect(root).toHaveClass(/dark/);
  await expect(themeButton).toBeVisible();

  await themeButton.click();
  await expect(root).toHaveAttribute('data-theme', 'light');
  await expect(root).not.toHaveClass(/dark/);
  await expect(page.getByRole('button', { name: 'Switch to dark mode' })).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.localStorage.getItem('gpt-image-panel-theme'))).toBe('light');

  await page.reload();
  await expect(page.getByRole('heading', { name: 'Prompt', exact: true })).toBeVisible();
  await expect(root).toHaveAttribute('data-theme', 'light');
  await expect(root).not.toHaveClass(/dark/);
  await expect(page.getByRole('button', { name: 'Switch to dark mode' })).toBeVisible();
});

test('settings drawer traps focus and key form controls have accessible names', async ({ page }) => {
  await loadApp(page);

  await expect(page.getByRole('textbox', { name: 'Model' })).toHaveValue('preset-default-model');
  await expect(page.getByLabel('Response format')).toHaveValue('url');
  await page.getByRole('button', { name: 'Settings' }).click();
  const drawer = page.getByRole('dialog', { name: 'Settings' });
  await expect(drawer).toBeVisible();
  await expect(page.getByLabel('API URL')).toHaveValue('https://api.example.com');
  await expect(page.getByLabel('Default model')).toHaveValue('preset-default-model');
  await expect(page.getByLabel('Default response format')).toHaveValue('url');
  await expect(page.getByLabel('Webhook URL')).toHaveValue('https://hooks.example.com/***');
  await expect(page.getByLabel('Sync interval hours')).toHaveValue('0');
  await expect(page.getByLabel('Timeout seconds')).toHaveValue('60');
  await expect(drawer).toContainText('Literal keys are saved as plaintext.');
  await expect(page.getByLabel('Filter prompt')).toBeVisible();

  for (let index = 0; index < 12; index += 1) {
    await page.keyboard.press('Tab');
    await expect.poll(() => drawer.evaluate((node) => node.contains(document.activeElement))).toBe(true);
  }

  await page.keyboard.press('Escape');
  await expect(drawer).toBeHidden();
});

test('settings drawer saves prompt optimizer timeout seconds', async ({ page }) => {
  await loadApp(page);

  await page.getByRole('button', { name: 'Settings' }).click();
  await page.getByLabel('Timeout seconds').fill('90');
  const saveRequest = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/settings' && request.method() === 'POST');
  await page.getByRole('button', { name: 'Save Preset' }).click();
  const request = await saveRequest;

  expect(request.postDataJSON().prompt_optimizer).toMatchObject({
    timeout_seconds: 90
  });
});

test('settings drawer saves R2 sync interval hours', async ({ page }) => {
  await loadApp(page);

  await page.getByRole('button', { name: 'Settings' }).click();
  await page.getByLabel('Sync interval hours').fill('6');
  const saveRequest = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/settings' && request.method() === 'POST');
  await page.getByRole('button', { name: 'Save Preset' }).click();
  const request = await saveRequest;

  expect(request.postDataJSON().r2_backup).toMatchObject({
    sync_interval_hours: 6
  });
});

test('settings drawer edits the prompt optimizer system prompt', async ({ page }) => {
  await loadApp(page);

  await page.getByRole('button', { name: 'Settings' }).click();
  const drawer = page.getByRole('dialog', { name: 'Settings' });
  await drawer.getByRole('button', { name: 'Edit System Prompt' }).click();

  const editor = page.getByRole('dialog', { name: 'Prompt Optimizer System Prompt' });
  await expect(editor).toBeVisible();
  const prompt = editor.getByRole('textbox', { name: 'System prompt' });
  await expect(prompt).toHaveValue('Default optimizer system prompt');

  await prompt.fill('Custom optimizer system prompt');
  const saveRequest = page.waitForRequest(
    (request) => new URL(request.url()).pathname === '/api/prompt/optimizer-system-prompt' && request.method() === 'POST'
  );
  await editor.getByRole('button', { name: 'Save' }).click();
  const request = await saveRequest;
  expect(request.postDataJSON()).toEqual({ system_prompt: 'Custom optimizer system prompt' });
  await expect(page.getByRole('status')).toContainText('Prompt Optimizer system prompt saved');
  await expect(editor).toBeHidden();
});

test('settings drawer tests and closes health results', async ({ page }) => {
  await loadApp(page);

  await page.getByRole('button', { name: 'Settings' }).click();
  const drawer = page.getByRole('dialog', { name: 'Settings' });

  await drawer.getByRole('button', { name: 'Test Prompt Optimizer' }).click();
  const optimizerHealth = page.getByTestId('prompt-optimizer-health-result');
  await expect(optimizerHealth).toBeVisible();
  await expect(optimizerHealth).toContainText('Prompt optimizer responded successfully with model gpt-4o-mini');
  await optimizerHealth.getByRole('button', { name: 'Close' }).click();
  await expect(optimizerHealth).toHaveCount(0);

  await drawer.getByRole('button', { name: 'Health check' }).click();
  const presetHealth = page.getByTestId('preset-health-result');
  await expect(presetHealth).toBeVisible();
  await presetHealth.getByRole('button', { name: 'Close' }).click();
  await expect(presetHealth).toHaveCount(0);
});

test('settings drawer edits overall config overrides', async ({ page }) => {
  await loadApp(page);

  await page.getByRole('button', { name: 'Settings' }).click();
  const drawer = page.getByRole('dialog', { name: 'Settings' });
  await drawer.getByRole('button', { name: 'Overall Config' }).click();

  const modal = page.getByRole('dialog', { name: 'Overall Config' });
  await expect(modal).toBeVisible();
  await expect(modal).toContainText('ENABLE_METRICS');
  await expect(modal).toContainText('WEBHOOK_SIGNING_SECRET');
  await expect(modal).toContainText('restart');
  await expect(modal).toContainText('build only');

  await modal.getByTestId('overall-config-ENABLE_METRICS').locator('input[type="checkbox"]').check();
  await modal.getByTestId('overall-config-WEBHOOK_SIGNING_SECRET').locator('input').fill('********');
  await modal.getByTestId('overall-config-ACCESS_KEY_COOKIE_NAME').getByRole('button', { name: 'Reset to .env' }).click();

  const saveRequest = page.waitForRequest(
    (request) => new URL(request.url()).pathname === '/api/settings/overall-config' && request.method() === 'PUT'
  );
  await modal.getByRole('button', { name: 'Save config' }).click();
  const request = await saveRequest;
  expect(request.postDataJSON()).toEqual({
    updates: [
      { name: 'ENABLE_METRICS', value: true },
      { name: 'WEBHOOK_SIGNING_SECRET', value: '********' },
      { name: 'ACCESS_KEY_COOKIE_NAME', clear_override: true }
    ]
  });
  await expect(page.getByRole('status')).toContainText('Overall config saved');
});

test('active preset response format default is applied to prompt form', async ({ page }) => {
  await loadApp(page, {
    settings: {
      ...settingsResponse,
      default_response_format: 'b64_json',
      presets: settingsResponse.presets.map((preset) => ({
        ...preset,
        default_response_format: 'b64_json'
      }))
    }
  });

  await expect(page.getByLabel('Response format')).toHaveValue('b64_json');

  const generateRequest = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/generate');
  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('preset response format prompt');
  await page.getByRole('button', { name: 'Generate', exact: true }).click();
  const request = await generateRequest;
  expect(request.postDataJSON()).toMatchObject({
    prompt: 'preset response format prompt',
    response_format: 'b64_json'
  });
});

test('settings drawer deletes the active preset and switches to fallback', async ({ page }) => {
  await loadApp(page, {
    settings: {
      ...settingsResponse,
      presets: [
        ...settingsResponse.presets,
        {
          ...settingsResponse.presets[0],
          id: 'alt',
          name: 'Alt preset',
          default_model: 'alt-model',
          default_response_format: 'b64_json'
        }
      ]
    }
  });

  await page.getByRole('button', { name: 'Settings' }).click();
  const drawer = page.getByRole('dialog', { name: 'Settings' });
  await expect(drawer).toContainText('Default');
  await expect(drawer).toContainText('Alt preset');

  await drawer.getByRole('button', { name: 'Delete' }).click();
  const confirm = page.getByRole('dialog', { name: 'Delete preset?' });
  await expect(confirm).toContainText('Delete preset "Default"?');
  await confirm.getByRole('button', { name: 'Delete' }).click();

  await expect(page.getByRole('status')).toContainText('Preset deleted');
  await expect(drawer.getByText('Default', { exact: true })).toHaveCount(0);
  await expect(drawer).toContainText('Alt preset');
  await expect(page.getByRole('main').getByRole('textbox', { name: 'Model' })).toHaveValue('alt-model');
  await expect(page.getByRole('main').getByLabel('Response format')).toHaveValue('b64_json');
});

test('generation, gallery edit source, batch favorite, and lightbox flows work with mocked API', async ({ page }) => {
  await loadApp(page);

  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('browser smoke prompt');
  await page.getByRole('button', { name: 'Generate', exact: true }).click();
  await expect(page.getByRole('img', { name: 'Generated preview' })).toBeVisible();

  await page.locator('.gallery-card').first().getByRole('button', { name: 'Edit' }).click();
  await expect(page.getByRole('status')).toContainText('Gallery image ready for edits');
  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('browser edit prompt');
  await page.getByRole('button', { name: 'Edits' }).click();
  await expect(page.getByRole('img', { name: 'Generated preview' })).toBeVisible();

  const filterRequest = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return response.request().method() === 'GET' && url.pathname === '/api/gallery' && url.searchParams.get('prompt') === 'First';
  });
  await page.getByLabel('Filter prompt').fill('First');
  await filterRequest;
  await expect(page.getByRole('img', { name: 'First gallery image' })).toBeVisible();

  const firstCard = page.locator('.gallery-card').filter({ hasText: 'First gallery image' });
  const favoriteButton = firstCard.locator('button').nth(3);
  const favoriteRequest = page.waitForRequest((request) => {
    const url = new URL(request.url());
    if (request.method() !== 'PATCH' || url.pathname !== '/api/gallery/img-1/favorite') return false;
    const body = JSON.parse(request.postData() || '{}');
    return body.favorite === true;
  });
  await favoriteButton.click();
  await favoriteRequest;
  await expect(favoriteButton).toHaveAttribute('aria-pressed', 'true');
  await expect(favoriteButton).toHaveCSS('color', 'rgb(217, 119, 6)');

  await page.getByRole('button', { name: 'Select' }).click();
  await page.getByRole('button', { name: 'Select page' }).click();
  await page.getByRole('button', { name: 'Favorite selected', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Updated');

  await page.getByRole('button', { name: 'Cancel selection' }).click();
  await page.getByRole('img', { name: 'First gallery image' }).click();
  const lightbox = page.getByRole('dialog', { name: 'Image Details' });
  await expect(lightbox).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(lightbox).toBeHidden();
});

test('gallery shows the source image while thumbnail generation is still queued', async ({ page }) => {
  await loadApp(page, {
    galleryImages: [
      {
        ...baseGalleryImages[0],
        id: 'pending-thumbnail',
        prompt: 'Pending thumbnail image',
        filename: 'pending-thumbnail.png',
        thumbnail_url: '/api/thumb/pending-thumbnail.png',
        thumbnail_status: 'queued'
      }
    ]
  });

  const image = page.getByRole('img', { name: 'Pending thumbnail image' });
  await expect(image).toBeVisible();
  await expect(image).toHaveAttribute('src', '/api/image/pending-thumbnail.png');
});

test('empty quantity falls back to 1 on generate', async ({ page }) => {
  await loadApp(page);

  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('empty quantity prompt');
  await page.getByLabel('Quantity').fill('');

  const generateRequest = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/generate');
  await page.getByRole('button', { name: 'Generate', exact: true }).click();
  const request = await generateRequest;

  expect(request.postDataJSON()).toMatchObject({
    prompt: 'empty quantity prompt',
    n: 1
  });
  await expect(page.getByLabel('Quantity')).toHaveValue('1');
});

test('prompt helper tags append once and optimizer replaces prompt with undo', async ({ page }) => {
  await loadApp(page);

  const prompt = page.getByRole('textbox', { name: 'Prompt', exact: true });
  await prompt.fill('small cabin');
  await page.getByRole('button', { name: 'High detail' }).click();
  await expect(prompt).toHaveValue('small cabin, high detail');

  await page.getByRole('button', { name: 'High detail' }).click();
  await expect(prompt).toHaveValue('small cabin, high detail');
  await expect(page.getByRole('status')).toContainText('Tag already exists');

  const optimizeRequest = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/prompt/optimize');
  await page.getByRole('button', { name: 'Optimize', exact: true }).click();
  const request = await optimizeRequest;
  expect(request.postDataJSON()).toMatchObject({
    prompt: 'small cabin, high detail',
    target_language: 'en',
    api_path: '/v1/images/generations'
  });
  await expect(prompt).toHaveValue('Optimized small cabin, high detail');
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(prompt).toHaveValue('small cabin, high detail');
});

test('floating prompt optimizer stays hidden when unavailable', async ({ page }) => {
  await loadApp(page, {
    settings: {
      ...settingsResponse,
      prompt_optimizer: {
        ...settingsResponse.prompt_optimizer,
        enabled: false
      }
    }
  });

  await expect(page.getByTestId('prompt-optimizer-assistant-trigger')).toHaveCount(0);
});

test('AI Assistant controls use shared prompt optimizer API config', async ({ page }) => {
  await loadApp(page, {
    settings: {
      ...settingsResponse,
      prompt_optimizer: {
        ...settingsResponse.prompt_optimizer,
        enabled: false
      },
      ai_assistant: {
        ...settingsResponse.ai_assistant,
        api_url: '',
        model: '',
        has_api_key: false
      }
    }
  });

  await page.getByLabel('Instruction').fill('sunlit alley with a bicycle');

  await expect(page.getByText('Enable AI Assistant and configure Prompt Optimizer in Settings')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Rewrite' })).toBeEnabled();
  await expect(page.getByTestId('ai-assistant-panel').getByRole('button', { name: 'Quick optimize' })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Check' })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Variants' })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Params' })).toBeEnabled();
});

test('AI Assistant Quick optimize uses the prompt optimizer flow', async ({ page }) => {
  await loadApp(page);

  const prompt = page.getByRole('textbox', { name: 'Prompt', exact: true });
  await prompt.fill('sunlit alley with a bicycle');

  const assistantPanel = page.getByTestId('ai-assistant-panel');
  await assistantPanel.getByLabel('Instruction').fill('make it rainy at dusk');

  const optimizeRequest = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/prompt/optimize');
  await assistantPanel.getByRole('button', { name: 'Quick optimize' }).click();
  const request = await optimizeRequest;
  expect(request.postDataJSON()).toMatchObject({
    prompt: 'sunlit alley with a bicycle',
    intent: 'make it rainy at dusk',
    target_language: 'en',
    api_path: '/v1/images/generations'
  });

  await expect(assistantPanel).toContainText('Quick optimized prompt');
  await expect(assistantPanel).toContainText('Optimized sunlit alley with a bicycle');
  await assistantPanel.getByRole('button', { name: 'Apply' }).click();
  await expect(prompt).toHaveValue('Optimized sunlit alley with a bicycle');
});

test('floating prompt optimizer compares, rejects, cleans up, and accepts without covering the editor', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await loadApp(page);

  const prompt = page.getByRole('textbox', { name: 'Prompt', exact: true });
  await prompt.fill('sunlit alley with a bicycle');

  const trigger = page.getByTestId('prompt-optimizer-assistant-trigger');
  await expect(trigger).toBeVisible();

  const triggerBox = await trigger.boundingBox();
  const promptBox = await prompt.boundingBox();
  expect(triggerBox).not.toBeNull();
  expect(promptBox).not.toBeNull();
  expect((promptBox?.y || 0) + (promptBox?.height || 0)).toBeLessThan(triggerBox?.y || Number.POSITIVE_INFINITY);

  await trigger.click();
  const dialog = page.getByRole('dialog', { name: 'Quick optimize' });
  await expect(dialog).toBeVisible();

  const intentInput = dialog.getByLabel('Modification intent');
  await expect(intentInput).toHaveValue('');
  await intentInput.fill('make it rainy at dusk');

  const optimizeRequest = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/prompt/optimize');
  await dialog.getByRole('button', { name: 'Optimize', exact: true }).click();
  const request = await optimizeRequest;
  const body = request.postDataJSON();
  expect(body).toMatchObject({
    prompt: 'sunlit alley with a bicycle',
    intent: 'make it rainy at dusk',
    target_language: 'en',
    api_path: '/v1/images/generations'
  });

  await expect(page.getByTestId('prompt-optimizer-original')).toContainText('sunlit alley with a bicycle');
  await expect(page.getByTestId('prompt-optimizer-optimized')).toContainText('Optimized ');

  await dialog.getByRole('button', { name: 'Reject' }).click();
  await expect(dialog).toBeHidden();
  await expect(prompt).toHaveValue('sunlit alley with a bicycle');

  await trigger.click();
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel('Modification intent')).toHaveValue('');

  await dialog.getByLabel('Modification intent').fill('make it rainy at dusk');
  const acceptRequest = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/prompt/optimize');
  await dialog.getByRole('button', { name: 'Optimize', exact: true }).click();
  const acceptPayload = (await acceptRequest).postDataJSON();
  expect(acceptPayload).toMatchObject({
    prompt: 'sunlit alley with a bicycle',
    intent: 'make it rainy at dusk'
  });
  await dialog.getByRole('button', { name: 'Accept' }).click();

  await expect(prompt).toHaveValue('Optimized sunlit alley with a bicycle');
});

test('prompt optimize sends localized target language', async ({ page }) => {
  await loadApp(page, { language: 'zh-CN' });

  await page.getByRole('textbox', { name: '提示词', exact: true }).fill('一只小机器人');
  const optimizeRequest = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/prompt/optimize');
  await page.getByRole('button', { name: '优化', exact: true }).click();
  const request = await optimizeRequest;
  expect(request.postDataJSON()).toMatchObject({
    prompt: '一只小机器人',
    target_language: 'zh-CN'
  });
});

test('floating prompt optimizer opens on click and can be long-press dragged', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await loadApp(page);

  const trigger = page.getByTestId('prompt-optimizer-assistant-trigger');
  await expect(trigger).toBeVisible();

  const triggerRect = () =>
    trigger.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
    });

  const initialBox = await triggerRect();
  expect(initialBox).not.toBeNull();

  await trigger.click();
  await expect(page.getByRole('dialog', { name: 'Quick optimize' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: 'Quick optimize' })).toBeHidden();

  const dragInitialBox = await triggerRect();
  const pointerOffsetX = 24;
  const pointerOffsetY = Math.round((dragInitialBox?.height || 0) / 2);
  const startX = Math.round((dragInitialBox?.x || 0) + pointerOffsetX);
  const startY = Math.round((dragInitialBox?.y || 0) + pointerOffsetY);
  const dragTargetX = startX - 130;
  const dragTargetY = startY - 110;
  await page.mouse.move(startX, startY);
  await page.mouse.down();
  await page.waitForTimeout(320);

  const heldBox = await triggerRect();
  const heldStyle = await trigger.evaluate((element) => ({
    left: (element as HTMLElement).style.left,
    top: (element as HTMLElement).style.top,
    bottom: (element as HTMLElement).style.bottom
  }));
  expect(heldBox).not.toBeNull();
  expect(Math.abs(Math.round(parseFloat(heldStyle.left)) - Math.round(dragInitialBox?.x || 0))).toBeLessThanOrEqual(3);
  expect(Math.abs(Math.round(parseFloat(heldStyle.top)) - Math.round(dragInitialBox?.y || 0))).toBeLessThanOrEqual(3);
  expect(heldStyle.bottom).toBe('auto');

  await page.mouse.move(dragTargetX, dragTargetY, { steps: 8 });
  await page.mouse.up();

  const movedBox = await triggerRect();
  const movedStyle = await trigger.evaluate((element) => ({
    left: (element as HTMLElement).style.left,
    top: (element as HTMLElement).style.top
  }));
  expect(movedBox).not.toBeNull();
  expect(movedBox?.x || 0).toBeLessThan((dragInitialBox?.x || 0) - 40);
  expect(movedBox?.y || 0).toBeLessThan((dragInitialBox?.y || 0) - 40);
  expect(Math.abs(Math.round(parseFloat(movedStyle.left) + pointerOffsetX) - dragTargetX)).toBeLessThanOrEqual(3);
  expect(Math.abs(Math.round(parseFloat(movedStyle.top) + pointerOffsetY) - dragTargetY)).toBeLessThanOrEqual(3);

  await page.mouse.move(startX - 8, startY - 8);
  const settledBox = await triggerRect();
  expect(settledBox).not.toBeNull();
  expect(
    Math.abs(
      Math.round((settledBox?.x || 0) + (settledBox?.width || 0) / 2) -
        Math.round((movedBox?.x || 0) + (movedBox?.width || 0) / 2)
    )
  ).toBeLessThanOrEqual(1);
  expect(
    Math.abs(
      Math.round((settledBox?.y || 0) + (settledBox?.height || 0) / 2) -
        Math.round((movedBox?.y || 0) + (movedBox?.height || 0) / 2)
    )
  ).toBeLessThanOrEqual(1);

  await page.mouse.move(
    Math.round((settledBox?.x || 0) + (settledBox?.width || 0) / 2),
    Math.round((settledBox?.y || 0) + (settledBox?.height || 0) / 2)
  );
  await page.mouse.down();
  await page.waitForTimeout(320);
  await page.mouse.move(-140, -120, { steps: 8 });
  await page.mouse.up();

  const clampedBox = await triggerRect();
  expect(clampedBox).not.toBeNull();
  expect(clampedBox?.x || 0).toBeGreaterThanOrEqual(12);
  expect(clampedBox?.y || 0).toBeGreaterThanOrEqual(12);
  expect((clampedBox?.x || 0) + (clampedBox?.width || 0)).toBeLessThanOrEqual(390 - 12);
  expect((clampedBox?.y || 0) + (clampedBox?.height || 0)).toBeLessThanOrEqual(844 - 12);

  await page.reload();
  await expect(page.getByRole('heading', { name: 'Prompt', exact: true })).toBeVisible();

  const reloadedBox = await triggerRect();
  expect(reloadedBox).not.toBeNull();
  expect(reloadedBox?.x || 0).toBeLessThan((dragInitialBox?.x || 0) - 40);
  expect(reloadedBox?.y || 0).toBeLessThan((dragInitialBox?.y || 0) - 40);

  await trigger.click();
  await expect(page.getByRole('dialog', { name: 'Quick optimize' })).toBeVisible();
});

test('prompt snippets drawer saves, searches, edits, copies, deletes, and uses templates', async ({ page }) => {
  await loadApp(page);

  const prompt = page.getByRole('textbox', { name: 'Prompt', exact: true });
  const promptsButton = page.getByRole('button', { name: 'Prompt snippets' });
  const jobsButton = page.getByRole('button', { name: 'Job History' });
  const promptsBox = await promptsButton.boundingBox();
  const jobsBox = await jobsButton.boundingBox();
  expect(promptsBox?.x ?? 0).toBeLessThan(jobsBox?.x ?? Number.POSITIVE_INFINITY);

  await prompt.fill('fresh current prompt\nsecond line');
  await promptsButton.click();
  const drawer = page.getByRole('dialog', { name: 'Prompt Snippets' });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByText('Product hero')).toBeVisible();

  await drawer.getByRole('button', { name: 'Save current' }).click();
  await expect(drawer.getByRole('heading', { name: 'fresh current prompt' })).toBeVisible();
  await expect(page.getByRole('status')).toContainText('Prompt snippet saved');

  await drawer.getByLabel('Search snippets').fill('product');
  await expect(drawer.getByText('Product hero')).toBeVisible();
  await expect(drawer.getByText('Portrait base')).toBeHidden();

  await drawer.getByRole('button', { name: 'Copy' }).click();
  await expect(prompt).toHaveValue('fresh current prompt\nsecond line');
  await expect(page.getByRole('status')).toContainText('Prompt copied');
  await expect(page.getByRole('status')).toHaveCount(0);

  await drawer.getByRole('button', { name: 'Edit' }).click();
  await drawer.getByLabel('Title').fill('Product hero updated');
  await drawer.getByRole('button', { name: 'Update' }).click();
  await expect(drawer.getByText('Product hero updated')).toBeVisible();

  await drawer.getByRole('button', { name: 'Use' }).click();
  await expect(drawer).toBeHidden();
  await expect(prompt).toHaveValue('studio product photography');

  await promptsButton.click();
  const reopenedDrawer = page.getByRole('dialog', { name: 'Prompt Snippets' });
  await expect(reopenedDrawer.getByText('Product hero updated')).toBeVisible();
  const updatedSnippet = reopenedDrawer.locator('article').filter({ hasText: 'Product hero updated' });
  await updatedSnippet.getByRole('button', { name: 'Delete' }).click();
  const confirmDialog = page.getByRole('dialog', { name: 'Delete prompt snippet?' });
  await confirmDialog.getByRole('button', { name: 'Delete' }).click();
  await expect(reopenedDrawer.getByText('Product hero updated')).toBeHidden();
  await expect(page.getByRole('status')).toContainText('Prompt snippet deleted');
});

test('gallery cards can reuse prompt or full generation parameters', async ({ page }) => {
  await loadApp(page);

  const prompt = page.getByRole('textbox', { name: 'Prompt', exact: true });
  await page.locator('.gallery-card').first().getByRole('button', { name: 'Use prompt' }).click();
  await expect(prompt).toHaveValue('First gallery image');
  await expect(page.getByRole('textbox', { name: 'Model' })).toHaveValue('preset-default-model');
  await expect(page.getByLabel('API path')).toHaveValue('/v1/images/generations');

  await page.locator('.gallery-card').first().getByRole('button', { name: 'Use all' }).click();
  await expect(prompt).toHaveValue('First gallery image');
  await expect(page.getByRole('textbox', { name: 'Model' })).toHaveValue('gpt-image-2');
  await expect(page.getByLabel('API path')).toHaveValue('/v1/responses');

  const generateRequest = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/generate');
  await page.getByRole('button', { name: 'Generate', exact: true }).click();
  const request = await generateRequest;
  expect(request.postDataJSON()).toMatchObject({
    prompt: 'First gallery image',
    api_path: '/v1/responses',
    model: 'gpt-image-2'
  });
});

test('lightbox use all reuses parameters and edit api path is ignored', async ({ page }) => {
  await loadApp(page);

  await page.locator('.gallery-card').nth(1).getByRole('img', { name: 'Second gallery image' }).click();
  const lightbox = page.getByRole('dialog', { name: 'Image Details' });
  await expect(lightbox).toBeVisible();
  await lightbox.getByRole('button', { name: 'Use all' }).click();

  await expect(lightbox).toBeHidden();
  await expect(page.getByRole('textbox', { name: 'Prompt', exact: true })).toHaveValue('Second gallery image');
  await expect(page.getByLabel('API path')).toHaveValue('/v1/images/generations');
  await expect(page.getByRole('status')).toContainText('edit API path was ignored');
});

test('lightbox navigates images across gallery pages', async ({ page }) => {
  await loadApp(page, { galleryImages: manyGalleryImages(10) });

  await page.getByRole('img', { name: 'Paged gallery image 1', exact: true }).click();
  const lightbox = page.getByRole('dialog', { name: 'Image Details' });
  await expect(lightbox).toBeVisible();
  await expect(lightbox.getByRole('button', { name: 'Previous image' })).toHaveCount(0);
  await expect(lightbox.getByRole('button', { name: 'Next image' })).toBeVisible();

  await lightbox.getByRole('button', { name: 'Next image' }).click();
  await expect(lightbox).toContainText('paged-img-2.png');
  await expect(page).toHaveURL(/image=paged-img-2/);

  await page.keyboard.press('ArrowLeft');
  await expect(lightbox).toContainText('paged-img-1.png');
  await expect(page).toHaveURL(/image=paged-img-1/);

  await page.keyboard.press('Escape');
  await expect(lightbox).toBeHidden();

  const nextPageRequest = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return (
      request.method() === 'GET' &&
      url.pathname === '/api/gallery' &&
      url.searchParams.get('page') === '2' &&
      url.searchParams.get('direction') === 'next' &&
      Boolean(url.searchParams.get('cursor'))
    );
  });
  await page.getByRole('img', { name: 'Paged gallery image 9', exact: true }).click();
  await expect(lightbox).toContainText('paged-img-9.png');
  await page.keyboard.press('ArrowRight');
  await nextPageRequest;

  await expect(lightbox).toContainText('paged-img-10.png');
  await expect(page).toHaveURL(/page=2/);
  await expect(page).toHaveURL(/image=paged-img-10/);
  await expect(lightbox.getByRole('button', { name: 'Next image' })).toHaveCount(0);

  await page.keyboard.press('ArrowRight');
  await expect(lightbox).toContainText('paged-img-10.png');
  await expect(page).toHaveURL(/image=paged-img-10/);
});

test('multi-image job results can be previewed individually', async ({ page }) => {
  const generatedJob = {
    ...job('job-generated', 'browser multi prompt'),
    image_id: 'multi-1',
    image_url: '/api/image/multi-1.png',
    images: [
      {
        image_id: 'multi-1',
        image_url: '/api/image/multi-1.png',
        filename: 'multi-1.png',
        image_width: 1,
        image_height: 1
      },
      {
        image_id: 'multi-2',
        image_url: '/api/image/multi-2.png',
        filename: 'multi-2.png',
        image_width: 1,
        image_height: 1
      }
    ]
  };
  await loadApp(page, { generatedJob });

  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('browser multi prompt');
  await page.getByRole('button', { name: 'Generate', exact: true }).click();
  const preview = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Preview' }) });
  await expect(preview.getByRole('img', { name: 'Generated preview' })).toBeVisible();
  await expect(preview.getByRole('button', { name: 'Select result 2' })).toBeVisible();

  await preview.getByRole('button', { name: 'Select result 2' }).click();
  await expect(preview.getByRole('link', { name: 'Download' })).toHaveAttribute('href', '/api/download/multi-2.png');
});

test('job history shows detailed terminal statuses', async ({ page }) => {
  const detailedUpstreamError = 'Upstream API error (400): Invalid model';
  await loadApp(page, {
    historyJobs: [
      job('cancelled-job', 'cancelled prompt', 'cancelled'),
      job('interrupted-job', 'interrupted prompt', 'interrupted'),
      {
        ...job('upstream-job', 'upstream prompt', 'upstream_error'),
        message: 'Generation failed',
        error: detailedUpstreamError
      }
    ]
  });

  await page.getByRole('button', { name: 'Job History' }).click();
  const jobsDrawer = page.getByRole('dialog', { name: 'Job History' });
  await jobsDrawer.getByRole('button', { name: 'History', exact: true }).click();
  await expect(jobsDrawer.getByText('cancelled', { exact: true })).toBeVisible();
  await expect(jobsDrawer.getByText('interrupted', { exact: true })).toBeVisible();
  await expect(jobsDrawer.getByText('upstream error', { exact: true })).toBeVisible();

  const upstreamJob = jobsDrawer.locator('article').filter({ hasText: 'upstream prompt' });
  await expect(upstreamJob.getByText('Generation failed', { exact: true })).toBeVisible();
  await expect(upstreamJob.getByText(detailedUpstreamError, { exact: true })).toBeHidden();
  await upstreamJob.getByRole('button', { name: 'Show error' }).click();
  await expect(upstreamJob.getByText(detailedUpstreamError, { exact: true })).toBeVisible();
  await expect(upstreamJob.getByRole('button', { name: 'Hide error' })).toBeVisible();
  await upstreamJob.getByRole('button', { name: 'Hide error' }).click();
  await expect(upstreamJob.getByText(detailedUpstreamError, { exact: true })).toBeHidden();

  await jobsDrawer.getByLabel('Errors only').check();
  await expect(jobsDrawer.getByText('upstream prompt')).toBeVisible();
  await expect(jobsDrawer.getByText('cancelled prompt')).toBeHidden();
  await expect(jobsDrawer.getByText('interrupted prompt')).toBeHidden();

  await jobsDrawer.getByLabel('Errors only').uncheck();
  await expect(jobsDrawer.getByText('cancelled prompt')).toBeVisible();
  await expect(jobsDrawer.getByText('interrupted prompt')).toBeVisible();
});

test('job history clear removes persisted history rows', async ({ page }) => {
  await loadApp(page, {
    historyJobs: [job('history-1', 'saved prompt'), job('history-2', 'another saved prompt')]
  });

  await page.getByRole('button', { name: 'Job History' }).click();
  const jobsDrawer = page.getByRole('dialog', { name: 'Job History' });
  await jobsDrawer.getByRole('button', { name: 'History', exact: true }).click();
  await expect(jobsDrawer.getByText('saved prompt', { exact: true })).toBeVisible();

  await jobsDrawer.getByRole('button', { name: 'Clear' }).click();
  const confirmDialog = page.getByRole('dialog', { name: 'Clear all job history?' });
  await expect(confirmDialog.getByText('local SQLite')).toBeVisible();
  await confirmDialog.getByRole('button', { name: 'Clear' }).click();

  await expect(jobsDrawer.getByText('No job history')).toBeVisible();
  await expect(jobsDrawer.getByText('saved prompt', { exact: true })).toBeHidden();
});

test('gallery url state restores filters, lightbox, and job history tab', async ({ page }) => {
  await mockApi(page);
  await page.goto('/?prompt=Second&favorite=true&image=img-2&jobs=history');

  await expect(page.getByLabel('Filter prompt')).toHaveValue('Second');
  await expect(page).toHaveURL(/prompt=Second/);
  await expect(page).toHaveURL(/favorite=true/);

  const lightbox = page.getByRole('dialog', { name: 'Image Details' });
  await expect(lightbox).toBeVisible();
  await expect(lightbox).toContainText('img-2.png');
  await expect(page).toHaveURL(/image=img-2/);

  await page.keyboard.press('Escape');
  await expect(lightbox).toBeHidden();
  await expect(page).not.toHaveURL(/image=img-2/);

  const jobsDrawer = page.getByRole('dialog', { name: 'Job History' });
  await expect(jobsDrawer).toBeVisible();
  await expect(jobsDrawer.getByText('saved prompt')).toBeVisible();
  await expect(page).toHaveURL(/jobs=history/);

  const promptFilterRequest = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return request.method() === 'GET' && url.pathname === '/api/gallery' && url.searchParams.get('prompt') === 'First';
  });
  await page.getByLabel('Filter prompt').fill('First');
  await promptFilterRequest;
  await expect(page).toHaveURL(/prompt=First/);
});

test('gallery page input jumps to the requested page on Enter', async ({ page }) => {
  await loadApp(page, { galleryImages: manyGalleryImages(10) });

  await expect(page.getByRole('img', { name: 'Paged gallery image 1', exact: true })).toBeVisible();
  const pageInput = page.getByLabel('Jump to page');
  await expect(pageInput).toHaveValue('1');

  const nextPageRequest = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return (
      request.method() === 'GET' &&
      url.pathname === '/api/gallery' &&
      url.searchParams.get('page') === '2' &&
      !url.searchParams.has('cursor')
    );
  });
  await pageInput.fill('2');
  await pageInput.press('Enter');
  await nextPageRequest;

  await expect(page.getByRole('img', { name: 'Paged gallery image 10', exact: true })).toBeVisible();
  await expect(page.getByRole('img', { name: 'Paged gallery image 1', exact: true })).toBeHidden();
  await expect(pageInput).toHaveValue('2');
  await expect(page).toHaveURL(/page=2/);
});

test('gallery handles 500 mocked images with lightweight cursor paging, filtering, and selection', async ({ page }) => {
  await loadApp(page, { galleryImages: manyGalleryImages(500) });

  await expect(page.getByRole('img', { name: 'Paged gallery image 1', exact: true })).toBeVisible();
  const nextPageRequest = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return (
      request.method() === 'GET' &&
      url.pathname === '/api/gallery' &&
      url.searchParams.get('page') === '2' &&
      url.searchParams.get('direction') === 'next' &&
      Boolean(url.searchParams.get('cursor'))
    );
  });
  await page.getByRole('button', { name: 'Next' }).click();
  const nextRequest = await nextPageRequest;
  const nextUrl = new URL(nextRequest.url());
  expect(nextUrl.searchParams.get('include_counts')).toBe('false');
  expect(nextUrl.searchParams.get('include_filter_options')).toBe('false');
  await expect(page.getByRole('img', { name: 'Paged gallery image 10', exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'Select' }).click();
  await page.getByRole('button', { name: 'Select page' }).click();
  await page.getByRole('button', { name: 'Favorite selected', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Updated');
  await page.getByRole('button', { name: 'Cancel selection' }).click();

  const filterRequest = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return request.method() === 'GET' && url.pathname === '/api/gallery' && url.searchParams.get('prompt') === '500';
  });
  await page.getByLabel('Filter prompt').fill('500');
  await filterRequest;
  await expect(page.getByRole('img', { name: 'Paged gallery image 500', exact: true })).toBeVisible();
  await expect(page.getByRole('img', { name: 'Paged gallery image 10', exact: true })).toBeHidden();
});

test('gallery selects current filtered results through a batch token', async ({ page }) => {
  await loadApp(page, { galleryImages: manyGalleryImages(10) });

  await page.getByLabel('Filter prompt').fill('Paged gallery image');
  await expect(page.getByRole('img', { name: 'Paged gallery image 1', exact: true })).toBeVisible();

  const tokenRequest = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return request.method() === 'POST' && url.pathname === '/api/gallery/batch/selection-tokens';
  });
  await page.getByRole('button', { name: 'Select' }).click();
  await page.getByRole('button', { name: 'Select filtered' }).click();
  await tokenRequest;
  await expect(page.getByText('10 selected from current filters')).toBeVisible();

  const favoriteRequest = page.waitForRequest((request) => {
    const url = new URL(request.url());
    if (request.method() !== 'PATCH' || url.pathname !== '/api/gallery/batch/favorite') return false;
    const body = JSON.parse(request.postData() || '{}');
    return typeof body.selection_token === 'string' && body.favorite === true;
  });
  await page.getByRole('button', { name: 'Favorite selected', exact: true }).click();
  await favoriteRequest;
  await expect(page.getByRole('status')).toContainText('Updated 10 selected images');
});

test('cross-page batch delete refreshes filtered gallery state after the optimistic update', async ({ page }) => {
  await loadApp(page, { galleryImages: manyGalleryImages(10) });

  const filterRequest = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return response.request().method() === 'GET' && url.pathname === '/api/gallery' && url.searchParams.get('prompt') === 'Paged gallery image';
  });
  await page.getByLabel('Filter prompt').fill('Paged gallery image');
  await filterRequest;

  const tokenRequest = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return request.method() === 'POST' && url.pathname === '/api/gallery/batch/selection-tokens';
  });
  await page.getByRole('button', { name: 'Select' }).click();
  await page.getByRole('button', { name: 'Select filtered' }).click();
  await tokenRequest;

  const refreshRequest = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      response.request().method() === 'GET' &&
      url.pathname === '/api/gallery' &&
      url.searchParams.get('prompt') === 'Paged gallery image' &&
      url.searchParams.get('page') === '1'
    );
  });
  await page.getByRole('button', { name: 'Delete selected' }).click();
  const confirmDialog = page.getByRole('dialog', { name: 'Delete 10 selected images?' });
  await confirmDialog.getByRole('button', { name: 'Delete selected' }).click();
  await refreshRequest;

  await expect(page.getByText('No images', { exact: true })).toBeVisible();
  await expect(page.getByText('No images match', { exact: true })).toBeVisible();
});

test('favorites-only batch unfavorite reloads the page with the remaining matches', async ({ page }) => {
  await loadApp(page, {
    galleryImages: manyGalleryImages(10).map((image) => ({ ...image, favorite: true }))
  });

  const favoritesRequest = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return response.request().method() === 'GET' && url.pathname === '/api/gallery' && url.searchParams.get('favorite') === 'true';
  });
  await page.getByLabel('Favorites').check();
  await favoritesRequest;

  await page.getByRole('button', { name: 'Select' }).click();
  await page.getByRole('button', { name: 'Select page' }).click();
  const refreshRequest = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      response.request().method() === 'GET' &&
      url.pathname === '/api/gallery' &&
      url.searchParams.get('favorite') === 'true' &&
      url.searchParams.get('page') === '1'
    );
  });
  await page.getByRole('button', { name: 'Unfavorite selected', exact: true }).click();
  await refreshRequest;

  await expect(page.getByRole('img', { name: 'Paged gallery image 10', exact: true })).toBeVisible();
  await expect(page.getByRole('img', { name: 'Paged gallery image 1', exact: true })).toBeHidden();
  await expect(page.getByRole('button', { name: 'Show size' }).locator('..')).toContainText('1 image');
});

test('gallery queued thumbnails use source images until thumbnails are ready', async ({ page }) => {
  const fullImageRequests: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (url.pathname === '/api/image/queued-thumb.png') fullImageRequests.push(url.pathname);
  });

  await loadApp(page, {
    galleryImages: [
      {
        ...baseGalleryImages[0],
        id: 'queued-thumb',
        prompt: 'Queued thumbnail image',
        filename: 'queued-thumb.png',
        thumbnail_url: '/api/thumb/queued-thumb.png',
        thumbnail_status: 'queued'
      }
    ]
  });

  const image = page.getByRole('img', { name: 'Queued thumbnail image' });
  await expect(image).toBeVisible();
  await expect(image).toHaveAttribute('src', '/api/image/queued-thumb.png');
  expect(fullImageRequests).toContain('/api/image/queued-thumb.png');
});

test('gallery thumbnail refresh rotates queued probes across later pending ids', async ({ page }) => {
  const refreshedIds = new Set<string>();
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (request.method() === 'GET' && url.pathname.startsWith('/api/gallery/paged-img-')) {
      refreshedIds.add(url.pathname.split('/').pop() || '');
    }
  });

  await loadApp(page, {
    galleryImages: manyGalleryImages(5).map((image) => ({ ...image, thumbnail_status: 'queued' as const }))
  });

  await expect(page.getByRole('img', { name: 'Paged gallery image 1', exact: true })).toBeVisible();
  await expect.poll(() => refreshedIds.has('paged-img-5'), { timeout: 6000 }).toBe(true);
});

test('lightbox navigates across pages with 2000 mocked images', async ({ page }) => {
  await loadApp(page, { galleryImages: manyGalleryImages(2000) });

  const nextPageRequest = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return (
      request.method() === 'GET' &&
      url.pathname === '/api/gallery' &&
      url.searchParams.get('page') === '2' &&
      url.searchParams.get('direction') === 'next' &&
      url.searchParams.get('include_counts') === 'false' &&
      url.searchParams.get('include_filter_options') === 'false' &&
      Boolean(url.searchParams.get('cursor'))
    );
  });
  await page.getByRole('img', { name: 'Paged gallery image 9', exact: true }).click();
  const lightbox = page.getByRole('dialog', { name: 'Image Details' });
  await expect(lightbox).toContainText('paged-img-9.png');
  await page.keyboard.press('ArrowRight');
  await nextPageRequest;

  await expect(lightbox).toContainText('paged-img-10.png');
  await expect(page).toHaveURL(/page=2/);
  await expect(page).toHaveURL(/image=paged-img-10/);
});

test('single image delete uses custom confirmation and can be undone before the server delete', async ({ page }) => {
  const deleteRequests: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (request.method() === 'DELETE' && url.pathname === '/api/gallery/img-1') deleteRequests.push(url.pathname);
  });
  await loadApp(page);

  await page.locator('.gallery-card').first().getByRole('button', { name: 'Delete' }).click();
  const confirmDialog = page.getByRole('dialog', { name: 'Delete image?' });
  await expect(confirmDialog).toBeVisible();
  await expect(confirmDialog).toContainText('5 seconds');

  await confirmDialog.getByRole('button', { name: 'Delete' }).click();
  await expect(page.getByRole('status')).toContainText('Image will be deleted in 5 seconds');
  await expect(page.getByRole('img', { name: 'First gallery image' })).toBeHidden();

  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(page.getByRole('status')).toContainText('Image deletion undone');
  await expect(page.getByRole('img', { name: 'First gallery image' })).toBeVisible();
  await page.waitForTimeout(5200);
  expect(deleteRequests).toHaveLength(0);
});

test('single image delete is not revived by a stale gallery refresh', async ({ page }) => {
  await loadApp(page);

  let interceptStaleRefresh = false;
  let resolveStaleRefreshStarted: () => void = () => {};
  let releaseStaleRefresh: () => void = () => {};
  let resolveStaleRefreshFinished: () => void = () => {};
  const staleRefreshStarted = new Promise<void>((resolve) => {
    resolveStaleRefreshStarted = resolve;
  });
  const staleRefreshCanFinish = new Promise<void>((resolve) => {
    releaseStaleRefresh = resolve;
  });
  const staleRefreshFinished = new Promise<void>((resolve) => {
    resolveStaleRefreshFinished = resolve;
  });

  await page.route('**/api/gallery?*', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const isPageRefresh =
      request.method() === 'GET' &&
      url.pathname === '/api/gallery' &&
      url.searchParams.get('page') === '1' &&
      url.searchParams.get('page_size') === '9' &&
      !url.searchParams.has('include_total_bytes');

    if (!interceptStaleRefresh || !isPageRefresh) {
      await route.fallback();
      return;
    }

    interceptStaleRefresh = false;
    const staleResponse = galleryResponse(baseGalleryImages, false, 1);
    resolveStaleRefreshStarted();
    await staleRefreshCanFinish;
    try {
      await route.fulfill(json(staleResponse));
    } catch {
      // The fixed code aborts this stale request before starting the post-delete refresh.
    }
    resolveStaleRefreshFinished();
  });

  await page.locator('.gallery-card').first().getByRole('button', { name: 'Delete' }).click();
  const confirmDialog = page.getByRole('dialog', { name: 'Delete image?' });
  await confirmDialog.getByRole('button', { name: 'Delete' }).click();
  await expect(page.getByRole('img', { name: 'First gallery image' })).toBeHidden();

  interceptStaleRefresh = true;
  await page.evaluate(() => window.dispatchEvent(new PopStateEvent('popstate')));
  await staleRefreshStarted;

  await page.waitForResponse((response) => {
    const url = new URL(response.url());
    return response.request().method() === 'DELETE' && url.pathname === '/api/gallery/img-1';
  });
  releaseStaleRefresh();
  await staleRefreshFinished;

  await expect(page.getByRole('status')).toContainText('Image deleted');
  await expect(page.getByRole('img', { name: 'First gallery image' })).toBeHidden();
});

test('delete all requires typed confirmation before submitting', async ({ page }) => {
  const deleteAllRequest = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return request.method() === 'DELETE' && url.pathname === '/api/gallery';
  });
  await loadApp(page);

  await page.getByRole('button', { name: 'Delete All' }).click();
  const confirmDialog = page.getByRole('dialog', { name: 'Delete all gallery images?' });
  await expect(confirmDialog).toBeVisible();
  await expect(confirmDialog.getByRole('button', { name: 'DELETE' })).toBeDisabled();

  await confirmDialog.getByRole('textbox').fill('DELETE');
  await expect(confirmDialog.getByRole('button', { name: 'DELETE' })).toBeEnabled();
  await confirmDialog.getByRole('button', { name: 'DELETE' }).click();
  await deleteAllRequest;
  await expect(page.getByRole('status')).toContainText('All server images deleted');
});

test('uploaded edit sources append, submit, and clear', async ({ page }) => {
  await loadApp(page);

  const upload = page.getByLabel('Upload edit image');
  await upload.setInputFiles([{ name: 'first.png', mimeType: 'image/png', buffer: PNG_BYTES }]);
  await expect(page.getByRole('button', { name: /Upload · first\.png/ })).toBeVisible();

  await upload.setInputFiles([{ name: 'second.png', mimeType: 'image/png', buffer: PNG_BYTES }]);
  await expect(page.getByRole('button', { name: /Upload · first\.png/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /Upload · second\.png/ })).toBeVisible();

  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('browser upload edit prompt');
  const editRequestPromise = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/edits');
  await page.getByRole('button', { name: 'Edits' }).click();
  const editRequest = await editRequestPromise;
  const body = editRequest.postDataBuffer()?.toString('latin1') || '';
  expect(body).toContain('name="image[]"');
  expect(body).toContain('filename="first.png"');
  expect(body).toContain('filename="second.png"');

  await page.getByRole('button', { name: 'Clear edit sources' }).click();
  await expect(page.getByRole('button', { name: /Upload · first\.png/ })).toBeHidden();
  await expect(page.getByRole('button', { name: /Upload · second\.png/ })).toBeHidden();
  await page.getByRole('button', { name: 'Edits' }).click();
  await expect(page.getByText('Please upload an image or choose one from gallery first')).toBeVisible();
});

test('failed edit submit clears the temporary queued preview', async ({ page }) => {
  await loadApp(page, { editUploadFailure: true });

  await page.getByLabel('Upload edit image').setInputFiles([{ name: 'source.png', mimeType: 'image/png', buffer: PNG_BYTES }]);
  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('browser failed edit prompt');
  await page.getByRole('button', { name: 'Edits' }).click();

  await expect(page.getByText('Upload image is required. (422)')).toBeVisible();
  await expect(page.getByText('Queued', { exact: true })).toBeHidden();
});

test('gallery edit source can be combined with uploaded references', async ({ page }) => {
  await loadApp(page);

  await page.getByLabel('Upload edit image').setInputFiles([{ name: 'extra.png', mimeType: 'image/png', buffer: PNG_BYTES }]);
  await page.locator('.gallery-card').first().getByRole('button', { name: 'Edit' }).click();
  await expect(page.getByRole('button', { name: /Gallery · Gallery: img-1\.png/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /Upload · extra\.png/ })).toBeVisible();

  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('browser edit prompt');
  const editRequestPromise = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/edits/from-gallery/img-1');
  await page.getByRole('button', { name: 'Edits' }).click();
  const editRequest = await editRequestPromise;
  const body = editRequest.postDataBuffer()?.toString('latin1') || '';
  expect(body).toContain('filename="extra.png"');
});

test('job drawer open baseline with 500 running rows', async ({ page }) => {
  test.skip(process.env.RUN_PERFORMANCE_TESTS !== 'true', 'set RUN_PERFORMANCE_TESTS=true to run performance baselines');
  await loadApp(page, { runningJobs: manyJobs(500) });

  const startedAt = await page.evaluate(() => performance.now());
  await page.getByRole('button', { name: 'Job History' }).click();
  await expect(page.getByRole('dialog', { name: 'Job History' })).toBeVisible();
  await expect(page.getByText('history prompt 499')).toBeVisible();
  const elapsedMs = await page.evaluate((start) => performance.now() - start, startedAt);

  expect(elapsedMs).toBeLessThan(500);
});
