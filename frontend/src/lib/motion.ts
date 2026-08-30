import { cubicIn, cubicOut } from 'svelte/easing';
import type { TransitionConfig } from 'svelte/transition';

/**
 * Overlay choreography for dialogs and drawers.
 *
 * The root element carries the opacity (backdrop + panel together); the panel
 * carries only the travel, so the two never compound into a double fade.
 * Durations mirror the --dur-* tokens in app.css.
 */
const BACKDROP_ENTER_MS = 160;
const BACKDROP_EXIT_MS = 120;
const PANEL_ENTER_MS = 240;
const PANEL_EXIT_MS = 140;

const DIALOG_SCALE = 0.985;

function prefersReducedMotion(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
}

function duration(ms: number): number {
  return prefersReducedMotion() ? 0 : ms;
}

/** A leaving overlay must stop swallowing clicks before it finishes animating. */
function releasePointer(node: Element) {
  if (node instanceof HTMLElement) node.style.pointerEvents = 'none';
}

/**
 * Svelte aborts a running outro when the block re-enters and reuses the same
 * node, so the intro has to hand pointer events back.
 */
function restorePointer(node: Element) {
  if (node instanceof HTMLElement) node.style.pointerEvents = '';
}

export function overlayIn(node: Element): TransitionConfig {
  restorePointer(node);
  return {
    duration: duration(BACKDROP_ENTER_MS),
    easing: cubicOut,
    css: (t) => `opacity: ${t}`
  };
}

export function overlayOut(node: Element): TransitionConfig {
  releasePointer(node);
  return {
    duration: duration(BACKDROP_EXIT_MS),
    easing: cubicIn,
    css: (t) => `opacity: ${t}`
  };
}

export function dialogIn(_node: Element): TransitionConfig {
  return {
    duration: duration(PANEL_ENTER_MS),
    easing: cubicOut,
    css: (_t, u) => `transform: scale(${1 - (1 - DIALOG_SCALE) * u})`
  };
}

export function dialogOut(_node: Element): TransitionConfig {
  return {
    duration: duration(PANEL_EXIT_MS),
    easing: cubicIn,
    css: (_t, u) => `transform: scale(${1 - (1 - DIALOG_SCALE) * u})`
  };
}

export function drawerIn(_node: Element): TransitionConfig {
  return {
    duration: duration(PANEL_ENTER_MS),
    easing: cubicOut,
    css: (_t, u) => `clip-path: inset(0 0 0 ${u * 100}%)`
  };
}

export function drawerOut(_node: Element): TransitionConfig {
  return {
    duration: duration(PANEL_EXIT_MS),
    easing: cubicIn,
    css: (_t, u) => `clip-path: inset(0 0 0 ${u * 100}%)`
  };
}
