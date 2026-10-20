import { test, expect } from '@playwright/test';

type MemoryItem = { id: string; type: string; key: string; content: string; confidence: number; status: string; source_type: string; source_ref: string | null; revision: number; updated_at: string };

function seedItems(): MemoryItem[] {
  return [
    { id: 'item-1', type: 'profile', key: 'name', content: '张三', confidence: 1.0, status: 'active', source_type: 'extracted', source_ref: 'conv-1', revision: 2, updated_at: '2026-10-01T10:00:00Z' },
    { id: 'item-2', type: 'preference', key: 'language', content: '用英文回复', confidence: 0.92, status: 'active', source_type: 'human', source_ref: null, revision: 1, updated_at: '2026-10-02T10:00:00Z' },
    { id: 'item-3', type: 'profile', key: 'phone', content: '13800001111', confidence: 0.9, status: 'deleted', source_type: 'extracted', source_ref: 'conv-1', revision: 1, updated_at: '2026-10-03T10:00:00Z' },
  ];
}

const customer = { id: 'cust-1', external_keys: ['sha256:abc'], item_count: 3, last_active_at: '2026-10-03T10:00:00Z', created_at: '2026-09-30T10:00:00Z' };
const identity = { id: 'ident-1', source: 'channel', external_key: 'sha256:abc', created_at: '2026-09-30T10:00:00Z' };

function seedPending() {
  return [{ id: 'pend-1', customer_id: 'cust-1', customer_external_keys: ['sha256:abc'], type: 'preference', key: 'contact_channel', content: '偏好邮件联系', confidence: 0.6, status: 'pending', source_type: 'extracted', source_ref: 'conv-1', revision: 1, updated_at: '2026-10-04T10:00:00Z' }];
}

