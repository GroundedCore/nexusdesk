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

    // Customer detail: identities + items grouped by type.
    await page.getByRole('button', { name: 'sha256:abc' }).click();
    await expect(page).toHaveURL(/#\/memory\/customers\/cust-1$/);
    await expect(page.getByRole('heading', { name: '客户详情' })).toBeVisible();
    await expect(page.getByText('身份绑定')).toBeVisible();
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

    // Soft delete then restore.
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
