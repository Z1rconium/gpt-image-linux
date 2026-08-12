<script lang="ts">
  import { t } from '$lib/i18n';
  import type { ApiPath, ResponseFormatDefault } from '$lib/api/types/common';
  import type {
    AIAssistantSettingsInput,
    ApiPreset,
    R2HealthResponse,
    SettingsResponse
  } from '$lib/api/types/settings';
  import { RESPONSE_FORMAT_OPTIONS } from '$lib/utils/promptForm';
  import AiAssistantSettingsSection from './AiAssistantSettingsSection.svelte';
  import NodeImageSettingsSection from './NodeImageSettingsSection.svelte';
  import PromptOptimizerSettingsSection from './PromptOptimizerSettingsSection.svelte';
  import R2SettingsSection from './R2SettingsSection.svelte';

  export let settings: SettingsResponse | null = null;
  export let activePresetId = '';
  export let activatingPresetId = '';
  export let presetName = '';
  export let apiUrl = '';
  export let apiPath: ApiPath = '/v1/images/generations';
  export let defaultModel = '';
  export let defaultResponseFormat: ResponseFormatDefault = 'url';
  export let apiKey = '';
  export let apiKeyInputType = 'password';
  export let upstreamSocks5Proxy = '';
  export let webhookUrl = '';
  export let r2BackupEnabled = false;
  export let r2EndpointUrl = '';
  export let r2BucketName = '';
  export let r2Region = 'auto';
  export let r2KeyPrefix = 'gallery/';
  export let r2SyncIntervalHours: number | string = 0;
  export let r2AccessKeyId = '';
  export let r2SecretAccessKey = '';
  export let r2AccessKeyIdInputType = 'password';
  export let r2SecretAccessKeyInputType = 'password';
  export let r2Health: R2HealthResponse | null = null;
  export let r2HealthChecking = false;
  export let nodeImageEnabled = false;
  export let nodeImageApiKey = '';
  export let promptOptimizerEnabled = false;
  export let promptOptimizerApiUrl = '';
  export let promptOptimizerModel = '';
  export let promptOptimizerTimeoutSeconds: number | string = 60;
  export let promptOptimizerApiKey = '';
  export let promptOptimizerApiKeyInputType = 'password';
  export let promptOptimizerHealthChecking = false;
  export let aiAssistantEnabled = false;
  export let aiAssistantVisionModel = '';
  export let aiAssistantHealthChecking = false;
  export let onCreate: () => void = () => {};
  export let onDelete: (presetId: string) => void = () => {};
  export let activateSelectedPreset: (presetId: string) => void = () => {};
  export let keyLabel: (preset: ApiPreset) => string = () => '';
  export let openOverallConfigModal: () => void = () => {};
  export let normalizeR2SyncIntervalHours: () => void = () => {};
  export let checkR2Health: () => void = () => {};
  export let normalizePromptOptimizerTimeout: () => void = () => {};
  export let openSystemPromptEditor: () => void = () => {};
  export let checkPromptOptimizerHealth: () => void = () => {};
  export let checkAiAssistantHealth: () => void = () => {};
