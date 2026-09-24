import {test,expect} from '@playwright/test';
const appId='11111111-1111-4111-8111-111111111111',aid='22222222-2222-4222-8222-222222222222',kid='33333333-3333-4333-8333-333333333333',eid='44444444-4444-4444-8444-444444444444';
for(const role of ['admin','viewer'])test(`enterprise integrations ${role}`,async({page})=>{
 let app:any={id:appId,name:'企业门户',description:'集成配置',enabled:true,agent_ids:[aid],agent_versions:{},knowledge_base_ids:[],revision:1,rpm:120,max_concurrency:5};
 let hook:any=null;const writes:any[]=[];const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/v1/**',async route=>{
  const r=route.request(),path=new URL(r.url()).pathname.replace('/api/v1','');
  if(r.method()!=='GET')writes.push({path,body:r.postDataJSON()});
  if(path==='/me')return route.fulfill({json:{role,tenant:'企业测试'}});
  if(path==='/deployment')return route.fulfill({json:{quickstart:false}});
  if(path==='/open-platform/agents')return route.fulfill({json:[{id:aid,name:'企业助手',published_version:2}]});
  if(path.endsWith('/versions'))return route.fulfill({json:[{version:2},{version:1}]});
  if(path==='/open-platform/knowledge-bases')return route.fulfill({json:[{id:kid,name:'员工知识库'}]});
  if(path===`/open-platform/applications/${appId}`){if(r.method()==='PUT')app={...app,...r.postDataJSON(),revision:app.revision+1};return route.fulfill({json:app});}
  if(path.endsWith('/webhook')){if(r.method()==='PUT'){hook={...r.postDataJSON(),revision:1};return route.fulfill({json:{...hook,secret:'whsec_once_only'}});}return route.fulfill({json:hook});}
  if(path.endsWith('/deliveries'))return route.fulfill({json:{items:[{id:eid,event_type:'run.failed',status:'failed',attempts:5,http_status:503,created_at:'2026-09-23T03:00:00Z',resource_id:aid}],total:1}});
  if(path.endsWith('/retry'))return route.fulfill({json:{id:eid}});
  return route.fulfill({json:[]});
 });
 await page.goto(`/#/open-platform/${appId}/integrations`);
 await expect(page.getByText('Agent 版本固定',{exact:true})).toBeVisible();
 if(role==='viewer'){
  await expect(page.getByRole('button',{name:'保存集成配置',exact:true})).toBeDisabled();
  await page.getByRole('tab',{name:'Webhook 回调',exact:true}).click();
  await expect(page.getByRole('button',{name:'保存回调配置',exact:true})).toBeDisabled();
  expect(writes).toHaveLength(0);return;
 }
 await page.getByRole('combobox',{name:'企业助手',exact:true}).click();
 await page.getByText('v1',{exact:true}).click();
 await page.getByRole('combobox',{name:'知识库同步授权',exact:true}).click();
 await page.getByText('员工知识库',{exact:true}).click();await page.keyboard.press('Escape');
 await page.getByRole('button',{name:'保存集成配置',exact:true}).click();
 await expect.poll(()=>app.agent_versions[aid]).toBe(1);
 expect(app.knowledge_base_ids).toEqual([kid]);
 await page.screenshot({path:'test-results/open-integrations.png',fullPage:true});
 const downloadPromise=page.waitForEvent('download');await page.getByRole('link',{name:'Python SDK · 0.2.0'}).click();const download=await downloadPromise;expect(download.suggestedFilename()).toBe('nexusdesk-python.zip');expect(await download.failure()).toBeNull();
 await page.getByRole('tab',{name:'Webhook 回调',exact:true}).click();
 await page.getByLabel('回调地址',{exact:true}).fill('https://internal.example.com/events');
 await page.getByLabel('启用回调',{exact:true}).click();
 await page.getByRole('button',{name:'保存回调配置',exact:true}).click();
 await expect(page.getByText('whsec_once_only',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'我已保存',exact:true}).click();
 await expect(page.getByText('whsec_once_only',{exact:true})).toHaveCount(0);
 await page.getByRole('button',{name:'重新投递',exact:true}).click();
 await expect.poll(()=>writes.some(w=>w.path.endsWith('/retry'))).toBe(true);
 await page.screenshot({path:'test-results/open-webhook.png',fullPage:true});
 await page.getByRole('tab',{name:'应用配置',exact:true}).click();
 await page.getByRole('button',{name:'编辑应用',exact:true}).click();
 await page.getByLabel('应用名称',{exact:true}).fill('新名称');
 await page.getByRole('button',{name:/确.*定/}).click();
 await expect.poll(()=>app.name).toBe('新名称');
 expect(app.agent_versions[aid]).toBe(1);expect(app.knowledge_base_ids).toEqual([kid]);
 expect(errors).toEqual([]);
});
