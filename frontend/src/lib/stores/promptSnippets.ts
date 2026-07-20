import { writable } from 'svelte/store';
import { apiFetch } from '$lib/api/client';
import type { PromptSnippet, PromptSnippetCreateInput, PromptSnippetListResponse, PromptSnippetUpdateInput } from '$lib/api/types/snippets';

export type PromptSnippetsState = {
  snippets: PromptSnippet[];
  loading: boolean;
  saving: boolean;
  query: string;
};

const initialPromptSnippetsState: PromptSnippetsState = {
  snippets: [],
  loading: false,
  saving: false,
  query: ''
};

function createPromptSnippetsStore() {
  const { subscribe, update } = writable<PromptSnippetsState>(initialPromptSnippetsState);

  async function loadSnippets(query = '') {
    update((state) => ({ ...state, loading: true, query }));
    try {
      const response = await apiFetch<PromptSnippetListResponse>(
        '/api/prompt-snippets/search',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: query.trim() })
        },
        'loading prompt snippets'
      );
      update((state) => ({
        ...state,
        snippets: response.snippets,
        loading: false,
        query
      }));
    } catch (error) {
      update((state) => ({ ...state, loading: false }));
      throw error;
    }
  }

  async function createSnippet(input: PromptSnippetCreateInput) {
    update((state) => ({ ...state, saving: true }));
    try {
      const snippet = await apiFetch<PromptSnippet>(
        '/api/prompt-snippets',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(input)
        },
        'creating prompt snippet'
      );
      update((state) => ({
        ...state,
        snippets: [snippet, ...state.snippets.filter((item) => item.id !== snippet.id)],
        saving: false
      }));
      return snippet;
    } catch (error) {
      update((state) => ({ ...state, saving: false }));
      throw error;
    }
  }

  async function updateSnippet(snippetId: string, input: PromptSnippetUpdateInput) {
    update((state) => ({ ...state, saving: true }));
    try {
      const snippet = await apiFetch<PromptSnippet>(
        `/api/prompt-snippets/${encodeURIComponent(snippetId)}`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(input)
        },
        'updating prompt snippet'
      );
      update((state) => ({
        ...state,
        snippets: state.snippets.map((item) => (item.id === snippet.id ? snippet : item)),
        saving: false
      }));
      return snippet;
    } catch (error) {
      update((state) => ({ ...state, saving: false }));
      throw error;
    }
  }

  async function deleteSnippet(snippetId: string) {
    update((state) => ({ ...state, saving: true }));
    try {
      await apiFetch(
        `/api/prompt-snippets/${encodeURIComponent(snippetId)}`,
        { method: 'DELETE' },
        'deleting prompt snippet'
      );
      update((state) => ({
        ...state,
        snippets: state.snippets.filter((item) => item.id !== snippetId),
        saving: false
      }));
    } catch (error) {
      update((state) => ({ ...state, saving: false }));
      throw error;
    }
  }

  return {
    subscribe,
    loadSnippets,
    createSnippet,
    updateSnippet,
    deleteSnippet
  };
}

export const promptSnippetsStore = createPromptSnippetsStore();
