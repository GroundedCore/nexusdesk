import { test, expect } from '@playwright/test';

test('real backend: create, publish, trial conversation, records, versions and archive',async({page,request})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 const name=`布局验收助手-${Date.now()}`;
 await page.goto('/');await page.getByRole('navigation').getByRole('button',{name:'Agent 管理'}).click();
 await page.getByRole('button',{name:'新增 Agent'}).click();await page.getByLabel('Agent 名称').fill(name);await page.getByLabel('Agent 简介').fill('用于验证新版配置工作台与试聊记录。');
 await page.getByRole('button',{name:/发\s*布/,exact:true}).click();await expect(page.getByText('已发布 v1',{exact:true})).toBeVisible();
 await page.getByLabel('试聊消息',{exact:true}).fill('你好，请介绍一下你能做什么');await page.getByRole('button',{name:'发送试聊消息'}).click();
 await expect(page.locator('.agent-message.assistant')).toBeVisible({timeout:30000});
 await expect(page.getByRole('button',{name:'停止生成'})).toHaveCount(0,{timeout:15000});
 await page.screenshot({path:'test-results/agents-live-chat.png',fullPage:true});
 await page.getByRole('button',{name:'对话记录',exact:true}).click();await expect(page.getByRole('button',{name:/playground-/})).toHaveCount(1);
 await page.getByRole('button',{name:/playground-/}).click();await expect(page.locator('.agent-record-detail')).toContainText('你好，请介绍一下你能做什么');
 await page.getByRole('button',{name:'Agent 配置',exact:true}).click();await expect(page.locator('.agent-message.assistant')).toBeVisible();
 await page.getByLabel('行为指令').fill('你是一位专业的客服，回答要简洁。');await page.getByRole('button',{name:'保存草稿',exact:true}).click();await expect(page.getByRole('status')).toContainText('草稿已保存');
 await page.getByRole('button',{name:'版本历史',exact:true}).click();await expect(page.locator('.agent-diff')).toContainText('行为指令');await page.getByRole('button',{name:'关闭',exact:true}).click();
 await page.getByRole('button',{name:/发\s*布/,exact:true}).click();await expect(page.getByText('已发布 v2',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'版本历史',exact:true}).click();await page.locator('.agent-version').filter({hasText:'版本 1'}).getByRole('button',{name:'切换到此版本'}).click();await expect(page.getByText('已发布 v1',{exact:true})).toBeVisible();await page.getByRole('button',{name:'关闭',exact:true}).click();
 await page.getByRole('button',{name:'返回列表',exact:true}).click();await page.getByLabel('搜索 Agent').fill(name);await expect(page.locator('.agent-card')).toHaveCount(1);
 const catalog=await request.get(`/api/v1/agents/catalog?q=${encodeURIComponent(name)}`);const agent=(await catalog.json()).items[0];
 const conversation=await request.post('/api/v1/conversations',{data:{external_id:`business-${Date.now()}`,agent_id:agent.id}});expect(conversation.status()).toBe(201);
 await page.getByRole('button',{name:`配置 ${name}`,exact:true}).click();await page.getByRole('button',{name:'对话记录',exact:true}).click();await expect(page.locator('.ant-table-tbody .ant-table-row')).toHaveCount(2);
 await page.getByRole('combobox',{name:'会话来源'}).click();await page.locator('.ant-select-dropdown').getByText('业务会话',{exact:true}).click();await expect(page.locator('.ant-table-tbody .ant-table-row')).toHaveCount(1);await expect(page.getByRole('button',{name:/business-/})).toBeVisible();
 await page.screenshot({path:'test-results/agents-live-records.png',fullPage:true});
 await page.getByRole('button',{name:'返回列表',exact:true}).click();await expect(page.getByLabel('搜索 Agent')).toHaveValue(name);await page.getByRole('button',{name:`${name} 更多操作`}).click();await page.getByRole('menuitem',{name:'归档 Agent'}).click();await expect(page.getByText('暂无符合条件的 Agent')).toBeVisible();
 await page.getByRole('checkbox',{name:'显示已归档'}).check();await expect(page.locator('.agent-card')).toHaveCount(1);await page.getByRole('button',{name:`${name} 更多操作`}).click();await page.getByRole('menuitem',{name:'恢复 Agent'}).click();await expect(page.locator('.agent-card')).toContainText('已发布');
 expect(errors).toEqual([]);
});
