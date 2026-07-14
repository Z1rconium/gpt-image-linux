declare module '*.svelte?lazy-retry' {
  import type { Component } from 'svelte';

  const component: Component<any>;
  export default component;
}
