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

function applyTheme(nextTheme: Theme) {
  if (!browser) return;
  const root = document.documentElement;
  root.classList.toggle('dark', nextTheme === 'dark');
  root.dataset.theme = nextTheme;
  root.style.colorScheme = nextTheme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', nextTheme === 'dark' ? '#09090b' : '#fafaf9');
}

function setTheme(nextTheme: Theme) {
  theme.set(nextTheme);
  applyTheme(nextTheme);
}

function cleanupMediaListener() {
  if (!mediaQueryList || !mediaListener) return;
  mediaQueryList.removeEventListener('change', mediaListener);
  mediaQueryList = null;
  mediaListener = null;
}

function init() {
  if (!browser) return () => {};

  cleanupMediaListener();

  // Remove the former manual override so existing installations resume following the system.
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Theme synchronization still works when storage is unavailable.
  }
  setTheme(systemTheme());

  mediaQueryList = window.matchMedia(MEDIA_QUERY);
  mediaListener = (event) => {
    setTheme(event.matches ? 'dark' : 'light');
  };
  mediaQueryList.addEventListener('change', mediaListener);

  return cleanupMediaListener;
}

export const themeStore = {
  subscribe: theme.subscribe,
  init
};
