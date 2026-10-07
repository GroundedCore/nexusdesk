import {test,expect} from '@playwright/test';

async function mock(page:any, options:{initialized?:boolean;providers?:boolean}={}) {
  const writes:any[]=[];let expired=false;
  await page.route('**/api/v1/**',async(route:any)=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname.replace('/api/v1','');
    const credential=req.headers().authorization;
    if(req.method()==='POST')writes.push({path,body:req.postDataJSON()});
    if(path==='/auth/local/status')return route.fulfill({json:{initialized:options.initialized!==false,development_access:options.initialized===false}});
    if(path==='/sso/providers')return route.fulfill({json:options.providers?[{id:'provider-1',name:'企业微信',kind:'wecom',platform_origin:url.origin}]:[]});
    if(path==='/me')return credential&&!expired?route.fulfill({json:{role:'admin',tenant:'local',actor:'enterprise:test'}}):route.fulfill({status:401,json:{detail:'invalid_local_session'}});
    if(path==='/auth/local/login')return req.postDataJSON().password==='correct-password'?route.fulfill({json:{access_token:'local_test',expires_in:3600,user:{name:'管理员',role:'admin'}}}):route.fulfill({status:401,json:{detail:'invalid_local_credentials'}});
    if(path==='/auth/local/logout'||path==='/auth/local/password')return route.fulfill({status:204});
    if(path==='/deployment')return route.fulfill({json:{quickstart:false}});
    if(path==='/policy')return route.fulfill({json:{tool_allowed_hosts:[]}});
    if(path==='/observability/summary')return route.fulfill({json:{}});
    return route.fulfill({json:[]});
  });
  return {writes,expire:()=>{expired=true;}};
}

async function signIn(page:any) {
  await page.getByLabel('账号',{exact:true}).fill('rescue.admin');
  await page.getByLabel('密码',{exact:true}).fill('correct-password');
  await page.getByRole('button',{name:'登录',exact:true}).click();
}

