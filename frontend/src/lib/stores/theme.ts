import { browser } from '$app/environment';
import { writable } from 'svelte/store';

export type Theme = 'light' | 'dark';

const STORAGE_KEY = 'gpt-image-panel-theme';
const MEDIA_QUERY = '(prefers-color-scheme: dark)';

// The inline script in app.html already detected the system theme and applied
// it to the DOM before hydration, so seed the store from the DOM to avoid a
// flash of the wrong theme between first store read and init().
function initialTheme(): Theme {
  if (!browser) return 'dark';
  const fromDom = document.documentElement.dataset.theme;
  return fromDom === 'light' || fromDom === 'dark' ? fromDom : 'dark';
}

const theme = writable<Theme>(initialTheme());

let mediaQueryList: MediaQueryList | null = null;
let mediaListener: ((event: MediaQueryListEvent) => void) | null = null;

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

  // Fallback for environments where the app.html inline script did not run.
  if (!document.documentElement.dataset.theme) {
    setTheme(window.matchMedia(MEDIA_QUERY).matches ? 'dark' : 'light');
  } else {
    theme.set(initialTheme());
  }

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
