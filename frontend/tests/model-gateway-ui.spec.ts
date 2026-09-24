import { test, expect } from '@playwright/test';

test('existing profile JSON fills missing defaults and preserves configured values', async ({ page }) => {
  await page.route('**/api/v1/model-gateway/catalog/profiles?**', route => route.fulfill({ json: {
    items: [{ id: 'legacy-profile', name: '已有方案', enabled: true, revision: 1, spec: { name: '已有方案', operation: 'chat', model_id: 'model-chat', timeout_seconds: 60, parameters: { temperature: 0, max_tokens: 4096 } } }], total: 1,
  } }));
  await page.getByRole('button', { name: '配置方案', exact: true }).click();
  await page.getByRole('button', { name: '已有方案', exact: true }).click();
  const editor = page.getByRole('textbox', { name: '配置 JSON', exact: true });
  const spec = JSON.parse(await editor.inputValue());
  expect(spec).toMatchObject({ timeout_seconds: 60, retries: 0, fallback_model_ids: [], require_tools: false, parameters: { temperature: 0, max_tokens: 4096, thinking: null } });
  await page.getByRole('combobox', { name: /^调用能力/ }).selectOption('embed');
  expect(JSON.parse(await editor.inputValue()).parameters).toEqual({});
  await page.getByRole('combobox', { name: /^调用能力/ }).selectOption('chat');
  expect(JSON.parse(await editor.inputValue()).parameters).toEqual({ temperature: null, max_tokens: null, thinking: null });
});

test('thinking mode stays in sync with profile JSON and unsupported models keep defaults', async ({ page }) => {
  await page.route('**/api/v1/model-gateway/catalog/connections?**', route => route.fulfill({ json: { items: [{ id: 'deepseek', name: 'DeepSeek', enabled: true, revision: 1, spec: { protocol: 'openai_compatible', base_url: 'https://api.deepseek.com' } }], total: 1 } }));
  await page.route('**/api/v1/model-gateway/catalog/models?**', route => route.fulfill({ json: { items: [
    { id: 'thinking-model', name: 'DeepSeek Pro', enabled: true, spec: { connection_id: 'deepseek', model_name: 'deepseek-v4-pro' } },
    { id: 'other-model', name: 'Other model', enabled: true, spec: { connection_id: 'other', model_name: 'unknown' } },
  ], total: 2 } }));
  await page.getByRole('button', { name: '刷新资源' }).click();
  await page.getByRole('button', { name: '配置方案', exact: true }).click();
  await page.getByRole('button', { name: '新建配置方案' }).click();
  await page.getByRole('combobox', { name: /^主模型/ }).selectOption('thinking-model');
  const mode = page.getByLabel('思考模式', { exact: true });
  await expect(mode).toHaveValue('default');
  await mode.selectOption('enabled');
  const editor = page.getByRole('textbox', { name: '配置 JSON', exact: true });
  let spec = JSON.parse(await editor.inputValue());
  expect(spec.parameters).toEqual({ temperature: null, max_tokens: null, thinking: 'enabled' });
  await editor.fill(JSON.stringify({ ...spec, parameters: { ...spec.parameters, thinking: 'disabled' } }));
  await expect(mode).toHaveValue('disabled');
  await mode.selectOption('default');
  spec = JSON.parse(await editor.inputValue());
  expect(spec.parameters.thinking).toBeNull();
  await page.getByRole('combobox', { name: /^主模型/ }).selectOption('other-model');
  await expect(mode.locator('option[value="enabled"]')).toHaveJSProperty('disabled', true);
  await expect(mode.locator('option[value="disabled"]')).toHaveJSProperty('disabled', true);
});

test('provider models load dynamically, refresh and retain manual fallback', async ({ page }) => {
  let failed = false;
  await page.route('**/api/v1/model-gateway/catalog/connections?**', route => route.fulfill({ json: {
    items: [{ id: 'provider-1', name: 'DeepSeek', enabled: true, revision: 1, spec: { protocol: 'openai_compatible' } }, { id: 'provider-2', name: 'Other provider', enabled: true, revision: 1, spec: { protocol: 'openai_compatible' } }], total: 2,
  } }));
  await page.route('**/api/v1/model-gateway/connections/*/available-models', route => {
    if (failed) return route.fulfill({ status: 502, json: { detail: 'model_discovery_unavailable' } });
    return route.fulfill({ json: { items: [{ id: 'deepseek-flash' }, { id: 'deepseek-v4-pro' }], truncated: false } });
  });
  await page.getByRole('button', { name: '刷新资源' }).click();
  await page.getByRole('button', { name: '新建模型' }).click();
  await page.getByRole('combobox', { name: /^供应商连接/ }).selectOption('provider-1');
  await expect(page.getByLabel('从渠道选择模型')).toBeEnabled();
  await page.getByLabel('从渠道选择模型').selectOption('deepseek-v4-pro');
  await expect(page.getByLabel('模型标识', { exact: true })).toHaveValue('deepseek-v4-pro');
  const spec = JSON.parse(await page.getByRole('textbox', { name: '配置 JSON', exact: true }).inputValue());
  expect(spec.model_name).toBe('deepseek-v4-pro');
  await page.getByRole('combobox', { name: /^供应商连接/ }).selectOption('provider-2');
  await expect(page.getByLabel('模型标识', { exact: true })).toHaveValue('');
  await expect(page.getByLabel('从渠道选择模型')).toBeEnabled();
  failed = true;
  await page.getByRole('button', { name: '刷新模型列表' }).click();
  await expect(page.getByText('获取失败，请检查渠道凭据和服务地址；也可手动填写模型标识。')).toBeVisible();
  await page.getByLabel('模型标识', { exact: true }).fill('manual-model');
  await expect(page.getByLabel('模型标识', { exact: true })).toHaveValue('manual-model');
});

