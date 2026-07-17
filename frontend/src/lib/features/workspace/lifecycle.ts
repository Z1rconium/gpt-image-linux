type WorkspaceLifecycleOptions = {
  onPopstate: () => void;
  onKeydown: (event: KeyboardEvent) => void;
  prefetchCommonPanels: () => void;
  cleanup: () => void;
};

export function installWorkspaceLifecycle(options: WorkspaceLifecycleOptions) {
  window.addEventListener('popstate', options.onPopstate);
  window.addEventListener('keydown', options.onKeydown);
  const idleHandle =
    typeof window.requestIdleCallback === 'function'
      ? window.requestIdleCallback(options.prefetchCommonPanels, { timeout: 3000 })
      : window.setTimeout(options.prefetchCommonPanels, 1800);

  return () => {
    window.removeEventListener('popstate', options.onPopstate);
    window.removeEventListener('keydown', options.onKeydown);
    if (typeof idleHandle === 'number' && 'cancelIdleCallback' in window) {
      window.cancelIdleCallback(idleHandle);
    } else {
      window.clearTimeout(idleHandle);
    }
    options.cleanup();
  };
}
