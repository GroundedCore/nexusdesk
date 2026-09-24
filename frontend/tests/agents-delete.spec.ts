import { test, expect } from '@playwright/test';

for (const role of ['admin','viewer']) {
 test(`archived agent deletion confirmation and permissions: ${role}`, async({page})=>{
  let deleted=false, calls=0;
  const agent={id:'archived',name:'已归档客服',description:'历史配置',archived:true,draft_revision:2,published_version:1,draft:{system_prompt:'客服',tool_names:[],knowledge_base_ids:[],max_model_rounds:6}};
  await page.route('**/api/v1/**',async route=>{
   const path=new URL(route.request().url()).pathname;
   if(path.endsWith('/me'))return route.fulfill({json:{role,tenant:'test'}});
   if(path.endsWith('/agents/catalog'))return route.fulfill({json:{items:deleted?[]:[agent],total:deleted?0:1,page:1,page_size:12}});
   if(path.endsWith('/agents/archived') && route.request().method()==='DELETE'){
    calls++;expect(new URL(route.request().url()).searchParams.get('revision')).toBe('2');
    if(calls===1)return route.fulfill({status:409,json:{detail:'agent_referenced_by_channels'}});
    deleted=true;return route.fulfill({json:{deleted:true,preserved_conversations:1}});
   }
   return route.fulfill({json:path.endsWith('/modules')?[]:{}});
  });
  await page.goto('/');await page.getByRole('navigation').getByRole('button',{name:'Agent 管理'}).click();
  await page.getByRole('button',{name:'已归档客服 更多操作'}).click();
  if(role==='viewer'){await expect(page.getByRole('menuitem',{name:'永久删除'})).toHaveCount(0);expect(calls).toBe(0);return;}
  await page.getByRole('menuitem',{name:'永久删除'}).click();
  const dialog=page.getByRole('dialog',{name:'永久删除智能体'});
  await expect(dialog).toContainText('历史对话与运行记录会保留');await expect(dialog).toContainText('无法恢复');
  await dialog.getByRole('button',{name:/取\s*消/}).click();expect(calls).toBe(0);
  await page.getByRole('button',{name:'已归档客服 更多操作'}).click();await page.getByRole('menuitem',{name:'永久删除'}).click();
  await dialog.getByRole('button',{name:'确认永久删除'}).click();await expect(dialog.getByRole('alert')).toContainText('仍被渠道引用');await expect(dialog).toBeVisible();
  await dialog.getByRole('button',{name:'确认永久删除'}).click();await expect(dialog).toHaveCount(0);await expect(page.getByText('暂无符合条件的 Agent')).toBeVisible();expect(calls).toBe(2);
 });
}

test('real backend deletes only the newly created fixture and preserves its conversation',async({page,request})=>{
 const name=`删除验收-${Date.now()}`;
 const created=await request.post('/api/v1/agents',{data:{name,description:'删除功能自动验收',config:{system_prompt:'测试',tool_names:[],knowledge_base_ids:[],max_model_rounds:6}}});expect(created.status()).toBe(201);const agent=await created.json();
 const conv=await request.post('/api/v1/conversations',{data:{external_id:`delete-test-${Date.now()}`,agent_id:agent.id}});expect(conv.status()).toBe(201);const conversation=await conv.json();
 expect((await request.patch(`/api/v1/agents/${agent.id}/archive`,{data:{archived:true,revision:1}})).ok()).toBeTruthy();
 await page.goto('/');await page.getByRole('navigation').getByRole('button',{name:'Agent 管理'}).click();await page.getByRole('checkbox',{name:'显示已归档'}).check();await page.getByLabel('搜索 Agent').fill(name);await expect(page.locator('.agent-card')).toHaveCount(1);
 await page.getByRole('button',{name:`${name} 更多操作`}).click();await page.getByRole('menuitem',{name:'永久删除'}).click();await page.getByRole('dialog').getByRole('button',{name:'确认永久删除'}).click();await expect(page.getByText('暂无符合条件的 Agent')).toBeVisible();
 expect((await request.get(`/api/v1/agents/${agent.id}`)).status()).toBe(404);
 const history=await request.get(`/api/v1/conversations/${conversation.id}`);expect(history.status()).toBe(200);expect((await history.json()).deleted_agent.name).toBe(name);
 await page.getByRole('navigation').getByRole('button',{name:'会话工作台'}).click();await page.getByRole('button').filter({hasText:conversation.external_id.slice(0,28)}).click();await expect(page.getByText(`原智能体「${name}」已永久删除。历史对话与运行记录保留，此会话为只读。`)).toBeVisible();await expect(page.getByRole('button',{name:'重新打开会话'})).toHaveCount(0);
});