</script>

      <div class="mobile-drawer-scroll min-h-0 flex-1 overflow-y-auto p-5">
        <button
          type="button"
          class="control-focus mb-5 w-full rounded-lg border border-stone-300 px-3 py-2.5 text-sm font-semibold text-stone-700 transition-colors hover:border-stone-400 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-200 dark:hover:border-zinc-600 dark:hover:bg-zinc-800"
          on:click={openOverallConfigModal}
        >
          {$t.settings.overallConfig}
        </button>

        <div class="mb-5 flex items-center justify-between">
          <h3 class="text-sm font-semibold text-stone-800 dark:text-zinc-200">{$t.settings.presets}</h3>
          <div class="flex gap-2">
            <button type="button" class="control-focus rounded-lg border border-stone-300 px-3 py-1.5 text-xs text-stone-700 hover:bg-stone-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800" on:click={onCreate}>
              {$t.settings.newPreset}
            </button>
            <button
              type="button"
              disabled={!settings || settings.presets.length <= 1 || !activePresetId || Boolean(activatingPresetId)}
              class="control-focus rounded-lg border border-stone-300 px-3 py-1.5 text-xs text-stone-700 hover:bg-stone-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
              on:click={() => onDelete(activePresetId)}
            >
              {$t.settings.deletePreset}
            </button>
          </div>
        </div>

        <div class="mb-6 max-h-[260px] space-y-2 overflow-y-auto">
          {#each settings?.presets || [] as preset}
            <button
              type="button"
              class={`control-focus w-full rounded-md border px-3 py-2.5 text-left transition-colors ${
                preset.id === settings?.active_preset_id
                  ? 'border-emerald-500/70 bg-emerald-500/10 text-stone-900 dark:text-zinc-100'
                  : 'border-stone-200 bg-stone-50/80 text-stone-700 hover:border-stone-300 hover:bg-stone-100 dark:border-zinc-800 dark:bg-zinc-950/40 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:bg-zinc-800/70'
              }`}
              on:click={() => activateSelectedPreset(preset.id)}
            >
              <div class="flex items-start justify-between gap-3">
                <div class="min-w-0">
                  <div class="truncate text-sm font-medium">{preset.name || $t.common.untitledPreset}</div>
                  <div class="mt-1 truncate font-mono text-xs text-stone-500 dark:text-zinc-500">{preset.api_url || $t.common.noApiUrl}</div>
                </div>
                <span class="shrink-0 rounded-md border border-stone-300 px-2 py-0.5 text-[11px] font-medium text-stone-500 dark:border-zinc-700 dark:text-zinc-500">
                  {activatingPresetId === preset.id ? $t.settings.switchingPreset : preset.id === settings?.active_preset_id ? $t.common.active : $t.common.switch}
                </span>
              </div>
              <div class="mt-2 flex items-center justify-between gap-3 text-xs text-stone-500 dark:text-zinc-500">
                <span class="truncate font-mono">{preset.api_path}</span>
                <span class="shrink-0 font-mono">{keyLabel(preset)}</span>
              </div>
            </button>
          {/each}
        </div>

        <div class="space-y-4">
          <label class="block">
            <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.presetName}</span>
            <input bind:value={presetName} class="control-focus w-full rounded-lg border border-stone-300 bg-stone-50 px-3 py-2.5 text-sm text-stone-900 focus:border-emerald-500 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100" />
          </label>
          <label class="block">
            <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.apiUrl}</span>
            <input bind:value={apiUrl} class="control-focus w-full rounded-lg border border-stone-300 bg-stone-50 px-3 py-2.5 font-mono text-sm text-stone-900 focus:border-emerald-500 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100" placeholder="https://api.example.com" />
          </label>
          <label class="block">
            <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.apiPath}</span>
            <select bind:value={apiPath} class="control-focus form-select border-stone-300 bg-stone-50 text-stone-900 focus:border-emerald-500 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100">
              <option value="/v1/images/generations">/v1/images/generations</option>
              <option value="/v1/responses">/v1/responses</option>
              <option value="/v1/chat/completions">/v1/chat/completions</option>
            </select>
          </label>
          <label class="block">
            <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.defaultModel}</span>
            <input bind:value={defaultModel} class="control-focus w-full rounded-lg border border-stone-300 bg-stone-50 px-3 py-2.5 font-mono text-sm text-stone-900 focus:border-emerald-500 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100" placeholder="gpt-image-2" />
          </label>
          <label class="block">
            <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.defaultResponseFormat}</span>
            <select bind:value={defaultResponseFormat} class="control-focus form-select border-stone-300 bg-stone-50 text-stone-900 focus:border-emerald-500 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100">
              {#each RESPONSE_FORMAT_OPTIONS as responseFormat}
                <option value={responseFormat}>{responseFormat || $t.promptForm.defaultResponseFormat}</option>
              {/each}
            </select>
          </label>
          <label class="block">
            <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.apiKey}</span>
            <input bind:value={apiKey} type={apiKeyInputType} class="control-focus w-full rounded-lg border border-stone-300 bg-stone-50 px-3 py-2.5 font-mono text-sm text-stone-900 focus:border-emerald-500 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100" />
            <span class="mt-1.5 block text-xs text-stone-500 dark:text-zinc-500">{$t.settings.apiKeyHint}</span>
          </label>
          <label class="block">
            <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.upstreamSocks5Proxy}</span>
            <input bind:value={upstreamSocks5Proxy} class="control-focus w-full rounded-lg border border-stone-300 bg-stone-50 px-3 py-2.5 font-mono text-sm text-stone-900 focus:border-emerald-500 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100" placeholder="socks5://127.0.0.1:1080" />
            <span class="mt-1.5 block text-xs text-stone-500 dark:text-zinc-500">{$t.settings.upstreamSocks5ProxyHint}</span>
          </label>
          <label class="block">
            <span class="mb-1.5 block text-xs font-medium text-stone-600 dark:text-zinc-400">{$t.settings.webhookUrl}</span>
            <input bind:value={webhookUrl} class="control-focus w-full rounded-lg border border-stone-300 bg-stone-50 px-3 py-2.5 font-mono text-sm text-stone-900 focus:border-emerald-500 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100" placeholder="https://..." />
            <span class="mt-1.5 block text-xs text-stone-500 dark:text-zinc-500">{$t.settings.webhookUrlHint}</span>
          </label>

          <R2SettingsSection
            bind:enabled={r2BackupEnabled}
            bind:endpointUrl={r2EndpointUrl}
            bind:bucketName={r2BucketName}
            bind:region={r2Region}
            bind:keyPrefix={r2KeyPrefix}
            bind:syncIntervalHours={r2SyncIntervalHours}
            bind:accessKeyId={r2AccessKeyId}
            bind:secretAccessKey={r2SecretAccessKey}
            accessKeyInputType={r2AccessKeyIdInputType}
            secretKeyInputType={r2SecretAccessKeyInputType}
            health={r2Health}
            healthChecking={r2HealthChecking}
            onNormalizeInterval={normalizeR2SyncIntervalHours}
            onCheck={checkR2Health}
          />

          <NodeImageSettingsSection
            bind:enabled={nodeImageEnabled}
            bind:apiKey={nodeImageApiKey}
          />

          <PromptOptimizerSettingsSection
            bind:enabled={promptOptimizerEnabled}
            bind:apiUrl={promptOptimizerApiUrl}
            bind:model={promptOptimizerModel}
            bind:timeoutSeconds={promptOptimizerTimeoutSeconds}
            bind:apiKey={promptOptimizerApiKey}
            apiKeyInputType={promptOptimizerApiKeyInputType}
            healthChecking={promptOptimizerHealthChecking}
            onNormalizeTimeout={normalizePromptOptimizerTimeout}
            onOpenSystemPrompt={openSystemPromptEditor}
            onCheck={checkPromptOptimizerHealth}
          />

          <AiAssistantSettingsSection
            bind:enabled={aiAssistantEnabled}
            bind:visionModel={aiAssistantVisionModel}
            healthChecking={aiAssistantHealthChecking}
            onCheck={checkAiAssistantHealth}
          />
        </div>
      </div>