for (const role of ['admin', 'viewer']) {
  test(`memory console: customers, detail editing, pending queue (${role})`, async ({ page }) => {
    const items = seedItems();
    let pending = seedPending();
    let lastListQuery = '';
    await page.route('**/api/v1/**', async route => {
      const request = route.request();
      const url = new URL(request.url());
      const path = url.pathname;
      if (path.endsWith('/me')) return route.fulfill({ json: { role, tenant: '测试企业' } });
      if (path.endsWith('/memory/customers')) {
        const q = url.searchParams.get('q') || '';
        lastListQuery = q;
        const matched = q && !customer.external_keys[0].includes(q);
        return route.fulfill({ json: { items: matched ? [] : [customer], total: matched ? 0 : 1, page: 1, page_size: 20 } });
      }
      if (path.endsWith('/memory/customers/cust-1')) return route.fulfill({ json: { id: 'cust-1', created_at: customer.created_at, identities: [identity], items } });
      if (path.endsWith('/memory/pending')) return route.fulfill({ json: { items: pending, total: pending.length, page: 1, page_size: 20 } });
      const itemAction = path.match(/\/memory\/items\/([^/]+)(?:\/(restore|confirm|reject))?$/);
      if (itemAction) {
        const [, id, action] = itemAction;
        if (action === 'confirm' || action === 'reject') {
          pending = pending.filter(row => row.id !== id);
          return route.fulfill({ json: { id, status: action === 'confirm' ? 'active' : 'deleted' } });
        }
        const item = items.find(row => row.id === id);
        if (!item) return route.fulfill({ status: 404, json: { detail: 'memory_item_not_found' } });
        if (action === 'restore') item.status = 'active';
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
    });

    await page.goto('/#/memory');
    await expect(page.getByRole('button', { name: 'sha256:abc' })).toBeVisible();

    // Search by external_key (server-side) and empty state.
    await page.getByLabel('搜索客户标识').fill('nobody');
    await page.getByLabel('搜索客户标识').press('Enter');
    await expect(page.getByText('没有符合条件的客户')).toBeVisible();
    expect(lastListQuery).toBe('nobody');
    await page.getByLabel('搜索客户标识').fill('');
    await page.getByLabel('搜索客户标识').press('Enter');
    await expect(page.getByRole('button', { name: 'sha256:abc' })).toBeVisible();

    // Customer detail: identities on the overview tab + items grouped by type tabs.
    await page.getByRole('button', { name: 'sha256:abc' }).click();
    await expect(page).toHaveURL(/#\/memory\/customers\/cust-1$/);
    await expect(page.getByRole('heading', { name: '客户详情' })).toBeVisible();
    await expect(page.getByText('身份绑定')).toBeVisible();
    await page.getByRole('tab', { name: '客户画像' }).click();
    await expect(page.getByText('13800001111')).toBeVisible();
    await expect(page.getByRole('link', { name: '来源会话' }).first()).toHaveAttribute('href', '#/conversations/_all?focus=conv-1');

    if (role === 'viewer') {
      await expect(page.getByRole('button', { name: /编\s*辑/ })).toHaveCount(0);
      await expect(page.getByRole('button', { name: /删\s*除/ })).toHaveCount(0);
      await expect(page.getByRole('button', { name: /恢\s*复/ })).toHaveCount(0);
      await page.goto('/#/memory/pending');
      await expect(page.getByText('偏好邮件联系')).toBeVisible();
      await expect(page.getByRole('button', { name: /确\s*认/ })).toHaveCount(0);
      await expect(page.getByRole('button', { name: /拒\s*绝/ })).toHaveCount(0);
      return;
    }

    // Edit and save.
    await page.locator('tr').filter({ hasText: '张三' }).getByRole('button', { name: /编\s*辑/ }).click();
    await page.getByLabel('记忆内容').fill('张三丰');
    await page.getByRole('dialog').getByRole('button', { name: /保\s*存/ }).click();
    await expect(page.getByText('记忆已更新')).toBeVisible();
    await expect(page.locator('td').filter({ hasText: '张三丰' })).toBeVisible();

    // Soft delete then restore (preference tab).
    await page.getByRole('tab', { name: '客户偏好' }).click();
    await page.locator('tr').filter({ hasText: '用英文回复' }).getByRole('button', { name: /删\s*除/ }).click();
    await page.locator('.ant-popover').getByRole('button', { name: /确\s*认/ }).click();
    await expect(page.getByText('记忆已删除')).toBeVisible();
    await page.locator('tr').filter({ hasText: '用英文回复' }).getByRole('button', { name: /恢\s*复/ }).click();
    await expect(page.getByText('记忆已恢复')).toBeVisible();
    await expect(page.locator('tr').filter({ hasText: '用英文回复' }).getByText('生效中')).toBeVisible();

    // Pending queue: confirm removes the entry.
    await page.getByRole('button', { name: '返回客户列表' }).click();
    await page.getByRole('tab', { name: '待确认队列' }).click();
    await expect(page.getByText('偏好邮件联系')).toBeVisible();
    await page.locator('tr').filter({ hasText: '偏好邮件联系' }).getByRole('button', { name: /确\s*认/ }).click();
    await page.locator('.ant-popover').getByRole('button', { name: /确\s*认/ }).click();
    await expect(page.getByText('暂无待确认的记忆')).toBeVisible();
    await page.screenshot({ path: `test-results/memory-${role}.png`, fullPage: true });
  });
}

// Phase 6 · memory visualization: stats columns, 8-tab detail, injection explainability.
function phase6Items() {
  return [
    { id: 'item-1', type: 'profile', key: 'name', content: '张三丰', prev_content: '张三', confidence: 1.0, status: 'active', source_type: 'human', source_ref: 'conv-1', revision: 3, protected: true, last_referenced_at: '2026-10-12T10:00:00Z', reference_count: 4, effective_confidence: 1.0, created_at: '2026-09-30T10:00:00Z', updated_at: '2026-10-12T10:00:00Z' },
    { id: 'pend-1', type: 'preference', key: 'contact_channel', content: '偏好邮件联系', confidence: 0.6, status: 'pending', source_type: 'extracted', source_ref: 'conv-1', revision: 1, updated_at: '2026-10-11T10:00:00Z' },
  ];
}

const phase6Customer = { id: 'cust-1', external_keys: ['sha256:abc'], item_count: 2, last_active_at: '2026-10-12T10:00:00Z', created_at: '2026-09-30T10:00:00Z', memory_stats: { total: 2, profile: 1, preference: 1, event: 0, pending: 1, flags: ['vip'], last_updated_at: '2026-10-12T10:00:00Z' } };
const phase6Injections = [{ run_id: 'run-1', injected_at: '2026-10-13T09:00:00Z', item_count: 2, conversation_id: 'conv-1' }];
const phase6RunDetail = [
  { item_id: 'item-1', key: 'name', type: 'profile', status: 'active', section: 'profile', score: 0.87, item_deleted: false },
  { item_id: 'item-gone', key: null, type: null, status: null, section: 'preference', score: 0.5, item_deleted: true },
];
const phase6Timeline = [
  { id: 2, actor: 'operator@corp', action: 'memory.item.edited', resource: 'memory_item:item-1', details: { revision: 3 }, created_at: '2026-10-12T10:00:00Z' },
  { id: 1, actor: 'agent:extractor', action: 'memory.extract.applied', resource: 'memory_item:item-1', details: { added: 1 }, created_at: '2026-10-01T08:00:00Z' },
];
const phase6ItemInjections = [{ run_id: 'run-1', injected_at: '2026-10-13T09:00:00Z', section: 'profile', score: 0.87, conversation_id: 'conv-1' }];

function phase6Routes(items: any[]) {
  return async (route: any) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path.endsWith('/me')) return route.fulfill({ json: { role: 'operator', tenant: '测试企业' } });
    if (path.endsWith('/memory/customers') && request.method() === 'GET') return route.fulfill({ json: { items: [phase6Customer], total: 1, page: 1, page_size: 20 } });
    if (path.endsWith('/memory/customers/cust-1/injections')) return route.fulfill({ json: { items: phase6Injections } });
    if (path.endsWith('/memory/customers/cust-1/audit')) return route.fulfill({ json: { items: phase6Timeline } });
    if (path.endsWith('/memory/customers/cust-1')) return route.fulfill({ json: { id: 'cust-1', created_at: phase6Customer.created_at, identities: [identity], items } });
    if (path.endsWith('/memory/runs/run-1/injections')) return route.fulfill({ json: { run_id: 'run-1', items: phase6RunDetail } });
    if (path.endsWith('/memory/runs/run-old/injections')) return route.fulfill({ json: { run_id: 'run-old', items: [] } });
    if (path.endsWith('/memory/items/item-1/timeline')) return route.fulfill({ json: { item: phase6Items()[0], timeline: phase6Timeline, injection_count: 1, last_injected_at: '2026-10-13T09:00:00Z' } });
    if (path.endsWith('/memory/items/item-1/injections')) return route.fulfill({ json: { items: phase6ItemInjections } });
    if (path.endsWith('/modules')) return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  };
}

