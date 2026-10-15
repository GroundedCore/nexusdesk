import { test, expect } from '@playwright/test';

const agents = [
  { id: 'agent-1', name: '客服甲', published_version: 1 },
  { id: 'agent-2', name: '客服乙', published_version: 1 },
  // Published but without conversations: not on the workbench entry page.
  { id: 'agent-3', name: '客服丙', published_version: 1 },
];

const counts = {
  items: [
    { agent_id: 'agent-1', name: '客服甲', total: 1, bot: 1, waiting: 0, human: 0, closed: 0 },
    { agent_id: 'agent-2', name: '客服乙', total: 1, bot: 1, waiting: 0, human: 0, closed: 0 },
  ],
  total: 2,
};

const conversations = [
  { id: 'conv-1', external_id: 'console-aaa-0001', agent_id: 'agent-1', mode: 'bot', assigned_to: null, updated_at: '2026-10-10T10:00:00Z' },
  { id: 'conv-2', external_id: 'console-bbb-0002', agent_id: 'agent-2', mode: 'bot', assigned_to: null, updated_at: '2026-10-10T11:00:00Z' },
];

test('conversation workbench: filter sessions by agent in all view', async ({ page }) => {
  const listQueries: (string | null)[] = [];
  await page.route('**/api/v1/**', async route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith('/me')) return route.fulfill({ json: { role: 'admin', tenant: '测试企业' } });
    if (path.endsWith('/agents/counts')) return route.fulfill({ json: counts });
    if (path.endsWith('/agents')) return route.fulfill({ json: agents });
    if (path.endsWith('/conversations')) {
      const agentId = url.searchParams.get('agent_id');
      listQueries.push(agentId);
      const items = agentId ? conversations.filter(item => item.agent_id === agentId) : conversations;
      return route.fulfill({ json: { items, total: items.length, page: 1, page_size: 10 } });
    }
    if (path.endsWith('/modules')) return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });

  await page.goto('/#/conversations/_all');
  await expect(page.getByRole('button', { name: /console-aaa/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /console-bbb/ })).toBeVisible();

  // The "agent in use" selector only offers agents shown on the entry page.
  const selector = page.getByLabel('使用的 Agent');
  await expect(selector.locator('option')).toHaveCount(3);
  await expect(selector.locator('option', { hasText: '客服丙' })).toHaveCount(0);

  // Picking an agent re-queries the list with agent_id.
  await selector.selectOption('agent-1');
  await expect(page.getByRole('button', { name: /console-aaa/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /console-bbb/ })).toHaveCount(0);
  expect(listQueries).toContain('agent-1');

  // Back to the default runtime option restores the unfiltered list.
  await selector.selectOption('');
  await expect(page.getByRole('button', { name: /console-bbb/ })).toBeVisible();
  expect(listQueries.at(-1)).toBeNull();
});
