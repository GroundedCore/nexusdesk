import { test, expect } from '@playwright/test';
import { signInAsAdmin } from './e2e-auth';

test('knowledge, publication, conversation, confirmed ticket, handoff and evaluation', async ({ page, request }) => {
  // The full journey includes waiting for the knowledge build/publish task.
  test.setTimeout(180_000);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const stamp = Date.now();
  const kb = `浏览器知识库-${stamp}`;
  const agent = `浏览器客服-${stamp}`;
  const nav = async (name: string) => { await page.getByRole('navigation').getByRole('button', { name, exact: false }).click(); };
  const auth = await signInAsAdmin(page, request);
  await page.goto('/#/overview');
  await expect(page.getByRole('heading', { name: '平台总览', exact: true })).toBeVisible();
  // Publishing an agent now requires a published Chat model profile; create a demo one via the gateway API.
  const connection = await (await request.post('/api/v1/model-gateway/connections', { data: { name: `联调-${stamp}`, protocol: 'demo' }, headers: auth })).json();
  const model = await (await request.post('/api/v1/model-gateway/models', { data: { name: `联调-${stamp}`, connection_id: connection.id, model_name: 'demo', operations: ['chat'], tool_calling: true }, headers: auth })).json();
  const profile = await (await request.post('/api/v1/model-gateway/profiles', { data: { name: `方案-${stamp}`, model_id: model.id, operation: 'chat' }, headers: auth })).json();
  expect((await request.post(`/api/v1/model-gateway/profiles/${profile.id}/publish`, { data: { revision: 1 }, headers: auth })).status()).toBe(200);
  // The knowledge workspace is upload-and-publish based (no inline text editor):
  // seed the base and document through the API — automatic mode parses, chunks,
  // builds and publishes — then verify retrieval through the 检索测试 tab.
  const kbCreated = await request.post('/api/v1/knowledge-bases', { data: { name: kb }, headers: auth });
  expect(kbCreated.ok()).toBeTruthy();
  const kbId = (await kbCreated.json()).id;
  // Uploads are deduped by content hash (409 on repeats), so keep the body unique per run.
  const upload = await request.post(`/api/v1/knowledge-workspace/uploads?filename=${encodeURIComponent(`发货政策-${stamp}.md`)}&mode=automatic&kb=${kbId}`, {
    headers: { ...auth, 'Content-Type': 'text/markdown; charset=utf-8' },
    data: `# 发货政策\n\n付款后 48 小时内发货，配送问题可申请客服工单。\n\n文档编号 ${stamp}`,
  });
  expect(upload.status()).toBe(202);
  await expect(async () => {
    const library = await (await request.get(`/api/v1/knowledge-workspace/libraries/${kbId}`, { headers: auth })).json();
    expect(library.published_document_count).toBe(1);
  }).toPass({ timeout: 90000, intervals: [1000, 2000, 5000] });
  await nav('知识库');
  await page.getByRole('tab', { name: '检索测试' }).click();
  await page.locator('.knowledge-search-fields .ant-select').first().click();
  await page.locator('.ant-select-item-option').filter({ hasText: kb }).click();
  await page.getByPlaceholder('输入一个真实的客户问题').fill('发货');
  await page.getByRole('button', { name: '测试检索' }).click();
  await expect(page.locator('.knowledge-result-card').first()).toContainText('48 小时');
  await nav('Agent 管理');
  await page.getByRole('button', { name: '新增 Agent' }).click();
  await page.getByLabel('Agent 名称', { exact: true }).fill(agent);
  await page.getByLabel('Chat 模型方案').selectOption(profile.id);
  await page.locator('#agent-knowledge').getByRole('button', { name: '添加' }).click();
  await page.getByLabel(kb, { exact: true }).check();
  await page.getByRole('button', { name: '完成选择' }).click();
  await page.getByRole('button', { name: '保存草稿' }).click();
  // Saving a brand-new agent navigates /agents/new -> /agents/{id}/config, and
  // the route change clears the inline notice immediately; the topbar save
  // state is the durable signal that the draft was saved.
  await expect(page.locator('.agent-save-state')).toContainText('已保存');
  await page.getByRole('button', { name: /发\s*布/, exact: true }).click();
  await expect(page.getByRole('status')).toContainText('已保存并发布');
  await page.getByRole('button', { name: '返回列表', exact: true }).click();
  // The workbench landing page is the agent stats overview: a freshly published
  // agent has no conversations yet, so it appears neither as a stats card nor in
  // the "全部会话" agent selector. Enter its workbench directly by URL instead.
  const agentList = await (await request.get('/api/v1/agents?include_archived=true', { headers: auth })).json();
  const agentId = agentList.find((item: { id: string; name: string }) => item.name === agent)?.id;
  expect(agentId).toBeTruthy();
  await page.goto(`/#/conversations/${agentId}`);
  await page.getByRole('button', { name: '新建会话' }).click();
  await page.getByLabel('消息内容').fill('什么时候发货？');
  await page.getByRole('button', { name: '发送消息' }).click();
  await expect(page.locator('.c-message').filter({ hasText: '48 小时' })).toBeVisible({ timeout: 20000 });
  // The run trace moved behind the per-message 查看运行轨迹 drawer.
  await page.getByRole('button', { name: '查看运行轨迹' }).first().click();
  await expect(page.locator('.trace')).toContainText('knowledge.retrieved');
  await page.keyboard.press('Escape');
  await page.getByLabel('消息内容').fill('请创建配送工单');
  await page.getByRole('button', { name: '发送消息' }).click();
  await expect(page.getByRole('button', { name: '确认创建' })).toBeEnabled({ timeout: 20000 });
  await page.getByRole('button', { name: '确认创建' }).click();
  // Ticket confirmation surfaces as a system message; no toast is emitted anymore.
  await expect(page.locator('.c-message.is-system')).toContainText('已创建');
  await page.getByRole('button', { name: '转人工', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('人工队列');
  const conversation = await page.locator('.list-item.selected strong').innerText();
  await nav('人工协同');
  const card = page.locator('.service-card').filter({ hasText: conversation });
  await card.getByRole('button', { name: '接管', exact: true }).click();
  await expect(card.getByRole('button', { name: '结束并恢复 Agent' })).toBeVisible();
  await page.goto(`/#/conversations/${agentId}`);
  await page.getByRole('button').filter({ hasText: conversation }).click();
  await page.getByLabel('消息内容').fill('人工客服已为您核查配送信息。');
  await page.getByRole('button', { name: '人工回复', exact: true }).click();
  await expect(page.locator('.c-message.is-human')).toContainText('人工客服已为您核查');
  await nav('人工协同');
  await card.getByRole('button', { name: '结束并恢复 Agent' }).click();
  await expect(page.getByRole('status')).toContainText('已恢复');
  // Evaluation moved into the agent workspace; the outer 质量大盘 only aggregates it.
  await page.goto(`/#/agents/${agentId}/evaluation`);
  await page.getByRole('button', { name: '新增用例' }).click();
  await page.getByLabel('用例名称').fill('退货知识问答');
  await page.getByLabel('用户输入', { exact: true }).fill('退货政策');
  await page.getByLabel('预期包含文本（每行一项）').fill('七天');
  await page.getByRole('button', { name: '保存用例' }).click();
  await expect(page.getByRole('status')).toContainText('用例已保存');
  await page.getByRole('button', { name: '运行评测' }).click();
  await expect(page.locator('.agent-evaluation-view .report')).toContainText('1/1 通过', { timeout: 20000 });
  await nav('质量大盘');
  await expect(page.locator('.quality-board')).toContainText(agent);
  await expect(page.locator('.quality-board')).toContainText('待复核队列');
  // The former 渠道接入 section was renamed to 开放平台.
  for (const name of ['工具目录', '服务工单', '开放平台', '运行与审计', '访问与策略', '平台总览']) {
    await nav(name);
    await expect(page.getByRole('heading', { name, exact: true, level: 1 })).toBeVisible();
    await expect(page.locator('.alert.error')).toHaveCount(0);
  }
  await page.screenshot({ path: 'test-results/platform-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: 'test-results/platform-mobile.png', fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  expect(errors).toEqual([]);
});
