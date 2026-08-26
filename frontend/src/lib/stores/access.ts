import { get, writable } from 'svelte/store';
import { apiFetch, setUnauthorizedHandler } from '$lib/api/client';
import { t } from '$lib/i18n';
import type { AccessStatus } from '$lib/api/types/access';

export type AccessState = {
  authenticated: boolean;
  loading: boolean;
  gateVisible: boolean;
  error: string;
  turnstileEnabled: boolean;
  turnstileSiteKey: string;
};

const initialAccessState: AccessState = {
  authenticated: false,
  loading: false,
  gateVisible: false,
  error: '',
  turnstileEnabled: false,
  turnstileSiteKey: ''
};

function createAccessStore() {
  const { subscribe, set, update } = writable<AccessState>(initialAccessState);

  function setGateVisible(gateVisible: boolean, error = '') {
    update((state) => ({
      ...state,
      authenticated: !gateVisible,
      gateVisible,
      error
    }));
  }

  async function checkAccess(onAuthenticated: () => Promise<void>) {
    try {
      const data = await apiFetch<AccessStatus>('/api/access/status', {}, 'checking access');
      update((state) => ({
        ...state,
        turnstileEnabled: data.turnstile_enabled === true && !!data.turnstile_site_key,
        turnstileSiteKey: data.turnstile_enabled === true ? data.turnstile_site_key || '' : ''
      }));
      if (data.authenticated) {
        setGateVisible(false);
        await onAuthenticated();
        return;
      }
      setGateVisible(true);
    } catch (error) {
      setGateVisible(true, error instanceof Error ? error.message : get(t).messages.accessCheckFailed);
    }
  }

  async function unlockAccess(
    accessKey: string,
    turnstileToken: string,
    onAuthenticated: () => Promise<void>
  ) {
    update((state) => ({ ...state, loading: true, error: '' }));
    try {
      const data = await apiFetch<AccessStatus>(
        '/api/access',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ access_key: accessKey, turnstile_token: turnstileToken })
        },
        'unlocking access'
      );
      if (!data.authenticated) throw new Error(get(t).messages.invalidAccessKey);
      setGateVisible(false);
      await onAuthenticated();
    } catch (error) {
      update((state) => ({
        ...state,
        error: error instanceof Error ? error.message : get(t).messages.invalidAccessKey
      }));
    } finally {
      update((state) => ({ ...state, loading: false }));
    }
  }

  function installUnauthorizedHandler() {
    setUnauthorizedHandler((message) => {
      setGateVisible(true, message || '');
    });
  }

  return {
    subscribe,
    set,
    checkAccess,
    unlockAccess,
    installUnauthorizedHandler
  };
}

export const accessStore = createAccessStore();
