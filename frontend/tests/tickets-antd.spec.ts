import { test, expect } from '@playwright/test';

for (const role of ['admin','viewer']) {
  test(`Ant Design ticket workspace: ${role}`, async ({page})=>{
    let ticket={id:'ticket-1',ticket_no:'TK-001',title:'设备无法启动',description:'客户反馈开机无响应',status:'in_progress',note:'已联系客户',revision:1,priority:3,updated_at:'2026-09-21T10:00:00Z'};
    let reject=true;
    await page.route('**/api/v1/**',async route=>{
      const path=new URL(route.request().url()).pathname;
      if(path.endsWith('/me')) return route.fulfill({json:{role,tenant:'测试企业'}});
      if(path.endsWith('/tickets/ticket-1')) {
        if(reject){reject=false;return route.fulfill({status:409,json:{detail:'ticket_revision_conflict'}});}
        ticket={...ticket,...route.request().postDataJSON(),revision:2};
        return route.fulfill({json:ticket});
      }
      if(path.endsWith('/tickets')) return route.fulfill({json:[ticket]});
      if(path.endsWith('/modules')) return route.fulfill({json:[]});
      return route.fulfill({json:{}});
    });
    await page.goto('/');
    await page.getByRole('navigation').getByRole('button',{name:'服务工单'}).click();
    await expect(page.getByText('仅筛选最近 100 条工单')).toBeVisible();
    await expect(page.getByText('工单中心',{exact:true})).toHaveCount(0);
    await page.getByRole('tab',{name:'待处理',exact:true}).click();
    await expect(page.getByText('没有符合条件的工单')).toBeVisible();
    await page.getByRole('tab',{name:'处理中',exact:true}).click();
    await page.getByLabel('搜索工单').fill('不存在');
    await expect(page.getByText('没有符合条件的工单')).toBeVisible();
    await page.getByLabel('搜索工单').fill('设备');
    await page.screenshot({path:`test-results/tickets-list-${role}.png`,fullPage:true});
    await page.getByRole('button',{name:'设备无法启动'}).click();
    await expect(page.getByText('客户反馈开机无响应')).toBeVisible();
    if(role==='viewer') {
      await expect(page.getByText('当前为只读角色')).toBeVisible();
      await expect(page.getByRole('button',{name:'解决工单'})).toHaveCount(0);
    } else {
      await page.getByRole('button',{name:'解决工单'}).click();
      await expect(page.getByText('请填写处理说明后再提交')).toBeVisible();
      await page.getByLabel('处理说明',{exact:true}).fill('已更换电源，恢复正常');
      await page.getByRole('button',{name:'解决工单'}).click();
      await expect(page.getByText('409 · ticket_revision_conflict')).toBeVisible();
      await expect(page.getByLabel('处理说明',{exact:true})).toHaveValue('已更换电源，恢复正常');
      await page.getByRole('button',{name:'解决工单'}).click();
      await expect(page.getByText('工单已更新')).toBeVisible();
      await expect(page.getByLabel('处理说明',{exact:true})).toHaveValue('');
    }
    await page.screenshot({path:`test-results/tickets-${role}.png`,fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await expect(page.getByText('工单详情',{exact:true})).toBeVisible();
    if(role==='admin'){await expect(page.getByRole('button',{name:'关闭工单'})).toBeInViewport();}
    await page.screenshot({path:`test-results/tickets-mobile-${role}.png`,fullPage:true});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBeTruthy();
  });
}
