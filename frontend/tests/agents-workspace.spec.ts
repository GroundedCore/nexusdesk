import { test, expect } from '@playwright/test';

for (const role of ['admin','viewer']) {
 test(`agent workspace layout and interactions: ${role}`,async({page})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  let agent={id:'agent-1',name:'售后服务助手',description:'解答产品问题，查询知识库，帮助客户跟进售后服务。',draft:{system_prompt:'你是一位专业的售后客服。',tool_names:[],knowledge_base_ids:[],max_model_rounds:6,model_profile_id:"profile-1",model_profile_version:1},draft_revision:1,published_version:1,archived:false,updated_at:'2026-09-22T02:00:00Z'};
  let failSave=true;const mutations:string[]=[];
  await page.route('**/api/v1/**',async route=>{
   const req=route.request();const url=new URL(req.url());const path=url.pathname.replace('/api/v1','');
   if(path==='/me')return route.fulfill({json:{role,tenant:'测试企业'}});
   if(path==='/agents/catalog')return route.fulfill({json:{items:url.searchParams.get('q')==='不存在'?[]:[agent,...Array.from({length:4},(_,i)=>({...agent,id:`agent-${i+2}`,name:['销售顾问','订单助手','知识问答','客户接待'][i],published_version:i%2?1:null}))],total:url.searchParams.get('q')==='不存在'?0:5,page:1,page_size:12}});
   if(path==='/agents/agent-1' && req.method()==='PUT') {mutations.push('save');if(failSave){failSave=false;return route.fulfill({status:409,json:{detail:'agent_revision_conflict'}});}const body=req.postDataJSON();agent={...agent,name:body.name,description:body.description,draft:body.config,draft_revision:agent.draft_revision+1};return route.fulfill({json:agent});}
   if(path==='/agents/agent-1/publish'){mutations.push('publish');agent={...agent,published_version:2};return route.fulfill({json:{version:2}});}
   if(path==='/agents/agent-1')return route.fulfill({json:agent});
   if(path.endsWith('/versions'))return route.fulfill({json:[{version:1,created_at:'2026-09-22T02:00:00Z'}]});
   if(path.endsWith('/diff'))return route.fulfill({json:{changes:[{field:'system_prompt',published:'旧行为指令',draft:agent.draft.system_prompt}]}});
   if(path==='/agents/agent-1/conversations')return route.fulfill({json:{items:[{id:'conv-1',external_id:'playground-001',agent_id:agent.id,source:'playground',mode:'bot',updated_at:'2026-09-22T03:00:00Z',last_message:'您好，有什么可以帮您？'}],total:1,page:1,page_size:12}});
   if(path==='/conversations/conv-1')return route.fulfill({json:{id:'conv-1',mode:'bot',messages:[{id:'msg-2',seq:2,role:'assistant',content:'您好，有什么可以帮您？',created_at:'2026-09-22T03:00:00Z'}],runs:[],actions:[]}});
   if(path==='/conversations/conv-1/history')return route.fulfill({json:{items:[{id:'msg-1',seq:1,role:'user',content:'之前的历史消息',created_at:'2026-09-22T02:59:00Z'}],has_more:false}});
   if(path.startsWith('/model-gateway/catalog/'))return route.fulfill({json:{items:[],total:0}});
   if(path==='/model-gateway/profiles'||path==='/tools'||path==='/knowledge-bases'||path==='/modules')return route.fulfill({json:[]});
   return route.fulfill({json:{}});
  });
  await page.goto('/');await page.getByRole('navigation').getByRole('button',{name:'Agent 管理'}).click();
  await expect(page.locator('.agent-card')).toHaveCount(5);
  await page.screenshot({path:`test-results/agents-list-${role}.png`,fullPage:true});
  await page.getByLabel('搜索 Agent').fill('不存在');await expect(page.getByText('暂无符合条件的 Agent')).toBeVisible();
  await page.getByRole('button',{name:/重\s*置/,exact:true}).click();await page.getByRole('button',{name:'配置 售后服务助手',exact:true}).click();
  await expect(page.getByRole('button',{name:'对话记录',exact:true})).toBeVisible();
  await expect(page.getByLabel('Agent 名称')).toHaveValue('售后服务助手');
  await page.screenshot({path:`test-results/agents-editor-${role}.png`,fullPage:true});
  if(role==='admin') {
   await page.getByLabel('Agent 名称').fill('修改中的助手');
   await page.getByRole('button',{name:'对话记录',exact:true}).click();
   await page.getByRole('button',{name:'playground-001',exact:true}).click();
   await expect(page.getByText('您好，有什么可以帮您？')).toBeVisible();
   await page.getByRole('button',{name:'加载更早消息'}).click();await expect(page.getByText('之前的历史消息')).toBeVisible();
   await page.screenshot({path:'test-results/agents-records.png',fullPage:true});
   await page.getByRole('button',{name:'Agent 配置',exact:true}).click();await expect(page.getByLabel('Agent 名称')).toHaveValue('修改中的助手');
   await page.getByRole('button',{name:/发\s*布/,exact:true}).click();await expect(page.getByRole('alert')).toContainText('agent_revision_conflict');expect(mutations).toEqual(['save']);
   await page.getByRole('button',{name:/发\s*布/,exact:true}).click();await expect(page.getByText('已发布 v2',{exact:true})).toBeVisible();expect(mutations).toEqual(['save','save','publish']);
   await page.getByLabel('Agent 名称').fill('尚未保存');page.once('dialog',d=>d.dismiss());await page.getByRole('button',{name:'返回列表',exact:true}).click();await expect(page.getByLabel('Agent 名称')).toHaveValue('尚未保存');
   page.once('dialog',d=>d.accept());await page.getByRole('button',{name:'返回列表',exact:true}).click();await expect(page.locator('.agent-card')).toHaveCount(5);
   await page.getByRole('button',{name:'配置 修改中的助手',exact:true}).click();
  }else{await expect(page.getByLabel('Agent 名称')).toBeDisabled();await expect(page.getByRole('button',{name:/发\s*布/,exact:true})).toBeDisabled();await expect(page.getByLabel('试聊消息',{exact:true})).toBeDisabled();}
  await page.setViewportSize({width:390,height:844});
  await expect(page.getByLabel('Agent 名称')).toBeVisible();await page.locator('.agent-mobile-tabs').getByRole('button',{name:/试\s*聊/,exact:true}).click();await expect(page.getByLabel('试聊消息',{exact:true})).toBeVisible();
  await page.screenshot({path:`test-results/agents-mobile-${role}.png`,fullPage:true});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBeTruthy();expect(errors).toEqual([]);
 });
}
