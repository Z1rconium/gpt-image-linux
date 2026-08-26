import { expect, test, type Request } from '@playwright/test';
import {
  PNG_BYTES,
  baseGalleryImages,
  galleryResponse,
  job,
  json,
  loadApp,
  manyGalleryImages,
  manyJobs,
  mockApi,
  settingsResponse
} from './fixtures/mockApi';

function gallerySearchBody(request: Request) {
  const url = new URL(request.url());
  if (request.method() !== 'POST' || url.pathname !== '/api/gallery/search') return null;
  return request.postDataJSON() as Record<string, unknown>;
}

test('generation, gallery edit source, batch favorite, and lightbox flows work with mocked API', async ({ page }) => {
  await loadApp(page);

  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('browser smoke prompt');
  await page.getByRole('button', { name: 'Generate', exact: true }).click();
  await expect(page.getByRole('img', { name: 'Generated preview' })).toBeVisible();

  await page.locator('.gallery-card').first().getByRole('button', { name: 'Edit' }).click();
  const editDialog = page.getByRole('dialog', { name: 'Edit this image' });
  await expect(editDialog).toBeVisible();
  await editDialog.getByRole('button', { name: 'Clear prompt and describe changes', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Gallery image ready for edits');
  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('browser edit prompt');
  await page.getByRole('button', { name: 'Edits' }).click();
  await expect(page.getByRole('img', { name: 'Generated preview' })).toBeVisible();

  const filterRequest = page.waitForResponse((response) => {
    const body = gallerySearchBody(response.request());
    return body?.prompt === 'First';
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
  await expect(favoriteButton).toHaveCSS('color', 'rgb(180, 83, 9)');

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

test('gallery edit choice loads compatible parameters without changing the active API path', async ({ page }) => {
  await loadApp(page);

  await page.getByLabel('API path').selectOption('/v1/responses');
  await page.locator('.gallery-card').first().getByRole('button', { name: 'Edit' }).click();
  const editDialog = page.getByRole('dialog', { name: 'Edit this image' });
  await editDialog.getByRole('button', { name: 'Keep original prompt', exact: true }).click();

  await expect(page.getByRole('textbox', { name: 'Prompt', exact: true })).toHaveValue('First gallery image');
  await expect(page.getByLabel('API path')).toHaveValue('/v1/responses');
  await expect(page.getByRole('textbox', { name: 'Model' })).toHaveValue('gpt-image-2');
  const promptForm = page.locator('section.app-surface').first();
  const sizeButton = promptForm.locator('button').filter({ hasText: '1024x1024' });
  await expect(sizeButton).toBeEnabled();
  await expect(promptForm.getByLabel(/^Quality/)).toHaveValue('high');
  await expect(promptForm.getByLabel(/^Format\s/)).toHaveValue('webp');
  await expect(promptForm.getByLabel(/^Background/)).toHaveValue('auto');
  await expect(promptForm.getByLabel('Compression')).toHaveValue('80');
  await expect(promptForm.getByLabel('Quantity')).toHaveValue('2');
  await expect(promptForm.getByLabel(/^Response format/)).toHaveValue('url');
  await expect(page.getByRole('button', { name: 'Preview Gallery: img-1.png' })).toBeVisible();
});

test('clearing the gallery prompt focuses the prompt field and cancel preserves form state', async ({ page }) => {
  await loadApp(page);

  const prompt = page.getByRole('textbox', { name: 'Prompt', exact: true });
  await prompt.fill('keep this prompt');
  await page.locator('.gallery-card').first().getByRole('button', { name: 'Edit' }).click();
  const editDialog = page.getByRole('dialog', { name: 'Edit this image' });
  await editDialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(prompt).toHaveValue('keep this prompt');
  await expect(page.getByRole('button', { name: 'Preview Gallery: img-1.png' })).toHaveCount(0);

  await page.locator('.gallery-card').first().getByRole('button', { name: 'Edit' }).click();
  await page.getByRole('dialog', { name: 'Edit this image' }).getByRole('button', { name: 'Clear prompt and describe changes', exact: true }).click();
  await expect(prompt).toHaveValue('');
  await expect(prompt).toBeFocused();
});

test('lightbox edit entry uses the same gallery edit choice flow', async ({ page }) => {
  await loadApp(page);

  await page.getByRole('img', { name: 'First gallery image' }).click();
  const lightbox = page.getByRole('dialog', { name: 'Image Details' });
  await lightbox.getByRole('button', { name: 'Edit', exact: true }).click();
  const editDialog = page.getByRole('dialog', { name: 'Edit this image' });
  await editDialog.getByRole('button', { name: 'Keep original prompt', exact: true }).click();

  await expect(lightbox).toBeHidden();
  await expect(page.getByRole('button', { name: 'Preview Gallery: img-1.png' })).toBeVisible();
});

test('gallery uploads single and selected images to NodeImage and copies result links', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await loadApp(page);

  const firstCard = page.locator('.gallery-card').filter({ hasText: 'First gallery image' });
  const actionGrid = firstCard.locator('.gallery-card-actions');
  await expect(actionGrid.locator('button, a')).toHaveCount(7);
  expect(await actionGrid.evaluate((element) => getComputedStyle(element).gridTemplateColumns.split(' ').length)).toBe(4);
  const singleRequest = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return request.method() === 'POST' && url.pathname === '/api/gallery/img-1/nodeimage-upload';
  });
  await firstCard.getByRole('button', { name: 'Upload to NodeImage' }).click();
  await singleRequest;

  let resultDialog = page.getByRole('dialog', { name: 'NodeImage upload results' });
  await expect(resultDialog).toBeVisible();
  await expect(resultDialog).toContainText('https://cdn.nodeimage.com/img-1.png');
  await resultDialog.getByRole('button', { name: 'Copy direct link' }).click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe('https://cdn.nodeimage.com/img-1.png');
  await resultDialog.getByRole('button', { name: 'Close NodeImage results' }).click();

  await page.getByRole('button', { name: 'Select' }).click();
  await page.getByRole('button', { name: 'Select page' }).click();
  const batchRequest = page.waitForRequest((request) => {
    const url = new URL(request.url());
    if (request.method() !== 'POST' || url.pathname !== '/api/gallery/batch/nodeimage-upload') return false;
    return (request.postDataJSON() as { ids?: string[] }).ids?.length === 2;
  });
  const batchCreateResponse = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return response.request().method() === 'POST' && url.pathname === '/api/gallery/batch/nodeimage-upload';
  });
  const batchEventsRequest = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return request.method() === 'GET' && /^\/api\/gallery\/nodeimage-upload-jobs\/[^/]+\/events$/.test(url.pathname);
  });
  await page.getByRole('button', { name: 'Upload selected to NodeImage', exact: true }).click();
  await batchRequest;
  await expect.poll(async () => (await batchCreateResponse).status()).toBe(202);
  await batchEventsRequest;

  resultDialog = page.getByRole('dialog', { name: 'NodeImage upload results' });
  await expect(resultDialog).toBeVisible();
  await expect(resultDialog).toContainText('2 uploaded');
  await expect(resultDialog.getByText('Direct link', { exact: true })).toHaveCount(2);
});