test('memory visualization: stats columns, pending deep link, injections drill-down, item drawer', async ({ page }) => {
  await page.route('**/api/v1/**', phase6Routes(phase6Items()));
  await page.goto('/#/memory');

  // FR-2: stats columns, VIP badge, highlighted pending count deep-links to the pending tab.
  await expect(page.getByText('1 条待确认')).toBeVisible();
  await expect(page.getByText('VIP 客户')).toBeVisible();
  await page.getByRole('button', { name: '1 条待确认' }).click();
  await expect(page).toHaveURL(/#\/memory\/customers\/cust-1\?tab=pending$/);
  await expect(page.getByText('偏好邮件联系')).toBeVisible();

  // FR-3: overview tab shows stats and identity; audit tab renders the memory.* timeline.
  await page.getByRole('tab', { name: '概览' }).click();
  await expect(page.getByText('身份绑定')).toBeVisible();
  await page.getByRole('tab', { name: '审计记录' }).click();
  const auditPanel = page.getByRole('tabpanel', { name: '审计记录' });
  await expect(auditPanel.getByText('自动抽取')).toBeVisible();
  await expect(auditPanel.getByText('人工编辑')).toBeVisible();

  // FR-4: injections tab lists runs; expanding a run shows item details incl. deleted items.
  await page.getByRole('tab', { name: '注入记录' }).click();
  const runRow = page.locator('tr').filter({ hasText: 'run-1'.slice(0, 8) }).first();
  await expect(runRow).toBeVisible();
  await runRow.locator('.ant-table-row-expand-icon').click();
  await expect(page.getByRole('button', { name: 'name' })).toBeVisible();
  await expect(page.getByText('已删除').first()).toBeVisible();

  // FR-5: clicking an injected item opens the detail drawer with fields, diff, timeline, injections.
  await page.getByRole('button', { name: 'name' }).click();
  const drawer = page.getByRole('dialog', { name: '记忆详情' });
  await expect(drawer.locator('.memory-item-fields').getByText('张三丰')).toBeVisible();
  await expect(drawer.getByText('内容变更（上一版 → 当前）')).toBeVisible();
  await expect(drawer.getByLabel('上一版内容').getByText('张三', { exact: true })).toBeVisible();
  await expect(drawer.getByText('生命周期时间线')).toBeVisible();
  await expect(drawer.getByText('自动抽取')).toBeVisible();
  await expect(drawer.getByText('最近注入')).toBeVisible();
  await expect(drawer.getByText('0.87').first()).toBeVisible();
  await page.keyboard.press('Escape');
});

test('conversation workbench drawer shows per-run injections with pre-launch empty state', async ({ page }) => {
  const conversation = { id: 'conv-1', external_id: 'sess-abc', mode: 'bot', agent_id: null, assigned_to: null, customer_id: 'cust-1', revision: 1, source: 'business', updated_at: '2026-10-13T10:00:00Z' };
  const runs = [
    { id: 'run-1', conversation_id: 'conv-1', status: 'completed', error_code: null, created_at: '2026-10-13T09:00:00Z' },
    { id: 'run-old', conversation_id: 'conv-1', status: 'completed', error_code: null, created_at: '2026-09-01T09:00:00Z' },
  ];
  await page.route('**/api/v1/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith('/me')) return route.fulfill({ json: { role: 'viewer', tenant: '测试企业' } });
    if (path.endsWith('/conversations') && request.method() === 'GET') return route.fulfill({ json: { items: [conversation], total: 1, page: 1, page_size: 10 } });
    if (path.endsWith('/conversations/conv-1')) return route.fulfill({ json: { ...conversation, messages: [], runs, actions: [] } });
    if (path.endsWith('/agents')) return route.fulfill({ json: [] });
    if (path.endsWith('/memory/customers/cust-1')) return route.fulfill({ json: { id: 'cust-1', created_at: phase6Customer.created_at, identities: [identity], items: phase6Items() } });
    if (path.endsWith('/memory/runs/run-1/injections')) return route.fulfill({ json: { run_id: 'run-1', items: phase6RunDetail } });
    if (path.endsWith('/memory/runs/run-old/injections')) return route.fulfill({ json: { run_id: 'run-old', items: [] } });
    if (path.endsWith('/memory/items/item-1/timeline')) return route.fulfill({ json: { item: phase6Items()[0], timeline: phase6Timeline, injection_count: 1, last_injected_at: '2026-10-13T09:00:00Z' } });
    if (path.endsWith('/memory/items/item-1/injections')) return route.fulfill({ json: { items: phase6ItemInjections } });
    if (path.endsWith('/modules')) return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });
  await page.goto('/#/conversations/_all?focus=conv-1');
  await expect(page.getByRole('heading', { name: 'sess-abc' })).toBeVisible();

  // FR-6: the newest run expands by default with its injected items (viewer read-only).
  await page.getByRole('button', { name: '客户画像' }).click();
  const drawer = page.getByRole('dialog', { name: '客户画像与偏好' });
  await expect(drawer.getByText('本次对话注入')).toBeVisible();
  await expect(drawer.locator('.memory-run-injections').getByText('name', { exact: true })).toBeVisible();

  // AC-8: a pre-launch run shows the explicit empty state instead of an error.
  await drawer.getByText('run-old').click();
  await expect(drawer.getByText('该会话无注入明细记录（功能上线前数据）')).toBeVisible();
});

