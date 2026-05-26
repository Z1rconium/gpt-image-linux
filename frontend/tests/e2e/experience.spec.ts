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
  thumbnail_url: string;
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
    api_key_masked: '********',
    has_api_key: true,
    api_key_source: 'stored',
    api_key_env_var: null
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

function galleryResponse(images = baseGalleryImages, includeTotalBytes = false, requestedPage = 1) {
  const pageSize = 9;
  const totalPages = Math.max(Math.ceil(images.length / pageSize), 1);
  const parsedPage = Number.isFinite(requestedPage) ? requestedPage : 1;
  const page = Math.min(Math.max(parsedPage, 1), totalPages);
  const pageImages = images.slice((page - 1) * pageSize, page * pageSize);

  return {
    total: images.length,
    total_bytes: includeTotalBytes ? images.reduce((sum, image) => sum + image.bytes, 0) : 0,
    page,
    page_size: pageSize,
    total_pages: totalPages,
    has_prev: page > 1,
    has_next: page < totalPages,
    images: pageImages,
    filter_options: {
      models: ['gpt-image-2'],
      presets: ['Default'],
      sizes: ['1024x1024', '1536x1024']
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

function manyGalleryImages(count: number) {
  return Array.from({ length: count }, (_, index) => ({
    ...baseGalleryImages[index % baseGalleryImages.length],
    id: `paged-img-${index + 1}`,
    prompt: `Paged gallery image ${index + 1}`,
    filename: `paged-img-${index + 1}.png`,
    thumbnail_url: `/api/thumb/paged-img-${index + 1}.png`
  }));
}

async function mockApi(page: Page, options: MockOptions = {}) {
  let authenticated = options.authenticated ?? true;
  let galleryImages = [...(options.galleryImages ?? baseGalleryImages)];
  let promptSnippets = [...(options.promptSnippets ?? basePromptSnippets)];
  let promptSnippetCounter = promptSnippets.length + 1;
  const mockedSettings = options.settings ?? settingsResponse;
  const runningJobs = options.runningJobs ?? [];
  let historyJobs = options.historyJobs ?? [job('history-1', 'saved prompt')];

  await page.addInitScript(() => {
    localStorage.setItem('gpt-image-panel-language', 'en');
  });

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
    if (url.pathname === '/api/settings') {
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
    if (url.pathname.endsWith('/health') && url.pathname.startsWith('/api/settings/presets/')) {
      await route.fulfill(json({ status: 'ok', checks: [{ name: 'api_url', status: 'ok', message: 'ok' }] }));
      return;
    }
    if (url.pathname === '/api/gallery' && request.method() === 'GET') {
      const prompt = url.searchParams.get('prompt') || '';
      const requestedPage = Number.parseInt(url.searchParams.get('page') || '1', 10);
      const images = prompt
        ? galleryImages.filter((image) => image.prompt.toLowerCase().includes(prompt.toLowerCase()))
        : galleryImages;
      await route.fulfill(json(galleryResponse(images, url.searchParams.get('include_total_bytes') === 'true', requestedPage)));
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
      const ids = new Set<string>(body.ids || []);
      galleryImages = galleryImages.filter((entry) => !ids.has(entry.id));
      await route.fulfill(json({ status: 'ok', count: body.ids?.length || 0, file_count: body.ids?.length || 0 }));
      return;
    }
    if (url.pathname === '/api/gallery/batch/favorite') {
      const body = JSON.parse(request.postData() || '{}');
      await route.fulfill(json({ status: 'ok', count: body.ids?.length || 0, file_count: 0 }));
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
  await expect(page.getByRole('heading', { name: 'Prompt', exact: true })).toBeVisible();
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
  await expect(drawer).toContainText('Literal keys are saved as plaintext.');
  await expect(page.getByLabel('Filter prompt')).toBeVisible();

  for (let index = 0; index < 12; index += 1) {
    await page.keyboard.press('Tab');
    await expect.poll(() => drawer.evaluate((node) => node.contains(document.activeElement))).toBe(true);
  }

  await page.keyboard.press('Escape');
  await expect(drawer).toBeHidden();
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

  await page.getByLabel('Filter prompt').fill('First');
  await expect(page.getByRole('img', { name: 'First gallery image' })).toBeVisible();

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
  await page.getByRole('button', { name: 'Optimize' }).click();
  const request = await optimizeRequest;
  expect(request.postDataJSON()).toMatchObject({
    prompt: 'small cabin, high detail',
    api_path: '/v1/images/generations'
  });
  await expect(prompt).toHaveValue('Optimized small cabin, high detail');
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(prompt).toHaveValue('small cabin, high detail');
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

  await page.getByRole('img', { name: 'Paged gallery image 9', exact: true }).click();
  await expect(lightbox).toContainText('paged-img-9.png');
  const nextPageRequest = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return request.method() === 'GET' && url.pathname === '/api/gallery' && url.searchParams.get('page') === '2';
  });
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

  await page.getByLabel('Filter prompt').fill('First');
  await expect(page).toHaveURL(/prompt=First/);
});

test('gallery page input jumps to the requested page on Enter', async ({ page }) => {
  await loadApp(page, { galleryImages: manyGalleryImages(10) });

  await expect(page.getByRole('img', { name: 'Paged gallery image 1', exact: true })).toBeVisible();
  const pageInput = page.getByLabel('Jump to page');
  await expect(pageInput).toHaveValue('1');

  const nextPageRequest = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return request.method() === 'GET' && url.pathname === '/api/gallery' && url.searchParams.get('page') === '2';
  });
  await pageInput.fill('2');
  await pageInput.press('Enter');
  await nextPageRequest;

  await expect(page.getByRole('img', { name: 'Paged gallery image 10', exact: true })).toBeVisible();
  await expect(page.getByRole('img', { name: 'Paged gallery image 1', exact: true })).toBeHidden();
  await expect(pageInput).toHaveValue('2');
  await expect(page).toHaveURL(/page=2/);
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
