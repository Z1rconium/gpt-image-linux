import { browser } from '$app/environment';
import { writable } from 'svelte/store';

export type Theme = 'light' | 'dark';

const STORAGE_KEY = 'gpt-image-panel-theme';
const MEDIA_QUERY = '(prefers-color-scheme: dark)';

const theme = writable<Theme>('dark');

let mediaQueryList: MediaQueryList | null = null;
let mediaListener: ((event: MediaQueryListEvent) => void) | null = null;

function systemTheme(): Theme {
  if (!browser) return 'dark';
  return window.matchMedia(MEDIA_QUERY).matches ? 'dark' : 'light';
}

function readStoredTheme(): Theme | null {
  if (!browser) return null;
  const value = window.localStorage.getItem(STORAGE_KEY);
  return value === 'light' || value === 'dark' ? value : null;
}

function applyTheme(nextTheme: Theme) {
  if (!browser) return;
  const root = document.documentElement;
  root.classList.toggle('dark', nextTheme === 'dark');
  root.dataset.theme = nextTheme;
  root.style.colorScheme = nextTheme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', nextTheme === 'dark' ? '#09090b' : '#fafaf9');
}

function setTheme(nextTheme: Theme, persist = true) {
  theme.set(nextTheme);
  applyTheme(nextTheme);
  if (browser && persist) window.localStorage.setItem(STORAGE_KEY, nextTheme);
}

function cleanupMediaListener() {
  if (!mediaQueryList || !mediaListener) return;
  mediaQueryList.removeEventListener('change', mediaListener);
  mediaQueryList = null;
  mediaListener = null;
}

function init() {
  if (!browser) return;

  cleanupMediaListener();

  const initialTheme = readStoredTheme() || systemTheme();
  theme.set(initialTheme);
  applyTheme(initialTheme);

  mediaQueryList = window.matchMedia(MEDIA_QUERY);
  mediaListener = (event) => {
    if (readStoredTheme()) return;
    const nextTheme: Theme = event.matches ? 'dark' : 'light';
    theme.set(nextTheme);
    applyTheme(nextTheme);
  };
  mediaQueryList.addEventListener('change', mediaListener);
}

function toggle() {
  let nextTheme: Theme = 'dark';
  theme.update((current) => {
    nextTheme = current === 'dark' ? 'light' : 'dark';
    return nextTheme;
  });
  applyTheme(nextTheme);
  if (browser) window.localStorage.setItem(STORAGE_KEY, nextTheme);
}

export const themeStore = {
  subscribe: theme.subscribe,
  init,
  set: setTheme,
  toggle
};