test('direct key is write-only, can be replaced or cleared, and supports legacy environment mode', async ({ page }) => {
  let saved: Record<string, unknown> = {};
  await page.route('**/api/v1/model-gateway/connections**', async route => {
    if (!['POST', 'PUT'].includes(route.request().method())) return route.fallback();
    saved = route.request().postDataJSON();
    const { api_key, clear_api_key, ...spec } = saved;
    await route.fulfill({ json: { id: 'direct-key', name: spec.name, spec, revision: 2, enabled: true, credential_source: clear_api_key ? 'none' : spec.credential_ref ? 'environment' : 'stored', credential_configured: !clear_api_key } });
  });
  await page.getByRole('button', { name: '渠道管理', exact: true }).click();
  await page.getByRole('button', { name: '新建渠道' }).click();
  const key = page.getByLabel('API Key', { exact: true });
  await expect(key).toHaveAttribute('type', 'password');
  await key.fill('fake-ui-provider-key');
  await expect(page.getByRole('textbox', { name: '配置 JSON', exact: true })).not.toHaveValue(/fake-ui-provider-key/);
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await expect(key).toHaveValue('');
  expect(saved.api_key).toBe('fake-ui-provider-key');
  await expect(key).toHaveAttribute('placeholder', '已配置，留空保留原密钥');
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await expect.poll(() => saved.api_key).toBeUndefined();
  await key.fill('fake-ui-replacement');
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await expect.poll(() => saved.api_key).toBe('fake-ui-replacement');
  await expect(key).toHaveValue('');
  await page.getByLabel('清除已保存的 API Key', { exact: true }).check();
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await expect.poll(() => saved.clear_api_key).toBe(true);
  await expect(page.getByLabel('清除已保存的 API Key', { exact: true })).toHaveCount(0);
  await page.getByLabel('凭据方式').selectOption('environment');
  await page.getByLabel('凭据环境变量').fill('AGENT_MODEL_SECRET_TEST');
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await expect.poll(() => saved.credential_ref).toBe('AGENT_MODEL_SECRET_TEST');
  expect(saved.api_key).toBeUndefined();
});

test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const resources = [
      { id: 'model-chat', name: '客服对话模型', enabled: true, revision: 2, published_version: null, spec: { name: '客服对话模型', model_name: 'chat-v1', connection_id: 'connection-1', operations: ['chat'], tool_calling: true } },
      { id: 'model-embed', name: '知识向量模型', enabled: false, revision: 1, published_version: null, spec: { name: '知识向量模型', model_name: 'embed-v1', connection_id: 'connection-1', operations: ['embed'] } },
    ];
    let data: unknown = [];
    if (path.endsWith('/me')) data = { role: 'admin', tenant: '测试工作区' };
    if (path.endsWith('/connections')) data = [{ id: 'connection-1', name: '演示服务', enabled: true, revision: 1, published_version: null, credential_configured: false, spec: { name: '演示服务', protocol: 'demo', concurrency: 8 } }];
    if (path.endsWith('/models')) data = resources;
    if (path.includes('/catalog/')) {
      const params = new URL(route.request().url()).searchParams;
      let rows = data as typeof resources;
      const q = params.get('q')?.toLowerCase() || '';
      rows = rows.filter(row => JSON.stringify(row).toLowerCase().includes(q));
      if (params.has('enabled')) rows = rows.filter(row => row.enabled === (params.get('enabled') === 'true'));
      if (params.has('operation')) rows = rows.filter(row => row.spec.operations?.includes(params.get('operation')!));
      if (params.has('connection_id')) rows = rows.filter(row => row.spec.connection_id === params.get('connection_id'));
      data = { items: rows, total: rows.length, page: 1, page_size: 12 };
    }
    if (path.endsWith('/settings')) data = { revision: 0, spec: { input_review: 'off', output_review: 'off', pricing: {} } };
    if (path.endsWith('/statistics')) data = { summary: { requests: 2, completed: 1, failed: 1, blocked: 0, average_ms: 600, estimated_cost: null, unknown_cost_attempts: 2 }, daily: [], models: [], operations: [] };
    if (path.endsWith('/call-records')) data = { total: 1, items: [{ id: 'call-error', profile_name: '测试方案', operation: 'embed', status: 'failed', error_code: 'model_timeout', duration_ms: 1000, profile_version: 1, created_at: new Date().toISOString(), estimated_cost: null, unknown_cost_attempts: 1 }] };
    if (path.endsWith('/calls')) data = [
      { id: 'call-ok', operation: 'chat', status: 'completed', error_code: null, duration_ms: 200, profile_version: 1 },
      { id: 'call-error', operation: 'embed', status: 'failed', error_code: 'model_timeout', duration_ms: 1000, profile_version: 1 },
    ];
    if (path.endsWith('/calls/call-error')) data = { id: 'call-error', attempts: [{ error_code: 'model_timeout', usage: null }] };
    await route.fulfill({ json: data });
  });
  await page.goto('/');
  await page.getByRole('navigation').getByRole('button', { name: '模型网关' }).click();
});

