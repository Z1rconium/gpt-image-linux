<script lang="ts">
  import { t } from '$lib/i18n';
  import type {
    ApiPath,
    ApiPreset,
    PresetHealthResponse,
    PresetHealthStatus,
    PromptOptimizerSystemPromptResponse,
    ResponseFormatDefault,
    SettingsInput,
    SettingsResponse
  } from '$lib/api/types';
  import { dialog } from '$lib/actions/dialog';
  import { RESPONSE_FORMAT_OPTIONS, normalizeResponseFormat } from '$lib/utils/promptForm';

  const MASKED_API_KEY_VALUE = '********';

  export let open = false;
  export let settings: SettingsResponse | null = null;
  export let saving = false;
  export let health: PresetHealthResponse | null = null;
  export let healthChecking = false;
  export let onClose: () => void = () => {};
  export let onSave: (body: SettingsInput) => Promise<void> | void = () => {};
  export let onCreate: () => Promise<void> | void = () => {};
  export let onActivate: (presetId: string) => Promise<void> | void = () => {};
  export let onDelete: (presetId: string) => Promise<void> | void = () => {};
  export let onHealthCheck: (presetId: string) => Promise<void> | void = () => {};
  export let onLoadPromptOptimizerSystemPrompt: () => Promise<PromptOptimizerSystemPromptResponse> = async () => ({
    system_prompt: '',
    default_system_prompt: '',
    customized: false
  });
  export let onSavePromptOptimizerSystemPrompt: (systemPrompt: string) => Promise<PromptOptimizerSystemPromptResponse> = async (
    systemPrompt
  ) => ({
    system_prompt: systemPrompt,
    default_system_prompt: '',
    customized: true
  });

  let activePresetId = '';
  let presetName = '';
  let apiUrl = '';
  let defaultModel = '';
  let defaultResponseFormat: ResponseFormatDefault = 'url';
  let apiKey = '';
  let apiPath: ApiPath = '/v1/images/generations';
  let upstreamSocks5Proxy = '';
  let webhookUrl = '';
  let apiKeyInputType = 'password';
  let activatingPresetId = '';
  let promptOptimizerEnabled = false;
  let promptOptimizerApiUrl = '';
  let promptOptimizerModel = 'gpt-4o-mini';
  let promptOptimizerTimeoutSeconds: number | string = 60;
  let promptOptimizerApiKey = '';
  let promptOptimizerApiKeyInputType = 'password';
  let systemPromptOpen = false;
  let systemPromptLoading = false;
  let systemPromptSaving = false;
  let systemPromptText = '';
  let systemPromptError = '';

  $: activePreset = settings?.presets.find((preset) => preset.id === settings.active_preset_id) || settings?.presets[0] || null;
  $: if (settings && activePreset) {
    activePresetId = settings.active_preset_id;
    presetName = activePreset.name || '';
    apiUrl = activePreset.api_url || settings.api_url || '';
    defaultModel = activePreset.default_model || settings.default_model || 'gpt-image-2';
    apiKey =
      activePreset.api_key_source === 'env' && activePreset.api_key_env_var
        ? `\${${activePreset.api_key_env_var}}`
        : activePreset.has_api_key || settings.has_api_key
          ? MASKED_API_KEY_VALUE
          : '';
    apiPath = activePreset.api_path || settings.api_path || '/v1/images/generations';
    defaultResponseFormat = normalizeResponseFormat(activePreset.default_response_format ?? settings.default_response_format, 'url');
    upstreamSocks5Proxy = settings.has_upstream_socks5_proxy ? settings.upstream_socks5_proxy_masked : '';
    webhookUrl = settings.has_webhook_url ? settings.webhook_url_masked : '';
    promptOptimizerEnabled = Boolean(settings.prompt_optimizer?.enabled);
    promptOptimizerApiUrl = settings.prompt_optimizer?.api_url || '';
    promptOptimizerModel = settings.prompt_optimizer?.model || 'gpt-4o-mini';
    promptOptimizerTimeoutSeconds = settings.prompt_optimizer?.timeout_seconds || 60;
    promptOptimizerApiKey =
      settings.prompt_optimizer?.api_key_source === 'env' && settings.prompt_optimizer.api_key_env_var
        ? `\${${settings.prompt_optimizer.api_key_env_var}}`
        : settings.prompt_optimizer?.has_api_key
          ? MASKED_API_KEY_VALUE
          : '';
  }
  $: apiKeyInputType = apiKey.trim().startsWith('${') && apiKey.trim().endsWith('}') ? 'text' : 'password';
  $: promptOptimizerApiKeyInputType = promptOptimizerApiKey.trim().startsWith('${') && promptOptimizerApiKey.trim().endsWith('}') ? 'text' : 'password';
  $: if (!open && systemPromptOpen) {
    systemPromptOpen = false;
    systemPromptError = '';
  }

  function normalizePromptOptimizerTimeout() {
    const parsed = Number.parseInt(String(promptOptimizerTimeoutSeconds), 10);
    promptOptimizerTimeoutSeconds = Number.isFinite(parsed) && parsed > 0 ? parsed : 60;
  }

  function promptOptimizerTimeoutValue() {
    const parsed = Number.parseInt(String(promptOptimizerTimeoutSeconds), 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 60;
  }

  async function save() {
    const proxyValue = upstreamSocks5Proxy.trim();
    const currentProxyMask = settings?.upstream_socks5_proxy_masked || '';
    const webhookValue = webhookUrl.trim();
    const currentWebhookMask = settings?.webhook_url_masked || '';
    await onSave({
      active_preset_id: activePresetId,
      preset_name: presetName.trim(),
      api_url: apiUrl.trim(),
      default_model: defaultModel.trim(),
      default_response_format: defaultResponseFormat,
      api_key: apiKey.trim() === MASKED_API_KEY_VALUE ? null : apiKey.trim(),
      api_path: apiPath,
      upstream_socks5_proxy: proxyValue === currentProxyMask ? null : proxyValue,
      webhook_url: webhookValue === currentWebhookMask ? null : webhookValue,
      prompt_optimizer: {
        enabled: promptOptimizerEnabled,
        api_url: promptOptimizerApiUrl.trim(),
        model: promptOptimizerModel.trim(),
        timeout_seconds: promptOptimizerTimeoutValue(),
        api_key: promptOptimizerApiKey.trim() === MASKED_API_KEY_VALUE ? null : promptOptimizerApiKey.trim()
      }
    });
  }

  function keyLabel(preset: ApiPreset) {
    if (preset.api_key_source === 'env') {
      return `${$t.settings.envRef}: ${preset.api_key_env_var || preset.api_key_masked}`;
    }
    return preset.has_api_key ? preset.api_key_masked : $t.common.noKey;
  }

  async function activateSelectedPreset(presetId: string) {
    if (!presetId || presetId === settings?.active_preset_id || activatingPresetId) return;
    activatingPresetId = presetId;
    try {
      await onActivate(presetId);
    } finally {
      activatingPresetId = '';
    }
  }

  async function checkHealth() {
    if (!activePresetId) return;
    await onHealthCheck(activePresetId);
  }

  function healthStatusLabel(status: PresetHealthStatus) {
    if (status === 'ok') return $t.settings.healthOk;
    if (status === 'warning') return $t.settings.healthWarning;
    return $t.settings.healthError;
  }

  function healthPanelClass(status: PresetHealthStatus) {
    if (status === 'ok') return 'border-emerald-500/40 bg-emerald-500/10 text-emerald-100';
    if (status === 'warning') return 'border-amber-500/40 bg-amber-500/10 text-amber-100';
    return 'border-red-500/40 bg-red-500/10 text-red-100';
  }

  function healthBadgeClass(status: PresetHealthStatus) {
    if (status === 'ok') return 'border-emerald-500/40 text-emerald-300';
    if (status === 'warning') return 'border-amber-500/40 text-amber-300';
    return 'border-red-500/40 text-red-300';
  }

  async function openSystemPromptEditor() {
    systemPromptOpen = true;
    systemPromptLoading = true;
    systemPromptError = '';
    try {
      const response = await onLoadPromptOptimizerSystemPrompt();
      systemPromptText = response.system_prompt;
    } catch (error) {
      systemPromptError = error instanceof Error ? error.message : $t.messages.requestFailed;
    } finally {
      systemPromptLoading = false;
    }
  }

  function closeSystemPromptEditor() {
    if (systemPromptSaving) return;
    systemPromptOpen = false;
    systemPromptError = '';
  }

  async function saveSystemPrompt() {
    const nextSystemPrompt = systemPromptText.trim();
    if (!nextSystemPrompt) {
      systemPromptError = $t.settings.systemPromptRequired;
      return;
    }
    systemPromptSaving = true;
    systemPromptError = '';
    try {
      const response = await onSavePromptOptimizerSystemPrompt(nextSystemPrompt);
      systemPromptText = response.system_prompt;
      systemPromptOpen = false;
    } catch (error) {
      systemPromptError = error instanceof Error ? error.message : $t.messages.requestFailed;
    } finally {
      systemPromptSaving = false;
    }
  }
</script>

{#if open}
  <div class="fixed inset-0 z-50">
    <button class="drawer-backdrop absolute inset-0" type="button" tabindex="-1" aria-label={$t.settings.closeLabel} on:click={onClose}></button>
    <aside
      class="fade-in absolute right-0 top-0 flex h-full w-full max-w-lg flex-col border-l border-zinc-800 bg-zinc-900 shadow-2xl"
      aria-labelledby="settings-drawer-title"
      use:dialog={{ open, onClose }}
    >
      <div class="flex items-center justify-between border-b border-zinc-800 p-5">
        <div>
          <h2 id="settings-drawer-title" class="text-lg font-semibold text-zinc-100">{$t.settings.title}</h2>
          <p class="mt-1 text-xs text-zinc-500">{$t.settings.subtitle}</p>
        </div>
        <button type="button" class="control-focus rounded-lg p-1.5 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100" aria-label={$t.settings.closeLabel} on:click={onClose}>x</button>
      </div>

      <div class="min-h-0 flex-1 overflow-y-auto p-5">
        <div class="mb-5 flex items-center justify-between">
          <h3 class="text-sm font-semibold text-zinc-200">{$t.settings.presets}</h3>
          <div class="flex gap-2">
            <button type="button" class="control-focus rounded-lg border border-zinc-700 px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800" on:click={onCreate}>
              {$t.settings.newPreset}
            </button>
            <button
              type="button"
              disabled={!settings || settings.presets.length <= 1 || !activePresetId || Boolean(activatingPresetId)}
              class="control-focus rounded-lg border border-zinc-700 px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40"
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
                  ? 'border-emerald-500/70 bg-emerald-500/10 text-zinc-100'
                  : 'border-zinc-800 bg-zinc-950/40 text-zinc-300 hover:border-zinc-700 hover:bg-zinc-800/70'
              }`}
              on:click={() => activateSelectedPreset(preset.id)}
            >
              <div class="flex items-start justify-between gap-3">
                <div class="min-w-0">
                  <div class="truncate text-sm font-medium">{preset.name || $t.common.untitledPreset}</div>
                  <div class="mt-1 truncate font-mono text-xs text-zinc-500">{preset.api_url || $t.common.noApiUrl}</div>
                </div>
                <span class="shrink-0 rounded-md border border-zinc-700 px-2 py-0.5 text-[11px] font-medium text-zinc-500">
                  {activatingPresetId === preset.id ? $t.settings.switchingPreset : preset.id === settings?.active_preset_id ? $t.common.active : $t.common.switch}
                </span>
              </div>
              <div class="mt-2 flex items-center justify-between gap-3 text-xs text-zinc-500">
                <span class="truncate font-mono">{preset.api_path}</span>
                <span class="shrink-0 font-mono">{keyLabel(preset)}</span>
              </div>
            </button>
          {/each}
        </div>

        <div class="space-y-4">
          <label class="block">
            <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.settings.presetName}</span>
            <input bind:value={presetName} class="control-focus w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 focus:border-emerald-500" />
          </label>
          <label class="block">
            <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.settings.apiUrl}</span>
            <input bind:value={apiUrl} class="control-focus w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 font-mono text-sm text-zinc-100 focus:border-emerald-500" placeholder="https://api.example.com" />
          </label>
          <label class="block">
            <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.settings.apiPath}</span>
            <select bind:value={apiPath} class="control-focus w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 focus:border-emerald-500">
              <option value="/v1/images/generations">/v1/images/generations</option>
              <option value="/v1/responses">/v1/responses</option>
              <option value="/v1/chat/completions">/v1/chat/completions</option>
            </select>
          </label>
          <label class="block">
            <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.settings.defaultModel}</span>
            <input bind:value={defaultModel} class="control-focus w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 font-mono text-sm text-zinc-100 focus:border-emerald-500" placeholder="gpt-image-2" />
          </label>
          <label class="block">
            <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.settings.defaultResponseFormat}</span>
            <select bind:value={defaultResponseFormat} class="control-focus w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 focus:border-emerald-500">
              {#each RESPONSE_FORMAT_OPTIONS as responseFormat}
                <option value={responseFormat}>{responseFormat || $t.promptForm.defaultResponseFormat}</option>
              {/each}
            </select>
          </label>
          <label class="block">
            <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.settings.apiKey}</span>
            <input bind:value={apiKey} type={apiKeyInputType} class="control-focus w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 font-mono text-sm text-zinc-100 focus:border-emerald-500" />
            <span class="mt-1.5 block text-xs text-zinc-500">{$t.settings.apiKeyHint}</span>
          </label>
          <label class="block">
            <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.settings.upstreamSocks5Proxy}</span>
            <input bind:value={upstreamSocks5Proxy} class="control-focus w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 font-mono text-sm text-zinc-100 focus:border-emerald-500" placeholder="socks5://127.0.0.1:1080" />
            <span class="mt-1.5 block text-xs text-zinc-500">{$t.settings.upstreamSocks5ProxyHint}</span>
          </label>
          <label class="block">
            <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.settings.webhookUrl}</span>
            <input bind:value={webhookUrl} class="control-focus w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 font-mono text-sm text-zinc-100 focus:border-emerald-500" placeholder="https://..." />
            <span class="mt-1.5 block text-xs text-zinc-500">{$t.settings.webhookUrlHint}</span>
          </label>

          <section class="border-t border-zinc-800 pt-4">
            <div class="mb-3 flex items-center justify-between gap-3">
              <div>
                <h3 class="text-sm font-semibold text-zinc-200">{$t.settings.promptOptimizer}</h3>
                <p class="mt-1 text-xs text-zinc-500">{$t.settings.promptOptimizerHint}</p>
              </div>
              <label class="flex items-center gap-2 text-xs font-medium text-zinc-300">
                <input bind:checked={promptOptimizerEnabled} type="checkbox" class="control-focus accent-emerald-500" />
                {$t.settings.promptOptimizerEnabled}
              </label>
            </div>
            <div class="space-y-4">
              <label class="block">
                <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.settings.promptOptimizerApiUrl}</span>
                <input bind:value={promptOptimizerApiUrl} class="control-focus w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 font-mono text-sm text-zinc-100 focus:border-emerald-500" placeholder="https://api.openai.com/v1/chat/completions" />
              </label>
              <label class="block">
                <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.settings.promptOptimizerModel}</span>
                <input bind:value={promptOptimizerModel} class="control-focus w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 font-mono text-sm text-zinc-100 focus:border-emerald-500" placeholder="gpt-4o-mini" />
              </label>
              <label class="block">
                <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.settings.promptOptimizerTimeout}</span>
                <input
                  bind:value={promptOptimizerTimeoutSeconds}
                  type="number"
                  min="1"
                  step="1"
                  inputmode="numeric"
                  class="control-focus w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 font-mono text-sm text-zinc-100 focus:border-emerald-500"
                  on:blur={normalizePromptOptimizerTimeout}
                />
              </label>
              <label class="block">
                <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.settings.promptOptimizerApiKey}</span>
                <input bind:value={promptOptimizerApiKey} type={promptOptimizerApiKeyInputType} class="control-focus w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 font-mono text-sm text-zinc-100 focus:border-emerald-500" />
                <span class="mt-1.5 block text-xs text-zinc-500">{$t.settings.apiKeyHint}</span>
              </label>
              <button
                type="button"
                class="control-focus w-full rounded-lg border border-zinc-700 px-3 py-2.5 text-sm font-semibold text-zinc-200 transition-colors hover:border-zinc-600 hover:bg-zinc-800"
                on:click={openSystemPromptEditor}
              >
                {$t.settings.editSystemPrompt}
              </button>
            </div>
          </section>
        </div>
      </div>

      <div class="space-y-3 border-t border-zinc-800 p-5">
        {#if health}
          <div class={`rounded-lg border p-3 text-xs ${healthPanelClass(health.status)}`}>
            <div class="flex items-center justify-between gap-3">
              <span class="font-semibold">{$t.settings.healthStatus}</span>
              <span class={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${healthBadgeClass(health.status)}`}>
                {healthStatusLabel(health.status)}
              </span>
            </div>
            <div class="mt-2 space-y-1.5">
              {#each health.checks as check}
                <div class="rounded-md border border-zinc-800 bg-zinc-950/50 p-2 text-zinc-300">
                  <div class="flex items-center justify-between gap-2">
                    <span class="font-mono text-[11px] text-zinc-500">{check.name}</span>
                    <span class={`rounded border px-1.5 py-0.5 text-[10px] font-semibold ${healthBadgeClass(check.status)}`}>
                      {healthStatusLabel(check.status)}
                    </span>
                  </div>
                  <div class="mt-1 leading-relaxed text-zinc-400">{check.message}</div>
                </div>
              {/each}
            </div>
          </div>
        {/if}
        <div class="grid grid-cols-2 gap-3">
          <button
            type="button"
            disabled={healthChecking || !activePresetId}
            class="control-focus rounded-xl border border-zinc-700 px-4 py-3 text-sm font-semibold text-zinc-200 transition-colors hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50"
            on:click={checkHealth}
          >
            {healthChecking ? $t.settings.healthChecking : $t.settings.healthCheck}
          </button>
          <button
            type="button"
            disabled={saving}
            class="control-focus rounded-xl bg-emerald-600 px-4 py-3 text-sm font-semibold text-white transition-colors hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
            on:click={save}
          >
            {saving ? $t.settings.saving : $t.settings.savePreset}
          </button>
        </div>
      </div>
    </aside>

    {#if systemPromptOpen}
      <div class="fixed inset-0 z-[70] flex items-center justify-center bg-black/70 p-4">
        <button class="absolute inset-0" type="button" tabindex="-1" aria-label={$t.settings.closeSystemPromptEditor} on:click={closeSystemPromptEditor}></button>
        <div
          class="fade-in relative flex max-h-[88vh] w-full max-w-3xl flex-col rounded-xl border border-zinc-800 bg-zinc-900 shadow-2xl"
          aria-labelledby="system-prompt-dialog-title"
          use:dialog={{ open: systemPromptOpen, onClose: closeSystemPromptEditor }}
        >
          <div class="flex items-start justify-between gap-4 border-b border-zinc-800 p-5">
            <div class="min-w-0">
              <h2 id="system-prompt-dialog-title" class="text-base font-semibold text-zinc-100">{$t.settings.systemPromptTitle}</h2>
              <p class="mt-1 text-xs leading-5 text-zinc-500">{$t.settings.systemPromptHint}</p>
            </div>
            <button type="button" class="control-focus rounded-lg p-1.5 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100" aria-label={$t.settings.closeSystemPromptEditor} on:click={closeSystemPromptEditor}>
              x
            </button>
          </div>

          <div class="min-h-0 flex-1 overflow-y-auto p-5">
            {#if systemPromptLoading}
              <div class="rounded-lg border border-zinc-800 bg-zinc-950/60 p-4 text-sm text-zinc-400">{$t.settings.systemPromptLoading}</div>
            {:else}
              <label class="block">
                <span class="mb-2 block text-xs font-medium text-zinc-400">{$t.settings.systemPromptLabel}</span>
                <textarea
                  bind:value={systemPromptText}
                  class="control-focus min-h-[420px] w-full resize-y rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-3 font-mono text-xs leading-5 text-zinc-100 focus:border-emerald-500"
                  spellcheck="false"
                  data-autofocus
                ></textarea>
              </label>
            {/if}
            {#if systemPromptError}
              <div class="mt-3 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-200">{systemPromptError}</div>
            {/if}
          </div>

          <div class="flex justify-end gap-3 border-t border-zinc-800 p-5">
            <button type="button" class="control-focus rounded-lg border border-zinc-700 px-4 py-2 text-sm text-zinc-300 hover:bg-zinc-800" on:click={closeSystemPromptEditor}>
              {$t.common.close}
            </button>
            <button
              type="button"
              disabled={systemPromptLoading || systemPromptSaving}
              class="control-focus rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
              on:click={saveSystemPrompt}
            >
              {systemPromptSaving ? $t.settings.systemPromptSaving : $t.settings.systemPromptSave}
            </button>
          </div>
        </div>
      </div>
    {/if}
  </div>
{/if}
