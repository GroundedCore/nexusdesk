import { test, expect, type Page } from '@playwright/test';

async function switchLanguage(page:Page, scope:string, name:string) {
 await page.locator(scope+' .language-switch').click();
 await page.getByRole('menuitem').filter({hasText:name}).click();
}

test('language switch preserves draft, reply policy and hash; preference survives refresh', async ({page})=>{
  let saved:any;
  let agent={id:'i18n-agent',name:'保存',description:'用户输入保持原文',industry:'gaming',tags:['knowledge'],is_example:true,draft:{system_prompt:'中文原始指令',tool_names:[],knowledge_base_ids:[],max_model_rounds:6,reply_language:'auto'},draft_revision:1,published_version:null,archived:false};
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/v1/**',async route=>{
    const req=route.request(),path=new URL(req.url()).pathname.replace('/api/v1','');
    if(path==='/me')return route.fulfill({json:{role:'admin',tenant:'test'}});
    if(path==='/agents/i18n-agent'){
      if(req.method()==='PUT'){saved=req.postDataJSON();agent={...agent,name:saved.name,draft:saved.config,draft_revision:2};}
      return route.fulfill({json:agent});
    }
    if(path.startsWith('/model-gateway/catalog/'))return route.fulfill({json:{items:[],total:0}});
    return route.fulfill({json:[]});
  });
  await page.goto('/#/agents/i18n-agent/config');
  await page.getByLabel('Agent 名称',{exact:true}).fill('未保存的用户名称');
  await switchLanguage(page, '.agent-topbar', 'English');
  await expect(page.getByLabel('Agent name',{exact:true})).toHaveValue('未保存的用户名称');
  await expect(page.getByLabel('Industry',{exact:true})).toHaveValue('gaming');
  await expect(page).toHaveURL(/#\/agents\/i18n-agent\/config$/);
  await page.getByLabel('Response language',{exact:true}).selectOption('zh-TW');
  await page.getByRole('button',{name:'Save draft',exact:true}).click();
  await expect.poll(()=>saved?.config.reply_language).toBe('zh-TW');
  expect(saved.industry).toBe('gaming');expect(saved.tags).toEqual(['knowledge']);
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('lang','en');
  await expect(page.getByLabel('Agent name',{exact:true})).toHaveValue('未保存的用户名称');
  await switchLanguage(page, '.agent-topbar', '繁體中文');
  await expect(page.getByRole('button',{name:'保存草稿',exact:true})).toBeVisible();
  await page.setViewportSize({width:390,height:844});
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  await expect(page.getByRole('button',{name:/發\s*佈/,exact:true})).toBeInViewport();
  const publishBox=await page.getByRole('button',{name:/發\s*佈/,exact:true}).boundingBox();expect(publishBox!.x+publishBox!.width).toBeLessThanOrEqual(390);
  await page.screenshot({path:'test-results/i18n-workspace-traditional-mobile.png',fullPage:true});
  expect(errors).toEqual([]);
});

for(const [browserLanguage,expected] of [['en-US','en'],['zh-HK','zh-TW'],['zh-CN','zh-CN'],['hi-IN','hi'],['fr-FR','zh-CN']]){
 test(`initial language follows browser: ${browserLanguage}`,async({browser})=>{
  const context=await browser.newContext({locale:browserLanguage,baseURL:process.env.E2E_BASE_URL || 'http://127.0.0.1:5173'});const page=await context.newPage();
  await page.goto('/#/agents');await expect(page.locator('html')).toHaveAttribute('lang',expected);
  await context.close();
 });
}

test('live filters retain stable codes across languages and refresh',async({page})=>{
 await page.goto('/#/agents?industry=gaming&tag=after_sales');
 await expect(page.locator('.agent-card')).toHaveCount(1);
 await switchLanguage(page, '.console-header', 'English');
 await expect(page.getByRole('button',{name:'Industry：Gaming',exact:true})).toHaveAttribute('aria-pressed','true');
 await expect(page.locator('.agent-card')).toHaveCount(1);
 await page.reload();await expect(page.locator('.console-header .language-switch')).toContainText('English');
 await page.screenshot({path:'test-results/i18n-agents-english.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});
 await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
});

for (const language of ['en','hi']) test(`${language} pages render without runtime errors`,async({page})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(lang=>localStorage.setItem('agent-platform-language',lang),language);
 for(const route of ['overview','conversations','agents','knowledge','tools','handoff','tickets','channels','evaluation','observability','access','models/models','models/connections','models/profiles','models/playground','models/logs','models/monitor','models/access_keys','models/sensitive_words','models/alert_rules','models/quota']){
   await page.goto('/#/'+route);
   await expect(page.locator('.console-header h1')).toBeVisible();
   await expect(page.locator('.console-header h1')).not.toContainText(/[\u4e00-\u9fff]/);
   await expect(page.locator('html')).toHaveAttribute('lang',language);
 }
 expect(errors).toEqual([]);
});

test('Hindi filters preserve codes, refresh preference and fit mobile',async({page})=>{
 await page.goto('/#/agents?industry=gaming&tag=after_sales');
 await expect(page.locator('.agent-card')).toHaveCount(1);
 await switchLanguage(page, '.console-header', 'हिन्दी');
 await expect(page.locator('.console-header h1')).toHaveText('एजेंट');
 await expect(page.getByRole('button',{name:'उद्योग：गेमिंग',exact:true})).toHaveAttribute('aria-pressed','true');
 await expect(page.locator('.agent-card')).toHaveCount(1);
 await expect(page.locator('.agent-card')).toContainText('玩家客服助手');
 await page.reload();
 await expect(page.locator('html')).toHaveAttribute('lang','hi');
 await expect(page).toHaveURL(/industry=gaming&tag=after_sales$/);
 await expect(page.locator('.agent-pagination')).toContainText('कुल: 1');
 await page.screenshot({path:'test-results/i18n-hindi-list.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});
 await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
 await page.screenshot({path:'test-results/i18n-hindi-mobile.png',fullPage:true});
});

test('Hindi workspace preserves unsaved content and saves independent reply language',async({page})=>{
 let saved:any;
 let agent={id:'hindi-agent',name:'用户原名',description:'原文',industry:'gaming',tags:['knowledge'],is_example:true,draft:{system_prompt:'用户原始指令',tool_names:[],knowledge_base_ids:[],max_model_rounds:6,reply_language:'auto'},draft_revision:1,published_version:null,archived:false};
 await page.route('**/api/v1/**',async route=>{
  const req=route.request(),path=new URL(req.url()).pathname.replace('/api/v1','');
  if(path==='/me')return route.fulfill({json:{role:'admin',tenant:'test'}});
  if(path==='/agents/hindi-agent'){
   if(req.method()==='PUT'){saved=req.postDataJSON();agent={...agent,name:saved.name,draft:saved.config,draft_revision:2};}
   return route.fulfill({json:agent});
  }
  if(path.startsWith('/model-gateway/catalog/'))return route.fulfill({json:{items:[],total:0}});
  return route.fulfill({json:[]});
 });
 await page.goto('/#/agents/hindi-agent/config');
 await page.getByLabel('Agent 名称',{exact:true}).fill('未保存的名字');
 await switchLanguage(page, '.agent-topbar', 'हिन्दी');
 await expect(page.getByLabel('एजेंट का नाम',{exact:true})).toHaveValue('未保存的名字');
 await page.getByLabel('उत्तर की भाषा',{exact:true}).selectOption('hi');
 await page.getByRole('button',{name:'ड्राफ़्ट सहेजें',exact:true}).click();
 await expect.poll(()=>saved?.config.reply_language).toBe('hi');
 expect(saved.config.system_prompt).toBe('用户原始指令');
 await page.reload();
 await expect(page.getByLabel('उत्तर की भाषा',{exact:true})).toHaveValue('hi');
 await page.screenshot({path:'test-results/i18n-hindi-workspace.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});
 const publish=page.getByRole('button',{name:'प्रकाशित करें',exact:true});
 await expect(publish).toBeInViewport();const box=await publish.boundingBox();expect(box!.x+box!.width).toBeLessThanOrEqual(390);
 await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
 await page.screenshot({path:'test-results/i18n-hindi-workspace-mobile.png',fullPage:true});
 await switchLanguage(page, '.agent-topbar', 'English');
 await expect(page.getByLabel('Response language',{exact:true})).toHaveValue('hi');
 await expect(page.getByLabel('Agent name',{exact:true})).toHaveValue('未保存的名字');
});
