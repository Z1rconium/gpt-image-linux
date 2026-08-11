import { browser } from '$app/environment';
import { get, writable } from 'svelte/store';
import en from './locales/en';
import type { Language, Translation } from './types';

export type { Language, Translation } from './types';

const STORAGE_KEY = 'gpt-image-panel-language';
const DEFAULT_LANGUAGE: Language = 'en';
const emptyTranslation = {} as Translation;
const translationCache = new Map<Language, Translation>([[DEFAULT_LANGUAGE, en]]);
let latestLoad = 0;

const loaders: Record<Language, () => Promise<{ default: Translation }>> = {
  en: async () => ({ default: en }),
  'zh-CN': () => import('./locales/zh-CN')
};

function normalizeLanguage(value: string | null | undefined): Language | null {
  const normalized = String(value || '').toLowerCase();
  if (normalized.startsWith('zh')) return 'zh-CN';
  if (normalized.startsWith('en')) return 'en';
  return null;
}

function getInitialLanguage(): Language {
  if (!browser) return DEFAULT_LANGUAGE;
  return normalizeLanguage(localStorage.getItem(STORAGE_KEY)) || normalizeLanguage(navigator.language) || DEFAULT_LANGUAGE;
}

async function loadTranslation(nextLanguage: Language): Promise<Translation> {
  const cached = translationCache.get(nextLanguage);
  if (cached) return cached;
  const loaded = (await loaders[nextLanguage]()).default;
  translationCache.set(nextLanguage, loaded);
  return loaded;
}

const initialLanguage = getInitialLanguage();
let requestedLanguage = initialLanguage;

export const language = writable<Language>(initialLanguage);
export const t = writable<Translation>(initialLanguage === DEFAULT_LANGUAGE ? en : emptyTranslation);
export const i18nReady = writable(initialLanguage === DEFAULT_LANGUAGE);

export async function setLanguage(nextLanguage: Language): Promise<void> {
  requestedLanguage = nextLanguage;
  const loadId = ++latestLoad;
  const cached = translationCache.get(nextLanguage);
  if (cached) {
    language.set(nextLanguage);
    t.set(cached);
    i18nReady.set(true);
    return;
  }
  i18nReady.set(false);
  try {
    const translation = await loadTranslation(nextLanguage);
    if (loadId !== latestLoad) return;
    language.set(nextLanguage);
    t.set(translation);
    i18nReady.set(true);
  } catch (error) {
    if (loadId !== latestLoad) return;
    if (nextLanguage !== DEFAULT_LANGUAGE) {
      try {
        const fallback = await loadTranslation(DEFAULT_LANGUAGE);
        if (loadId !== latestLoad) return;
        requestedLanguage = DEFAULT_LANGUAGE;
        language.set(DEFAULT_LANGUAGE);
        t.set(fallback);
        i18nReady.set(true);
        return;
      } catch {
        // Preserve the original loading failure below.
      }
    }
    throw error;
  }
}

export async function initI18n(): Promise<void> {
  await setLanguage(get(language));
}

export async function toggleLanguage(): Promise<void> {
  await setLanguage(requestedLanguage === 'zh-CN' ? 'en' : 'zh-CN');
}

export function translate(): Translation {
  return get(t);
}

if (browser) {
  language.subscribe((value) => {
    localStorage.setItem(STORAGE_KEY, value);
    document.documentElement.lang = value;
  });
}
