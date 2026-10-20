import { test, expect } from '@playwright/test';

// Phase 6 · 客户记忆可视化与注入可解释性 (docs/requirement/agent-memory/phase-6-memory-visualization).
// Covers P6-FE-01..07 per 04-test-plan.md §6. All APIs are mocked via page.route;
// the dev server only serves the SPA.

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function seedItems(): any[] {
  return [
    { id: 'item-1', type: 'profile', key: 'name', content: '张三丰', prev_content: '张三', confidence: 1.0, effective_confidence: 0.95, status: 'active', source_type: 'human', source_ref: 'conv-1', revision: 3, protected: true, last_referenced_at: '2026-10-12T10:00:00Z', reference_count: 4, created_at: '2026-09-30T10:00:00Z', updated_at: '2026-10-12T10:00:00Z' },
    { id: 'item-2', type: 'preference', key: 'language', content: '用英文回复', confidence: 0.92, effective_confidence: 0.87, status: 'active', source_type: 'extracted', source_ref: 'conv-1', revision: 1, protected: false, last_referenced_at: '2026-10-11T10:00:00Z', reference_count: 2, created_at: '2026-10-01T10:00:00Z', updated_at: '2026-10-11T10:00:00Z' },
    { id: 'item-3', type: 'event', key: 'repair_2026_10', content: '2026-10 设备报修', confidence: 0.8, effective_confidence: 0.64, status: 'active', source_type: 'ticket', source_ref: null, revision: 1, protected: false, last_referenced_at: null, reference_count: 0, created_at: '2026-10-10T10:00:00Z', updated_at: '2026-10-10T10:00:00Z' },
    { id: 'pend-1', type: 'preference', key: 'contact_channel', content: '偏好邮件联系', confidence: 0.6, effective_confidence: 0.6, status: 'pending', source_type: 'extracted', source_ref: 'conv-1', revision: 1, protected: false, last_referenced_at: null, reference_count: 0, created_at: '2026-10-11T10:00:00Z', updated_at: '2026-10-11T10:00:00Z' },
    { id: 'flag-vip', type: 'flag', key: 'vip', content: '年框客户', confidence: 1.0, effective_confidence: 1.0, status: 'active', source_type: 'human', source_ref: null, revision: 1, protected: true, last_referenced_at: null, reference_count: 0, created_at: '2026-10-03T10:00:00Z', updated_at: '2026-10-03T10:00:00Z' },
  ];
}

const cust1 = {
  id: 'cust-1', external_keys: ['sha256:abc'], item_count: 5, last_active_at: '2026-10-12T10:00:00Z', created_at: '2026-09-30T10:00:00Z',
  memory_stats: { total: 5, profile: 1, preference: 2, event: 1, pending: 1, flags: ['vip', 'complaint_risk'], last_updated_at: '2026-10-12T10:00:00Z' },
};
const cust2 = {
  id: 'cust-2', external_keys: ['sha256:xyz'], item_count: 12, last_active_at: '2026-10-13T10:00:00Z', created_at: '2026-09-29T10:00:00Z',
  memory_stats: { total: 12, profile: 5, preference: 4, event: 3, pending: 0, flags: ['blacklist'], last_updated_at: '2026-10-13T10:00:00Z' },
};
const identity = { id: 'ident-1', source: 'channel', external_key: 'sha256:abc', created_at: '2026-09-30T10:00:00Z' };