test('ticket detail links to customer memory', async ({ page }) => {
  const ticket = { id: 'ticket-1', ticket_no: 'TK-100', title: '设备无法启动', description: '客户反馈开机无响应', status: 'open', note: '', revision: 1, priority: 2, customer_id: 'cust-1', updated_at: '2026-10-05T10:00:00Z' };
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/me')) return route.fulfill({ json: { role: 'admin', tenant: '测试企业' } });
    if (path.endsWith('/tickets')) return route.fulfill({ json: [ticket] });
    if (path.endsWith('/memory/customers/cust-1')) return route.fulfill({ json: { id: 'cust-1', created_at: customer.created_at, identities: [identity], items: seedItems() } });
    if (path.endsWith('/modules')) return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });
  await page.goto('/#/tickets');
  await page.getByRole('button', { name: '设备无法启动' }).click();
  const link = page.getByRole('link', { name: '查看客户记忆' });
  await expect(link).toBeVisible();
  await expect(link).toHaveAttribute('href', '#/memory/customers/cust-1');
  await link.click();
  await expect(page).toHaveURL(/#\/memory\/customers\/cust-1$/);
  await expect(page.getByRole('heading', { name: '客户详情' })).toBeVisible();
});

// Phase 4 governance console: flag card group, protected/expired item actions,
// delete and merge customer (both with a second confirmation).
function governanceItems() {
  return [
    { id: 'item-p', type: 'profile', key: 'name', content: '张三', confidence: 1.0, status: 'active', source_type: 'human', source_ref: null, revision: 3, protected: true, last_referenced_at: '2026-10-10T10:00:00Z', reference_count: 4, effective_confidence: 1.0, updated_at: '2026-10-01T10:00:00Z' },
    { id: 'item-x', type: 'preference', key: 'language', content: '用英文回复', confidence: 0.92, status: 'expired', source_type: 'extracted', source_ref: 'conv-1', revision: 1, protected: false, last_referenced_at: null, reference_count: 0, effective_confidence: 0.21, updated_at: '2026-10-02T10:00:00Z' },
    { id: 'flag-vip', type: 'flag', key: 'vip', content: '年框客户', confidence: 1.0, status: 'active', source_type: 'human', source_ref: null, revision: 1, protected: true, last_referenced_at: null, reference_count: 0, effective_confidence: 1.0, updated_at: '2026-10-03T10:00:00Z' },
  ];
}

