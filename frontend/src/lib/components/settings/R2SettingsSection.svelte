<script lang="ts">
  import type { PresetHealthStatus, R2HealthResponse } from '$lib/api/types';
  import { t } from '$lib/i18n';

  export let enabled = false;
  export let endpointUrl = '';
  export let bucketName = '';
  export let region = 'auto';
  export let keyPrefix = 'gallery/';
  export let syncIntervalHours: number | string = 0;
  export let accessKeyId = '';
  export let secretAccessKey = '';
  export let accessKeyInputType = 'password';
  export let secretKeyInputType = 'password';
  export let health: R2HealthResponse | null = null;
  export let healthChecking = false;
  export let onNormalizeInterval: () => void = () => {};
  export let onCheck: () => void | Promise<void> = () => {};

  function statusLabel(status: PresetHealthStatus) {
    if (status === 'ok') return $t.settings.healthOk;
    if (status === 'warning') return $t.settings.healthWarning;
    return $t.settings.healthError;
  }
  function panelClass(status: PresetHealthStatus) {
    if (status === 'ok') return 'border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-500/40 dark:bg-emerald-500/10 dark:text-emerald-100';
    if (status === 'warning') return 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-100';
    return 'border-red-200 bg-red-50 text-red-800 dark:border-red-500/40 dark:bg-red-500/10 dark:text-red-100';
  }
  function badgeClass(status: PresetHealthStatus) {
    if (status === 'ok') return 'border-emerald-300 text-emerald-700 dark:border-emerald-500/40 dark:text-emerald-300';
    if (status === 'warning') return 'border-amber-300 text-amber-700 dark:border-amber-500/40 dark:text-amber-300';
    return 'border-red-300 text-red-700 dark:border-red-500/40 dark:text-red-300';
  }
</script>

<section class="border-t border-stone-200 pt-4 dark:border-zinc-800">
  <div class="mb-3 flex items-center justify-between gap-3">
    <div><h3 class="text-sm font-semibold text-stone-800 dark:text-zinc-200">{$t.settings.r2Backup}</h3><p class="mt-1 text-xs text-stone-500 dark:text-zinc-500">{$t.settings.r2BackupHint}</p></div>
    <label class="flex items-center gap-2 text-xs font-medium text-stone-700 dark:text-zinc-300"><input bind:checked={enabled} type="checkbox" class="control-focus accent-emerald-500" />{$t.settings.r2BackupEnabled}</label>
  </div>
  <div class="space-y-4">
    <label class="block"><span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.r2EndpointUrl}</span><input bind:value={endpointUrl} class="control-focus w-full rounded-md border border-stone-300 bg-stone-50 px-3 py-2.5 font-mono text-sm text-stone-900 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100" placeholder="https://ACCOUNT_ID.r2.cloudflarestorage.com" /></label>
    <label class="block"><span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.r2BucketName}</span><input bind:value={bucketName} class="control-focus w-full rounded-md border border-stone-300 bg-stone-50 px-3 py-2.5 font-mono text-sm text-stone-900 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100" /></label>
    <div class="grid gap-4 sm:grid-cols-2">
      <label><span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.r2Region}</span><input bind:value={region} class="control-focus w-full rounded-md border border-stone-300 bg-stone-50 px-3 py-2.5 font-mono text-sm text-stone-900 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100" placeholder="auto" /></label>
      <label><span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.r2KeyPrefix}</span><input bind:value={keyPrefix} class="control-focus w-full rounded-md border border-stone-300 bg-stone-50 px-3 py-2.5 font-mono text-sm text-stone-900 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100" placeholder="gallery/" /></label>
    </div>
    <label class="block"><span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.r2SyncIntervalHours}</span><input bind:value={syncIntervalHours} type="number" min="0" step="1" class="control-focus w-full rounded-md border border-stone-300 bg-stone-50 px-3 py-2.5 font-mono text-sm text-stone-900 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100" on:blur={onNormalizeInterval} /><span class="mt-1.5 block text-xs text-stone-500 dark:text-zinc-500">{$t.settings.r2SyncIntervalHint}</span></label>
    <label class="block"><span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.r2AccessKeyId}</span><input bind:value={accessKeyId} type={accessKeyInputType} class="control-focus w-full rounded-md border border-stone-300 bg-stone-50 px-3 py-2.5 font-mono text-sm text-stone-900 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100" /><span class="mt-1.5 block text-xs text-stone-500 dark:text-zinc-500">{$t.settings.r2SecretHint}</span></label>
    <label class="block"><span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.r2SecretAccessKey}</span><input bind:value={secretAccessKey} type={secretKeyInputType} class="control-focus w-full rounded-md border border-stone-300 bg-stone-50 px-3 py-2.5 font-mono text-sm text-stone-900 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100" /><span class="mt-1.5 block text-xs text-stone-500 dark:text-zinc-500">{$t.settings.r2SecretHint}</span></label>
    <button type="button" disabled={healthChecking} class="control-focus w-full rounded-md border border-stone-300 px-3 py-2.5 text-sm font-semibold text-stone-700 hover:bg-stone-100 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800" on:click={onCheck}>{healthChecking ? $t.settings.r2HealthChecking : $t.settings.r2HealthCheck}</button>
    {#if health}
      <div class={`rounded-md border p-3 text-xs ${panelClass(health.status)}`}>
        <div class="flex items-center justify-between gap-3"><span class="font-semibold">{$t.settings.r2HealthStatus}</span><span class={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${badgeClass(health.status)}`}>{statusLabel(health.status)}</span></div>
        <div class="mt-2 space-y-1.5">{#each health.checks as check}<div class="rounded-md border border-stone-200 bg-white/70 p-2 text-stone-700 dark:border-zinc-800 dark:bg-zinc-950/50 dark:text-zinc-300"><div class="flex justify-between gap-2"><span class="font-mono text-[11px] text-stone-500 dark:text-zinc-500">{check.name}</span><span class={`rounded border px-1.5 py-0.5 text-[10px] ${badgeClass(check.status)}`}>{statusLabel(check.status)}</span></div><div class="mt-1 text-stone-600 dark:text-zinc-400">{check.message}</div></div>{/each}</div>
      </div>
    {/if}
  </div>
</section>