// Run-dimension injection list, newest first (server order).
const injectionList = [
  { run_id: 'run-2-newest', injected_at: '2026-10-14T09:00:00Z', item_count: 3, conversation_id: 'conv-2' },
  { run_id: 'run-1', injected_at: '2026-10-13T09:00:00Z', item_count: 2, conversation_id: 'conv-1' },
];
const run1Detail = [
  { item_id: 'item-1', key: 'name', type: 'profile', status: 'active', section: 'profile', score: 0.95, item_deleted: false },
  { item_id: 'item-gone', key: null, type: null, status: null, section: 'preference', score: 0.5, item_deleted: true },
];
const run2Detail = [
  { item_id: 'item-1', key: 'name', type: 'profile', status: 'active', section: 'profile', score: 0.95, item_deleted: false },
  { item_id: 'item-2', key: 'language', type: 'preference', status: 'active', section: 'preference', score: 0.87, item_deleted: false },
  { item_id: 'flag-vip', key: 'vip', type: 'flag', status: 'active', section: 'flag', score: null, item_deleted: false },
];
// Full lifecycle for item-1: 抽取 → 确认 → 编辑 → 衰退迁移 → 恢复 (newest first).
const itemTimeline = [
  { id: 5, actor: 'operator@corp', action: 'memory.item.restored', resource: 'memory_item:item-1', details: {}, created_at: '2026-10-13T10:00:00Z' },
  { id: 4, actor: 'system:decay', action: 'memory.decay_pending', resource: 'memory_item:item-1', details: { from: 'profile', to: 'profile_decayed' }, created_at: '2026-10-12T18:00:00Z' },
  { id: 3, actor: 'operator@corp', action: 'memory.item.edited', resource: 'memory_item:item-1', details: { revision: 3 }, created_at: '2026-10-12T10:00:00Z' },
  { id: 2, actor: 'operator@corp', action: 'memory.item.confirmed', resource: 'memory_item:item-1', details: {}, created_at: '2026-10-02T09:00:00Z' },
  { id: 1, actor: 'agent:extractor', action: 'memory.extract.applied', resource: 'memory_item:item-1', details: { added: 1 }, created_at: '2026-10-01T08:00:00Z' },
];
const customerAudit = [
  { id: 12, actor: 'operator@corp', action: 'memory.flag.upserted', resource: 'memory_customer:cust-1', details: { key: 'vip' }, created_at: '2026-10-12T11:00:00Z' },
  { id: 11, actor: 'agent:extractor', action: 'memory.extract.applied', resource: 'memory_customer:cust-1', details: { added: 2, updated: 1 }, created_at: '2026-10-11T08:00:00Z' },
];
const itemInjections = [
  { run_id: 'run-2-newest', injected_at: '2026-10-14T09:00:00Z', section: 'profile', score: 0.95, conversation_id: 'conv-2' },
  { run_id: 'run-1', injected_at: '2026-10-13T09:00:00Z', section: 'profile', score: 0.87, conversation_id: 'conv-1' },
];

interface RouteOptions {
  role?: string;
  items?: any[];
  injections?: any[];
  audit?: any[];
  customers?: any[];
  requested?: string[];
  onListQuery?: (url: URL) => void;
}

