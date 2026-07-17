import type en from './locales/en';

export type Language = 'en' | 'zh-CN';

type TranslationValue = string | ((...args: any[]) => string) | Record<string, unknown>;
type TranslationSchema<T> = {
  [K in keyof T]: T[K] extends (...args: infer Args) => string
    ? (...args: Args) => string
    : T[K] extends string
      ? string
      : T[K] extends Record<string, TranslationValue>
        ? TranslationSchema<T[K]>
        : never;
};

export type Translation = TranslationSchema<typeof en>;