test('search and combined status/capability filters show matching models', async ({ page }) => {
  await page.getByRole('button', { name: '模型广场', exact: true }).click();
  await expect(page.locator('.gateway-card')).toHaveCount(2);
  await page.getByLabel('搜索资源').fill('embed-v1');
  await expect(page.locator('.gateway-card')).toHaveCount(1);
  await expect(page.locator('.gateway-card')).toContainText('知识向量模型');
  await page.getByLabel('资源状态').selectOption('enabled');
  await expect(page.getByText('没有匹配的资源，请调整筛选条件')).toBeVisible();
  await page.getByLabel('搜索资源').fill('');
  await page.getByRole('button', { name: '对话生成', exact: true }).click();
  await expect(page.locator('.gateway-card')).toHaveCount(1);
  await expect(page.locator('.gateway-card')).toContainText('客服对话模型');
  await page.getByLabel('筛选接入渠道').selectOption('connection-1');
  await expect(page.locator('.gateway-card')).toHaveCount(1);
  await page.screenshot({ path: 'test-results/gateway-catalog-desktop.png', fullPage: true });
});

test('form and JSON stay in sync without dropping advanced properties', async ({ page }) => {
  await page.getByRole('button', { name: '渠道管理', exact: true }).click();
  await page.getByRole('button', { name: '演示服务', exact: true }).click();
  await page.getByLabel('资源名称', { exact: true }).fill('新的服务名称');
  const spec = JSON.parse(await page.getByRole('textbox', { name: '配置 JSON', exact: true }).inputValue());
  expect(spec).toMatchObject({ name: '新的服务名称', concurrency: 8, protocol: 'demo' });
  await page.getByRole('textbox', { name: '配置 JSON', exact: true }).fill(JSON.stringify({ ...spec, name: 'JSON 修改' }));
  await expect(page.getByLabel('资源名称', { exact: true })).toHaveValue('JSON 修改');
  await page.getByRole('textbox', { name: '配置 JSON', exact: true }).fill('{');
  await expect(page.getByText('请先修正下方 JSON，再使用表单编辑。')).toBeVisible();
});

test('gateway fits mobile viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByLabel('搜索资源')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: 'test-results/gateway-catalog-mobile.png', fullPage: true });
});

test('logs filter and open diagnostics; monitoring uses returned call records', async ({ page }) => {
  await page.getByRole('button', { name: '调用日志', exact: true }).click();
  await page.getByLabel('调用状态').selectOption('failed');
  await expect(page.getByRole('cell', { name: 'model_timeout', exact: true })).toBeVisible();
  await expect(page.getByRole('cell', { name: 'call-ok', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '查看尝试与用量' }).click();
  await expect(page.getByRole('dialog')).toContainText('model_timeout');
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '监控概览', exact: true }).click();
  await expect(page.locator('.gateway-stat').filter({ hasText: '平均耗时' })).toContainText('600 ms');
  await expect(page.locator('.gateway-stat').filter({ hasText: '失败调用' })).toContainText('1');
});

test('new model drawer saves through existing endpoint', async ({ page }) => {
  let saved: Record<string, unknown> | undefined;
  await page.route('**/api/v1/model-gateway/models', async route => {
    if (route.request().method() !== 'POST') return route.fallback();
    saved = route.request().postDataJSON();
    await route.fulfill({ json: { id: 'new-model', name: saved!.name, spec: saved, revision: 1, enabled: true, published_version: null } });
  });
  await page.getByRole('button', { name: '新建模型' }).click();
  await page.getByLabel('资源名称', { exact: true }).fill('新建测试模型');
  await page.getByLabel('模型标识', { exact: true }).fill('test-model');
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('已保存');
  expect(saved).toMatchObject({ name: '新建测试模型', connection_id: 'connection-1', operations: ['chat'] });
});

test('viewer can inspect models but cannot edit', async ({ page }) => {
  await page.route('**/api/v1/me', route => route.fulfill({ json: { role: 'viewer', tenant: '测试工作区' } }));
  await page.reload();
  await page.getByRole('navigation').getByRole('button', { name: '模型网关' }).click();
  await expect(page.getByRole('button', { name: '新建模型' })).toBeDisabled();
  await page.locator('.gateway-card').first().click();
  await expect(page.getByLabel('资源名称', { exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: '保存', exact: true })).toBeDisabled();
});