/** Route handler covering the Phase 6 memory console API surface. */
function visualizationRoutes(options: RouteOptions = {}) {
  const items = options.items ?? seedItems();
  const customers = options.customers ?? [cust1, cust2];
  const requested = options.requested;
  return async (route: any) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    requested?.push(path + url.search);
    if (path.endsWith('/me')) return route.fulfill({ json: { role: options.role ?? 'operator', tenant: '测试企业' } });
    if (path.endsWith('/memory/customers') && request.method() === 'GET') {
      options.onListQuery?.(url);
      const q = url.searchParams.get('q') || '';
      const flag = url.searchParams.get('flag') || '';
      let matched = customers;
      if (q) matched = matched.filter(row => row.external_keys.some(key => key.includes(q)));
      if (flag) matched = matched.filter(row => row.memory_stats.flags.includes(flag));
      return route.fulfill({ json: { items: matched, total: matched.length, page: 1, page_size: 20 } });
    }
    if (path.endsWith('/memory/customers/cust-1/injections')) return route.fulfill({ json: { items: options.injections ?? injectionList } });
    if (path.endsWith('/memory/customers/cust-1/audit')) return route.fulfill({ json: { items: options.audit ?? customerAudit } });
    if (path.endsWith('/memory/customers/cust-1')) return route.fulfill({ json: { id: 'cust-1', created_at: cust1.created_at, identities: [identity], items } });
    if (path.endsWith('/memory/runs/run-1/injections')) return route.fulfill({ json: { run_id: 'run-1', items: run1Detail } });
    if (path.endsWith('/memory/runs/run-2-newest/injections')) return route.fulfill({ json: { run_id: 'run-2-newest', items: run2Detail } });
    if (path.endsWith('/memory/runs/run-old/injections')) return route.fulfill({ json: { run_id: 'run-old', items: [] } });
    if (path.endsWith('/memory/items/item-1/timeline')) {
      const item = items.find(row => row.id === 'item-1') || null;
      return route.fulfill({ json: { item, timeline: itemTimeline, injection_count: 2, last_injected_at: '2026-10-14T09:00:00Z' } });
    }
    if (path.endsWith('/memory/items/item-1/injections')) return route.fulfill({ json: { items: itemInjections } });
    const itemAction = path.match(/\/memory\/items\/([^/]+)(?:\/(restore|confirm|reject))?$/);
    if (itemAction) {
      const [, id, action] = itemAction;
      const item = items.find(row => row.id === id);
      if (!item) return route.fulfill({ status: 404, json: { detail: 'memory_item_not_found' } });
      if (action === 'confirm') item.status = 'active';
      else if (action === 'reject') item.status = 'deleted';
      else if (action === 'restore') item.status = 'active';
      else if (request.method() === 'DELETE') item.status = 'deleted';
      else if (request.method() === 'PATCH') {
        item.content = request.postDataJSON().content;
        item.source_type = 'human';
        item.confidence = 1.0;
      }
      return route.fulfill({ json: item });
    }
    if (path.endsWith('/modules')) return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  };
}

