import { expect, test } from '@playwright/test';
import { loadApp, mockApi } from './fixtures/mockApi';

const localePath = '/src/lib/i18n/locales/';

function localeRequests(page: import('@playwright/test').Page) {
  const requested: string[] = [];
  page.on('request', (request) => {
    const pathname = new URL(request.url()).pathname;
    if (pathname.startsWith(localePath)) requested.push(pathname);
  });
  return requested;
}

test('persisted English loads only the English locale', async ({ page }) => {
  const requested = localeRequests(page);

  await loadApp(page, { language: 'en' });

  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  expect(requested.some((path) => path.endsWith('/en.ts'))).toBe(true);
  expect(requested.some((path) => path.endsWith('/zh-CN.ts'))).toBe(false);
});

test('persisted Chinese loads only the Chinese locale', async ({ page }) => {
  const requested = localeRequests(page);

  await loadApp(page, { language: 'zh-CN' });

  await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN');
  expect(requested.some((path) => path.endsWith('/zh-CN.ts'))).toBe(true);
  expect(requested.some((path) => path.endsWith('/en.ts'))).toBe(false);
});

test('browser Chinese is used when no language is persisted', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'language', {
      configurable: true,
      get: () => 'zh-CN'
    });
  });
  await mockApi(page, { language: null });

  await page.goto('/');

  await expect(page.getByRole('heading', { name: '提示词', exact: true })).toBeVisible();
  await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN');
});

test('the last language request wins a rapid loading race', async ({ page }) => {
  await loadApp(page, { language: 'en' });

  let releaseChinese: () => void = () => {};
  const chineseGate = new Promise<void>((resolve) => {
    releaseChinese = resolve;
  });
  let markChineseRequested: () => void = () => {};
  const chineseRequested = new Promise<void>((resolve) => {
    markChineseRequested = resolve;
  });

  await page.route('**/src/lib/i18n/locales/zh-CN.ts*', async (route) => {
    markChineseRequested();
    await chineseGate;
    await route.continue();
  });

  const race = page.evaluate(async () => {
    const moduleUrl = '/src/lib/i18n/index.ts';
    const i18n = await import(moduleUrl);
    const first = i18n.setLanguage('zh-CN');
    const second = i18n.setLanguage('en');
    await Promise.allSettled([first, second]);
  });

  await chineseRequested;
  releaseChinese();
  await race;

  await expect(page.getByRole('heading', { name: 'Prompt', exact: true })).toBeVisible();
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await expect.poll(() => page.evaluate(() => localStorage.getItem('gpt-image-panel-language'))).toBe('en');
});

test('a failed Chinese locale load falls back to English', async ({ page }) => {
  await mockApi(page, { language: 'zh-CN' });
  await page.route('**/src/lib/i18n/locales/zh-CN.ts*', async (route) => {
    await route.fulfill({
      status: 503,
      contentType: 'text/javascript',
      body: 'throw new Error("locale unavailable")'
    });
  });

  await page.goto('/');

  await expect(page.getByRole('heading', { name: 'Prompt', exact: true })).toBeVisible();
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await expect.poll(() => page.evaluate(() => localStorage.getItem('gpt-image-panel-language'))).toBe('en');
});

test('the first render stays textless until the selected locale is ready', async ({ page }) => {
  await mockApi(page, { language: 'zh-CN' });

  let releaseChinese: () => void = () => {};
  const chineseGate = new Promise<void>((resolve) => {
    releaseChinese = resolve;
  });
  let markChineseRequested: () => void = () => {};
  const chineseRequested = new Promise<void>((resolve) => {
    markChineseRequested = resolve;
  });

  await page.route('**/src/lib/i18n/locales/zh-CN.ts*', async (route) => {
    markChineseRequested();
    await chineseGate;
    await route.continue();
  });

  await page.goto('/');
  await chineseRequested;

  await expect(page.locator('[aria-busy="true"]')).toBeVisible();
  await expect(page.locator('body')).toHaveText('');
  await expect(page.getByText('Prompt', { exact: true })).toHaveCount(0);
  await expect(page.getByText('提示词', { exact: true })).toHaveCount(0);

  releaseChinese();
  await expect(page.getByRole('heading', { name: '提示词', exact: true })).toBeVisible();
});
