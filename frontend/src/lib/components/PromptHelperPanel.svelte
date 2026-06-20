<script lang="ts">
  import { language, t } from '$lib/i18n';
  import { promptTagCategories } from '$lib/prompt/tags';

  export let onAppend: (value: string) => void = () => {};

  let expanded = false;
</script>

<aside class="h-full rounded-xl border border-stone-200 bg-stone-50/80 p-3 dark:border-zinc-800 dark:bg-zinc-950/45">
  <div class="mb-3 flex items-center justify-between gap-3">
    <h3 class="text-xs font-semibold uppercase tracking-wide text-stone-500 dark:text-zinc-400">{$t.promptHelper.title}</h3>
    <button
      type="button"
      class="control-focus rounded-md border border-stone-300 px-2 py-1 text-xs text-stone-600 lg:hidden dark:border-zinc-700 dark:text-zinc-300"
      aria-label={expanded ? $t.common.close : $t.promptHelper.title}
      on:click={() => (expanded = !expanded)}
    >
      {expanded ? '-' : '+'}
    </button>
  </div>

  <div class={`${expanded ? 'flex' : 'hidden'} gap-3 overflow-x-auto pb-1 lg:block lg:space-y-3 lg:overflow-visible lg:pb-0`}>
    {#each promptTagCategories as category}
      <section class="min-w-[210px] lg:min-w-0">
        <div class="mb-2 text-[11px] font-medium text-stone-500 dark:text-zinc-500">{$t.promptHelper.categories[category.id]}</div>
        <div class="flex flex-wrap gap-2">
          {#each category.tags as tag}
            <button
              type="button"
              class="control-focus rounded-md border border-stone-300 bg-white px-2 py-1 text-xs text-stone-700 transition-colors hover:border-emerald-500/60 hover:bg-emerald-500/10 hover:text-emerald-700 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:text-emerald-100"
              on:click={() => onAppend(tag.value)}
              title={tag.value}
            >
              {tag.label[$language]}
            </button>
          {/each}
        </div>
      </section>
    {/each}
  </div>
</aside>
