import type { PromptOptimizeRequest } from '$lib/api/types/assistant';
import type { ApiPath } from '$lib/api/types/common';
import type { Language } from '$lib/i18n';
import type { PromptFormState } from '$lib/stores/preview';

type BuildPromptOptimizeRequestInput = {
  prompt: string;
  intent?: string;
  targetLanguage: Language;
  apiPath: ApiPath;
  model: string;
  size?: string;
  quality: PromptFormState['quality'];
};

export function buildPromptOptimizeRequest({
  prompt,
  intent,
  targetLanguage,
  apiPath,
  model,
  size,
  quality
}: BuildPromptOptimizeRequestInput): PromptOptimizeRequest {
  const request: PromptOptimizeRequest = {
    prompt: prompt.trim(),
    target_language: targetLanguage,
    api_path: apiPath,
    model: model.trim() || null,
    size: size?.trim() || null,
    quality
  };

  const trimmedIntent = intent?.trim();
  if (trimmedIntent) {
    request.intent = trimmedIntent;
  }

  return request;
}
