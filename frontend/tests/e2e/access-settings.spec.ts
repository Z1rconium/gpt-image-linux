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

test('image size dialog follows the light theme and preserves its dark palette', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light' });
  await loadApp(page);

  await page.getByRole('button', { name: 'Size', exact: true }).click();
  const sizeDialog = page.getByRole('dialog', { name: 'Image Size' });
  const selectedPreset = sizeDialog.getByRole('button', { name: 'auto', exact: true });
  const customSize = sizeDialog.getByLabel('Size', { exact: true });

  await expect(sizeDialog).toHaveCSS('background-color', 'rgb(255, 255, 255)');
  await expect(sizeDialog.getByRole('heading', { name: 'Image Size' })).toHaveCSS('color', 'rgb(12, 10, 9)');
  await expect(selectedPreset).toHaveCSS('color', 'rgb(4, 120, 87)');
  await expect(customSize).toHaveCSS('background-color', 'rgb(250, 250, 249)');
  await expect(customSize).toHaveCSS('color', 'rgb(28, 25, 23)');
  await page.screenshot({ path: '/tmp/gpt-image-size-light-desktop.png' });

  await sizeDialog.getByRole('button', { name: 'Close' }).click();
  await page.getByRole('button', { name: 'Switch to dark mode' }).click();
  await page.getByRole('button', { name: 'Size', exact: true }).click();

  await expect(sizeDialog).toHaveCSS('background-color', 'rgb(24, 24, 27)');
  await expect(sizeDialog.getByRole('heading', { name: 'Image Size' })).toHaveCSS('color', 'rgb(244, 244, 245)');
  await expect(selectedPreset).toHaveCSS('color', 'rgb(209, 250, 229)');
  await expect(customSize).toHaveCSS('background-color', 'rgb(9, 9, 11)');
  await expect(customSize).toHaveCSS('color', 'rgb(244, 244, 245)');
  await page.screenshot({ path: '/tmp/gpt-image-size-dark-desktop.png' });
});

test('settings and prompt snippets follow the active theme while open and after reopening', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light' });
  await loadApp(page);

  const root = page.locator('html');
  const settingsButton = page.getByRole('button', { name: 'Settings' });
  const promptsButton = page.getByRole('button', { name: 'Prompt snippets' });

  await expect(root).toHaveAttribute('data-theme', 'light');
  await settingsButton.click();
  const settingsDrawer = page.getByRole('dialog', { name: 'Settings' });
  const settingsTitle = settingsDrawer.getByRole('heading', { name: 'Settings' });
  const settingsApiUrl = settingsDrawer.getByLabel('API URL', { exact: true });
  const r2SyncInterval = settingsDrawer.getByLabel('Sync interval hours');
  const optimizerTimeout = settingsDrawer.getByLabel('Timeout seconds');
  const assistantVisionModel = settingsDrawer.getByLabel('Assistant vision engine');
  await expect(settingsDrawer).toHaveCSS('background-color', 'rgb(255, 255, 255)');
  await expect(settingsTitle).toHaveCSS('color', 'rgb(28, 25, 23)');
  await expect(settingsApiUrl).toHaveCSS('background-color', 'rgb(250, 250, 249)');
  await expect(settingsApiUrl).toHaveCSS('color', 'rgb(28, 25, 23)');
  await expect(r2SyncInterval).toHaveCSS('background-color', 'rgb(250, 250, 249)');
  await expect(optimizerTimeout).toHaveCSS('background-color', 'rgb(250, 250, 249)');
  await expect(assistantVisionModel).toHaveCSS('background-color', 'rgb(250, 250, 249)');
  await expect.poll(() => page.evaluate(() => window.localStorage.getItem('gpt-image-panel-theme'))).toBeNull();
  await page.screenshot({ path: '/tmp/gpt-image-settings-light-desktop.png' });
  await settingsDrawer.getByRole('button', { name: 'Close settings' }).click();

  await promptsButton.click();
  const promptsDrawer = page.getByRole('dialog', { name: 'Prompt Snippets' });
  const promptsTitle = promptsDrawer.getByRole('heading', { name: 'Prompt Snippets' });
  const promptsSearch = promptsDrawer.getByLabel('Search snippets');
  await expect(promptsDrawer).toHaveCSS('background-color', 'rgb(255, 255, 255)');
  await expect(promptsTitle).toHaveCSS('color', 'rgb(28, 25, 23)');
  await expect(promptsSearch).toHaveCSS('background-color', 'rgb(250, 250, 249)');
  await expect(promptsSearch).toHaveCSS('color', 'rgb(28, 25, 23)');
  await expect.poll(() => page.evaluate(() => window.localStorage.getItem('gpt-image-panel-theme'))).toBeNull();

  await page.getByRole('button', { name: 'Switch to dark mode' }).evaluate((button) =>
    button.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }))
  );
  await expect(root).toHaveAttribute('data-theme', 'dark');
  await expect(promptsDrawer).toHaveCSS('background-color', 'rgb(24, 24, 27)');
  await expect(promptsTitle).toHaveCSS('color', 'rgb(244, 244, 245)');
  await expect(promptsSearch).toHaveCSS('background-color', 'rgb(9, 9, 11)');
  await expect(promptsSearch).toHaveCSS('color', 'rgb(244, 244, 245)');
  await page.screenshot({ path: '/tmp/gpt-image-prompts-dark-desktop.png' });
  await promptsDrawer.getByRole('button', { name: 'Close prompt snippets' }).click();

  await settingsButton.click();
  await expect(settingsDrawer).toHaveCSS('background-color', 'rgb(24, 24, 27)');
  await expect(settingsTitle).toHaveCSS('color', 'rgb(244, 244, 245)');
  await expect(settingsApiUrl).toHaveCSS('background-color', 'rgb(9, 9, 11)');
  await page.getByRole('button', { name: 'Switch to light mode' }).evaluate((button) =>
    button.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }))
  );
  await expect(root).toHaveAttribute('data-theme', 'light');
  await expect(settingsDrawer).toHaveCSS('background-color', 'rgb(255, 255, 255)');
  await expect(settingsApiUrl).toHaveCSS('background-color', 'rgb(250, 250, 249)');
  await expect.poll(() => page.evaluate(() => window.localStorage.getItem('gpt-image-panel-theme'))).toBe('light');
  await settingsDrawer.getByRole('button', { name: 'Close settings' }).click();

  await promptsButton.click();
  await expect(promptsDrawer).toHaveCSS('background-color', 'rgb(255, 255, 255)');
  await promptsDrawer.getByRole('button', { name: 'Close prompt snippets' }).click();
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Prompt', exact: true })).toBeVisible();
  await expect(root).toHaveAttribute('data-theme', 'light');
  await page.getByRole('button', { name: 'Settings' }).click();
  await expect(page.getByRole('dialog', { name: 'Settings' })).toHaveCSS('background-color', 'rgb(255, 255, 255)');
});

