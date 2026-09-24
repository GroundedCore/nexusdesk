import { test, expect } from '@playwright/test';

test('scope is exclusive and mine survives refresh, workspace return and history',async({page})=>{
  const agent={id:'mine-agent',name:'我的游戏助手',created_by:'staff:me',industry:'gaming',tags:['knowledge'],is_example:false,draft:{system_prompt:'test',tool_names:[],knowledge_base_ids:[],max_model_rounds:6},draft_revision:1,published_version:null,archived:false};
  let lastQuery='';
  await page.route('**/api/v1/**',async route=>{
    const url=new URL(route.request().url());const path=url.pathname.replace('/api/v1','');
    if(path==='/me')return route.fulfill({json:{role:'admin',tenant:'test',actor:'staff:me'}});
    if(path==='/agents/catalog'){lastQuery=url.search;return route.fulfill({json:{items:[agent],total:1,page:1,page_size:12}});}
    if(path==='/agents/mine-agent')return route.fulfill({json:agent});
    if(path.startsWith('/model-gateway/catalog/'))return route.fulfill({json:{items:[],total:0}});
    return route.fulfill({json:[]});
  });
  await page.goto('/#/agents?industry=gaming&tag=knowledge&examples=1&page=3');
  await expect(page.getByRole('button',{name:'范围：仅看案例',exact:true})).toHaveAttribute('aria-pressed','true');
  await page.getByRole('button',{name:'范围：我创建的',exact:true}).click();
  await expect(page.getByRole('button',{name:'范围：仅看案例',exact:true})).toHaveAttribute('aria-pressed','false');
  await expect.poll(()=>new URLSearchParams(lastQuery).get('mine_only')).toBe('true');
  expect(new URLSearchParams(lastQuery).get('examples_only')).toBe('false');
  expect(new URLSearchParams(lastQuery).get('page')).toBe('1');
  await expect(page).toHaveURL(/industry=gaming&tag=knowledge&scope=mine$/);
  const filtered=page.url();await page.reload();
  await expect(page.getByRole('button',{name:'范围：我创建的',exact:true})).toHaveAttribute('aria-pressed','true');
  await page.locator('.agent-card-main').click();
  await page.getByRole('button',{name:'返回列表',exact:true}).click();
  await expect(page).toHaveURL(filtered);
  await page.getByRole('button',{name:'范围：仅看案例',exact:true}).click();
  await expect.poll(()=>new URLSearchParams(lastQuery).get('mine_only')).toBe('false');
  await expect.poll(()=>new URLSearchParams(lastQuery).get('examples_only')).toBe('true');
  await page.goBack();await expect(page.getByRole('button',{name:'范围：我创建的',exact:true})).toHaveAttribute('aria-pressed','true');
  await page.screenshot({path:'test-results/agent-mine-scope.png',fullPage:true});
  await page.getByRole('button',{name:/重\s*置/,exact:true}).click();
  await expect(page.getByRole('button',{name:'范围：全部',exact:true})).toHaveAttribute('aria-pressed','true');
  await expect(page).toHaveURL(/#\/agents$/);
  await page.setViewportSize({width:390,height:844});
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
});

test('live industry filters combine, survive refresh and return from workspace', async ({ page }) => {
  await page.goto('/#/agents');
  await page.getByRole('button', { name: '行业：游戏', exact: true }).click();
  await expect(page.locator('.agent-card')).toHaveCount(2);
  await page.getByRole('button', { name: '用途：售后支持', exact: true }).click();
  await page.getByRole('button',{name:'范围：仅看案例',exact:true}).click();
  await expect(page.locator('.agent-card')).toHaveCount(1);
  await expect(page.locator('.agent-card')).toContainText('玩家客服助手');
  const filtered = page.url();
  await page.reload();
  await expect(page.getByRole('button', { name: '行业：游戏', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('button',{name:'范围：仅看案例',exact:true})).toHaveAttribute('aria-pressed','true');
  await expect(page.locator('.agent-card')).toHaveCount(1);
  await page.locator('.agent-card-main').click();
  await expect(page.getByLabel('所属行业')).toHaveValue('gaming');
  await page.getByRole('button', { name: '返回列表', exact: true }).click();
  await expect(page).toHaveURL(filtered);
  await page.getByRole('button', { name: '行业：电商零售', exact: true }).click();
  await expect(page.locator('.agent-card')).toHaveCount(2);
  await page.goBack();
  await expect(page.locator('.agent-card')).toHaveCount(1);
  await page.screenshot({ path: 'test-results/agent-tag-filters.png', fullPage: true });
  await page.getByRole('button', { name: /重\s*置/, exact: true }).click();
  await expect(page).toHaveURL(/#\/agents$/);
  await expect(page.getByRole('button', { name: '行业：全部', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await page.setViewportSize({width:390,height:844});
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBeTruthy();
});

test('classification editor persists industry tags and example marker', async ({ page }) => {
  let agent = {id:'agent-1',name:'编辑分类',description:'',industry:'gaming',tags:['knowledge'],is_example:true,draft:{system_prompt:'test',tool_names:[],knowledge_base_ids:[],max_model_rounds:6},draft_revision:1,published_version:null,archived:false};
  let saved:Record<string,unknown>|undefined;
  await page.route('**/api/v1/**',async route=>{
    const req=route.request();const path=new URL(req.url()).pathname.replace('/api/v1','');
    if(path==='/me')return route.fulfill({json:{role:'admin',tenant:'test'}});
    if(path==='/agents/catalog')return route.fulfill({json:{items:[agent],total:1,page:1,page_size:12}});
    if(path==='/agents/agent-1'){
      if(req.method()==='PUT'){saved=req.postDataJSON();agent={...agent,...saved,draft:agent.draft,draft_revision:2};}
      return route.fulfill({json:agent});
    }
    if(path.startsWith('/model-gateway/catalog/'))return route.fulfill({json:{items:[],total:0}});
    return route.fulfill({json:[]});
  });
  await page.goto('/#/agents/agent-1/config');
  await page.getByLabel('所属行业').selectOption('retail');
  await page.getByLabel('用途标签').click();
  await page.getByRole('option',{name:'售后支持',exact:true}).click();
  await page.getByLabel('Agent 名称').click();
  await page.getByRole('checkbox',{name:'案例数据'}).uncheck();
  await page.getByRole('button',{name:'保存草稿',exact:true}).click();
  await expect.poll(()=>saved?.industry).toBe('retail');
  expect(saved?.tags).toEqual(['knowledge','after_sales']);
  expect(saved?.is_example).toBe(false);
  await page.reload();
  await expect(page.getByLabel('所属行业')).toHaveValue('retail');
  await expect(page.getByRole('checkbox',{name:'案例数据'})).not.toBeChecked();
});