test('local login returns to requested route and logout revokes session',async({page})=>{
  const state=await mock(page);await page.goto('/#/access');
  await expect(page).toHaveURL(/#\/login$/);await signIn(page);
  await expect(page).toHaveURL(/#\/access$/);await expect(page.getByRole('heading',{name:'访问与策略',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'账户菜单'}).click();await page.getByRole('menuitem',{name:'退出登录'}).click();
  await page.getByRole('dialog').getByRole('button',{name:'退出登录',exact:true}).click();
  await expect(page.getByRole('heading',{name:'欢迎回来'})).toBeVisible();
  expect(state.writes.some(w=>w.path==='/auth/local/logout')).toBeTruthy();
  expect(await page.evaluate(()=>sessionStorage.getItem('agent-platform-token'))).toBeNull();
});

test('incorrect password remains on login, permits retry, and clears password',async({page})=>{
  await mock(page);await page.goto('/#/login');
  await page.getByLabel('账号',{exact:true}).fill('rescue.admin');await page.getByLabel('密码',{exact:true}).fill('wrong-password');
  await page.getByRole('button',{name:'登录',exact:true}).click();
  await expect(page.getByText('账号或密码不正确',{exact:true})).toBeVisible();
  await expect(page.getByLabel('密码',{exact:true})).toHaveValue('');
  expect(await page.evaluate(()=>sessionStorage.getItem('agent-platform-token'))).toBeNull();
  await signIn(page);await expect(page.getByRole('button',{name:'账户菜单'})).toBeVisible();
});

test('password change validates confirmation and returns to login',async({page})=>{
  const state=await mock(page);await page.goto('/#/login');await signIn(page);
  await page.getByRole('button',{name:'账户菜单'}).click();await page.getByRole('menuitem',{name:'修改密码'}).click();
  const modal=page.getByRole('dialog');await modal.getByLabel('原密码',{exact:true}).fill('correct-password');
  await modal.getByLabel('新密码',{exact:true}).fill('new-strong-password');await modal.getByLabel('确认新密码',{exact:true}).fill('not-matching');
  await modal.getByRole('button',{name:/确\s*定/,exact:true}).click();await expect(modal.getByText('两次输入的密码不一致')).toBeVisible();
  await modal.getByLabel('确认新密码',{exact:true}).fill('new-strong-password');await modal.getByRole('button',{name:/确\s*定/,exact:true}).click();
  await expect(page.getByRole('heading',{name:'欢迎回来'})).toBeVisible();
  expect(state.writes.find(w=>w.path==='/auth/local/password').body).toEqual({old_password:'correct-password',new_password:'new-strong-password'});
});

test('expired session redirects and remembers route',async({page})=>{
  const state=await mock(page);await page.goto('/#/login');await signIn(page);
  await expect(page.getByRole('button',{name:'账户菜单'})).toBeVisible();state.expire();
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
  await expect(page).toHaveURL(/#\/login$/);await expect(page.getByText('登录已过期，请重新登录')).toBeVisible();
  expect(await page.evaluate(()=>sessionStorage.getItem('agent-platform-token'))).toBeNull();
});

test('enterprise tab authenticates through trusted popup',async({page})=>{
  await mock(page,{providers:true});
  await page.context().route('**/api/v1/sso/start/**',route=>{const url=new URL(route.request().url());return route.fulfill({contentType:'text/html',body:`<script>window.opener.postMessage({type:'nexusdesk.sso',channel:${JSON.stringify(url.searchParams.get('channel'))},session:{access_token:'ssow_test',expires_in:3600}},location.origin);window.close();</script>`});});
  await page.goto('/#/login');await page.getByRole('tab',{name:'企业登录',exact:true}).click();
  await page.getByRole('button',{name:'企业微信',exact:true}).click();
  await expect(page.getByRole('button',{name:'账户菜单'})).toBeVisible();
  expect(await page.evaluate(()=>sessionStorage.getItem('agent-platform-token'))).toBe('ssow_test');
});

test('login layout fits desktop and mobile with initialization guidance',async({page},testInfo)=>{
  await mock(page,{initialized:false});await page.goto('/');
  await expect(page.getByText('本地管理员尚未初始化，请联系部署管理员完成设置。')).toBeVisible();
  await expect(page).toHaveTitle(/nexusdesk/);
  await page.screenshot({path:testInfo.outputPath('login-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBe(390);
  await expect(page.getByRole('button',{name:'登录',exact:true})).toBeVisible();
  await page.screenshot({path:testInfo.outputPath('login-mobile.png'),fullPage:true});
  await page.getByRole('tab',{name:'企业登录',exact:true}).click();await expect(page.getByText('暂未配置企业登录，请使用账号登录')).toBeVisible();
});

test('login layout keeps the primary action on screen across viewport sizes',async({page})=>{
  // The layout used to break on short viewports because every breakpoint keyed off
  // width only: 1024x600 overflowed by 149px and on 320x568 the login button sat
  // 172px below the fold. A fixed 380px spacer on the brand panel caused the rest.
  interface Size { width:number; height:number; label:string; scrolls?:boolean }
  const sizes:Size[]=[
    {width:1920,height:1080,label:'desktop-1080'},
    {width:1440,height:900,label:'desktop-900'},
    {width:1366,height:768,label:'laptop-768'},
    {width:1280,height:720,label:'laptop-720'},
    {width:1152,height:700,label:'laptop-700'},
    {width:1024,height:768,label:'tablet-landscape'},
    {width:1024,height:600,label:'tablet-short'},
    {width:834,height:1112,label:'tablet-portrait'},
    {width:768,height:1024,label:'tablet-tall'},
    {width:414,height:896,label:'phone-large'},
    {width:390,height:844,label:'phone'},
    {width:360,height:640,label:'phone-small'},
    {width:320,height:568,label:'phone-tiny'},
    // A stacked phone layout on a narrow window may scroll a little; only the
    // action staying visible is guaranteed there.
    {width:700,height:800,label:'narrow-window',scrolls:true},
  ];
  await mock(page,{initialized:true});
  for(const {width,height,label,scrolls} of sizes){
    await page.setViewportSize({width,height});
    await page.goto('/#/login');
    await expect(page.locator('.login-form-inner')).toBeVisible();
    const m=await page.evaluate(()=>{
      const submit=document.querySelector('.login-submit')?.getBoundingClientRect();
      return {hOverflow:document.documentElement.scrollWidth-window.innerWidth,
        vOverflow:document.documentElement.scrollHeight-window.innerHeight,
        submitBelowFold:submit?Math.round(submit.bottom-window.innerHeight):null};
    });
    expect(m.hOverflow,`${label}: horizontal overflow`).toBeLessThanOrEqual(0);
    expect(m.submitBelowFold,`${label}: login button below the fold`).toBeLessThan(0);
    if(!scrolls)expect(m.vOverflow,`${label}: vertical overflow`).toBeLessThanOrEqual(0);
  }
});

test('deployment administrator must change password before workspace access',async({page})=>{
  let mustChange=true;
  await page.route('**/api/v1/**',async route=>{
    const req=route.request(),path=new URL(req.url()).pathname.replace('/api/v1','');
    if(path==='/auth/local/status')return route.fulfill({json:{initialized:true,development_access:false}});
    if(path==='/sso/providers')return route.fulfill({json:[]});
    if(path==='/auth/local/login')return route.fulfill({json:{access_token:'local_default',must_change_password:mustChange}});
    if(path==='/me')return route.fulfill(mustChange?{status:403,json:{detail:'default_password_change_required'}}:{json:{role:'admin',tenant:'local'}});
    if(path==='/auth/local/password'){
      expect(req.postDataJSON()).toEqual({old_password:'nexusdesk',new_password:'new-personal-password'});mustChange=false;return route.fulfill({status:204});
    }
    if(path==='/observability/summary'||path==='/deployment')return route.fulfill({json:{}});
    return route.fulfill({json:[]});
  });
  await page.goto('/#/login');
  await page.getByLabel('账号',{exact:true}).fill('admin');await page.getByLabel('密码',{exact:true}).fill('nexusdesk');
  await page.getByRole('button',{name:'登录',exact:true}).click();
  await expect(page.getByText('首次登录，请修改初始密码',{exact:true})).toBeVisible();
  await page.evaluate(()=>location.hash='/tools');
  await expect(page.getByText('首次登录，请修改初始密码',{exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'账户菜单'})).toHaveCount(0);
  await page.getByLabel('原密码',{exact:true}).fill('nexusdesk');await page.getByLabel('新密码',{exact:true}).fill('new-personal-password');
  await page.getByLabel('确认新密码',{exact:true}).fill('new-personal-password');await page.getByRole('button',{name:'修改密码并重新登录'}).click();
  await expect(page.getByRole('heading',{name:'欢迎回来'})).toBeVisible();
  await page.getByLabel('账号',{exact:true}).fill('admin');await page.getByLabel('密码',{exact:true}).fill('new-personal-password');await page.getByRole('button',{name:'登录',exact:true}).click();
  await expect(page.getByRole('button',{name:'账户菜单'})).toBeVisible();
});
