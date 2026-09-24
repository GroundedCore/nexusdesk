import { test, expect } from '@playwright/test';
const id='11111111-1111-4111-8111-111111111111';
const agent='22222222-2222-4222-8222-222222222222';
for(const role of ['admin','viewer'])test(`open platform ${role}`,async({page})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 let app={id,name:'企业服务门户',description:'面向员工的一站式知识问答与业务服务。',enabled:true,agent_ids:[agent],rpm:120,max_concurrency:5,revision:1,created_at:'2026-09-23T03:00:00Z'};
 let keys:any[]=[];const writes:any[]=[];
 await page.route('**/api/v1/**',async route=>{
  const req=route.request(),path=new URL(req.url()).pathname.replace('/api/v1','');
  if(path==='/me')return route.fulfill({json:{role,tenant:'企业工作空间'}});
  if(path==='/deployment')return route.fulfill({json:{quickstart:false}});
  if(req.method()!=='GET')writes.push({path,body:req.postDataJSON()});
  if(path==='/open-platform/agents')return route.fulfill({json:[{id:agent,name:'企业知识助手',published_version:2}]});
  if(path==='/open-platform/applications'){
   if(req.method()==='POST'){app={...app,...req.postDataJSON()};return route.fulfill({status:201,json:app});}
   return route.fulfill({json:{items:[app],total:1}});
  }
  if(path===`/open-platform/applications/${id}`){if(req.method()==='PUT')app={...app,...req.postDataJSON(),revision:app.revision+1};return route.fulfill({json:app});}
  if(path.endsWith('/keys')){if(req.method()==='POST'){keys=[{id:'k1',prefix:'opk_demo…test',active:true,created_at:'2026-09-23T03:00:00Z',expires_at:null}];return route.fulfill({status:201,json:{...keys[0],key:'opk_test_once_only'}});}return route.fulfill({json:keys});}
  if(path.endsWith('/logs'))return route.fulfill({json:{items:[],total:0}});
  return route.fulfill({json:[]});
 });
 await page.route('**/openapi/v1/**',async route=>{
  expect(route.request().headers()['authorization']).toBe('Bearer opk_test_once_only');
  const path=new URL(route.request().url()).pathname;
  if(path.endsWith('/conversations'))return route.fulfill({status:201,json:{conversation_id:id,agent_id:agent}});
  return route.fulfill({contentType:'text/event-stream',headers:{'X-Request-ID':id},body:`event: accepted\ndata: {"run_id":"${id}"}\n\nid: 1\nevent: run.completed\ndata: {"id":"${id}","run_id":"${id}","status":"completed","output":"企业集成调试成功","error_code":null}\n\n`});
 });
 await page.goto('/#/open-platform');
 await expect(page.getByRole('heading',{name:'企业服务门户'})).toBeVisible();
 await page.screenshot({path:`test-results/open-platform-${role}-catalog.png`,fullPage:true});
 if(role==='viewer')await expect(page.getByRole('button',{name:/创建应用$/})).toBeDisabled();
 await page.getByRole('main').getByRole('button',{name:/管理$/}).click();
 await expect(page.getByRole('tab',{name:'应用配置'})).toBeVisible();
 if(role==='viewer')await expect(page.getByRole('button',{name:'编辑应用',exact:true})).toBeDisabled();
 else {
  await page.getByRole('button',{name:'编辑应用',exact:true}).click();
  await page.getByLabel('应用名称',{exact:true}).fill('企业知识服务');
  await page.getByRole('button',{name:/确.*定/}).click();
  await expect(page.getByRole('heading',{name:'企业知识服务'})).toBeVisible();
 }
 await page.getByRole('tab',{name:'应用密钥',exact:true}).click();
 if(role==='viewer'){await expect(page.getByRole('button',{name:/创建密钥$/})).toBeDisabled();expect(writes).toHaveLength(0);return;}
 await page.getByRole('button',{name:/创建密钥$/}).click();
 await page.getByRole('button',{name:/确.*定/}).click();
 await expect(page.getByText('opk_test_once_only',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'我已保存'}).click();
 await expect(page.getByText('opk_test_once_only',{exact:true})).toHaveCount(0);
 await page.getByRole('tab',{name:'接入文档'}).click();
 await expect(page.getByText('接入约定',{exact:true})).toBeVisible();
 await page.getByRole('tab',{name:'在线调试'}).click();
 await page.getByLabel('应用密钥',{exact:true}).fill('opk_test_once_only');
 await page.getByLabel('消息',{exact:true}).fill('你好');
 await page.getByRole('button',{name:'发送消息',exact:true}).click();
 await expect(page.getByText('企业集成调试成功',{exact:true})).toBeVisible();
 expect(await page.evaluate(()=>JSON.stringify({...sessionStorage,...localStorage}))).not.toContain('opk_test_once_only');
 await page.screenshot({path:'test-results/open-platform-debug.png',fullPage:true});
 await page.getByRole('tab',{name:'调用日志'}).click();
 await page.getByRole('tab',{name:'在线调试'}).click();
 await expect(page.getByLabel('应用密钥',{exact:true})).toHaveValue('');
 await page.reload();await expect(page.getByLabel('应用密钥',{exact:true})).toHaveValue('');
 expect(errors).toEqual([]);
});