test('NodeImage batch upload shows queued progress while waiting for job events', async ({ page }) => {
  await loadApp(page, { nodeImageBatchDelayMs: 500 });

  await page.getByRole('button', { name: 'Select' }).click();
  await page.getByRole('button', { name: 'Select page' }).click();
  const createResponse = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return response.request().method() === 'POST' && url.pathname === '/api/gallery/batch/nodeimage-upload';
  });
  const eventsRequest = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return request.method() === 'GET' && /^\/api\/gallery\/nodeimage-upload-jobs\/[^/]+\/events$/.test(url.pathname);
  });

  await page.getByRole('button', { name: 'Upload selected to NodeImage', exact: true }).click();
  await expect.poll(async () => (await createResponse).status()).toBe(202);
  await expect(page.getByRole('status').filter({ hasText: 'Uploading to NodeImage' })).toContainText('Uploaded 0 / 2');
  await expect(page.getByRole('status').filter({ hasText: 'Uploading to NodeImage' })).toContainText('0%');
  await eventsRequest;

  const resultDialog = page.getByRole('dialog', { name: 'NodeImage upload results' });
  await expect(resultDialog).toBeVisible();
  await expect(resultDialog).toContainText('2 uploaded');
});

test('NodeImage batch upload displays partial failure results from the job payload', async ({ page }) => {
  await loadApp(page, { nodeImageBatchFailureIds: ['img-2'] });

  await page.getByRole('button', { name: 'Select' }).click();
  await page.getByRole('button', { name: 'Select page' }).click();
  await page.getByRole('button', { name: 'Upload selected to NodeImage', exact: true }).click();

  const resultDialog = page.getByRole('dialog', { name: 'NodeImage upload results' });
  await expect(resultDialog).toBeVisible();
  await expect(resultDialog).toContainText('1 uploaded, 1 failed');
  await expect(resultDialog.getByRole('heading', { name: 'img-1.png' })).toBeVisible();
  await expect(resultDialog.getByRole('heading', { name: 'img-2.png' })).toBeVisible();
  await expect(resultDialog).toContainText('NodeImage upload failed in fixture');
});

