import { writable } from 'svelte/store';

export type NodeImageResultItem = {
  imageId: string;
  label: string;
  status: 'ok' | 'error';
  url: string;
  markdown: string;
  error: string;
};

export type NodeImageResultState = {
  items: NodeImageResultItem[];
  uploadedCount: number;
  failedCount: number;
};

function createNodeImageResultStore() {
  const { subscribe, set } = writable<NodeImageResultState | null>(null);

  return {
    subscribe,
    show(result: NodeImageResultState) {
      set(result);
    },
    clear() {
      set(null);
    }
  };
}

export const nodeImageResult = createNodeImageResultStore();
