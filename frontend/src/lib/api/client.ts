import { translate } from '$lib/i18n';

type UnauthorizedHandler = (message?: string) => void;

let unauthorizedHandler: UnauthorizedHandler | null = null;

export const DEFAULT_API_TIMEOUT_MS = 30_000;

export class ApiError extends Error {
  status: number;
  body: unknown;
  action: string;

  constructor(message: string, status: number, body: unknown, action = 'request') {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
    this.action = action;
  }
}

export function setUnauthorizedHandler(handler: UnauthorizedHandler | null) {
  unauthorizedHandler = typeof handler === 'function' ? handler : null;
}

function responseIsJson(response: Response): boolean {
  const contentType = (response.headers.get('content-type') || '').toLowerCase();
  return contentType.includes('application/json') || contentType.includes('+json');
}

async function readErrorBody(response: Response, isJson: boolean): Promise<{ body: unknown; text: string }> {
  const text = await response.text();
  let body: unknown = null;

  if (text && isJson) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }

  return { body, text };
}

export async function apiFetch<T>(url: string, options: RequestInit = {}, action = 'request'): Promise<T> {
  let response: Response;
  try {
    const signal = options.signal || AbortSignal.timeout(DEFAULT_API_TIMEOUT_MS);
    response = await fetch(url, {
      credentials: 'same-origin',
      ...options,
      signal,
      headers: {
        Accept: 'application/json',
        ...(options.headers || {})
      }
    });
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') throw error;
    const message = error instanceof Error ? error.message : translate().messages.failedToFetch;
    const networkError = new Error(translate().messages.networkError(message));
    (networkError as Error & { action?: string }).action = action;
    throw networkError;
  }

  const isJson = responseIsJson(response);

  if (!response.ok) {
    const { body, text } = await readErrorBody(response, isJson);

    if (response.status === 401) {
      const message = translate().messages.sessionExpired;
      unauthorizedHandler?.(message);
      throw new ApiError(message, response.status, body, action);
    }

    const data = body as { detail?: string; error?: string; message?: string } | null;
    const message = data?.detail || data?.error || data?.message || text || translate().messages.requestFailed;
    throw new ApiError(`${message}${response.status ? ` (${response.status})` : ''}`, response.status, body, action);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  if (isJson) {
    return (await response.json()) as T;
  }

  return (await response.text()) as T;
}
