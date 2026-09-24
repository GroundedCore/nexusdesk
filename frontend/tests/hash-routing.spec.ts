import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  let agent = { id: 'agent-1', name: '路由助手', description: '', draft: { system_prompt: 'test', tool_names: [], knowledge_base_ids: [], max_model_rounds: 6, model_profile_id: null, model_profile_version: null }, draft_revision: 1, published_version: null, archived: false };
  await page.route('**/api/v1/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace('/api/v1', '');
    if (path === '/me') return route.fulfill({ json: { role: 'admin', tenant: 'test' } });
    if (path === '/agents/catalog') return route.fulfill({ json: { items: [agent], total: 1, page: 1, page_size: 12 } });
    if (path === '/agents' && request.method() === 'POST') {
      const body = request.postDataJSON();
      agent = { ...agent, name: body.name, description: body.description, draft: body.config };
      return route.fulfill({ status: 201, json: agent });
    }
    if (path === '/agents/agent-1') return route.fulfill({ json: agent });
    if (path === '/agents/missing') return route.fulfill({ status: 404, json: { detail: 'agent_not_found' } });
    if (path.startsWith('/model-gateway/catalog/') || path === '/agents/agent-1/conversations') return route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 12 } });
    if (['/tools', '/knowledge-bases', '/modules'].includes(path)) return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });
});

test('top pages and gateway tabs survive refresh and browser history', async ({ page }) => {
  await page.goto('/#/models/profiles');
  await expect(page.getByRole('button', { name: '新建配置方案', exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('button', { name: '新建配置方案', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '渠道管理', exact: true }).click();
  await expect(page).toHaveURL(/#\/models\/connections$/);
  await page.getByRole('navigation').getByRole('button', { name: 'Agent 管理' }).click();
  await expect(page).toHaveURL(/#\/agents$/);
  await page.reload();
  await expect(page.locator('.agent-card')).toHaveCount(1);
  await page.goBack();
  await expect(page.getByRole('button', { name: '新建渠道', exact: true })).toBeVisible();
  await page.goBack();
  await expect(page.getByRole('button', { name: '新建配置方案', exact: true })).toBeVisible();
  await page.goForward();
  await expect(page).toHaveURL(/#\/models\/connections$/);
});

test('agent deep links and dirty Back cancellation preserve history and form', async ({ page }) => {
  await page.goto('/#/agents');
  await page.getByRole('button', { name: '配置 路由助手', exact: true }).click();
  await expect(page).toHaveURL(/#\/agents\/agent-1\/config$/);
  await page.reload();
  await expect(page.getByLabel('Agent 名称')).toHaveValue('路由助手');
  await page.getByRole('button', { name: '对话记录', exact: true }).click();
  await expect(page).toHaveURL(/#\/agents\/agent-1\/conversations$/);
  await page.reload();
  await expect(page.getByLabel('会话来源')).toBeVisible();
  await page.goBack();
  await page.getByLabel('Agent 名称').fill('未保存修改');
  page.once('dialog', dialog => dialog.dismiss());
  await page.goBack();
  await expect(page).toHaveURL(/#\/agents\/agent-1\/config$/);
  await expect(page.getByLabel('Agent 名称')).toHaveValue('未保存修改');
  page.once('dialog', dialog => dialog.accept());
  await page.goBack();
  await expect(page).toHaveURL(/#\/agents$/);
  await page.goForward();
  await expect(page.getByLabel('Agent 名称')).toHaveValue('路由助手');
  await page.goForward();
  await expect(page.getByLabel('会话来源')).toBeVisible();
});

test('new draft gets a permanent URL; missing agent and invalid paths recover', async ({ page }) => {
  await page.goto('/#/agents/new/config');
  await page.getByLabel('Agent 名称').fill('新路由助手');
  await page.getByRole('button', { name: '保存草稿', exact: true }).click();
  await expect(page).toHaveURL(/#\/agents\/agent-1\/config$/);
  await page.reload();
  await expect(page.getByLabel('Agent 名称')).toHaveValue('新路由助手');
  await page.goto('/#/agents/missing/config');
  await expect(page.getByRole('alert')).toContainText('无法打开 Agent');
  await page.getByRole('button', { name: '返回列表', exact: true }).click();
  await expect(page).toHaveURL(/#\/agents$/);
  await page.goto('/#/not-a-page');
  await expect(page).toHaveURL(/#\/overview$/);
  await page.evaluate(() => { window.location.hash = '/another-invalid-page'; });
  await expect(page).toHaveURL(/#\/overview$/);
});
