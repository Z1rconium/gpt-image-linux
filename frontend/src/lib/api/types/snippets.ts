export type PromptSnippet = {
  id: string;
  title: string;
  prompt: string;
  favorite: boolean;
  created_at: string;
  updated_at: string;
};

export type PromptSnippetListResponse = {
  snippets: PromptSnippet[];
};

export type PromptSnippetCreateInput = {
  title: string;
  prompt: string;
  favorite?: boolean;
};

export type PromptSnippetUpdateInput = {
  title?: string;
  prompt?: string;
  favorite?: boolean;
};