test('NodeImage batch upload cancellation calls the job cancel endpoint and keeps completed results', async ({ page }) => {
  await loadApp(page, { nodeImageBatchDelayMs: 1000 });

  await page.getByRole('button', { name: 'Select' }).click();
  await page.getByRole('button', { name: 'Select page' }).click();
  const createResponse = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return response.request().method() === 'POST' && url.pathname === '/api/gallery/batch/nodeimage-upload';
  });
  const cancelRequest = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return request.method() === 'POST' && /^\/api\/gallery\/nodeimage-upload-jobs\/[^/]+\/cancel$/.test(url.pathname);
  });
  const terminalStatusRequest = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return request.method() === 'GET' && /^\/api\/gallery\/nodeimage-upload-jobs\/[^/]+$/.test(url.pathname);
  });

  await page.getByRole('button', { name: 'Upload selected to NodeImage', exact: true }).click();
  await expect.poll(async () => (await createResponse).status()).toBe(202);
  const operationStatus = page.getByRole('status').filter({ hasText: 'Uploading to NodeImage' });
  await expect(operationStatus).toContainText('Uploaded 0 / 2');
  await operationStatus.getByRole('button', { name: 'Cancel', exact: true }).click();
  await cancelRequest;
  await terminalStatusRequest;

  const resultDialog = page.getByRole('dialog', { name: 'NodeImage upload results' });
  await expect(resultDialog).toBeVisible();
  await expect(resultDialog).toContainText('1 uploaded, 1 cancelled');
  await expect(resultDialog.getByText('Uploaded', { exact: true })).toHaveCount(1);
  await expect(resultDialog.getByText('Not uploaded', { exact: true })).toHaveCount(1);
});

test('NodeImage batch upload keeps tracking the job when the cancellation request fails', async ({ page }) => {
  await loadApp(page, { nodeImageBatchDelayMs: 750, nodeImageCancelFailure: true });

  await page.getByRole('button', { name: 'Select' }).click();
  await page.getByRole('button', { name: 'Select page' }).click();
  await page.getByRole('button', { name: 'Upload selected to NodeImage', exact: true }).click();

  const operationStatus = page.getByRole('status').filter({ hasText: 'Uploading to NodeImage' });
  await expect(operationStatus).toContainText('Uploaded 0 / 2');
  await operationStatus.getByRole('button', { name: 'Cancel', exact: true }).click();

  await expect(page.getByRole('alert').filter({ hasText: 'Temporary cancellation failure' })).toBeVisible();
  await expect(operationStatus.getByRole('button', { name: 'Cancel', exact: true })).toBeEnabled();

  const resultDialog = page.getByRole('dialog', { name: 'NodeImage upload results' });
  await expect(resultDialog).toBeVisible();
  await expect(resultDialog).toContainText('2 uploaded');
  await expect(page.getByRole('status').filter({ hasText: 'NodeImage upload complete: 2 succeeded, 0 failed' })).toBeVisible();
});

test('NodeImage batch upload keeps tracking after cancellation status polling fails', async ({ page }) => {
  await loadApp(page, { nodeImageBatchDelayMs: 1000, nodeImageCancelStatusFailure: true });

  await page.getByRole('button', { name: 'Select' }).click();
  await page.getByRole('button', { name: 'Select page' }).click();
  await page.getByRole('button', { name: 'Upload selected to NodeImage', exact: true }).click();

  const operationStatus = page.getByRole('status').filter({ hasText: 'Uploading to NodeImage' });
  await expect(operationStatus).toContainText('Uploaded 0 / 2');
  await operationStatus.getByRole('button', { name: 'Cancel', exact: true }).click();

  await expect(page.getByRole('alert').filter({ hasText: 'Temporary cancellation status failure' })).toBeVisible();
  await expect(operationStatus.getByRole('button', { name: 'Cancel', exact: true })).toBeEnabled();

  const resultDialog = page.getByRole('dialog', { name: 'NodeImage upload results' });
  await expect(resultDialog).toBeVisible();
  await expect(resultDialog).toContainText('1 uploaded, 1 cancelled');
});

