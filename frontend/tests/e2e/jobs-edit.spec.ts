import { expect, test, type Page } from '@playwright/test';
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

async function dispatchImagePaste(page: Page, targetSelector: string, fileNames: string[]) {
  return page.evaluate(
    ({ selector, names }) => {
      const target = document.querySelector(selector);
      if (!target) throw new Error(`Paste target not found: ${selector}`);

      const clipboardData = new DataTransfer();
      names.forEach((name) => {
        clipboardData.items.add(new File(['clipboard image'], name, { type: 'image/png' }));
      });
      return target.dispatchEvent(
        new ClipboardEvent('paste', {
          clipboardData,
          bubbles: true,
          cancelable: true
        })
      );
    },
    { selector: targetSelector, names: fileNames }
  );
}

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

test('successful jobs refresh page one lightly without opening a per-job event stream', async ({ page }) => {
  const galleryRefreshes: Array<Record<string, unknown>> = [];
  const perJobEventRequests: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (request.method() === 'POST' && url.pathname === '/api/gallery/search') {
      galleryRefreshes.push(request.postDataJSON() as Record<string, unknown>);
    }
    if (/^\/api\/generate\/(?!jobs\/)[^/]+\/events$/.test(url.pathname)) {
      perJobEventRequests.push(url.pathname);
    }
  });
  await loadApp(page);
  galleryRefreshes.length = 0;

  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('light refresh prompt');
  await page.getByRole('button', { name: 'Generate', exact: true }).click();

  await expect(page.getByRole('img', { name: 'Generated preview' })).toBeVisible();
  await expect.poll(() => galleryRefreshes.length).toBe(1);
  expect(galleryRefreshes[0]).toMatchObject({
    page: 1,
    include_counts: false,
    include_filter_options: false
  });
  expect(perJobEventRequests).toEqual([]);
});

test('successful jobs keep a later gallery page in place and announce new images', async ({ page }) => {
  const galleryRequests: Array<Record<string, unknown>> = [];
  page.on('request', (request) => {
    if (request.method() !== 'POST' || new URL(request.url()).pathname !== '/api/gallery/search') return;
    galleryRequests.push(request.postDataJSON() as Record<string, unknown>);
  });
  await loadApp(page, { galleryImages: manyGalleryImages(20) });
  await page.getByRole('button', { name: 'Next', exact: true }).click();
  await expect(page).toHaveURL(/page=2/);
  galleryRequests.length = 0;

  await page.getByRole('textbox', { name: 'Prompt', exact: true }).fill('later page prompt');
  await page.getByRole('button', { name: 'Generate', exact: true }).click();

  await expect(page.getByRole('status')).toContainText('New images are available in the gallery');
  await expect(page).toHaveURL(/page=2/);
  expect(galleryRequests).toEqual([]);
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

test('pasted clipboard images become edit sources outside editable controls', async ({ page }) => {
  await loadApp(page);

  const bodyPasteDefaultAllowed = await dispatchImagePaste(page, 'body', ['clipboard.png']);
  expect(bodyPasteDefaultAllowed).toBe(false);
  await expect(page.getByRole('button', { name: /Upload · clipboard\.png/ })).toBeVisible();
  await expect(page.getByRole('status')).toContainText('Reference image added from clipboard');

  const prompt = page.getByRole('textbox', { name: 'Prompt', exact: true });
  await prompt.focus();
  const promptPasteDefaultAllowed = await dispatchImagePaste(page, '#prompt', ['prompt.png']);
  expect(promptPasteDefaultAllowed).toBe(true);
  await expect(page.getByRole('button', { name: /Upload · prompt\.png/ })).toHaveCount(0);
});

test('clipboard paste at the edit source limit reports an error without a success toast', async ({ page }) => {
  await loadApp(page);

  const fileNames = Array.from({ length: 16 }, (_, index) => `clipboard-${index + 1}.png`);
  await dispatchImagePaste(page, 'body', fileNames);
  await expect(page.getByRole('button', { name: /^Upload · clipboard-\d+\.png$/ })).toHaveCount(16);
  await expect(page.getByRole('status')).toContainText('Reference image added from clipboard');
  await expect(page.getByRole('status')).toHaveCount(0, { timeout: 4_000 });

  const fullPasteDefaultAllowed = await dispatchImagePaste(page, 'body', ['clipboard-17.png']);
  expect(fullPasteDefaultAllowed).toBe(false);
  await expect(page.getByText('At most 16 edit source images are supported')).toBeVisible();
  await expect(page.getByRole('button', { name: /Upload · clipboard-17\.png/ })).toHaveCount(0);
  await expect(page.getByRole('status')).toHaveCount(0);
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

test('job history keeps a bounded render window for 500 cached rows', async ({ page }) => {
  test.skip(process.env.RUN_PERFORMANCE_TESTS !== 'true', 'set RUN_PERFORMANCE_TESTS=true to run performance baselines');
  await loadApp(page, { historyJobs: manyJobs(500) });

  await page.getByRole('button', { name: 'Job History' }).click();
  const jobsDrawer = page.getByRole('dialog', { name: 'Job History' });
  await jobsDrawer.getByRole('button', { name: 'History', exact: true }).click();

  const historyScroller = jobsDrawer.locator('.mobile-drawer-scroll');
  await expect(jobsDrawer.getByText('history prompt 0')).toBeVisible();
  expect(await jobsDrawer.locator('article').count()).toBeLessThanOrEqual(40);

  await historyScroller.evaluate((node) => node.scrollTo({ top: node.scrollHeight }));
  await expect(jobsDrawer.getByText('history prompt 499')).toBeVisible();
  expect(await jobsDrawer.locator('article').count()).toBeLessThanOrEqual(40);
});