function governanceRoutes(items: any[], hooks: { onDeleted?: () => void; onMerged?: (target: string) => void } = {}) {
  return async (route: any) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path.endsWith('/me')) return route.fulfill({ json: { role: 'operator', tenant: '测试企业' } });
    if (path.endsWith('/memory/customers') && request.method() === 'GET') return route.fulfill({ json: { items: [customer], total: 1, page: 1, page_size: 20 } });
    const flagAction = path.match(/\/memory\/customers\/cust-1\/flags\/(\w+)$/);
    if (flagAction) {
      const key = flagAction[1];
      const existing = items.find(row => row.type === 'flag' && row.key === key);
      if (request.method() === 'PUT') {
        const content = request.postDataJSON().content;
        if (existing) Object.assign(existing, { content, status: 'active', source_type: 'human' });
        else items.push({ id: `flag-${key}`, type: 'flag', key, content, confidence: 1.0, status: 'active', source_type: 'human', source_ref: null, revision: 1, protected: true, updated_at: '2026-10-11T10:00:00Z' });
        return route.fulfill({ json: { id: existing?.id || `flag-${key}` } });
      }
      if (request.method() === 'DELETE') {
        if (existing) existing.status = 'deleted';
        return route.fulfill({ json: { id: existing?.id } });
      }
    }
    if (path.endsWith('/memory/customers/cust-1/merge')) {
      hooks.onMerged?.(request.postDataJSON().target_customer_id);
      return route.fulfill({ json: { merged: true } });
    }
    if (path.endsWith('/memory/customers/cust-1')) {
      if (request.method() === 'DELETE') {
        hooks.onDeleted?.();
        return route.fulfill({ json: { items: items.length } });
      }
      return route.fulfill({ json: { id: 'cust-1', created_at: customer.created_at, identities: [identity], items } });
    }
    const itemAction = path.match(/\/memory\/items\/([^/]+)\/(release|restore)$/);
    if (itemAction) {
      const item = items.find(row => row.id === itemAction[1]);
      if (!item) return route.fulfill({ status: 404, json: { detail: 'memory_item_not_found' } });
      if (itemAction[2] === 'release') Object.assign(item, { protected: false, source_type: 'extracted' });
      if (itemAction[2] === 'restore') item.status = 'active';
      return route.fulfill({ json: item });
    }
    if (path.endsWith('/modules')) return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  };
}

