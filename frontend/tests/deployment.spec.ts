import { test, expect } from '@playwright/test';

test('model gateway guides configuration without requiring a default model', async ({ page }) => {
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/me')) return route.fulfill({ json: { role: 'admin', tenant: 'validation' } });
    if (path.endsWith('/deployment')) return route.fulfill({ json: { quickstart: false } });
    return route.fulfill({ json: { items: [], total: 0 } });
  });
  await page.goto('/#/models/models');
  await expect(page.getByText('尚未配置可用的对话模型', { exact: true })).toBeVisible();
  await expect(page.getByText('平台可以先启动。请添加供应商连接和 Chat 模型，发布配置方案后绑定到 Agent，即可开始对话。', { exact: true })).toBeVisible();
});

for (const quickstart of [true, false]) {
  test(`deployment banner: quickstart=${quickstart}`, async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/v1/**', async route => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith('/deployment')) return route.fulfill({ json: { quickstart } });
      if (path.endsWith('/me')) return route.fulfill({ json: { role: 'admin', tenant: 'demo' } });
      if (path.endsWith('/policy')) return route.fulfill({ json: { tool_allowed_hosts: [], model_backend: 'demo' } });
      return route.fulfill({ json: [] });
    });
    await page.goto('/#/access');
    await expect(page.getByText('NexusDesk · 快速体验模式')).toHaveCount(quickstart ? 1 : 0);
    await expect(page.getByRole('heading', { name: '访问与策略', exact: true })).toBeVisible();
    expect(errors).toEqual([]);
  });
}
