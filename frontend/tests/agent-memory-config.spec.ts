import { test, expect } from '@playwright/test';

// Phase 5 · Agent memory configuration switches (docs/requirement/agent-memory/phase-5-agent-memory-config).
// Covers P5-FE-01..07. The app must be running; this spec only drives the mocked API.

const CAPS = { summary_enabled: true, memory_inject_enabled: true, memory_extract_enabled: true, memory_inject_max_items: 20, memory_search_limit: 5, memory_blacklist_auto_handoff: false };

function draft(overrides: Record<string, unknown> = {}) {
  return {
    system_prompt: '你是一位专业的售后客服。',
    tool_names: [],
    knowledge_base_ids: [],
    max_model_rounds: 6,
    model_profile_id: null,
    model_profile_version: null,
    reply_language: 'auto',
    summary_enabled: true,
    memory_enabled: true,
    memory_read_enabled: true,
    memory_write: true,
    memory_types: ['profile', 'preference', 'event'],
    memory_search_enabled: true,
    memory_write_mode: 'auto',
    memory_inject_max_items: null,
    memory_search_limit: null,
    memory_blacklist_auto_handoff: false,
    ...overrides,
  };
}

/** Route handler mirroring the agent editor's API surface. */
function agentRoutes(options: { role: string; caps?: Record<string, unknown>; draft?: Record<string, unknown>; onSave?: (config: any) => void }) {
  const caps = options.caps ?? CAPS;
  let agent: any = { id: 'agent-1', name: '售后服务助手', description: '解答产品问题', draft: draft(options.draft), draft_revision: 1, published_version: 1, archived: false, updated_at: '2026-10-01T00:00:00Z' };
  return async (route: any) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace('/api/v1', '');
    if (path === '/me') return route.fulfill({ json: { role: options.role, tenant: '测试企业' } });
    if (path === '/agents/memory-config-caps') return route.fulfill({ json: caps });
    if (path === '/agents/catalog') return route.fulfill({ json: { items: [agent], total: 1, page: 1, page_size: 12 } });
    if (path === '/agents/agent-1' && request.method() === 'PUT') {
      const body = request.postDataJSON();
      options.onSave?.(body.config);
      agent = { ...agent, name: body.name, description: body.description, draft: body.config, draft_revision: agent.draft_revision + 1 };
      return route.fulfill({ json: agent });
    }
    if (path === '/agents/agent-1') return route.fulfill({ json: agent });
    if (path === '/agents/agent-1/evaluation-reports') return route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 1, versions: [] } });
    if (path.startsWith('/model-gateway/catalog/')) return route.fulfill({ json: { items: [], total: 0 } });
    if (path === '/tools' || path === '/knowledge-bases' || path === '/modules') return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  };
}

async function openEditor(page: any, options: Parameters<typeof agentRoutes>[0]) {
  await page.route('**/api/v1/**', agentRoutes(options));
  await page.goto('/#/agents/agent-1/config');
  await expect(page.getByLabel('Agent 名称')).toHaveValue('售后服务助手');
}

test('P5-FE-01 memory group renders 4 groups with correct defaults', async ({ page }) => {
  await openEditor(page, { role: 'admin' });
  const memory = page.locator('#agent-memory');
  await expect(memory).toBeVisible();
  await expect(memory.getByRole('heading', { name: '会话记忆' })).toBeVisible();
  await expect(memory.getByRole('heading', { name: '客户长期记忆' })).toBeVisible();
  // Defaults: all switches on except blacklist auto-handoff.
  await expect(page.getByRole('switch', { name: '滚动摘要' })).toBeChecked();
  await expect(page.getByRole('switch', { name: '启用客户长期记忆' })).toBeChecked();
  await expect(page.getByRole('switch', { name: '读取记忆' })).toBeChecked();
  await expect(page.getByRole('switch', { name: '自动写入记忆' })).toBeChecked();
  await expect(page.getByRole('radio', { name: '自动写入' })).toBeChecked();
  await expect(page.getByRole('radio', { name: '全部人工确认' })).not.toBeChecked();
  await expect(page.getByRole('checkbox', { name: '客户画像' })).toBeChecked();
  await expect(page.getByRole('checkbox', { name: '客户偏好' })).toBeChecked();
  await expect(page.getByRole('checkbox', { name: '事件记忆' })).toBeChecked();
  await expect(page.getByRole('switch', { name: '启用 memory_search 检索' })).toBeChecked();
  // Limits are initialized to the platform caps (20 / 5), not blank.
  await expect(page.getByLabel('单次注入上限')).toHaveValue('20');
  await expect(page.getByLabel('检索返回上限')).toHaveValue('5');
  await expect(page.getByRole('switch', { name: '黑名单客户自动转人工' })).not.toBeChecked();
  await page.screenshot({ path: 'test-results/agent-memory-defaults.png', fullPage: true });
});

