type WorkspaceLifecycleOptions = {
  onPopstate: () => void;
  onKeydown: (event: KeyboardEvent) => void;
  prefetchCommonPanels: () => void;
  shouldPrefetch: () => boolean;
  cleanup: () => void;
};

export function installWorkspaceLifecycle(options: WorkspaceLifecycleOptions) {
  window.addEventListener('popstate', options.onPopstate);
  window.addEventListener('keydown', options.onKeydown);
  let cancelPrefetch: (() => void) | null = null;

  if (options.shouldPrefetch()) {
    if (typeof window.requestIdleCallback === 'function') {
      const idleHandle = window.requestIdleCallback(options.prefetchCommonPanels, { timeout: 3000 });
      cancelPrefetch = () => window.cancelIdleCallback(idleHandle);
    } else {
      const timeoutHandle = window.setTimeout(options.prefetchCommonPanels, 1800);
      cancelPrefetch = () => window.clearTimeout(timeoutHandle);
    }
  }

  return () => {
    window.removeEventListener('popstate', options.onPopstate);
    window.removeEventListener('keydown', options.onKeydown);
    cancelPrefetch?.();
    options.cleanup();
  };
}
