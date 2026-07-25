import type { Component } from 'svelte';
import { get, writable } from 'svelte/store';

export type LazyLoadStatus = 'idle' | 'loading' | 'ready' | 'error';

export type LazyComponentState = {
  status: LazyLoadStatus;
  component: Component<any> | null;
  error: unknown;
};

type ComponentModule = { default: Component<any> };

export function createLazyComponent(loader: () => Promise<ComponentModule>) {
  const store = writable<LazyComponentState>({ status: 'idle', component: null, error: null });
  let pending: Promise<Component<any>> | null = null;
  let loadFailures = 0;

  async function load() {
    const current = get(store);
    if (current.component) return current.component;
    if (pending) return pending;
    if (current.status === 'error') throw current.error;

    store.set({ status: 'loading', component: null, error: null });
    pending = loader()
      .then((module) => {
        loadFailures = 0;
        store.set({ status: 'ready', component: module.default, error: null });
        return module.default;
      })
      .catch((error: unknown) => {
        loadFailures += 1;
        store.set({ status: 'error', component: null, error });
        throw error;
      })
      .finally(() => {
        pending = null;
      });
    return pending;
  }

  function reset() {
    if (get(store).status === 'error') {
      store.set({ status: 'idle', component: null, error: null });
    }
  }

  return {
    subscribe: store.subscribe,
    load,
    prefetch: () => load().catch(() => undefined),
    reset,
    retryRequiresReload: () => loadFailures > 1
  };
}