test('P5-FE-02 master switch hides every child option', async ({ page }) => {
  await openEditor(page, { role: 'admin' });
  const master = page.getByRole('switch', { name: '启用客户长期记忆' });
  await master.uncheck();
  await expect(master).not.toBeChecked();
  // Master off hides (not greys out) every dependent option, with a hint instead.
  await expect(page.getByRole('switch', { name: '读取记忆' })).toHaveCount(0);
  await expect(page.getByRole('switch', { name: '自动写入记忆' })).toHaveCount(0);
  await expect(page.getByRole('switch', { name: '启用 memory_search 检索' })).toHaveCount(0);
  await expect(page.getByRole('switch', { name: '黑名单客户自动转人工' })).toHaveCount(0);
  await expect(page.getByRole('checkbox', { name: '客户画像' })).toHaveCount(0);
  await expect(page.getByRole('radio', { name: '自动写入' })).toHaveCount(0);
  await expect(page.getByLabel('单次注入上限')).toHaveCount(0);
  await expect(page.getByLabel('检索返回上限')).toHaveCount(0);
  await expect(page.getByText('关闭后，此 Agent 不再读取、写入或检索客户记忆。')).toBeVisible();
  // Re-enabling restores the child options with their values intact.
  await master.check();
  await expect(page.getByRole('switch', { name: '读取记忆' })).toBeChecked();
  await expect(page.getByLabel('单次注入上限')).toHaveValue('20');
});

test('P5-FE-03 conditional fields: write policy and search limit', async ({ page }) => {
  await openEditor(page, { role: 'admin' });
  await page.getByRole('switch', { name: '自动写入记忆' }).uncheck();
  await expect(page.getByRole('radio', { name: '自动写入' })).toHaveCount(0);
  await page.getByRole('switch', { name: '自动写入记忆' }).check();
  await expect(page.getByRole('radio', { name: '自动写入' })).toBeVisible();
  await page.getByRole('switch', { name: '启用 memory_search 检索' }).uncheck();
  await expect(page.getByLabel('检索返回上限')).toHaveCount(0);
  await page.getByRole('switch', { name: '启用 memory_search 检索' }).check();
  await expect(page.getByLabel('检索返回上限')).toBeVisible();
  // Read off hides search, its limit and the injection limit together.
  await page.getByRole('switch', { name: '读取记忆' }).uncheck();
  await expect(page.getByRole('switch', { name: '启用 memory_search 检索' })).toHaveCount(0);
  await expect(page.getByLabel('单次注入上限')).toHaveCount(0);
});

test('P5-FE-04 presets fill the form without locking, then save', async ({ page }) => {
  let saved: any = null;
  await openEditor(page, { role: 'admin', onSave: config => { saved = config; } });
  const preset = page.getByLabel('应用预设');
  // High-compliance: everything on but review mode.
  await preset.selectOption('compliance');
  await expect(page.getByRole('radio', { name: '全部人工确认' })).toBeChecked();
  await expect(page.getByRole('switch', { name: '启用客户长期记忆' })).toBeChecked();
  // Knowledge Q&A: master switch off, children hidden.
  await preset.selectOption('knowledge');
  await expect(page.getByRole('switch', { name: '启用客户长期记忆' })).not.toBeChecked();
  await expect(page.getByRole('switch', { name: '读取记忆' })).toHaveCount(0);
  // Demo: everything off, including summary.
  await preset.selectOption('demo');
  await expect(page.getByRole('switch', { name: '滚动摘要' })).not.toBeChecked();
  // Short-term: master on, read/write/search off.
  await preset.selectOption('short_term');
  await expect(page.getByRole('switch', { name: '启用客户长期记忆' })).toBeChecked();
  await expect(page.getByRole('switch', { name: '读取记忆' })).not.toBeChecked();
  await expect(page.getByRole('switch', { name: '自动写入记忆' })).not.toBeChecked();
  // Standard: everything on, auto mode.
  await preset.selectOption('standard');
  await expect(page.getByRole('switch', { name: '读取记忆' })).toBeChecked();
  await expect(page.getByRole('radio', { name: '自动写入' })).toBeChecked();
  // Not locked: values can still be edited after applying.
  await page.getByRole('switch', { name: '滚动摘要' }).uncheck();
  await expect(page.getByRole('switch', { name: '滚动摘要' })).not.toBeChecked();
  // Re-apply high-compliance and save it.
  await preset.selectOption('compliance');
  await page.getByRole('button', { name: '保存草稿' }).click();
  await expect.poll(() => saved?.memory_write_mode).toBe('review');
});

