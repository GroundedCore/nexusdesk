import { test, expect } from '@playwright/test';

const gid='11111111-1111-4111-8111-111111111111';
for(const role of ['admin','viewer','post']) {
 test(`tool workspace: ${role}`,async({page})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  const writes:{path:string;body:any}[]=[];
  let group:any={id:gid,name:'订单与物流',description:'查询订单详情、发货进度与物流轨迹。',icon:'orders',revision:1,enabled:true,archived:false,api_count:0,reference_count:0,status:'unpublished',spec:{name:'collection',description:'Collection',url:'http://localhost/v1',auth_kind:'bearer',headers:{},headers_from_env:{},stored_headers:['Authorization'],parameters:{type:'object',properties:{},additionalProperties:false}}};
  let rows:any[]=[];
  await page.route('**/api/v1/**',async route=>{
   const req=route.request(),url=new URL(req.url()),path=url.pathname.replace('/api/v1',''),method=req.method();
   if(path==='/me')return route.fulfill({json:{role:role==='post'?'admin':role,tenant:'测试企业'}});
   if(method!=='GET')writes.push({path,body:req.postDataJSON()});
   if(path==='/tool-workspace/collections'&&method==='GET')return route.fulfill({json:{items:[group,...['客户服务','库存查询','售后政策'].map((name,i)=>({...group,id:`${i+2}1111111-1111-4111-8111-111111111111`,name,icon:['support','inventory','policy'][i]}))],total:4,page:1,page_size:12}});
   if(path===`/tool-workspace/collections/${gid}`&&method==='GET')return route.fulfill({json:{collection:group,items:rows,total:rows.length,page:1,page_size:20}});
   if(path===`/tool-workspace/collections/${gid}`&&method==='PUT'){const body=req.postDataJSON();group={...group,name:body.name,revision:group.revision+1,spec:{...group.spec,url:body.base_url,auth_kind:body.auth_kind,stored_headers:Object.keys(body.header_secrets)}};return route.fulfill({json:group})}
   if(path.endsWith('/apis')&&method==='POST'){const body=req.postDataJSON();rows=[{id:'22222222-2222-4222-8222-222222222222',...body,name:body.definition.name,spec:{...body.definition,header_secrets:undefined,stored_headers:[]},revision:1,enabled:true,archived:false,published_version:null,changed:true,reference_count:0,updated_at:'2026-09-22T10:00:00Z'}];group.api_count=1;return route.fulfill({status:201,json:rows[0]})}
   if(path.endsWith('/preview'))return route.fulfill({json:{revision:1,collection_revision:group.revision,definition:rows[0]?.spec,changes:[{field:'description',before:null,after:'查询订单详情'}],references:[]}});
   if(path.endsWith('/publish')){rows[0].published_version=1;rows[0].changed=false;return route.fulfill({json:{version:1}})}
   if(path.endsWith('/test'))return route.fulfill({json:{ok:true,data:{accepted:true}}});
   if(path.endsWith('/references'))return route.fulfill({json:[]});
   if(path.endsWith('/versions')||path.endsWith('/calls'))return route.fulfill({json:{items:[],total:0,page:1,page_size:20}});
   return route.fulfill({json:[]});
  });
  await page.goto('/#/tools',{waitUntil:'domcontentloaded'});
  await expect(page.locator('.tool-collection-table .ant-table-tbody > tr[data-row-key]')).toHaveCount(4);
  await page.screenshot({path:`test-results/tools-${role}-catalog.png`,fullPage:true});
  if(role==='viewer'){
   await expect(page.getByRole('button',{name:/新建工具集/})).toBeDisabled();
   await expect(page.getByRole('button',{name:'编辑工具集 订单与物流',exact:true})).toBeDisabled();
   await page.getByRole('button',{name:'管理 API'}).first().click();
   await expect(page.getByRole('button',{name:/新增 API/})).toBeDisabled();
   expect(writes).toHaveLength(0);return;
  }
  await page.getByRole('button',{name:'编辑工具集 订单与物流',exact:true}).click();
  await expect(page.getByLabel('Token',{exact:true})).toHaveValue('');
  await expect(page.getByLabel('Token',{exact:true})).toHaveAttribute('type','password');
  await page.getByLabel('Token',{exact:true}).fill('Bearer fake-replacement');
  await page.getByRole('button',{name:'保存草稿',exact:true}).click();
  await expect(page).toHaveURL(new RegExp(`#/tools/${gid}/apis$`));
  expect(writes[0].body.header_secrets).toEqual({Authorization:'Bearer fake-replacement'});
  await page.reload({waitUntil:'domcontentloaded'});
  await expect(page.getByRole('button',{name:/新增 API/})).toBeVisible();
  await page.getByRole('button',{name:/新增 API/}).click();
  await page.getByLabel('显示名称',{exact:true}).fill('查询订单');
  await page.getByLabel('调用标识',{exact:true}).fill('order_lookup');
  await page.getByLabel('用途描述',{exact:true}).fill('查询订单详情');
  await page.getByLabel('接口路径',{exact:true}).fill('/orders');
  await page.getByRole('button',{name:/添加参数/}).click();
  await page.getByLabel('参数名',{exact:true}).fill('order_id');
  await page.getByLabel('必填',{exact:true}).check();
  if(role==='post'){
   await page.getByRole('combobox',{name:'请求方法'}).click();
   await page.getByRole('combobox',{name:'请求方法'}).press('ArrowDown');
   await page.getByRole('combobox',{name:'请求方法'}).press('Enter');
   await page.getByRole('textbox',{name:'JSON Schema',exact:true}).fill(JSON.stringify({type:'object',properties:{order_id:{type:'string'},items:{type:'array',items:{type:'object',properties:{sku:{type:'string'}}}}},required:['order_id'],additionalProperties:false}));
  }
  await page.screenshot({path:`test-results/tools-${role}-api-editor.png`,fullPage:true});
  await page.getByRole('button',{name:'保存草稿',exact:true}).click();
  await expect(page.getByRole('cell',{name:/查询订单/})).toBeVisible();
  const created=writes.find(w=>w.path.endsWith('/apis'))!.body;
  expect(created.auth_mode).toBe('inherit');
  expect(created.definition.method).toBe(role==='post'?'POST':'GET');
  expect(created.definition.parameters.required).toEqual(['order_id']);
  await page.getByRole('button',{name:/^发\s*布$/}).click();
  await expect(page.getByRole('dialog')).toContainText('不会自动更新已有 Agent');
  await page.getByRole('button',{name:'确认发布',exact:true}).click();
  await expect(page.getByText('已发布 v1',{exact:true})).toBeVisible();
  if(role==='post'){
   await page.getByRole('row').filter({hasText:'order_lookup'}).getByRole('button',{name:'更多操作',exact:true}).hover();
   await page.getByRole('menuitem',{name:'调试',exact:true}).click();
   await page.getByLabel('真实试调用',{exact:true}).check();
   await expect(page.getByRole('button',{name:'发送 POST 请求',exact:true})).toBeDisabled();
   await page.getByLabel('我确认发送此 POST 请求，接口可能修改业务数据。',{exact:true}).check();
   await page.getByRole('button',{name:'发送 POST 请求',exact:true}).click();
   await expect(page.locator('.tw-test-result')).toContainText('accepted');
   expect(writes.find(w=>w.path.endsWith('/test'))!.body.confirmed).toBe(true);
   await page.getByRole('dialog').getByRole('button',{name:'关闭',exact:true}).click();
  }
  await page.getByRole('tab',{name:'发布历史',exact:true}).click();
  await page.reload({waitUntil:'domcontentloaded'});
  await expect(page.getByRole('tab',{name:'发布历史',exact:true})).toHaveAttribute('aria-selected','true');
  await page.getByRole('button',{name:/返回工具目录/}).click();
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('button',{name:/新建工具集/}).click();
  await page.screenshot({path:'test-results/tools-mobile-editor.png',fullPage:true});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBeTruthy();
  expect(errors).toEqual([]);
 });
}
