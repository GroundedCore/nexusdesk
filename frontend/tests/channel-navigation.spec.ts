import { test, expect } from '@playwright/test';

for (const role of ['admin', 'viewer']) {
  test(`channels belong to open platform: ${role}`, async ({ page }) => {
    const errors: string[] = [];
    const requests: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    const agentId = '22222222-2222-4222-8222-222222222222';
    let channels: object[] = [];
    await page.route('**/api/v1/**', async route => {
      const req = route.request();
      const path = new URL(req.url()).pathname.replace('/api/v1', '');
      requests.push(path);
      if (path === '/me') return route.fulfill({ json: { role, tenant: 'test' } });
      if (path === '/deployment') return route.fulfill({ json: { quickstart: false } });
      if (path === '/agents' || path === '/open-platform/agents') return route.fulfill({ json: [{ id: agentId, name: '客服 Agent', published_version: 1 }] });
      if (path === '/channels') {
        if (req.method() === 'POST') {
          expect(req.postDataJSON()).toEqual({ name: '客服消息入口', agent_id: agentId, signing_secret_ref: null });
          const channel = { id: '33333333-3333-4333-8333-333333333333', name: '客服消息入口', agent_id: agentId, enabled: true };
          channels = [channel];
          return route.fulfill({ status: 201, json: { ...channel, token: 'test-channel-token' } });
        }
        return route.fulfill({ json: channels });
      }
      return route.fulfill({ json: { items: [], total: 0 } });
    });
    await page.goto('/#/channels');
    await expect(page).toHaveURL(/#\/open-platform\/channels$/);
    await expect(page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: '渠道接入', exact: true })).toHaveCount(0);
    await expect(page.getByRole('tab', { name: '通用消息接入', exact: true })).toHaveAttribute('aria-selected', 'true');
    expect(requests.some(path => path.includes('/applications/channels'))).toBe(false);
    if (role === 'viewer') {
      await expect(page.getByRole('button', { name: '创建渠道', exact: true })).toBeDisabled();
    } else {
      await page.getByLabel('渠道名称', { exact: true }).fill('客服消息入口');
      await page.getByRole('combobox', { name: '已发布 Agent', exact: true }).selectOption(agentId);
      await page.getByRole('button', { name: '创建渠道', exact: true }).click();
      await expect(page.getByText('test-channel-token', { exact: true })).toBeVisible();
    }
    await page.getByRole('tab', { name: '应用接入', exact: true }).click();
    await expect(page).toHaveURL(/#\/open-platform$/);
    await expect(page.getByRole('heading', { name: '企业应用', exact: true })).toBeVisible();
    await page.goBack();
    await expect(page.getByRole('tab', { name: '通用消息接入', exact: true })).toHaveAttribute('aria-selected', 'true');
    await page.reload();
    await expect(page.getByRole('heading', { name: '通用消息接入', exact: true })).toBeVisible();
    expect(errors).toEqual([]);
  });
}