test('P5-FE-05 platform-disabled switch shows the "平台已禁用" tooltip', async ({ page }) => {
  await openEditor(page, { role: 'admin', caps: { ...CAPS, memory_inject_enabled: false } });
  const read = page.getByRole('switch', { name: '读取记忆' });
  await expect(read).toBeDisabled();
  await expect(read).not.toBeChecked();
  await page.getByText('读取记忆', { exact: true }).hover();
  await expect(page.getByRole('tooltip')).toContainText('平台已禁用');
});

test('P5-FE-05b platform caps: summary disabled and blacklist forced on', async ({ page }) => {
  await openEditor(page, { role: 'admin', caps: { ...CAPS, summary_enabled: false, memory_blacklist_auto_handoff: true } });
  const summary = page.getByRole('switch', { name: '滚动摘要' });
  await expect(summary).toBeDisabled();
  await expect(summary).not.toBeChecked();
  await page.getByText('滚动摘要', { exact: true }).hover();
  await expect(page.getByRole('tooltip')).toContainText('平台已禁用');
  const blacklist = page.getByRole('switch', { name: '黑名单客户自动转人工' });
  await expect(blacklist).toBeDisabled();
  await expect(blacklist).toBeChecked();
  await page.getByText('黑名单客户自动转人工', { exact: true }).hover();
  // antd keeps the previous hover's tooltip mounted, so match by name.
  await expect(
    page.getByRole('tooltip', { name: '平台已开启自动转人工' })
  ).toBeVisible();
});

test('P5-FE-06 injection limit above the platform cap warns before saving', async ({ page }) => {
  let saved: any = null;
  await openEditor(page, { role: 'admin', onSave: config => { saved = config; } });
  const inject = page.getByLabel('单次注入上限');
  await inject.fill('999');
  await inject.press('Tab');
  let message = '';
  page.once('dialog', async dialog => { message = dialog.message(); await dialog.accept(); });
  await page.getByRole('button', { name: '保存草稿' }).click();
  await expect.poll(() => message).toContain('将被截断为平台上限 20');
  await expect.poll(() => saved?.memory_inject_max_items).toBe(999);
  // A value equal to the platform cap is not truncated, so no warning fires.
  await inject.fill('20');
  await inject.press('Tab');
  let warned = false;
  page.once('dialog', async dialog => { warned = true; await dialog.accept(); });
  await page.getByRole('button', { name: '保存草稿' }).click();
  await expect.poll(() => saved?.memory_inject_max_items).toBe(20);
  expect(warned).toBe(false);
});

test('P5-FE-07 viewer sees every control read-only', async ({ page }) => {
  await openEditor(page, { role: 'viewer' });
  await expect(page.getByLabel('Agent 名称')).toBeDisabled();
  await expect(page.getByRole('switch', { name: '滚动摘要' })).toBeDisabled();
  await expect(page.getByRole('switch', { name: '启用客户长期记忆' })).toBeDisabled();
  await expect(page.getByRole('switch', { name: '读取记忆' })).toBeDisabled();
  await expect(page.getByRole('switch', { name: '自动写入记忆' })).toBeDisabled();
  await expect(page.getByRole('switch', { name: '启用 memory_search 检索' })).toBeDisabled();
  await expect(page.getByRole('switch', { name: '黑名单客户自动转人工' })).toBeDisabled();
  await expect(page.getByRole('checkbox', { name: '客户画像' })).toBeDisabled();
  await expect(page.getByRole('radio', { name: '自动写入' })).toBeDisabled();
  await expect(page.getByLabel('单次注入上限')).toBeDisabled();
  await expect(page.getByLabel('检索返回上限')).toBeDisabled();
  await expect(page.getByLabel('应用预设')).toBeDisabled();
  await expect(page.getByRole('button', { name: '保存草稿' })).toBeDisabled();
  await expect(page.getByRole('button', { name: '发布', exact: true })).toBeDisabled();
});
