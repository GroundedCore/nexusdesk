import {test,expect} from '@playwright/test';
const appId='11111111-1111-4111-8111-111111111111',aid='22222222-2222-4222-8222-222222222222',pid='33333333-3333-4333-8333-333333333333',uid='44444444-4444-4444-8444-444444444444';

test('enterprise provider setup, secret redaction, user roles and embed config',async({page})=>{
 let providers:any[]=[];let embed:any=null;let user:any={id:uid,name:'张三',enabled:true,role:null,revision:1,identities:[{id:aid,source:'provider:'+pid,subject:'employee-1'}]};
 const app={id:appId,name:'员工助手',enabled:true,agent_ids:[aid],revision:1,rpm:120,max_concurrency:5};
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/v1/**',async route=>{
  const r=route.request(),path=new URL(r.url()).pathname.replace('/api/v1','');
  if(path==='/me')return route.fulfill({json:{role:'admin',tenant:'local'}});
  if(path==='/deployment')return route.fulfill({json:{quickstart:false}});
  if(path==='/open-platform/identity/providers'){
   if(r.method()==='POST'){const {secret,...body}=r.postDataJSON();expect(secret).toBe('test-secret-only');providers=[{...body,id:pid,revision:1,secret_configured:true}];return route.fulfill({status:201,json:providers[0]});}
   return route.fulfill({json:providers});
  }
  if(path==='/open-platform/identity/users')return route.fulfill({json:{items:[user],total:1}});
  if(path.endsWith('/users/'+uid)){user={...user,...r.postDataJSON(),revision:2};return route.fulfill({json:user});}
  if(path==='/open-platform/applications/'+appId)return route.fulfill({json:app});
  if(path.endsWith('/embed')){if(r.method()==='PUT')embed={...r.postDataJSON(),revision:1};return route.fulfill({json:embed});}
  if(path==='/open-platform/agents')return route.fulfill({json:[{id:aid,name:'员工助手',published_version:1}]});
  return route.fulfill({json:[]});
 });
 await page.goto('/#/open-platform/identity');
 await page.getByRole('button',{name:'新增身份提供方',exact:true}).click();
 await page.getByLabel('显示名称',{exact:true}).fill('飞书企业登录');
 await page.getByRole('button',{name:'飞书 / Feishu',exact:true}).click();
 await page.getByLabel('App ID',{exact:true}).fill('cli_internal');
 await page.getByLabel('Tenant Key',{exact:true}).fill('tenant-key');
 await page.getByLabel('Client Secret / App Secret',{exact:true}).fill('test-secret-only');
 await page.getByRole('button',{name:/确.*定/}).click();
 await expect(page.getByText('飞书企业登录',{exact:true})).toBeVisible();
 await expect(page.locator('input[value="test-secret-only"]')).toHaveCount(0);
 await expect(page.getByText(new RegExp('/api/v1/sso/callback/'+pid))).toBeVisible();
 await page.getByRole('tab',{name:'企业用户映射'}).click();
 await page.getByRole('button',{name:'管理权限',exact:true}).click();
 await page.getByRole('combobox',{name:'工作台角色',exact:true}).click();
 await page.locator('.ant-select-item-option-content').filter({hasText:/^viewer$/}).click();
 await page.getByRole('button',{name:/确.*定/}).click();
 await expect.poll(()=>user.role).toBe('viewer');
 await page.goto(`/#/open-platform/${appId}/embed`);
 await page.getByLabel('启用嵌入聊天',{exact:true}).click();
 await page.getByRole('combobox',{name:'允许嵌入的来源地址'}).fill('https://oa.example.com');await page.keyboard.press('Enter');await page.keyboard.press('Escape');
 await page.getByRole('button',{name:'保存配置',exact:true}).click();
 await expect.poll(()=>embed?.origins).toEqual(['https://oa.example.com']);
 await expect(page.getByText(/NexusDeskChat.mount/)).toBeVisible();
 await page.screenshot({path:'test-results-phase3/open-identity-embed-settings.png',fullPage:true});
 expect(errors).toEqual([]);
});

test('embedded widget authenticates with user token and displays escaped messages',async({page})=>{
 let sent=false;const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/widget-test',route=>route.fulfill({contentType:'text/html',body:`<!doctype html><html><head><meta charset="utf-8"></head><body><h1>企业内部门户</h1><div id="chat" style="width:420px;height:700px"></div><script src="/embed.js"></script><script>NexusDeskChat.mount({appId:'${appId}',baseUrl:location.origin,container:'#chat',getToken:async()=>({access_token:'chat_user_credential'})})</script></body></html>`}));
 await page.route('**/api/v1/sso/embed/**',route=>route.fulfill({json:{title:'员工服务助手',color:'#6356d9',providers:[]}}));
 await page.route('**/openapi/v1/**',async route=>{
  const r=route.request(),path=new URL(r.url()).pathname.replace('/openapi/v1','');
  expect(r.headers()['authorization']).toBe('Bearer chat_user_credential');
  expect(r.headers()['x-external-user-id']).toBeUndefined();
  if(path==='/agents')return route.fulfill({json:[{id:aid,name:'员工助手'}]});
  if(path==='/conversations')return route.fulfill({status:201,json:{conversation_id:uid}});
  if(path.endsWith('/messages')&&r.method()==='POST'){expect(r.postDataJSON().message).toBe('请介绍报销流程');expect(r.headers()['idempotency-key']).toBeTruthy();sent=true;return route.fulfill({status:202,json:{id:pid,status:'completed'}});}
  if(path.endsWith('/messages'))return route.fulfill({json:{items:sent?[{role:'user',content:'请介绍报销流程'},{role:'assistant',content:'先提交申请，再由主管审批。<script>danger()</script>'}]:[]}});
  return route.fulfill({json:{}});
 });
 await page.goto('/widget-test');
 const frame=page.frameLocator('iframe');
 await expect(frame.getByText('员工服务助手',{exact:true})).toBeVisible();
 await frame.getByLabel('输入消息',{exact:true}).fill('请介绍报销流程');
 await frame.getByRole('button',{name:'发送',exact:true}).click();
 await expect(frame.getByText('先提交申请，再由主管审批。<script>danger()</script>',{exact:true})).toBeVisible();
 await expect(page.evaluate(()=>sessionStorage.getItem('agent-platform-token'))).resolves.toBeNull();
 await page.screenshot({path:'test-results-phase3/open-embedded-chat.png',fullPage:true});
 expect(errors).toEqual([]);
});

test('workbench SSO popup returns a session only to its opener',async({page})=>{
 await page.route('**/api/v1/**',async route=>{
  const r=route.request(),url=new URL(r.url()),path=url.pathname.replace('/api/v1','');
  if(path==='/sso/providers')return route.fulfill({json:[{id:pid,name:'企业 OIDC',kind:'oidc',platform_origin:url.origin}]});
  if(path==='/me')return route.fulfill({json:{role:r.headers()['authorization']==='Bearer ssow_verified'?'viewer':'admin',tenant:'local'}});
  if(path==='/policy')return route.fulfill({json:{tool_allowed_hosts:[]}});
  if(path==='/deployment')return route.fulfill({json:{quickstart:false}});
  return route.fulfill({json:[]});
 });
 await page.context().route('**/api/v1/sso/start/**',route=>{
  const url=new URL(route.request().url());
  return route.fulfill({contentType:'text/html',body:`<script>window.opener.postMessage({type:'nexusdesk.sso',channel:${JSON.stringify(url.searchParams.get('channel'))},session:{access_token:'ssow_verified',expires_in:3600}},location.origin);window.close();</script>`});
 });
 await page.goto('/#/access');
 await page.getByRole('button',{name:'企业 OIDC',exact:true}).click();
 await expect.poll(()=>page.evaluate(()=>sessionStorage.getItem('agent-platform-token'))).toBe('ssow_verified');
 await expect(page.getByRole('button',{name:'退出企业登录',exact:true})).toBeVisible();
});
