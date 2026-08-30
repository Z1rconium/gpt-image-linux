import type { Action } from 'svelte/action';

export type DialogParams = {
  open: boolean;
  onClose?: () => void;
};

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'area[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
  '[contenteditable="true"]'
].join(',');

type DialogEntry = {
  node: HTMLElement;
  onClose?: () => void;
  previouslyFocused: HTMLElement | null;
  inertSiblings: Element[];
};

const stack: DialogEntry[] = [];
let savedBodyOverflow: string | null = null;
let globalKeyHandlerInstalled = false;

function lockBodyScroll() {
  if (stack.length === 1) {
    savedBodyOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
  }
}

function unlockBodyScroll() {
  if (stack.length === 0) {
    document.body.style.overflow = savedBodyOverflow ?? '';
    savedBodyOverflow = null;
  }
}

function getFocusable(root: HTMLElement): HTMLElement[] {
  const nodes = Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
  return nodes.filter(
    (node) => !node.hasAttribute('disabled') && node.tabIndex !== -1 && node.offsetParent !== null
  );
}

function focusInitial(root: HTMLElement) {
  if (!root.isConnected) return;
  const explicit = root.querySelector<HTMLElement>('[data-autofocus]');
  if (explicit) {
    explicit.focus();
    return;
  }
  const focusable = getFocusable(root);
  if (focusable.length > 0) {
    focusable[0].focus();
    return;
  }
  if (!root.hasAttribute('tabindex')) root.setAttribute('tabindex', '-1');
  root.focus();
}

function applyInertToBackground(node: HTMLElement): Element[] {
  const siblings = Array.from(document.body.children).filter(
    (el) => el !== node && !node.contains(el) && !el.contains(node)
  );
  siblings.forEach((el) => {
    el.setAttribute('data-dialog-inert', '');
    (el as HTMLElement).inert = true;
    el.setAttribute('aria-hidden', 'true');
  });
  return siblings;
}

function clearInertFromBackground(siblings: Element[]) {
  siblings.forEach((el) => {
    if (el.hasAttribute('data-dialog-inert')) {
      el.removeAttribute('data-dialog-inert');
      (el as HTMLElement).inert = false;
      el.removeAttribute('aria-hidden');
    }
  });
}

function handleGlobalKeydown(event: KeyboardEvent) {
  const top = stack[stack.length - 1];
  if (!top) return;

  if (event.key === 'Escape') {
    event.preventDefault();
    event.stopPropagation();
    top.onClose?.();
    return;
  }
  if (event.key !== 'Tab') return;

  const focusable = getFocusable(top.node);
  if (focusable.length === 0) {
    event.preventDefault();
    top.node.focus();
    return;
  }
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  const activeEl = document.activeElement as HTMLElement | null;

  if (event.shiftKey) {
    if (activeEl === first || !top.node.contains(activeEl)) {
      event.preventDefault();
      last.focus();
    }
  } else if (activeEl === last || !top.node.contains(activeEl)) {
    event.preventDefault();
    first.focus();
  }
}

function ensureGlobalKeyHandler() {
  if (globalKeyHandlerInstalled) return;
  document.addEventListener('keydown', handleGlobalKeydown, true);
  globalKeyHandlerInstalled = true;
}

function pushEntry(entry: DialogEntry) {
  // Only the topmost dialog should keep aria-hidden/inert applied;
  // restore the previous top's siblings before applying the new top's.
  const previousTop = stack[stack.length - 1];
  if (previousTop) {
    clearInertFromBackground(previousTop.inertSiblings);
    previousTop.inertSiblings = [];
  }
  stack.push(entry);
  entry.inertSiblings = applyInertToBackground(entry.node);
  ensureGlobalKeyHandler();
}

function removeEntry(entry: DialogEntry) {
  const index = stack.indexOf(entry);
  if (index === -1) return;
  stack.splice(index, 1);
  clearInertFromBackground(entry.inertSiblings);
  entry.inertSiblings = [];

  const newTop = stack[stack.length - 1];
  if (newTop) {
    clearInertFromBackground(newTop.inertSiblings);
    newTop.inertSiblings = applyInertToBackground(newTop.node);
  }
}

/**
 * Svelte action that turns an element into a modal dialog/drawer:
 * - role="dialog" + aria-modal="true"
 * - body scroll lock while any dialog is open
 * - focus restoration on close
 * - Tab focus trap, Escape to close
 * - background siblings get inert + aria-hidden
 */
export const dialog: Action<HTMLElement, DialogParams> = (node, initialParams) => {
  let params: DialogParams = initialParams;
  let entry: DialogEntry | null = null;

  node.setAttribute('role', node.getAttribute('role') || 'dialog');
  node.setAttribute('aria-modal', 'true');
  if (!node.style.overscrollBehavior) node.style.overscrollBehavior = 'contain';

  function activate() {
    if (entry) return;
    entry = {
      node,
      onClose: params.onClose,
      previouslyFocused: (document.activeElement as HTMLElement | null) ?? null,
      inertSiblings: []
    };
    pushEntry(entry);
    lockBodyScroll();
    queueMicrotask(() => focusInitial(node));
  }

  function deactivate() {
    if (!entry) return;
    const previouslyFocused = entry.previouslyFocused;
    removeEntry(entry);
    entry = null;
    unlockBodyScroll();
    if (previouslyFocused && document.contains(previouslyFocused)) {
      previouslyFocused.focus();
    }
  }

  if (params.open) activate();

  return {
    update(next: DialogParams) {
      const wasOpen = params.open;
      params = next;
      if (entry) entry.onClose = next.onClose;
      if (next.open && !wasOpen) activate();
      else if (!next.open && wasOpen) deactivate();
    },
    destroy() {
      deactivate();
    }
  };
};