test('memory governance: flags, protected release, expired restore, delete customer', async ({ page }) => {
  const items = governanceItems();
  await page.route('**/api/v1/**', governanceRoutes(items));
  await page.goto('/#/memory/customers/cust-1');
  await expect(page.getByRole('heading', { name: '客户详情' })).toBeVisible();

  // Flag tab: existing VIP flag plus the two unflagged keys.
  await page.getByRole('tab', { name: '服务标记' }).click();
  await expect(page.getByText('年框客户')).toBeVisible();
  await expect(page.getByText('未标记').nth(1)).toBeVisible();

  // Upsert a complaint_risk flag with a note.
  await page.locator('tr').filter({ hasText: '投诉风险' }).getByRole('button', { name: /打\s*标/ }).click();
  await page.getByLabel('标记说明').fill('上月两次超时未响应');
  await page.getByRole('dialog').getByRole('button', { name: /保\s*存/ }).click();
  await expect(page.getByText('标记已保存')).toBeVisible();
  await expect(page.getByRole('cell', { name: '上月两次超时未响应' })).toBeVisible();

  // Edit then remove the VIP flag (Popconfirm is the second confirmation).
  await page.locator('tr').filter({ hasText: '年框客户' }).getByRole('button', { name: '编辑说明' }).click();
  await page.getByLabel('标记说明').fill('年框客户（2026 续约）');
  await page.getByRole('dialog').getByRole('button', { name: /保\s*存/ }).click();
  await expect(page.getByRole('cell', { name: '年框客户（2026 续约）' })).toBeVisible();
  await page.locator('tr').filter({ hasText: '年框客户（2026 续约）' }).getByRole('button', { name: /解\s*除/ }).click();
  await expect(page.getByText('确认解除「VIP 客户」标记？')).toBeVisible();
  await page.locator('.ant-popover').getByRole('button', { name: /确\s*认/ }).click();
  await expect(page.getByText('标记已解除')).toBeVisible();

  // Protected human item: badge + release back to automatic management (profile tab).
  await page.getByRole('tab', { name: '客户画像' }).click();
  const protectedRow = page.locator('tr').filter({ hasText: '张三' });
  await expect(protectedRow.getByText('人工保护')).toBeVisible();
  await protectedRow.getByRole('button', { name: '恢复自动管理' }).click();
  await page.locator('.ant-popover').getByRole('button', { name: /确\s*认/ }).click();
  await expect(page.getByText('已恢复自动管理')).toBeVisible();
  await expect(protectedRow.getByText('人工保护')).toHaveCount(0);

  // Expired item can be restored (refreshing its decay clock server-side; preference tab).
  await page.getByRole('tab', { name: '客户偏好' }).click();
  await page.locator('tr').filter({ hasText: '用英文回复' }).getByRole('button', { name: /恢\s*复/ }).click();
  await expect(page.getByText('记忆已恢复')).toBeVisible();

  // Delete customer: danger button then Popconfirm (second confirmation).
  await page.getByRole('button', { name: '删除客户' }).click();
  await expect(page.getByText('确认删除该客户及其全部记忆？此操作不可撤销')).toBeVisible();
  await page.locator('.ant-popover').getByRole('button', { name: '确认删除' }).click();
  await expect(page.getByText('客户已删除')).toBeVisible();
  await expect(page).toHaveURL(/#\/memory$/);
});

test('memory governance: merge customer with confirmation dialog', async ({ page }) => {
  const items = governanceItems();
  let mergedTarget = '';
  await page.route('**/api/v1/**', governanceRoutes(items, { onMerged: target => { mergedTarget = target; } }));
  await page.goto('/#/memory/customers/cust-1');
  await page.getByRole('button', { name: '合并到…' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText('合并后当前客户的身份与记忆将迁移到目标客户，当前客户将被删除。此操作不可撤销')).toBeVisible();
  await dialog.getByLabel('目标客户 ID').fill('cust-2');
  await dialog.getByRole('button', { name: '确认合并' }).click();
  await expect(page.getByText('客户已合并')).toBeVisible();
  expect(mergedTarget).toBe('cust-2');
  await expect(page).toHaveURL(/#\/memory$/);
});

test('pending queue marks :suggested rows as protected-update suggestions', async ({ page }) => {
  let pending: any[] = [
    { id: 'pend-2', customer_id: 'cust-1', customer_external_keys: ['sha256:abc'], type: 'preference', key: 'contact_channel:suggested', content: '偏好电话联系', confidence: 0.7, status: 'pending', source_type: 'extracted', source_ref: 'conv-1', revision: 1, updated_at: '2026-10-04T10:00:00Z' },
  ];
  await page.route('**/api/v1/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith('/me')) return route.fulfill({ json: { role: 'operator', tenant: '测试企业' } });
    if (path.endsWith('/memory/pending')) return route.fulfill({ json: { items: pending, total: pending.length, page: 1, page_size: 20 } });
    if (path.endsWith('/memory/items/pend-2/confirm')) {
      pending = [];
      return route.fulfill({ json: { id: 'pend-2', status: 'active' } });
    }
    if (path.endsWith('/modules')) return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });
  await page.goto('/#/memory/pending');
  const row = page.locator('tr').filter({ hasText: '偏好电话联系' });
  await expect(row.getByText('建议更新（人工保护）')).toBeVisible();
  await expect(row.getByText('contact_channel', { exact: true })).toBeVisible();
  await row.getByRole('button', { name: /确\s*认/ }).click();
  await expect(page.getByText('确认后将更新人工维护的原条目')).toBeVisible();
  await page.locator('.ant-popover').getByRole('button', { name: /确\s*认/ }).click();
  await expect(page.getByText('暂无待确认的记忆')).toBeVisible();
});

test('ticket detail degrades to 客户已删除 when the memory customer is gone', async ({ page }) => {
  const ticket = { id: 'ticket-1', ticket_no: 'TK-100', title: '设备无法启动', description: '客户反馈开机无响应', status: 'open', note: '', revision: 1, priority: 2, customer_id: 'cust-1', customer_active: false, updated_at: '2026-10-05T10:00:00Z' };
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/me')) return route.fulfill({ json: { role: 'admin', tenant: '测试企业' } });
    if (path.endsWith('/tickets')) return route.fulfill({ json: [ticket] });
    if (path.endsWith('/modules')) return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });
  await page.goto('/#/tickets');
  await page.getByRole('button', { name: '设备无法启动' }).click();
  await expect(page.getByText('客户已删除')).toBeVisible();
  await expect(page.getByRole('link', { name: '查看客户记忆' })).toHaveCount(0);
});

