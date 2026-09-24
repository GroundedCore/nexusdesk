import { test, expect } from '@playwright/test';

test('unconfigured model guides setup, allows draft saving and blocks publishing', async ({ page }) => {
  const mutations: string[] = [];
  const agent = { id: 'agent-1', name: '待配置助手', description: '', draft: { system_prompt: 'test', tool_names: [], knowledge_base_ids: [], max_model_rounds: 6, model_profile_id: null, model_profile_version: null }, draft_revision: 1, published_version: null, archived: false };
  await page.route('**/api/v1/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace('/api/v1', '');
    if (request.method() !== 'GET') mutations.push(path);
    if (path === '/me') return route.fulfill({ json: { role: 'admin', tenant: 'test' } });
    if (path === '/agents/catalog') return route.fulfill({ json: { items: [agent], total: 1, page: 1, page_size: 12 } });
    if (path === '/agents/agent-1' && request.method() === 'PUT') return route.fulfill({ json: { ...agent, name: request.postDataJSON().name, draft_revision: 2 } });
    if (path === '/agents/agent-1') return route.fulfill({ json: agent });
    if (path.startsWith('/model-gateway/catalog/')) return route.fulfill({ json: { items: [], total: 0 } });
    if (['/tools', '/knowledge-bases', '/modules'].includes(path)) return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });
  await page.goto('/');
  await page.getByRole('navigation').getByRole('button', { name: 'Agent 管理' }).click();
  await page.getByRole('button', { name: '配置 待配置助手', exact: true }).click();
  await expect(page.getByRole('option', { name: '部署默认模型' })).toHaveCount(0);
  await expect(page.getByText('尚未配置模型：', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: /发\s*布/, exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('请先选择已发布的 Chat 配置方案');
  expect(mutations).toEqual([]);
  await page.getByLabel('Agent 名称').fill('草稿助手');
  await page.getByRole('button', { name: /保\s*存/, exact: true }).click();
  await expect.poll(() => mutations).toEqual(['/agents/agent-1']);
  await page.getByRole('button', { name: '前往模型网关配置', exact: true }).click();
  await expect(page.getByRole('button', { name: '新建配置方案', exact: true })).toBeVisible();
});
