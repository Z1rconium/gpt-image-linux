import { writable } from 'svelte/store';
import { apiFetch } from '$lib/api/client';

type AdminGateState = {
  visible: boolean;
  loading: boolean;
  error: string;
};

type AdminGateOptions = {
  openSettings: () => void | Promise<void>;
  fallbackError: () => string;
};

const initialState: AdminGateState = {
  visible: false,
  loading: false,
  error: ''
};

export function createAdminGate(options: AdminGateOptions) {
  const { subscribe, update } = writable<AdminGateState>(initialState);

  async function openSettingsSecure() {
    try {
      const status = await apiFetch<{ authenticated: boolean }>(
        '/api/access/admin/status',
        {},
        'checking management access'
      );
      if (status.authenticated) {
        await options.openSettings();
        return;
      }
    } catch {
      // The step-up form handles a missing or expired management session.
    }
    update((current) => ({ ...current, visible: true, error: '' }));
  }

  async function unlock(adminKey: string) {
    update((current) => ({ ...current, loading: true, error: '' }));
    try {
      await apiFetch(
        '/api/access/admin',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ admin_key: adminKey })
        },
        'unlocking management settings'
      );
      update((current) => ({ ...current, visible: false }));
      await options.openSettings();
    } catch (error) {
      const message = error instanceof Error ? error.message : options.fallbackError();
      update((current) => ({ ...current, error: message }));
    } finally {
      update((current) => ({ ...current, loading: false }));
    }
  }

  return { subscribe, openSettingsSecure, unlock };
}