test('settings and prompt snippets preserve dark surfaces for a dark system theme', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await loadApp(page);

  await page.getByRole('button', { name: 'Settings' }).click();
  const settingsDrawer = page.getByRole('dialog', { name: 'Settings' });
  await expect(settingsDrawer).toHaveCSS('background-color', 'rgb(24, 24, 27)');
  await expect(settingsDrawer.getByLabel('API URL', { exact: true })).toHaveCSS('background-color', 'rgb(9, 9, 11)');
  await expect(settingsDrawer.getByLabel('Sync interval hours')).toHaveCSS('background-color', 'rgb(9, 9, 11)');
  await expect(settingsDrawer.getByLabel('Timeout seconds')).toHaveCSS('background-color', 'rgb(9, 9, 11)');
  await expect(settingsDrawer.getByLabel('Assistant vision engine')).toHaveCSS('background-color', 'rgb(9, 9, 11)');
  await expect(settingsDrawer.getByRole('heading', { name: 'R2 Backup' })).toHaveCSS('color', 'rgb(228, 228, 231)');
  await settingsDrawer.getByRole('button', { name: 'Test Prompt Optimizer' }).click();
  await expect(page.getByTestId('prompt-optimizer-health-result')).toHaveCSS('background-color', 'rgba(16, 185, 129, 0.1)');
  await expect.poll(() => page.evaluate(() => window.localStorage.getItem('gpt-image-panel-theme'))).toBeNull();
  await settingsDrawer.getByRole('button', { name: 'Close settings' }).click();

  await page.getByRole('button', { name: 'Prompt snippets' }).click();
  const promptsDrawer = page.getByRole('dialog', { name: 'Prompt Snippets' });
  await expect(promptsDrawer).toHaveCSS('background-color', 'rgb(24, 24, 27)');
  await expect(promptsDrawer.getByLabel('Search snippets')).toHaveCSS('background-color', 'rgb(9, 9, 11)');
  await expect(promptsDrawer.getByRole('heading', { name: 'Prompt Snippets' })).toHaveCSS('color', 'rgb(244, 244, 245)');
  await expect.poll(() => page.evaluate(() => window.localStorage.getItem('gpt-image-panel-theme'))).toBeNull();
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