test('observability page renders the memory governance section', async ({ page }) => {
  const memory = {
    window_hours: 24,
    extraction_daily: [{ day: '2026-10-14 00:00:00+00:00', runs: 2, added: 5, updated: 3, pending: 1, dropped: 0, pii_masked: 2 }],
    hit_rate: { customer_runs: 2, injected_runs: 1 },
    backlog: { pending: 3, oldest_age_seconds: 7200 },
    governance: [{ action: 'memory.flag.upserted', n: 4 }],
    pii_masked: 7,
    reinforce: { tasks_done: 5, tasks_failed: 1, ids_refreshed: 42 },
    decay: { decay_pending: 6, decay_expired: 2, evicted: 9, expired_restored: 1 },
    search: { calls: 8, empty: 3 },
  };
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/me')) return route.fulfill({ json: { role: 'viewer', tenant: '测试企业' } });
    if (path.endsWith('/observability/memory')) return route.fulfill({ json: memory });
    if (path.endsWith('/observability/summary')) return route.fulfill({ json: {} });
    if (path.endsWith('/observability/runs')) return route.fulfill({ json: [] });
    if (path.endsWith('/observability/gateways')) return route.fulfill({ json: { models: [], tools: [], known_usage: [] } });
    if (path.endsWith('/modules')) return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });
  await page.goto('/#/observability');
  await expect(page.getByText('记忆观测')).toBeVisible();
  await expect(page.getByText('记忆注入命中率')).toBeVisible();
  await expect(page.getByText('50% (1/2)')).toBeVisible();
  await expect(page.getByText('待确认积压')).toBeVisible();
  await page.getByText('治理操作（近 24 小时）').click();
  await expect(page.getByText('memory.flag.upserted')).toBeVisible();
  await page.getByText('记忆抽取量（近 7 天）').click();
  await expect(page.getByRole('cell', { name: '2026-10-14' })).toBeVisible();
});

