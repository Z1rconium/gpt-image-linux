import { expect, test } from '@playwright/test';
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
