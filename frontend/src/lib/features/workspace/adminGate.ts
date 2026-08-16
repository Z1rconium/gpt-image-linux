import { writable } from 'svelte/store';
import { apiFetch } from '$lib/api/client';

type AdminGateState = {
  visible: boolean;
  loading: boolean;
  error: string;
};

type AdminGateOptions = {
  fallbackError: () => string;
};

type ProtectedAction = () => void | Promise<void>;

const initialState: AdminGateState = {
  visible: false,
  loading: false,
  error: ''
};

export function createAdminGate(options: AdminGateOptions) {
  const { subscribe, update } = writable<AdminGateState>(initialState);
  let pendingAction: ProtectedAction | null = null;
  let checking = false;

  async function runProtected(action: ProtectedAction) {
    if (checking || pendingAction) return false;
    checking = true;
    pendingAction = action;
    let authenticated = false;
    try {
      const status = await apiFetch<{ authenticated: boolean }>(
        '/api/access/admin/status',
        {},
        'checking management access'
      );
      authenticated = status.authenticated;
    } catch {
      // The step-up form handles a missing or expired management session.
    } finally {
      checking = false;
    }

    if (authenticated) {
      pendingAction = null;
      await action();
      return true;
    }
    update((current) => ({ ...current, visible: true, error: '' }));
    return false;
  }

  function cancel() {
    pendingAction = null;
    update((current) => ({ ...current, visible: false, error: '' }));
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
        'unlocking protected action'
      );
    } catch (error) {
      const message = error instanceof Error ? error.message : options.fallbackError();
      update((current) => ({ ...current, error: message }));
      return false;
    } finally {
      update((current) => ({ ...current, loading: false }));
    }

    const action = pendingAction;
    pendingAction = null;
    update((current) => ({ ...current, visible: false, error: '' }));
    if (action) await action();
    return true;
  }

  return { subscribe, runProtected, cancel, unlock };
}
