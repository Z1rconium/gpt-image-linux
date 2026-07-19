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
    bottom: (element as HTMLElement).style.bottom,
    marginTop: getComputedStyle(element).marginTop
  }));
  expect(heldBox).not.toBeNull();
  expect(Math.abs(Math.round(heldBox?.x || 0) - Math.round(dragInitialBox?.x || 0))).toBeLessThanOrEqual(3);
  expect(Math.abs(Math.round(heldBox?.y || 0) - Math.round(dragInitialBox?.y || 0))).toBeLessThanOrEqual(3);
  expect(Math.abs(Math.round(parseFloat(heldStyle.left)) - Math.round(dragInitialBox?.x || 0))).toBeLessThanOrEqual(3);
  expect(Math.abs(Math.round(parseFloat(heldStyle.top)) - Math.round(dragInitialBox?.y || 0))).toBeLessThanOrEqual(3);
  expect(heldStyle.bottom).toBe('auto');
  expect(heldStyle.marginTop).toBe('0px');

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

test('reverse prompt dialog uploads, replaces, copies, saves, applies, and resets local images', async ({ page }) => {
  await loadApp(page);

  const prompt = page.getByRole('textbox', { name: 'Prompt', exact: true });
  await page.getByRole('button', { name: 'Reverse prompt', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Reverse prompt' });
  await expect(dialog).toBeVisible();

  const fileInput = dialog.locator('input[type="file"]');
  await fileInput.setInputFiles({ name: 'first.png', mimeType: 'image/png', buffer: PNG_BYTES });
  await expect(dialog.getByRole('img', { name: 'first.png' })).toBeVisible();
  await expect(dialog.getByRole('button', { name: 'Replace image' })).toBeVisible();
  await expect(dialog.getByRole('button', { name: 'Remove image' })).toBeVisible();

  const firstRequestPromise = page.waitForRequest((request) => {
    return request.method() === 'POST' && new URL(request.url()).pathname === '/api/assistant/image/prompt';
  });
  await dialog.getByRole('button', { name: 'Reverse prompt', exact: true }).click();
  const firstRequest = await firstRequestPromise;
  expect(firstRequest.postData()).toContain('name="target_language"');
  expect(firstRequest.postData()).toContain('en');
  await expect(dialog.getByRole('heading', { name: 'Generated prompt' })).toBeVisible();
  await expect(dialog).toContainText('A bright red square centered on a clean white background');
  await page.screenshot({ path: '/tmp/gpt-image-reverse-prompt-desktop.png' });

  await dialog.getByRole('button', { name: 'Copy', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Prompt copied');

  await fileInput.setInputFiles({ name: 'replacement.png', mimeType: 'image/png', buffer: PNG_BYTES });
  await expect(dialog.getByRole('img', { name: 'replacement.png' })).toBeVisible();
  await expect(dialog.getByRole('heading', { name: 'Generated prompt' })).toHaveCount(0);
  await dialog.getByRole('button', { name: 'Remove image' }).click();
  await expect(dialog.getByRole('button', { name: 'Choose an image' })).toBeVisible();

  await fileInput.setInputFiles({ name: 'saved.png', mimeType: 'image/png', buffer: PNG_BYTES });
  await dialog.getByRole('button', { name: 'Reverse prompt', exact: true }).click();
  await expect(dialog.getByRole('heading', { name: 'Generated prompt' })).toBeVisible();
  await dialog.getByRole('button', { name: 'Save snippet' }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole('status')).toContainText('Prompt snippet saved');

  await page.getByRole('button', { name: 'Prompt snippets' }).click();
  const snippets = page.getByRole('dialog', { name: 'Prompt Snippets' });
  await expect(snippets.getByRole('heading', { name: 'Reverse prompt', exact: true })).toBeVisible();
  await snippets.getByRole('button', { name: 'Close prompt snippets' }).click();

  await page.getByRole('button', { name: 'Reverse prompt', exact: true }).click();
  await expect(dialog.getByRole('button', { name: 'Choose an image' })).toBeVisible();
  await fileInput.setInputFiles({ name: 'apply.png', mimeType: 'image/png', buffer: PNG_BYTES });
  await dialog.getByRole('button', { name: 'Reverse prompt', exact: true }).click();
  await dialog.getByRole('button', { name: 'Apply to prompt' }).click();
  await expect(dialog).toBeHidden();
  await expect(prompt).toHaveValue('A bright red square centered on a clean white background');

  await page.getByRole('button', { name: 'Reverse prompt', exact: true }).click();
  await expect(dialog.getByRole('button', { name: 'Choose an image' })).toBeVisible();
  await dialog.getByRole('button', { name: 'Close', exact: true }).click();
  await expect(dialog).toBeHidden();
});

test('reverse prompt trial optimization iterates, preserves the last success, and uses the latest prompt', async ({ page }) => {
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
  await loadApp(page, {
    optimizedPrompts: ['First refined prompt', 'Second refined prompt', 'unused', 'Applied refined prompt'],
    optimizeFailureAt: 3,
    optimizeDelayMs: 150
  });

  const prompt = page.getByRole('textbox', { name: 'Prompt', exact: true });
  await page.getByRole('button', { name: 'Reverse prompt', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Reverse prompt' });
  const fileInput = dialog.locator('input[type="file"]');
  await fileInput.setInputFiles({ name: 'target.png', mimeType: 'image/png', buffer: PNG_BYTES });
  await dialog.getByRole('button', { name: 'Reverse prompt', exact: true }).click();
  await expect(dialog.getByRole('button', { name: 'Try optimization' })).toBeEnabled();

  const firstOptimizeRequest = page.waitForRequest(
    (request) => request.method() === 'POST' && new URL(request.url()).pathname === '/api/assistant/image/prompt/optimize'
  );
  await dialog.getByRole('button', { name: 'Try optimization' }).click();
  await expect(dialog).toContainText('Generating a trial image and comparing');
  await expect(dialog.getByRole('button', { name: 'Replace image' })).toBeDisabled();
  await expect(dialog.getByRole('button', { name: 'Remove image' })).toBeDisabled();
  await expect(dialog.getByRole('button', { name: 'Reverse prompt', exact: true })).toBeDisabled();
  await expect(dialog.getByRole('button', { name: 'Copy', exact: true })).toBeDisabled();
  const firstRequest = await firstOptimizeRequest;
  expect(firstRequest.postData()).toContain('A bright red square centered on a clean white background');

  await expect(dialog.getByTestId('image-prompt-comparison')).toBeVisible();
  await expect(dialog.getByRole('img', { name: 'Target image' })).toBeVisible();
  await expect(dialog.getByRole('img', { name: 'Trial image' })).toBeVisible();
  await expect(dialog).toContainText('Comparison summary 1');
  await expect(dialog).toContainText('First refined prompt');
  await expect(dialog).toContainText('Iteration 1');
  await expect(dialog).toContainText('preset-default-model · 896x896 · 140 ms');
  await page.screenshot({ path: '/tmp/gpt-image-reverse-prompt-optimized-desktop.png' });

  const secondOptimizeRequest = page.waitForRequest(
    (request) => request.method() === 'POST' && new URL(request.url()).pathname === '/api/assistant/image/prompt/optimize'
  );
  await dialog.getByRole('button', { name: 'Try optimization again' }).click();
  const secondRequest = await secondOptimizeRequest;
  expect(secondRequest.postData()).toContain('First refined prompt');
  await expect(dialog).toContainText('Second refined prompt');
  await expect(dialog).toContainText('Comparison summary 2');
  await expect(dialog).toContainText('Iteration 2');

  await dialog.getByRole('button', { name: 'Try optimization again' }).click();
  await expect(dialog.getByRole('alert')).toContainText('custom size unsupported');
  await expect(dialog).toContainText('Second refined prompt');
  await expect(dialog).toContainText('Comparison summary 2');
  await expect(dialog).toContainText('Iteration 2');

  await dialog.getByRole('button', { name: 'Copy', exact: true }).click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe('Second refined prompt');

  const saveRequestPromise = page.waitForRequest(
    (request) => request.method() === 'POST' && new URL(request.url()).pathname === '/api/prompt-snippets'
  );
  await dialog.getByRole('button', { name: 'Save snippet' }).click();
  const saveRequest = await saveRequestPromise;
  expect(saveRequest.postDataJSON()).toMatchObject({ prompt: 'Second refined prompt' });
  await expect(dialog).toBeHidden();

  await page.getByRole('button', { name: 'Reverse prompt', exact: true }).click();
  await fileInput.setInputFiles({ name: 'apply-target.png', mimeType: 'image/png', buffer: PNG_BYTES });
  await dialog.getByRole('button', { name: 'Reverse prompt', exact: true }).click();
  await dialog.getByRole('button', { name: 'Try optimization' }).click();
  await expect(dialog).toContainText('Applied refined prompt');
  await dialog.getByRole('button', { name: 'Apply to prompt' }).click();
  await expect(prompt).toHaveValue('Applied refined prompt');
});

test('reverse prompt explains incompatible generation presets after analysis', async ({ page }) => {
  const incompatiblePreset = {
    ...settingsResponse.presets[0],
    api_path: '/v1/responses'
  };
  await loadApp(page, {
    settings: {
      ...settingsResponse,
      api_path: '/v1/responses',
      presets: [incompatiblePreset]
    }
  });

  await page.getByRole('button', { name: 'Reverse prompt', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Reverse prompt' });
  await dialog.locator('input[type="file"]').setInputFiles({ name: 'target.png', mimeType: 'image/png', buffer: PNG_BYTES });
  await dialog.getByRole('button', { name: 'Reverse prompt', exact: true }).click();
  await expect(dialog.getByRole('button', { name: 'Try optimization' })).toBeDisabled();
  await expect(dialog.getByTestId('image-prompt-optimize-reason')).toContainText(
    'requires /v1/images/generations. The active preset uses /v1/responses'
  );
});

test('reverse prompt remains discoverable and disables analysis when Assistant config is unavailable', async ({ page }) => {
  await loadApp(page, {
    settings: {
      ...settingsResponse,
      ai_assistant: {
        ...settingsResponse.ai_assistant,
        enabled: false
      }
    }
  });

  await page.getByRole('button', { name: 'Reverse prompt', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Reverse prompt' });
  await expect(dialog).toContainText('AI Assistant is unavailable');
  await dialog.locator('input[type="file"]').setInputFiles({ name: 'local.png', mimeType: 'image/png', buffer: PNG_BYTES });
  await expect(dialog.getByRole('img', { name: 'local.png' })).toBeVisible();
  await expect(dialog.getByRole('button', { name: 'Reverse prompt', exact: true })).toBeDisabled();
});

test('reverse prompt header and dialog stay within a mobile viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await loadApp(page);

  const headerActions = [
    page.getByRole('button', { name: 'Reverse prompt', exact: true }),
    page.getByRole('button', { name: 'Prompt snippets' }),
    page.getByRole('button', { name: 'Job History' }),
    page.getByRole('button', { name: 'Settings' })
  ];
  for (const action of headerActions) {
    await expect(action).toBeVisible();
    const box = await action.boundingBox();
    expect(box?.x ?? -1).toBeGreaterThanOrEqual(0);
    expect(box?.width ?? 0).toBeGreaterThanOrEqual(44);
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
    expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(390);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

  await headerActions[0].click();
  const dialog = page.getByRole('dialog', { name: 'Reverse prompt' });
  const dialogBox = await dialog.boundingBox();
  expect(dialogBox?.x ?? -1).toBeGreaterThanOrEqual(0);
  expect(dialogBox?.y ?? -1).toBeGreaterThanOrEqual(0);
  expect((dialogBox?.x ?? 0) + (dialogBox?.width ?? 0)).toBeLessThanOrEqual(390);
  expect((dialogBox?.y ?? 0) + (dialogBox?.height ?? 0)).toBeLessThanOrEqual(844);
  await dialog.locator('input[type="file"]').setInputFiles({ name: 'mobile.png', mimeType: 'image/png', buffer: PNG_BYTES });
  await dialog.getByRole('button', { name: 'Reverse prompt', exact: true }).click();
  await dialog.getByRole('button', { name: 'Try optimization' }).click();
  const targetBox = await dialog.getByRole('img', { name: 'Target image' }).boundingBox();
  const trialBox = await dialog.getByRole('img', { name: 'Trial image' }).boundingBox();
  expect(trialBox?.y ?? 0).toBeGreaterThan(targetBox?.y ?? 0);
  expect(await dialog.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: '/tmp/gpt-image-reverse-prompt-mobile.png' });
});
