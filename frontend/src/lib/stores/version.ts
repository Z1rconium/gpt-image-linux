import { writable } from 'svelte/store';
import { apiFetch } from '$lib/api/client';

const VERSION_CHECK_TIMEOUT_MS = 4000;

type VersionState = {
  version: string;
  latestVersion: string;
  hasUpdate: boolean;
  releaseUrl: string | null;
};

const initialVersionState: VersionState = {
  version: '',
  latestVersion: '',
  hasUpdate: false,
  releaseUrl: null
};

async function fetchLatestVersion(): Promise<{ latest_version: string | null; has_update: boolean } | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), VERSION_CHECK_TIMEOUT_MS);
  try {
    return await apiFetch<{ latest_version: string | null; has_update: boolean }>(
      '/api/version/latest',
      { signal: controller.signal },
      'loading latest version'
    );
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

function createVersionStore() {
  const { subscribe, set } = writable<VersionState>(initialVersionState);

  return {
    subscribe,
    async loadVersion() {
      try {
        const [data, latest] = await Promise.all([
          apiFetch<{ version: string; github_repo?: string; release_url: string | null }>(
            '/api/version',
            {},
            'loading version'
          ),
          fetchLatestVersion()
        ]);
        set({
          version: data.version,
          releaseUrl: data.release_url,
          latestVersion: latest?.latest_version ?? '',
          hasUpdate: Boolean(latest?.has_update)
        });
      } catch {
        set(initialVersionState);
      }
    }
  };
}

export const versionStore = createVersionStore();