test('conversation workbench shows customer profile drawer (US-3)', async ({ page }) => {
  const conversation = { id: 'conv-1', external_id: 'sess-abc', mode: 'bot', agent_id: null, assigned_to: null, customer_id: 'cust-1', revision: 1, source: 'business', updated_at: '2026-10-05T10:00:00Z' };
  await page.route('**/api/v1/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path.endsWith('/me')) return route.fulfill({ json: { role: 'operator', tenant: '测试企业' } });
    if (path.endsWith('/conversations') && request.method() === 'GET') return route.fulfill({ json: { items: [conversation], total: 1, page: 1, page_size: 10 } });
    if (path.endsWith('/conversations/conv-1')) return route.fulfill({ json: { ...conversation, messages: [], runs: [], actions: [] } });
    if (path.endsWith('/agents')) return route.fulfill({ json: [] });
    if (path.endsWith('/memory/customers/cust-1')) return route.fulfill({ json: { id: 'cust-1', created_at: customer.created_at, identities: [identity], items: seedItems() } });
    if (path.endsWith('/modules')) return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });

  // Deep link from a memory source_ref focuses the conversation directly.
  await page.goto('/#/conversations/_all?focus=conv-1');
  await expect(page.getByRole('heading', { name: 'sess-abc' })).toBeVisible();

  await page.getByRole('button', { name: '客户画像' }).click();
  await expect(page.getByRole('dialog').getByText('张三')).toBeVisible();
  await expect(page.getByRole('dialog').getByText('用英文回复')).toBeVisible();
  // Deleted items stay out of the agent-facing snapshot.
  await expect(page.getByRole('dialog').getByText('13800001111')).toHaveCount(0);
  await page.getByRole('link', { name: '查看客户记忆' }).click();
  await expect(page).toHaveURL(/#\/memory\/customers\/cust-1$/);
  await expect(page.getByRole('heading', { name: '客户详情' })).toBeVisible();
});
