<script lang="ts">
  import { t } from '$lib/i18n';
  import type {
    ApiPath,
    ApiPreset,
    OverallConfigItem,
    OverallConfigResponse,
    OverallConfigUpdateRequest,
    PromptOptimizerHealthResponse,
    PresetHealthResponse,
    PresetHealthStatus,
    PromptOptimizerSystemPromptResponse,
    R2BackupSettingsInput,
    R2HealthResponse,
    ResponseFormatDefault,
    SettingsInput,
    SettingsResponse
  } from '$lib/api/types';
  import { dialog } from '$lib/actions/dialog';
  import { plainTextInput } from '$lib/actions/plainTextInput';
  import { swipeClose } from '$lib/actions/swipeClose';
  import { RESPONSE_FORMAT_OPTIONS, normalizeResponseFormat } from '$lib/utils/promptForm';

  const MASKED_API_KEY_VALUE = '********';

  export let open = false;
  export let settings: SettingsResponse | null = null;
  export let saving = false;
  export let health: PresetHealthResponse | null = null;
  export let healthChecking = false;
  export let r2Health: R2HealthResponse | null = null;
  export let r2HealthChecking = false;
  export let promptOptimizerHealth: PromptOptimizerHealthResponse | null = null;
  export let promptOptimizerHealthChecking = false;
  export let onClose: () => void = () => {};
  export let onSave: (body: SettingsInput) => Promise<void> | void = () => {};
  export let onCreate: () => Promise<void> | void = () => {};
  export let onActivate: (presetId: string) => Promise<void> | void = () => {};
  export let onDelete: (presetId: string) => Promise<void> | void = () => {};
  export let onHealthCheck: (presetId: string) => Promise<void> | void = () => {};
  export let onClearPresetHealth: () => void = () => {};
  export let onR2HealthCheck: (body: R2BackupSettingsInput) => Promise<void> | void = () => {};
  export let onPromptOptimizerHealthCheck: () => Promise<void> | void = () => {};
  export let onClearPromptOptimizerHealth: () => void = () => {};
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
  export let onLoadOverallConfig: () => Promise<OverallConfigResponse> = async () => ({
    items: [],
    restart_required_names: []
  });
  export let onSaveOverallConfig: (body: OverallConfigUpdateRequest) => Promise<OverallConfigResponse> = async () => ({
    items: [],
    restart_required_names: []
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
  let r2BackupEnabled = false;
  let r2EndpointUrl = '';
  let r2BucketName = '';
  let r2Region = 'auto';
  let r2KeyPrefix = 'gallery/';
  let r2SyncIntervalHours: number | string = 0;
  let r2AccessKeyId = '';
  let r2SecretAccessKey = '';
  let r2AccessKeyIdInputType = 'password';
  let r2SecretAccessKeyInputType = 'password';
  let systemPromptOpen = false;
  let systemPromptLoading = false;
  let systemPromptSaving = false;
  let systemPromptText = '';
  let systemPromptError = '';
  let overallConfigOpen = false;
  let overallConfigLoading = false;
  let overallConfigSaving = false;
  let overallConfigError = '';
  let overallConfigItems: OverallConfigItem[] = [];
  let overallConfigDraft: Record<string, string | boolean | number> = {};
  let overallConfigClears: Record<string, boolean> = {};

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
    r2BackupEnabled = Boolean(settings.r2_backup?.enabled);
    r2EndpointUrl = settings.r2_backup?.endpoint_url || '';
    r2BucketName = settings.r2_backup?.bucket_name || '';
    r2Region = settings.r2_backup?.region || 'auto';
    r2KeyPrefix = settings.r2_backup?.key_prefix || 'gallery/';
    r2SyncIntervalHours = settings.r2_backup?.sync_interval_hours ?? 0;
    r2AccessKeyId =
      settings.r2_backup?.access_key_id_source === 'env' && settings.r2_backup.access_key_id_env_var
        ? `\${${settings.r2_backup.access_key_id_env_var}}`
        : settings.r2_backup?.has_access_key_id
          ? MASKED_API_KEY_VALUE
          : '';
    r2SecretAccessKey =
      settings.r2_backup?.secret_access_key_source === 'env' && settings.r2_backup.secret_access_key_env_var
        ? `\${${settings.r2_backup.secret_access_key_env_var}}`
        : settings.r2_backup?.has_secret_access_key
          ? MASKED_API_KEY_VALUE
          : '';
  }
  $: apiKeyInputType = apiKey.trim().startsWith('${') && apiKey.trim().endsWith('}') ? 'text' : 'password';
  $: promptOptimizerApiKeyInputType = promptOptimizerApiKey.trim().startsWith('${') && promptOptimizerApiKey.trim().endsWith('}') ? 'text' : 'password';
  $: r2AccessKeyIdInputType = r2AccessKeyId.trim().startsWith('${') && r2AccessKeyId.trim().endsWith('}') ? 'text' : 'password';
  $: r2SecretAccessKeyInputType = r2SecretAccessKey.trim().startsWith('${') && r2SecretAccessKey.trim().endsWith('}') ? 'text' : 'password';
  $: if (!open && systemPromptOpen) {
    systemPromptOpen = false;
    systemPromptError = '';
  }
  $: if (!open && overallConfigOpen) {
    overallConfigOpen = false;
    overallConfigError = '';
  }
  $: overallConfigGroups = overallConfigItems.reduce(
    (groups, item) => {
      if (!groups[item.group]) groups[item.group] = [];
      groups[item.group].push(item);
      return groups;
    },
    {} as Record<string, OverallConfigItem[]>
  );
  $: overallConfigGroupNames = Object.keys(overallConfigGroups);

  function normalizePromptOptimizerTimeout() {
    const parsed = Number.parseInt(String(promptOptimizerTimeoutSeconds), 10);
    promptOptimizerTimeoutSeconds = Number.isFinite(parsed) && parsed > 0 ? parsed : 60;
  }

  function promptOptimizerTimeoutValue() {
    const parsed = Number.parseInt(String(promptOptimizerTimeoutSeconds), 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 60;
  }

  function normalizeR2SyncIntervalHours() {
    r2SyncIntervalHours = r2SyncIntervalHoursValue();
  }

  function r2SyncIntervalHoursValue() {
    const parsed = Number(r2SyncIntervalHours);
    return Number.isInteger(parsed) && parsed >= 0 ? parsed : 0;
  }

  function r2BackupPayload(): R2BackupSettingsInput {
    return {
      enabled: r2BackupEnabled,
      endpoint_url: r2EndpointUrl.trim(),
      bucket_name: r2BucketName.trim(),
      region: r2Region.trim() || 'auto',
      key_prefix: r2KeyPrefix.trim(),
      sync_interval_hours: r2SyncIntervalHoursValue(),
      access_key_id: r2AccessKeyId.trim() === MASKED_API_KEY_VALUE ? null : r2AccessKeyId.trim(),
      secret_access_key: r2SecretAccessKey.trim() === MASKED_API_KEY_VALUE ? null : r2SecretAccessKey.trim()
    };
  }

  function healthPanelClass(status: PresetHealthStatus | 'ok' | 'warning' | 'error') {
    if (status === 'ok') return 'border-emerald-500/40 bg-emerald-500/10 text-emerald-100';
    if (status === 'warning') return 'border-amber-500/40 bg-amber-500/10 text-amber-100';
    return 'border-red-500/40 bg-red-500/10 text-red-100';
  }

  function healthBadgeClass(status: PresetHealthStatus | 'ok' | 'warning' | 'error') {
    if (status === 'ok') return 'border-emerald-500/40 text-emerald-300';
    if (status === 'warning') return 'border-amber-500/40 text-amber-300';
    return 'border-red-500/40 text-red-300';
  }

  function closePromptOptimizerHealth() {
    onClearPromptOptimizerHealth();
  }

  function closePresetHealth() {
    onClearPresetHealth();
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
      },
      r2_backup: r2BackupPayload()
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

  async function checkR2Health() {
    await onR2HealthCheck(r2BackupPayload());
  }

  async function checkPromptOptimizerHealth() {
    await onPromptOptimizerHealthCheck();
  }

  function healthStatusLabel(status: PresetHealthStatus) {
    if (status === 'ok') return $t.settings.healthOk;
    if (status === 'warning') return $t.settings.healthWarning;
    return $t.settings.healthError;
  }

  function presetHealthDisplayStatus(response: PresetHealthResponse) {
    const blockingChecks = response.checks.filter((check) => check.name !== 'upstream_probe');
    if (blockingChecks.length === 0) return 'ok';
    if (blockingChecks.some((check) => check.status === 'error')) return 'error';
    if (blockingChecks.some((check) => check.status === 'warning')) return 'warning';
    return 'ok';
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

  function overallDraftValue(item: OverallConfigItem) {
    if (hasOverallDraft(item.name)) {
      return overallConfigDraft[item.name];
    }
    if (item.secret) return item.value_masked || '';
    return item.value;
  }

  function hasOverallDraft(name: string) {
    return Object.prototype.hasOwnProperty.call(overallConfigDraft, name);
  }

  function setOverallDraft(item: OverallConfigItem, value: string | boolean | number) {
    overallConfigDraft = { ...overallConfigDraft, [item.name]: value };
    overallConfigClears = { ...overallConfigClears, [item.name]: false };
  }

  async function openOverallConfigModal() {
    overallConfigOpen = true;
    overallConfigLoading = true;
    overallConfigError = '';
    try {
      const response = await onLoadOverallConfig();
      overallConfigItems = response.items;
      overallConfigDraft = {};
      overallConfigClears = {};
    } catch (error) {
      overallConfigError = error instanceof Error ? error.message : $t.messages.requestFailed;
    } finally {
      overallConfigLoading = false;
    }
  }

  function closeOverallConfigModal() {
    if (overallConfigSaving) return;
    overallConfigOpen = false;
    overallConfigError = '';
    overallConfigDraft = {};
    overallConfigClears = {};
  }

  function resetOverallConfigItem(item: OverallConfigItem) {
    overallConfigClears = { ...overallConfigClears, [item.name]: true };
    const { [item.name]: _discard, ...nextDraft } = overallConfigDraft;
    overallConfigDraft = nextDraft;
  }

  async function saveOverallConfigModal() {
    const updates = overallConfigItems
      .map((item) => {
        if (overallConfigClears[item.name]) return { name: item.name, clear_override: true };
        if (hasOverallDraft(item.name)) {
          return { name: item.name, value: overallConfigDraft[item.name] };
        }
        return null;
      })
      .filter(Boolean) as OverallConfigUpdateRequest['updates'];
    if (!updates.length) {
      overallConfigOpen = false;
      return;
    }
    overallConfigSaving = true;
    overallConfigError = '';
    try {
      const response = await onSaveOverallConfig({ updates });
      overallConfigItems = response.items;
      overallConfigDraft = {};
      overallConfigClears = {};
    } catch (error) {
      overallConfigError = error instanceof Error ? error.message : $t.messages.requestFailed;
    } finally {
      overallConfigSaving = false;
    }
  }

  function sourceLabel(source: OverallConfigItem['source']) {
    if (source === 'override') return $t.settings.overallConfigSourceOverride;
    if (source === 'env') return $t.settings.overallConfigSourceEnv;
    return $t.settings.overallConfigSourceDefault;
  }
</script>

{#if open}
  <div class="mobile-drawer-root fixed inset-0 z-50">
    <button class="drawer-backdrop absolute inset-0" type="button" tabindex="-1" aria-label={$t.settings.closeLabel} on:click={onClose}></button>
    <aside
      class="mobile-drawer-panel fade-in absolute right-0 top-0 flex h-full w-full max-w-lg flex-col border-l border-zinc-800 bg-zinc-900 shadow-2xl"
      aria-labelledby="settings-drawer-title"
      use:dialog={{ open, onClose }}
      use:swipeClose={{ enabled: open, onClose }}
    >
      <div class="flex items-center justify-between border-b border-zinc-800 p-5">
        <div>
          <h2 id="settings-drawer-title" class="text-lg font-semibold text-zinc-100">{$t.settings.title}</h2>
          <p class="mt-1 text-xs text-zinc-500">{$t.settings.subtitle}</p>
        </div>
        <button type="button" class="mobile-touch-target control-focus rounded-lg p-1.5 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100" aria-label={$t.settings.closeLabel} on:click={onClose}>x</button>
      </div>

      <div class="mobile-drawer-scroll min-h-0 flex-1 overflow-y-auto p-5">
        <button
          type="button"
          class="control-focus mb-5 w-full rounded-lg border border-zinc-700 px-3 py-2.5 text-sm font-semibold text-zinc-200 transition-colors hover:border-zinc-600 hover:bg-zinc-800"
          on:click={openOverallConfigModal}
        >
          {$t.settings.overallConfig}
        </button>

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
                <h3 class="text-sm font-semibold text-zinc-200">{$t.settings.r2Backup}</h3>
                <p class="mt-1 text-xs text-zinc-500">{$t.settings.r2BackupHint}</p>
              </div>
              <label class="flex items-center gap-2 text-xs font-medium text-zinc-300">
                <input bind:checked={r2BackupEnabled} type="checkbox" class="control-focus accent-emerald-500" />
                {$t.settings.r2BackupEnabled}
              </label>
            </div>
            <div class="space-y-4">
              <label class="block">
                <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.settings.r2EndpointUrl}</span>
                <input bind:value={r2EndpointUrl} class="control-focus w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 font-mono text-sm text-zinc-100 focus:border-emerald-500" placeholder="https://ACCOUNT_ID.r2.cloudflarestorage.com" />
              </label>
              <label class="block">
                <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.settings.r2BucketName}</span>
                <input bind:value={r2BucketName} class="control-focus w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 font-mono text-sm text-zinc-100 focus:border-emerald-500" />
              </label>
              <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <label class="block">
                  <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.settings.r2Region}</span>
                  <input bind:value={r2Region} class="control-focus w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 font-mono text-sm text-zinc-100 focus:border-emerald-500" placeholder="auto" />
                </label>
                <label class="block">
                  <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.settings.r2KeyPrefix}</span>
                  <input bind:value={r2KeyPrefix} class="control-focus w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 font-mono text-sm text-zinc-100 focus:border-emerald-500" placeholder="gallery/" />
                </label>
              </div>
              <label class="block">
                <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.settings.r2SyncIntervalHours}</span>
                <input bind:value={r2SyncIntervalHours} type="number" min="0" step="1" class="control-focus w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 font-mono text-sm text-zinc-100 focus:border-emerald-500" on:blur={normalizeR2SyncIntervalHours} />
                <span class="mt-1.5 block text-xs text-zinc-500">{$t.settings.r2SyncIntervalHint}</span>
              </label>
              <label class="block">
                <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.settings.r2AccessKeyId}</span>
                <input bind:value={r2AccessKeyId} type={r2AccessKeyIdInputType} class="control-focus w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 font-mono text-sm text-zinc-100 focus:border-emerald-500" />
                <span class="mt-1.5 block text-xs text-zinc-500">{$t.settings.r2SecretHint}</span>
              </label>
              <label class="block">
                <span class="mb-1.5 block text-xs font-medium text-zinc-400">{$t.settings.r2SecretAccessKey}</span>
                <input bind:value={r2SecretAccessKey} type={r2SecretAccessKeyInputType} class="control-focus w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 font-mono text-sm text-zinc-100 focus:border-emerald-500" />
                <span class="mt-1.5 block text-xs text-zinc-500">{$t.settings.r2SecretHint}</span>
              </label>
              <button
                type="button"
                disabled={r2HealthChecking}
                class="control-focus w-full rounded-lg border border-zinc-700 px-3 py-2.5 text-sm font-semibold text-zinc-200 transition-colors hover:border-zinc-600 hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50"
                on:click={checkR2Health}
              >
                {r2HealthChecking ? $t.settings.r2HealthChecking : $t.settings.r2HealthCheck}
              </button>
              {#if r2Health}
                <div class={`rounded-lg border p-3 text-xs ${healthPanelClass(r2Health.status)}`}>
                  <div class="flex items-center justify-between gap-3">
                    <span class="font-semibold">{$t.settings.r2HealthStatus}</span>
                    <span class={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${healthBadgeClass(r2Health.status)}`}>
                      {healthStatusLabel(r2Health.status)}
                    </span>
                  </div>
                  <div class="mt-2 space-y-1.5">
                    {#each r2Health.checks as check}
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
            </div>
          </section>

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
              <button
                type="button"
                disabled={promptOptimizerHealthChecking}
                class="control-focus w-full rounded-lg border border-emerald-500/40 px-3 py-2.5 text-sm font-semibold text-emerald-200 transition-colors hover:bg-emerald-500/10 disabled:cursor-not-allowed disabled:opacity-50"
                on:click={checkPromptOptimizerHealth}
              >
                {promptOptimizerHealthChecking ? $t.settings.promptOptimizerHealthChecking : $t.settings.promptOptimizerHealthCheck}
              </button>
            </div>
          </section>
        </div>
      </div>

      <div class="space-y-3 border-t border-zinc-800 p-5">
        {#if promptOptimizerHealth}
          <div class={`rounded-lg border p-3 text-xs ${healthPanelClass(promptOptimizerHealth.status)}`} data-testid="prompt-optimizer-health-result">
            <div class="flex items-center justify-between gap-3">
              <div class="min-w-0">
                <span class="font-semibold">{$t.settings.promptOptimizerHealth}</span>
                <div class="mt-1 text-[11px] text-inherit/70">
                  {promptOptimizerHealth.model}
                  {#if promptOptimizerHealth.duration_ms}{' '}- {promptOptimizerHealth.duration_ms} ms{/if}
                </div>
              </div>
              <div class="flex items-center gap-2">
                <span class={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${healthBadgeClass(promptOptimizerHealth.status)}`}>
                  {healthStatusLabel(promptOptimizerHealth.status)}
                </span>
                <button
                  type="button"
                  class="mobile-touch-target control-focus rounded-lg p-1.5 text-inherit/70 hover:bg-black/10 hover:text-inherit"
                  aria-label={$t.common.close}
                  on:click={closePromptOptimizerHealth}
                >
                  x
                </button>
              </div>
            </div>
            <div class="mt-2 rounded-md border border-zinc-800 bg-zinc-950/50 p-2 text-zinc-300">
              {promptOptimizerHealth.message}
            </div>
          </div>
        {/if}
        {#if health}
          {@const displayStatus = presetHealthDisplayStatus(health)}
          <div class={`rounded-lg border p-3 text-xs ${healthPanelClass(displayStatus)}`} data-testid="preset-health-result">
            <div class="flex items-center justify-between gap-3">
              <div class="min-w-0">
                <span class="font-semibold">{$t.settings.healthStatus}</span>
                <div class="mt-1 text-[11px] text-inherit/70">{$t.settings.healthTestResult}</div>
              </div>
              <div class="flex items-center gap-2">
                <span class={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${healthBadgeClass(displayStatus)}`}>
                  {healthStatusLabel(displayStatus)}
                </span>
                <button
                  type="button"
                  class="mobile-touch-target control-focus rounded-lg p-1.5 text-inherit/70 hover:bg-black/10 hover:text-inherit"
                  aria-label={$t.common.close}
                  on:click={closePresetHealth}
                >
                  x
                </button>
              </div>
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

    {#if overallConfigOpen}
      <div class="mobile-dialog-root fixed inset-0 z-[70] flex items-center justify-center bg-black/70 p-4">
        <button class="absolute inset-0" type="button" tabindex="-1" aria-label={$t.settings.closeOverallConfig} on:click={closeOverallConfigModal}></button>
        <div
          class="mobile-dvh-dialog fade-in relative flex max-h-[90vh] w-full max-w-5xl flex-col rounded-xl border border-zinc-800 bg-zinc-900 shadow-2xl"
          aria-labelledby="overall-config-dialog-title"
          use:dialog={{ open: overallConfigOpen, onClose: closeOverallConfigModal }}
        >
          <div class="flex items-start justify-between gap-4 border-b border-zinc-800 p-5">
            <div class="min-w-0">
              <h2 id="overall-config-dialog-title" class="text-base font-semibold text-zinc-100">{$t.settings.overallConfig}</h2>
              <p class="mt-1 text-xs leading-5 text-zinc-500">{$t.settings.overallConfigHint}</p>
            </div>
            <button type="button" class="mobile-touch-target control-focus rounded-lg p-1.5 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100" aria-label={$t.settings.closeOverallConfig} on:click={closeOverallConfigModal}>
              x
            </button>
          </div>

          <div class="min-h-0 flex-1 overflow-y-auto p-5">
            {#if overallConfigLoading}
              <div class="rounded-lg border border-zinc-800 bg-zinc-950/60 p-4 text-sm text-zinc-400">{$t.settings.overallConfigLoading}</div>
            {:else}
              <div class="space-y-6">
                {#each overallConfigGroupNames as group}
                  <section>
                    <h3 class="mb-3 text-sm font-semibold text-zinc-200">{group}</h3>
                    <div class="space-y-3">
                      {#each overallConfigGroups[group] as item}
                        <div class="rounded-lg border border-zinc-800 bg-zinc-950/50 p-3" data-testid={`overall-config-${item.name}`}>
                          <div class="mb-2 flex flex-wrap items-start justify-between gap-2">
                            <div class="min-w-0">
                              <div class="font-mono text-xs font-semibold text-zinc-100">{item.name}</div>
                              <div class="mt-1 text-xs leading-5 text-zinc-500">{item.description}</div>
                            </div>
                            <div class="flex shrink-0 flex-wrap justify-end gap-1.5">
                              <span class="rounded border border-zinc-700 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-zinc-400">{sourceLabel(item.source)}</span>
                              {#if item.restart_required}
                                <span class="rounded border border-amber-500/40 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-amber-300">{$t.settings.restartRequired}</span>
                              {/if}
                              {#if item.build_only}
                                <span class="rounded border border-sky-500/40 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-sky-300">{$t.settings.buildOnly}</span>
                              {/if}
                            </div>
                          </div>

                          <div class="grid grid-cols-1 gap-2 sm:grid-cols-[1fr_auto]">
                            {#if item.type === 'bool'}
                              <label class="flex items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-xs text-zinc-300">
                                <input
                                  type="checkbox"
                                  class="control-focus accent-emerald-500"
                                  checked={Boolean(overallDraftValue(item))}
                                  on:change={(event) => setOverallDraft(item, event.currentTarget.checked)}
                                />
                                {$t.common.active}
                              </label>
                            {:else}
                              <input
                                value={String(overallDraftValue(item) ?? '')}
                                type={item.type === 'int' || item.type === 'float' ? 'number' : item.secret ? 'password' : 'text'}
                                step={item.type === 'float' ? 'any' : '1'}
                                class="control-focus w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 font-mono text-sm text-zinc-100 focus:border-emerald-500"
                                on:input={(event) => setOverallDraft(item, event.currentTarget.value)}
                              />
                            {/if}
                            <button
                              type="button"
                              class="control-focus rounded-lg border border-zinc-700 px-3 py-2 text-xs font-semibold text-zinc-300 hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40"
                              disabled={!item.has_override && !hasOverallDraft(item.name)}
                              on:click={() => resetOverallConfigItem(item)}
                            >
                              {$t.settings.resetToEnv}
                            </button>
                          </div>
                          {#if item.has_override || item.is_env_set}
                            <div class="mt-2 grid grid-cols-1 gap-1 text-[11px] text-zinc-500 sm:grid-cols-2">
                              <div class="truncate">env: <span class="font-mono">{item.env_value_masked || '-'}</span></div>
                              <div class="truncate">override: <span class="font-mono">{item.override_value_masked || '-'}</span></div>
                            </div>
                          {/if}
                        </div>
                      {/each}
                    </div>
                  </section>
                {/each}
              </div>
            {/if}
            {#if overallConfigError}
              <div class="mt-3 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-200">{overallConfigError}</div>
            {/if}
          </div>

          <div class="flex justify-end gap-3 border-t border-zinc-800 p-5">
            <button type="button" class="control-focus rounded-lg border border-zinc-700 px-4 py-2 text-sm text-zinc-300 hover:bg-zinc-800" on:click={closeOverallConfigModal}>
              {$t.common.close}
            </button>
            <button
              type="button"
              disabled={overallConfigLoading || overallConfigSaving}
              class="control-focus rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
              on:click={saveOverallConfigModal}
            >
              {overallConfigSaving ? $t.settings.saving : $t.settings.saveOverallConfig}
            </button>
          </div>
        </div>
      </div>
    {/if}

    {#if systemPromptOpen}
      <div class="mobile-dialog-root fixed inset-0 z-[70] flex items-center justify-center bg-black/70 p-4">
        <button class="absolute inset-0" type="button" tabindex="-1" aria-label={$t.settings.closeSystemPromptEditor} on:click={closeSystemPromptEditor}></button>
        <div
          class="mobile-dvh-dialog fade-in relative flex max-h-[88vh] w-full max-w-3xl flex-col rounded-xl border border-zinc-800 bg-zinc-900 shadow-2xl"
          aria-labelledby="system-prompt-dialog-title"
          use:dialog={{ open: systemPromptOpen, onClose: closeSystemPromptEditor }}
        >
          <div class="flex items-start justify-between gap-4 border-b border-zinc-800 p-5">
            <div class="min-w-0">
              <h2 id="system-prompt-dialog-title" class="text-base font-semibold text-zinc-100">{$t.settings.systemPromptTitle}</h2>
              <p class="mt-1 text-xs leading-5 text-zinc-500">{$t.settings.systemPromptHint}</p>
            </div>
            <button type="button" class="mobile-touch-target control-focus rounded-lg p-1.5 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100" aria-label={$t.settings.closeSystemPromptEditor} on:click={closeSystemPromptEditor}>
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
                  class="system-prompt-textarea control-focus min-h-[420px] w-full resize-y rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-3 font-mono text-xs leading-5 text-zinc-100 focus:border-emerald-500"
                  spellcheck="false"
                  data-autofocus
                  use:plainTextInput
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