// ---------------------------------------------------------------------------
// P6-FE-01 列表统计列：总数/分类/待确认高亮/flag 徽标/最近更新 + 排序筛选 (AC-2)
// ---------------------------------------------------------------------------
test('P6-FE-01 customer list stats columns render, sorting and flag filter work', async ({ page }) => {
  const listQueries: URL[] = [];
  await page.route('**/api/v1/**', visualizationRoutes({ onListQuery: url => listQueries.push(url) }));
  await page.goto('/#/memory');

  // FR-2: stats columns for both customers.
  await expect(page.locator('th', { hasText: '记忆总数' })).toBeVisible();
  await expect(page.locator('th', { hasText: '分类条数' })).toBeVisible();
  await expect(page.locator('th', { hasText: '待确认' })).toBeVisible();
  await expect(page.locator('th', { hasText: '服务标记' })).toBeVisible();
  await expect(page.locator('th', { hasText: '最近更新' })).toBeVisible();
  const row1 = page.locator('tr', { hasText: 'sha256:abc' });
  await expect(row1.locator('td').nth(1)).toHaveText('5');
  await expect(row1.locator('.memory-type-counts')).toContainText('画像 1');
  await expect(row1.locator('.memory-type-counts')).toContainText('偏好 2');
  await expect(row1.locator('.memory-type-counts')).toContainText('事件 1');
  await expect(row1.getByRole('button', { name: '1 条待确认' })).toBeVisible();
  await expect(row1.getByText('VIP 客户')).toBeVisible();
  await expect(row1.getByText('投诉风险')).toBeVisible();
  await expect(row1.locator('td').nth(5)).toContainText('2026');
  const row2 = page.locator('tr', { hasText: 'sha256:xyz' });
  await expect(row2.locator('td').nth(1)).toHaveText('12');
  await expect(row2.getByText('黑名单')).toBeVisible();
  // No highlight for a zero pending count: plain text instead of a deep-link button.
  await expect(row2.getByRole('button', { name: /条待确认/ })).toHaveCount(0);

  // Sorting by 记忆总数 then 待确认 sends server-side sort params.
  await page.locator('th.ant-table-column-has-sorters', { hasText: '记忆总数' }).click();
  await expect.poll(() => listQueries.at(-1)?.searchParams.get('sort')).toBe('total');
  await page.locator('th.ant-table-column-has-sorters', { hasText: '待确认' }).click();
  await expect.poll(() => listQueries.at(-1)?.searchParams.get('sort')).toBe('pending');

  // Flag filter: VIP keeps cust-1 and drops the blacklisted cust-2.
  await page.locator('.memory-flag-filter').click();
  await page.locator('.ant-select-item-option[title="VIP 客户"]').click();
  await expect.poll(() => listQueries.at(-1)?.searchParams.get('flag')).toBe('vip');
  await expect(page.locator('tr', { hasText: 'sha256:xyz' })).toHaveCount(0);
  await expect(page.locator('tr', { hasText: 'sha256:abc' })).toBeVisible();

  // Pending count deep-links straight into the customer's pending tab.
  await page.getByRole('button', { name: '1 条待确认' }).click();
  await expect(page).toHaveURL(/#\/memory\/customers\/cust-1\?tab=pending$/);
  await expect(page.getByRole('tab', { name: /待确认/ })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByText('偏好邮件联系')).toBeVisible();
  await page.screenshot({ path: 'test-results/p6-fe-01-list-stats.png', fullPage: true });
});

// ---------------------------------------------------------------------------
// P6-FE-02 详情 8 Tab 懒加载 + 画像/偏好/事件编辑删除恢复回归 (AC-3)
// ---------------------------------------------------------------------------
test('P6-FE-02 detail eight tabs lazy-load; item edit/delete/restore regression', async ({ page }) => {
  const requested: string[] = [];
  await page.route('**/api/v1/**', visualizationRoutes({ requested }));
  await page.goto('/#/memory/customers/cust-1');
  await expect(page.getByRole('heading', { name: '客户详情' })).toBeVisible();

  // FR-3: all eight tabs are present.
  for (const name of ['概览', '客户画像', '客户偏好', '事件', '服务标记', '注入记录', '审计记录'])
    await expect(page.getByRole('tab', { name, exact: true })).toBeVisible();
  await expect(page.getByRole('tab', { name: /待确认/ })).toBeVisible();

  // Lazy loading: the audit endpoint and the injections-panel endpoint (no ?limit)
  // stay untouched until their tabs are opened. The overview tab's own preview
  // call uses ?limit=1 and must not count as the panel load.
  const auditCalls = () => requested.filter(p => p.includes('/memory/customers/cust-1/audit'));
  const panelCalls = () => requested.filter(p => p.includes('/memory/customers/cust-1/injections') && !p.includes('limit='));
  expect(auditCalls()).toHaveLength(0);
  expect(panelCalls()).toHaveLength(0);
  await page.getByRole('tab', { name: '注入记录' }).click();
  await expect.poll(() => panelCalls().length).toBeGreaterThan(0);
  expect(auditCalls()).toHaveLength(0);
  await page.getByRole('tab', { name: '审计记录' }).click();
  await expect.poll(() => auditCalls().length).toBeGreaterThan(0);

  // Regression: edit a profile item (画像 tab).
  await page.getByRole('tab', { name: '客户画像' }).click();
  const profileRow = page.locator('tr').filter({ hasText: '张三丰' });
  await expect(profileRow.getByText('人工保护')).toBeVisible();
  await profileRow.getByRole('button', { name: /编\s*辑/ }).click();
  await page.getByLabel('记忆内容').fill('张三丰（已核实）');
  await page.getByRole('dialog').getByRole('button', { name: /保\s*存/ }).click();
  await expect(page.getByText('记忆已更新')).toBeVisible();
  await expect(page.locator('td').filter({ hasText: '张三丰（已核实）' })).toBeVisible();

  // Regression: soft delete then restore a preference item (偏好 tab).
  await page.getByRole('tab', { name: '客户偏好' }).click();
  const prefRow = page.locator('tr').filter({ hasText: '用英文回复' });
  await prefRow.getByRole('button', { name: /删\s*除/ }).click();
  await page.locator('.ant-popover').getByRole('button', { name: /确\s*认/ }).click();
  await expect(page.getByText('记忆已删除')).toBeVisible();
  await prefRow.getByRole('button', { name: /恢\s*复/ }).click();
  await expect(page.getByText('记忆已恢复')).toBeVisible();
  await expect(prefRow.getByText('生效中')).toBeVisible();

  // 事件 tab renders event-type items.
  await page.getByRole('tab', { name: '事件' }).click();
  await expect(page.getByText('2026-10 设备报修')).toBeVisible();
  await page.screenshot({ path: 'test-results/p6-fe-02-eight-tabs.png', fullPage: true });
});

// ---------------------------------------------------------------------------
// P6-FE-03 注入记录下钻：倒序、展开明细、跳会话链接 (AC-4)
// ---------------------------------------------------------------------------
test('P6-FE-03 injections tab lists runs newest-first with expandable item detail', async ({ page }) => {
  await page.route('**/api/v1/**', visualizationRoutes());
  await page.goto('/#/memory/customers/cust-1');
  await page.getByRole('tab', { name: '注入记录' }).click();

  // FR-4: newest run first; row shows injected count and a conversation deep link.
  // Scope to the injections tabpanel: visited tabs stay mounted in the DOM.
  const panel = page.getByRole('tabpanel', { name: '注入记录' });
  const newest = panel.locator('tr[data-row-key="run-2-newest"]');
  const older = panel.locator('tr[data-row-key="run-1"]');
  await expect(panel.locator('.ant-table-tbody > tr.ant-table-row').nth(0)).toContainText('run-2-ne');
  await expect(panel.locator('.ant-table-tbody > tr.ant-table-row').nth(1)).toContainText('run-1');
  await expect(newest.locator('td').nth(3)).toHaveText('3');
  await expect(newest.getByRole('link', { name: '查看会话' })).toHaveAttribute('href', '#/conversations/_all?focus=conv-2');
  await expect(older.getByRole('link', { name: '查看会话' })).toHaveAttribute('href', '#/conversations/_all?focus=conv-1');

  // Expanding the newest run reveals item-level detail: key / type / section / score.
  await newest.locator('.ant-table-row-expand-icon').click();
  const expanded = panel.locator('tr.ant-table-expanded-row');
  await expect(expanded.getByRole('button', { name: 'name' })).toBeVisible();
  await expect(expanded.getByText('language')).toBeVisible();
  await expect(expanded.getByText('vip')).toBeVisible();
  await expect(expanded.getByRole('cell', { name: '画像' }).first()).toBeVisible();
  await expect(expanded.getByRole('cell', { name: '偏好' }).first()).toBeVisible();
  await expect(expanded.getByRole('cell', { name: '服务标记' }).first()).toBeVisible();
  await expect(expanded.getByText('0.95')).toBeVisible();
  await expect(expanded.getByText('0.87')).toBeVisible();
  // Flag rows carry no score (D1/D2: score is NULL for flags).
  await expect(expanded.locator('tr', { hasText: 'vip' }).locator('td').nth(3)).toHaveText('—');

  // The older run contains a physically deleted item: 已删除, no content restored (C3).
  await older.locator('.ant-table-row-expand-icon').click();
  const expandedOld = panel.locator('tr.ant-table-expanded-row').nth(1);
  await expect(expandedOld.getByText('已删除')).toBeVisible();
  await expect(expandedOld.getByRole('cell', { name: 'item-gon', exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/p6-fe-03-injection-drilldown.png', fullPage: true });
});

// ---------------------------------------------------------------------------
// P6-FE-04 条目详情抽屉：完整字段 / diff / 时间线 / 来源跳转 / 最近注入 (AC-6)
// ---------------------------------------------------------------------------
test('P6-FE-04 item detail drawer shows fields, diff, lifecycle timeline and injections', async ({ page }) => {
  await page.route('**/api/v1/**', visualizationRoutes());
  await page.goto('/#/memory/customers/cust-1');
  await page.getByRole('tab', { name: '客户画像' }).click();

  // Row click opens the single-item drawer (FR-5).
  await page.locator('tr').filter({ hasText: '张三丰' }).locator('td').first().click();
  const drawer = page.getByRole('dialog', { name: '记忆详情' });
  await expect(drawer).toBeVisible();

  // Full field set.
  const fields = drawer.locator('.memory-item-fields');
  await expect(fields.getByText('name')).toBeVisible();
  await expect(fields.getByText('画像')).toBeVisible();
  await expect(fields.getByText('生效中')).toBeVisible();
  await expect(fields.getByText('张三丰', { exact: true }).first()).toBeVisible();
  await expect(fields.getByText('1.00')).toBeVisible();
  await expect(fields.getByText('0.95')).toBeVisible();
  await expect(fields.getByText('人工维护')).toBeVisible();
  await expect(fields.getByText('是', { exact: true })).toBeVisible();
  await expect(fields.getByText('4', { exact: true })).toBeVisible();
  await expect(fields.getByText('3', { exact: true })).toBeVisible();
  // 来源跳转：source_ref links to the source conversation.
  await expect(fields.getByRole('link', { name: '来源会话' })).toHaveAttribute('href', '#/conversations/_all?focus=conv-1');

  // prev_content diff: removed line in the old column, added line in the current one.
  await expect(drawer.getByText('内容变更（上一版 → 当前）')).toBeVisible();
  await expect(drawer.getByLabel('上一版内容').locator('.memory-diff-line.is-del')).toHaveText('张三');
  await expect(drawer.getByLabel('当前内容').locator('.memory-diff-line.is-add')).toHaveText('张三丰');

  // Lifecycle timeline covers 抽取/确认/编辑/衰退迁移/恢复.
  await expect(drawer.getByText('生命周期时间线')).toBeVisible();
  for (const label of ['自动抽取', '确认', '人工编辑', '衰退迁移', '恢复'])
    await expect(drawer.locator('.memory-timeline').getByText(label, { exact: true })).toBeVisible();
  // Timeline is newest-first: 恢复 precedes 自动抽取.
  const timelineText = await drawer.locator('.memory-timeline').innerText();
  expect(timelineText.indexOf('恢复')).toBeLessThan(timelineText.indexOf('自动抽取'));

  // 最近注入：reverse lookup rows with run links (AC-7 surface).
  await expect(drawer.getByText('最近注入')).toBeVisible();
  const injections = drawer.locator('.memory-drawer-section').last();
  await expect(injections.getByText('run-2-ne')).toBeVisible();
  await expect(injections.getByText('run-1')).toBeVisible();
  await expect(injections.getByText('0.95')).toBeVisible();
  await expect(injections.getByRole('link', { name: '查看会话' }).first()).toHaveAttribute('href', '#/conversations/_all?focus=conv-2');
  await page.screenshot({ path: 'test-results/p6-fe-04-item-drawer.png', fullPage: true });
  await page.keyboard.press('Escape');
});

// ---------------------------------------------------------------------------
// P6-FE-05 会话工作台：本次对话注入视图 + 点击条目开详情 (AC-5)
// ---------------------------------------------------------------------------

/** Conversation workbench routes: list + detail with the given runs. */
function workbenchRoutes(runs: any[], options: RouteOptions = {}) {
  const base = visualizationRoutes(options);
  const conversation = { id: 'conv-1', external_id: 'sess-abc', mode: 'bot', agent_id: null, assigned_to: null, customer_id: 'cust-1', revision: 1, source: 'business', updated_at: '2026-10-14T10:00:00Z' };
  return async (route: any) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith('/conversations') && request.method() === 'GET')
      return route.fulfill({ json: { items: [conversation], total: 1, page: 1, page_size: 10 } });
    if (path.endsWith('/conversations/conv-1'))
      return route.fulfill({ json: { ...conversation, messages: [], runs: typeof runs === 'function' ? (runs as any)() : runs, actions: [] } });
    if (path.endsWith('/agents')) return route.fulfill({ json: [] });
    return base(route);
  };
}

test('P6-FE-05 conversation workbench shows this conversation\'s run injections', async ({ page }) => {
  const runs = [
    { id: 'run-1', conversation_id: 'conv-1', status: 'completed', error_code: null, created_at: '2026-10-13T09:00:00Z' },
    { id: 'run-2-newest', conversation_id: 'conv-1', status: 'completed', error_code: null, created_at: '2026-10-12T09:00:00Z' },
  ];
  await page.route('**/api/v1/**', workbenchRoutes(runs));
  await page.goto('/#/conversations/_all?focus=conv-1');
  await expect(page.getByRole('heading', { name: 'sess-abc' })).toBeVisible();

  // FR-6: 本次对话注入 section; the newest run expands by default with its items.
  await page.getByRole('button', { name: '客户画像' }).click();
  const drawer = page.getByRole('dialog', { name: '客户画像与偏好' });
  await expect(drawer.getByText('本次对话注入')).toBeVisible();
  const runItems = drawer.locator('.memory-run-injections');
  await expect(runItems.getByRole('button', { name: 'name' })).toBeVisible();
  await expect(runItems.getByText('已删除')).toBeVisible();

  // AC-5: clicking an injected item opens the single-item detail drawer.
  await runItems.getByRole('button', { name: 'name' }).click();
  const itemDrawer = page.getByRole('dialog', { name: '记忆详情' });
  await expect(itemDrawer).toBeVisible();
  await expect(itemDrawer.locator('.memory-item-fields').getByText('张三丰', { exact: true }).first()).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(itemDrawer).toHaveCount(0);

  // The second run expands on demand and lists its own injected entries.
  await runItems.getByText('run-2-ne').click();
  await expect(runItems.getByText('language')).toBeVisible();
  await expect(runItems.getByText('vip')).toBeVisible();
  await expect(runItems.getByText('服务标记').first()).toBeVisible();
  await page.screenshot({ path: 'test-results/p6-fe-05-workbench-injections.png', fullPage: true });
});

// ---------------------------------------------------------------------------
// P6-FE-06 空态：历史会话 / 无运行 / 未绑定客户 / 无注入记录 (AC-8)
// ---------------------------------------------------------------------------
test('P6-FE-06 empty states: pre-launch run, no runs, unbound customer, no injections', async ({ page }) => {
  let runs: any[] = [
    { id: 'run-old', conversation_id: 'conv-1', status: 'completed', error_code: null, created_at: '2026-09-01T09:00:00Z' },
  ];
  const base = visualizationRoutes({ injections: [] });
  const bound = { id: 'conv-1', external_id: 'sess-abc', mode: 'bot', agent_id: null, assigned_to: null, customer_id: 'cust-1', revision: 1, source: 'business', updated_at: '2026-10-14T10:00:00Z' };
  const unbound = { id: 'conv-2', external_id: 'sess-guest', mode: 'bot', agent_id: null, assigned_to: null, customer_id: null, revision: 1, source: 'business', updated_at: '2026-10-14T10:00:00Z' };
  await page.route('**/api/v1/**', async (route: any) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith('/conversations') && request.method() === 'GET')
      return route.fulfill({ json: { items: [bound, unbound], total: 2, page: 1, page_size: 10 } });
    if (path.endsWith('/conversations/conv-1'))
      return route.fulfill({ json: { ...bound, messages: [], runs, actions: [] } });
    if (path.endsWith('/conversations/conv-2'))
      return route.fulfill({ json: { ...unbound, messages: [], runs: [], actions: [] } });
    if (path.endsWith('/agents')) return route.fulfill({ json: [] });
    return base(route);
  });

  // AC-8: a run from before the feature launch shows the explicit empty state.
  await page.goto('/#/conversations/_all?focus=conv-1');
  await expect(page.getByRole('heading', { name: 'sess-abc' })).toBeVisible();
  await page.getByRole('button', { name: '客户画像' }).click();
  const drawer = page.getByRole('dialog', { name: '客户画像与偏好' });
  await expect(drawer.getByText('该会话无注入明细记录（功能上线前数据）')).toBeVisible();
  await expect(page.locator('.ant-alert-error')).toHaveCount(0);
  await page.keyboard.press('Escape');

  // No runs at all: dedicated empty copy, still no error.
  runs = [];
  await page.reload();
  await expect(page.getByRole('heading', { name: 'sess-abc' })).toBeVisible();
  await page.getByRole('button', { name: '客户画像' }).click();
  await expect(drawer.getByText('本次对话暂无运行记录，无注入明细')).toBeVisible();
  await page.keyboard.press('Escape');

  // Unbound customer: no memory entry point, page renders cleanly.
  await page.goto('/#/conversations/_all?focus=conv-2');
  await expect(page.getByRole('heading', { name: 'sess-guest' })).toBeVisible();
  await expect(page.getByRole('button', { name: '客户画像' })).toHaveCount(0);
  await expect(page.locator('.ant-alert-error')).toHaveCount(0);

  // Customer with no injection history: the injections tab shows 暂无注入记录.
  await page.goto('/#/memory/customers/cust-1');
  await page.getByRole('tab', { name: '注入记录' }).click();
  await expect(page.getByRole('tabpanel', { name: '注入记录' }).getByText('暂无注入记录')).toBeVisible();
  await expect(page.locator('.ant-alert-error')).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// P6-FE-07 viewer 全只读 (AC-11)
// ---------------------------------------------------------------------------
test('P6-FE-07 viewer role sees every visualization read-only', async ({ page }) => {
  await page.route('**/api/v1/**', visualizationRoutes({ role: 'viewer' }));
  await page.goto('/#/memory');
  await expect(page.locator('tr', { hasText: 'sha256:abc' })).toBeVisible();

  // Open the customer detail: no destructive/governance header actions.
  await page.getByRole('button', { name: 'sha256:abc' }).click();
  await expect(page.getByRole('heading', { name: '客户详情' })).toBeVisible();
  await expect(page.getByRole('button', { name: '删除客户' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '合并到…' })).toHaveCount(0);

  // Walk every tab so all panels mount, then assert zero mutation controls.
  for (const name of ['客户画像', '客户偏好', '事件', '服务标记', '注入记录', '审计记录'])
    await page.getByRole('tab', { name, exact: true }).click();
  await page.getByRole('tab', { name: /待确认/ }).click();
  await expect(page.getByRole('tabpanel', { name: /待确认/ }).getByText('偏好邮件联系')).toBeVisible();
  // Read views still render for the viewer.
  await page.getByRole('tab', { name: '注入记录' }).click();
  await expect(page.getByRole('tabpanel', { name: '注入记录' }).locator('.ant-table-tbody > tr.ant-table-row').first()).toContainText('run-2-ne');
  await page.getByRole('tab', { name: '审计记录' }).click();
  await expect(page.locator('.memory-timeline').getByText('自动抽取')).toBeVisible();

  await expect(page.getByRole('button', { name: /编\s*辑/ })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /删\s*除/ })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /恢\s*复/ })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /打\s*标/ })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /解\s*除/ })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /确\s*认/ })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /拒\s*绝/ })).toHaveCount(0);

  // The item drawer stays available as a read-only view.
  await page.getByRole('tab', { name: '客户画像' }).click();
  await page.locator('tr').filter({ hasText: '张三丰' }).locator('td').first().click();
  const drawer = page.getByRole('dialog', { name: '记忆详情' });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByRole('button', { name: /编\s*辑/ })).toHaveCount(0);
  await expect(drawer.getByRole('button', { name: /删\s*除/ })).toHaveCount(0);
  await page.keyboard.press('Escape');
});
