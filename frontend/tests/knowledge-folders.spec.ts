import { test, expect } from '@playwright/test';

test('folder navigation supports hierarchy search and creation destinations', async ({page}) => {
    const folders = [{id: 'a', name: '产品知识', parent_id: null}, {id: 'b', name: '平台操作指南', parent_id: 'a'}, {id: 'c', name: '安装部署与系统维护常见问题说明文档目录', parent_id: 'b'}, {id: 'd', name: '客户服务', parent_id: null}];
    let submitted: Record<string, unknown> | undefined;
    await page.route('**/api/v1/**', async route => {
        const path = new URL(route.request().url()).pathname;
        if (path.endsWith('/me')) return route.fulfill({json: {role: 'admin', tenant: 'test'}});
        if (path.endsWith('/folders')) { if(route.request().method() === 'POST') submitted = route.request().postDataJSON(); return route.fulfill({json: route.request().method() === 'POST' ? {id: 'new'} : folders}); }
        if (path.endsWith('/documents')) return route.fulfill({json: {items: [], total: 0}});
        if (path.endsWith('/capabilities')) return route.fulfill({json: {local_formats: []}});
        return route.fulfill({json: []});
    });
    await page.goto('/#/knowledge');
    const navigation = page.getByRole('complementary', {name: '文档目录导航'});
    await expect(navigation.getByText('平台操作指南', {exact:true})).toBeVisible();
    await navigation.getByRole('textbox', {name:'搜索目录'}).fill('安装');
    await expect(navigation.getByText(folders[2].name, {exact:true})).toBeVisible();
    await expect(navigation.getByText('客户服务', {exact:true})).toHaveCount(0);
    await navigation.getByText(folders[2].name, {exact:true}).click();
    await expect(page.getByRole('navigation', {name:'当前目录路径'}).getByRole('button', {name: '平台操作指南', exact: true})).toBeVisible();
    await navigation.getByRole('textbox', {name:'搜索目录'}).fill('');
    await navigation.getByRole('button', {name:'新建根目录', exact:true}).click();
    let dialog = page.getByRole('dialog');
    await dialog.getByRole('textbox').fill('新目录');
    await dialog.getByRole('button', {name:/确.*定/}).click();
    await expect.poll(() => submitted?.parent_id).toBe(null);
    await navigation.getByRole('button', {name:/新建子目录/}).click();
    dialog = page.getByRole('dialog');
    await dialog.getByRole('textbox').fill('子目录');
    await dialog.getByRole('button', {name:/确.*定/}).click();
    await expect.poll(() => submitted?.parent_id).toBe('c');
    await page.screenshot({path:'test-results-folder-desktop.png', fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await page.screenshot({path:'test-results-folder-mobile.png', fullPage:true});
});
