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

test('settings and prompt snippets fit a mobile viewport without horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ colorScheme: 'light' });
  await loadApp(page);

  await page.getByRole('button', { name: 'Settings' }).click();
  const settingsDrawer = page.getByRole('dialog', { name: 'Settings' });
  await settingsDrawer.getByRole('button', { name: 'Test Prompt Optimizer' }).click();
  const healthResult = page.getByTestId('prompt-optimizer-health-result');
  await expect(healthResult).toBeVisible();
  await expect(healthResult).toHaveCSS('background-color', 'rgb(236, 253, 245)');
  expect(await settingsDrawer.evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
  expect(await healthResult.evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
  const settingsBox = await settingsDrawer.boundingBox();
  expect(settingsBox?.x ?? -1).toBeGreaterThanOrEqual(0);
  expect((settingsBox?.x ?? 0) + (settingsBox?.width ?? 0)).toBeLessThanOrEqual(390);
  expect((settingsBox?.y ?? 0) + (settingsBox?.height ?? 0)).toBeLessThanOrEqual(844);
  await page.screenshot({ path: '/tmp/gpt-image-settings-light-mobile.png' });
  await settingsDrawer.getByRole('button', { name: 'Close settings' }).click();

  await page.getByRole('button', { name: 'Prompt snippets' }).click();
  const promptsDrawer = page.getByRole('dialog', { name: 'Prompt Snippets' });
  expect(await promptsDrawer.evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
  expect(
    await promptsDrawer.locator('button').evaluateAll((buttons) =>
      buttons.filter((button) => getComputedStyle(button).visibility !== 'hidden').every((button) => button.scrollWidth <= button.clientWidth + 1)
    )
  ).toBe(true);
  await expect.poll(async () => {
    const box = await promptsDrawer.boundingBox();
    return (box?.y ?? 0) + (box?.height ?? 0);
  }).toBeLessThanOrEqual(844);
  const promptsBox = await promptsDrawer.boundingBox();
  expect(promptsBox?.x ?? -1).toBeGreaterThanOrEqual(0);
  expect((promptsBox?.x ?? 0) + (promptsBox?.width ?? 0)).toBeLessThanOrEqual(390);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: '/tmp/gpt-image-prompts-light-mobile.png' });
});

test('main workspace hierarchy stays ordered and touch-safe across viewports', async ({ page }) => {
  await loadApp(page);
  const headings = ['Prompt', 'AI Assistant', 'Preview', 'Gallery'];
  const desktopTops: number[] = [];
  for (const name of headings) {
    const box = await page.getByRole('heading', { name, exact: true }).boundingBox();
    desktopTops.push(box?.y ?? -1);
  }
  expect(desktopTops).toEqual([...desktopTops].sort((a, b) => a - b));
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: '/tmp/gpt-image-workspace-desktop.png', fullPage: true });

  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await expect.poll(() => page.evaluate(() => {
    const trigger = document.querySelector<HTMLElement>('[data-testid="prompt-optimizer-assistant-trigger"]');
    if (!trigger) return ['optimizer trigger missing'];
    const triggerRect = trigger.getBoundingClientRect();
    return Array.from(document.querySelectorAll<HTMLElement>('main input, main select, main textarea, main button, main a'))
      .filter((element) => element.offsetParent !== null)
      .filter((element) => {
        const rect = element.getBoundingClientRect();
        return !(rect.right <= triggerRect.left || rect.left >= triggerRect.right || rect.bottom <= triggerRect.top || rect.top >= triggerRect.bottom);
      })
      .map((element) => element.getAttribute('aria-label') || element.textContent?.trim().slice(0, 40) || element.tagName);
  })).toEqual([]);
  const galleryActions = page.locator('.gallery-icon-action');
  for (let index = 0; index < Math.min(await galleryActions.count(), 6); index += 1) {
    const box = await galleryActions.nth(index).boundingBox();
    expect(box?.width ?? 0).toBeGreaterThanOrEqual(44);
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
  }
  await page.screenshot({ path: '/tmp/gpt-image-workspace-mobile.png', fullPage: true });
});

test('lazy settings module reloads after a first-load failure and then reopens instantly', async ({ page }) => {
  const settingsModulePattern = /SettingsDrawer/;
  await mockApi(page);
  await page.route(settingsModulePattern, async (route) => {
    await route.abort('failed');
  });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Prompt', exact: true })).toBeVisible();

  const settingsButton = page.getByRole('button', { name: 'Settings' });
  await settingsButton.click();
  await expect(page.getByText('This panel could not be loaded.')).toBeVisible();
  await page.unroute(settingsModulePattern);
  const reload = page.waitForEvent('framenavigated');
  await page.getByRole('button', { name: 'Retry' }).click();
  await reload;
  await expect(page.getByRole('heading', { name: 'Prompt', exact: true })).toBeVisible();
  await settingsButton.click();

  const drawer = page.getByRole('dialog', { name: 'Settings' });
  await expect(drawer).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(drawer).toBeHidden();
  await expect(settingsButton).toBeFocused();
  await settingsButton.click();
  await expect(drawer).toBeVisible();
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
