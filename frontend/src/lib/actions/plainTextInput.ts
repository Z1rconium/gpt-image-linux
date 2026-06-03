import type { Action } from 'svelte/action';

export const plainTextInput: Action<HTMLInputElement | HTMLTextAreaElement> = (node) => {
  node.setAttribute('autocapitalize', 'none');
  node.setAttribute('autocorrect', 'off');
  node.spellcheck = false;

  return {};
};
