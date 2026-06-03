import type { Action } from 'svelte/action';

export type SwipeCloseParams = {
  enabled?: boolean;
  onClose?: () => void;
  direction?: 'left' | 'right';
  minDistance?: number;
};

const INTERACTIVE_SELECTOR = [
  'a',
  'button',
  'input',
  'select',
  'textarea',
  'label',
  '[contenteditable="true"]',
  '[role="button"]',
  '[data-swipe-ignore]'
].join(',');

function shouldIgnoreTarget(target: EventTarget | null) {
  return target instanceof Element && Boolean(target.closest(INTERACTIVE_SELECTOR));
}

export const swipeClose: Action<HTMLElement, SwipeCloseParams> = (node, initialParams = {}) => {
  let params = initialParams;
  let pointerId: number | null = null;
  let startX = 0;
  let startY = 0;
  let startTime = 0;

  function reset() {
    pointerId = null;
    startX = 0;
    startY = 0;
    startTime = 0;
  }

  function canStart(event: PointerEvent) {
    return (
      params.enabled !== false &&
      Boolean(params.onClose) &&
      event.isPrimary &&
      event.pointerType !== 'mouse' &&
      !shouldIgnoreTarget(event.target)
    );
  }

  function handlePointerDown(event: PointerEvent) {
    if (!canStart(event)) return;
    pointerId = event.pointerId;
    startX = event.clientX;
    startY = event.clientY;
    startTime = Date.now();
  }

  function handlePointerUp(event: PointerEvent) {
    if (pointerId !== event.pointerId) return;

    const dx = event.clientX - startX;
    const dy = event.clientY - startY;
    const absX = Math.abs(dx);
    const absY = Math.abs(dy);
    const elapsed = Date.now() - startTime;
    const direction = params.direction || 'right';
    const passedDirection = direction === 'right' ? dx > 0 : dx < 0;
    const passedDistance = absX >= (params.minDistance || 72);
    const horizontalSwipe = absX > absY * 1.25;

    if (passedDirection && passedDistance && horizontalSwipe && elapsed <= 800) {
      params.onClose?.();
    }

    reset();
  }

  function handlePointerCancel(event: PointerEvent) {
    if (pointerId === event.pointerId) reset();
  }

  node.addEventListener('pointerdown', handlePointerDown);
  node.addEventListener('pointerup', handlePointerUp);
  node.addEventListener('pointercancel', handlePointerCancel);

  return {
    update(nextParams: SwipeCloseParams) {
      params = nextParams;
    },
    destroy() {
      node.removeEventListener('pointerdown', handlePointerDown);
      node.removeEventListener('pointerup', handlePointerUp);
      node.removeEventListener('pointercancel', handlePointerCancel);
    }
  };
};