test('NodeImage cancellation reports a completed job as complete', async ({ page }) => {
  await loadApp(page, { nodeImageBatchDelayMs: 1000, nodeImageCancelReturnsCompleted: true });

  await page.getByRole('button', { name: 'Select' }).click();
  await page.getByRole('button', { name: 'Select page' }).click();
  await page.getByRole('button', { name: 'Upload selected to NodeImage', exact: true }).click();

  const operationStatus = page.getByRole('status').filter({ hasText: 'Uploading to NodeImage' });
  await expect(operationStatus).toContainText('Uploaded 0 / 2');
  await operationStatus.getByRole('button', { name: 'Cancel', exact: true }).click();

  const resultDialog = page.getByRole('dialog', { name: 'NodeImage upload results' });
  await expect(resultDialog).toBeVisible();
  await expect(resultDialog).toContainText('2 uploaded');
  await expect(page.getByRole('status').filter({ hasText: 'NodeImage upload complete: 2 succeeded, 0 failed' })).toBeVisible();
  await expect(page.getByText('NodeImage upload cancelled: 2 succeeded, 0 failed')).toHaveCount(0);
});

test('NodeImage batch results use response filenames for cross-page selection', async ({ page }) => {
  await loadApp(page, { galleryImages: manyGalleryImages(10) });

  await page.getByLabel('Filter prompt').fill('Paged gallery image');
  await expect(page.getByRole('img', { name: 'Paged gallery image 1', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Select' }).click();
  await page.getByRole('button', { name: 'Select filtered' }).click();
  await expect(page.getByText('10 selected from current filters')).toBeVisible();
  await page.getByRole('button', { name: 'Upload selected to NodeImage', exact: true }).click();

  const resultDialog = page.getByRole('dialog', { name: 'NodeImage upload results' });
  await expect(resultDialog).toBeVisible();
  await expect(resultDialog.getByRole('heading', { name: 'paged-img-10.png' })).toBeVisible();
});

test('NodeImage batch results fall back for legacy responses without filenames', async ({ page }) => {
  await loadApp(page);
  await page.route('**/api/gallery/batch/nodeimage-upload', async (route) => {
    await route.fulfill(json({
      requested_count: 2,
      uploaded_count: 1,
      failed_count: 1,
      results: [
        {
          image_id: 'img-1',
          status: 'ok',
          url: 'https://cdn.nodeimage.com/img-1.png',
          markdown: '![image](https://cdn.nodeimage.com/img-1.png)',
          error: null
        },
        {
          image_id: 'missing-legacy-id',
          status: 'error',
          url: null,
          markdown: null,
          error: 'Gallery entry not found'
        }
      ]
    }));
  });

  await page.getByRole('button', { name: 'Select' }).click();
  await page.getByRole('button', { name: 'Select page' }).click();
  await page.getByRole('button', { name: 'Upload selected to NodeImage', exact: true }).click();

  const resultDialog = page.getByRole('dialog', { name: 'NodeImage upload results' });
  await expect(resultDialog.getByRole('heading', { name: 'img-1.png' })).toBeVisible();
  await expect(resultDialog.getByRole('heading', { name: 'missing-legacy-id' })).toBeVisible();
});

test('NodeImage results group failures first, progressively reveal successes, and copy complete ordered lists', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await loadApp(page, {
    galleryImages: manyGalleryImages(25),
    nodeImageBatchFailureIds: ['paged-img-25']
  });

  await page.getByLabel('Filter prompt').fill('Paged gallery image');
  await expect(page.getByRole('img', { name: 'Paged gallery image 1', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Select' }).click();
  await page.getByRole('button', { name: 'Select filtered' }).click();
  await page.getByRole('button', { name: 'Upload selected to NodeImage', exact: true }).click();

  const resultDialog = page.getByRole('dialog', { name: 'NodeImage upload results' });
  await expect(resultDialog).toBeVisible();
  await expect(resultDialog.locator('h3')).toHaveText(['Failed (1)', 'Uploaded (24)']);
  await expect(resultDialog.getByRole('heading', { name: 'paged-img-25.png' })).toBeVisible();
  await expect(resultDialog.getByRole('heading', { name: 'paged-img-20.png' })).toBeVisible();
  await expect(resultDialog.getByRole('heading', { name: 'paged-img-21.png' })).toBeHidden();
  await expect(resultDialog.getByRole('button', { name: 'Show more (4 remaining)' })).toBeVisible();
  await expect(resultDialog.getByText('Direct link', { exact: true })).toHaveCount(20);

  await resultDialog.getByRole('button', { name: 'Copy all direct links' }).click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(
    Array.from({ length: 24 }, (_, index) => `https://cdn.nodeimage.com/paged-img-${index + 1}.png`).join('\n')
  );
  await resultDialog.getByRole('button', { name: 'Copy all Markdown links' }).click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(
    Array.from({ length: 24 }, (_, index) => `![Paged gallery image ${index + 1}](https://cdn.nodeimage.com/paged-img-${index + 1}.png)`).join('\n')
  );

  await resultDialog.getByRole('button', { name: 'Show more (4 remaining)' }).click();
  await expect(resultDialog.getByRole('heading', { name: 'paged-img-24.png' })).toBeVisible();
  await expect(resultDialog.getByText('Direct link', { exact: true })).toHaveCount(24);
  await expect(resultDialog.getByRole('button', { name: /Show more/ })).toHaveCount(0);

  const desktopBounds = await resultDialog.boundingBox();
  expect(desktopBounds).not.toBeNull();
  expect(desktopBounds!.x).toBeGreaterThanOrEqual(0);
  expect(desktopBounds!.x + desktopBounds!.width).toBeLessThanOrEqual(1280);

  await page.setViewportSize({ width: 390, height: 844 });
  const mobileBounds = await resultDialog.boundingBox();
  expect(mobileBounds).not.toBeNull();
  expect(mobileBounds!.x).toBeGreaterThanOrEqual(0);
  expect(mobileBounds!.x + mobileBounds!.width).toBeLessThanOrEqual(390);
  expect(await resultDialog.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
});

test('gallery hides NodeImage upload actions when the integration is unavailable', async ({ page }) => {
  await loadApp(page, {
    settings: {
      ...settingsResponse,
      nodeimage: {
        ...settingsResponse.nodeimage,
        enabled: true,
        has_api_key: true,
        api_key_resolvable: false,
        api_key_source: 'env'
      }
    }
  });

  await expect(page.locator('.gallery-card').first().getByRole('button', { name: 'Upload to NodeImage' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Select' }).click();
  await page.getByRole('button', { name: 'Select page' }).click();
  await expect(page.getByRole('button', { name: 'Upload selected to NodeImage', exact: true })).toHaveCount(0);
  const actionGrid = page.locator('.gallery-card').first().locator('.gallery-card-actions');
  await expect(actionGrid.locator('button, a')).toHaveCount(6);
  expect(await actionGrid.evaluate((element) => getComputedStyle(element).gridTemplateColumns.split(' ').length)).toBe(4);
});

test('lightbox shows a ready thumbnail until the original image loads', async ({ page }) => {
  await loadApp(page, {
    galleryImages: [
      {
        ...baseGalleryImages[0],
        id: 'loading-original',
        prompt: 'Loading original image',
        filename: 'loading-original.png',
        thumbnail_url: '/api/thumb/loading-original.png',
        thumbnail_status: 'ready'
      }
    ]
  });

  let releaseOriginal: () => void = () => {};
  const originalGate = new Promise<void>((resolve) => {
    releaseOriginal = resolve;
  });
  await page.route('**/api/image/loading-original.png', async (route) => {
    await originalGate;
    await route.fulfill({ status: 200, contentType: 'image/png', body: PNG_BYTES });
  });

  await page.getByRole('img', { name: 'Loading original image' }).click();
  const lightbox = page.getByRole('dialog', { name: 'Image Details' });
  await expect(lightbox).toBeVisible();
  await expect(lightbox.getByRole('status')).toHaveText('Loading original image...');
  await expect(lightbox.locator('.lightbox-preview-img')).toBeVisible();
  await expect(lightbox.locator('.lightbox-img')).toHaveCSS('opacity', '0');

  releaseOriginal();
  await expect(lightbox.getByRole('status')).toHaveCount(0);
  await expect(lightbox.locator('.lightbox-img')).toHaveCSS('opacity', '1');
});

test('lightbox keeps describe, analyze, and stored AI metadata without reverse prompt action', async ({ page }) => {
  await loadApp(page);

  await page.getByRole('img', { name: 'First gallery image' }).click();
  const lightbox = page.getByRole('dialog', { name: 'Image Details' });
  await expect(lightbox.getByRole('button', { name: 'Describe', exact: true })).toBeVisible();
  await expect(lightbox.getByRole('button', { name: 'Analyze', exact: true })).toBeVisible();
  await expect(lightbox.getByRole('button', { name: 'Prompt', exact: true })).toHaveCount(0);
  await expect(lightbox).toContainText('Stored AI description');
  await expect(lightbox).toContainText('Stored AI prompt');
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
    const body = gallerySearchBody(request);
    return (
      body?.page === 2 &&
      body.direction === 'next' &&
      typeof body.cursor === 'string' &&
      body.cursor.length > 0
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

test('gallery url state restores durable filters, lightbox, and job history tab', async ({ page }) => {
  await mockApi(page);
  await page.goto('/?prompt=Second&favorite=true&image=img-2&jobs=history');

  await expect(page.getByLabel('Filter prompt')).toHaveValue('');
  await expect(page).not.toHaveURL(/prompt=/);
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
    const body = gallerySearchBody(request);
    return body?.prompt === 'First';
  });
  await page.getByLabel('Filter prompt').fill('First');
  await promptFilterRequest;
  await expect(page).not.toHaveURL(/prompt=/);
});

test('gallery page input jumps to the requested page on Enter', async ({ page }) => {
  await loadApp(page, { galleryImages: manyGalleryImages(10) });

  await expect(page.getByRole('img', { name: 'Paged gallery image 1', exact: true })).toBeVisible();
  const pageInput = page.getByLabel('Jump to page');
  await expect(pageInput).toHaveValue('1');

  const nextPageRequest = page.waitForRequest((request) => {
    const body = gallerySearchBody(request);
    return body?.page === 2 && body.cursor === null;
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
    const body = gallerySearchBody(request);
    return (
      body?.page === 2 &&
      body.direction === 'next' &&
      typeof body.cursor === 'string' &&
      body.cursor.length > 0
    );
  });
  await page.getByRole('button', { name: 'Next' }).click();
  const nextRequest = await nextPageRequest;
  const nextBody = gallerySearchBody(nextRequest);
  expect(nextBody?.include_counts).toBe(false);
  expect(nextBody?.include_filter_options).toBe(false);
  await expect(page.getByRole('img', { name: 'Paged gallery image 10', exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'Select' }).click();
  await page.getByRole('button', { name: 'Select page' }).click();
  await page.getByRole('button', { name: 'Favorite selected', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Updated');
  await page.getByRole('button', { name: 'Cancel selection' }).click();

  const filterRequest = page.waitForRequest((request) => {
    const body = gallerySearchBody(request);
    return body?.prompt === '500';
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
    const body = gallerySearchBody(response.request());
    return body?.prompt === 'Paged gallery image';
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
    const body = gallerySearchBody(response.request());
    return (
      body?.prompt === 'Paged gallery image' &&
      body.page === 1
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
    const body = gallerySearchBody(response.request());
    return body?.favorite === true;
  });
  await page.getByLabel('Favorites').check();
  await favoritesRequest;

  await page.getByRole('button', { name: 'Select' }).click();
  await page.getByRole('button', { name: 'Select page' }).click();
  const refreshRequest = page.waitForResponse((response) => {
    const body = gallerySearchBody(response.request());
    return (
      body?.favorite === true &&
      body.page === 1
    );
  });
  await page.getByRole('button', { name: 'Unfavorite selected', exact: true }).click();
  await refreshRequest;

  await expect(page.getByRole('img', { name: 'Paged gallery image 10', exact: true })).toBeVisible();
  await expect(page.getByRole('img', { name: 'Paged gallery image 1', exact: true })).toBeHidden();
  await expect(page.getByRole('button', { name: 'Show size' }).locator('..')).toContainText('1 image');
});

test('gallery queued thumbnails use lightweight placeholders until thumbnails are ready', async ({ page }) => {
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
  await expect(image).toHaveAttribute('src', /^data:image\/gif;base64,/);
  await page.waitForTimeout(250);
  expect(fullImageRequests).toEqual([]);
});

test('gallery thumbnail refresh batches all queued statuses without per-image requests', async ({ page }) => {
  const statusBatches: string[][] = [];
  const individualRequests: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (request.method() === 'POST' && url.pathname === '/api/gallery/thumbnails/status') {
      statusBatches.push((request.postDataJSON() as { ids?: string[] }).ids || []);
    }
    if (request.method() === 'GET' && url.pathname.startsWith('/api/gallery/paged-img-')) {
      individualRequests.push(url.pathname);
    }
  });

  await loadApp(page, {
    galleryImages: manyGalleryImages(5).map((image) => ({ ...image, thumbnail_status: 'queued' as const }))
  });

  await expect(page.getByRole('img', { name: 'Paged gallery image 1', exact: true })).toBeVisible();
  await expect.poll(() => statusBatches.some((ids) => ids.includes('paged-img-5')), { timeout: 6000 }).toBe(true);
  expect(statusBatches[0]).toHaveLength(5);
  expect(individualRequests).toEqual([]);
});

test('gallery AI batch analysis uses SSE without status polling', async ({ page }) => {
  const statusPolls: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (request.method() === 'GET' && url.pathname === '/api/assistant/gallery/batch/analyze/assistant-analysis-job') {
      statusPolls.push(url.pathname);
    }
  });
  await loadApp(page);

  await page.getByRole('button', { name: 'Select', exact: true }).click();
  await page.getByRole('img', { name: 'First gallery image', exact: true }).click();
  await page.getByRole('button', { name: 'AI analyze', exact: true }).click();

  await expect(page.getByRole('status')).toContainText('AI analysis complete. Analyzed 1.');
  expect(statusPolls).toEqual([]);
});

test('lightbox navigates across pages with 2000 mocked images', async ({ page }) => {
  await loadApp(page, { galleryImages: manyGalleryImages(2000) });

  const nextPageRequest = page.waitForRequest((request) => {
    const body = gallerySearchBody(request);
    return (
      body?.page === 2 &&
      body.direction === 'next' &&
      body.include_counts === false &&
      body.include_filter_options === false &&
      typeof body.cursor === 'string' &&
      body.cursor.length > 0
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

test('gallery mutations invalidate prefetched lightbox pages', async ({ page }) => {
  await loadApp(page, { galleryImages: manyGalleryImages(20) });

  const pageTwoResponse = page.waitForResponse((response) => {
    const body = gallerySearchBody(response.request());
    return body?.page === 2 && body.direction === 'next';
  });
  await page.getByRole('button', { name: 'Next', exact: true }).click();
  await pageTwoResponse;

  const firstPageThreePrefetch = page.waitForResponse((response) => {
    const body = gallerySearchBody(response.request());
    return body?.page === 3 && body.direction === 'next';
  });
  await page.getByRole('img', { name: 'Paged gallery image 18', exact: true }).click();
  await firstPageThreePrefetch;
  await page.keyboard.press('Escape');

  await page.getByRole('button', { name: 'Select', exact: true }).click();
  await page.getByRole('img', { name: 'Paged gallery image 10', exact: true }).click();
  const refreshedPageTwo = page.waitForResponse((response) => {
    const body = gallerySearchBody(response.request());
    return body?.page === 2 && body.cursor === null;
  });
  await page.getByRole('button', { name: 'Delete selected', exact: true }).click();
  const confirmDialog = page.getByRole('dialog', { name: 'Delete 1 selected image?' });
  await confirmDialog.getByRole('button', { name: 'Delete selected' }).click();
  await refreshedPageTwo;
  await page.getByRole('button', { name: 'Cancel selection', exact: true }).click();

  await expect(page.getByRole('img', { name: 'Paged gallery image 10', exact: true })).toBeHidden();
  await expect(page.getByRole('img', { name: 'Paged gallery image 19', exact: true })).toBeVisible();

  const refreshedPageThree = page.waitForResponse((response) => {
    const body = gallerySearchBody(response.request());
    return body?.page === 3 && body.direction === 'next';
  });
  await page.getByRole('img', { name: 'Paged gallery image 19', exact: true }).click();
  await refreshedPageThree;
  await page.keyboard.press('ArrowRight');

  const lightbox = page.getByRole('dialog', { name: 'Image Details' });
  await expect(lightbox).toContainText('paged-img-20.png');
  await expect(page).toHaveURL(/page=3/);
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

  await page.route('**/api/gallery/search', async (route) => {
    const request = route.request();
    const body = gallerySearchBody(request);
    const isPageRefresh =
      body?.page === 1 &&
      body.page_size === 9 &&
      body.include_total_bytes === false;

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
