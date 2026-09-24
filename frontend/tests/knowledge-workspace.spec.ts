import { test, expect } from '@playwright/test';

for (const role of ['admin', 'viewer']) {
    test(`knowledge workspace ${role}`, async ({ page }) => {
        test.setTimeout(120000);
        let saved = false; let previews = 0; let publishes = 0; let previewState = 'running'; let previewPolicy = {};
        const doc = { id: 'doc-1', title: '售后政策.docx', filename: '售后政策.docx', file_size: 2048, folder_id: null, processing: 'ready', current_version: 1, library_count: 1, chunk_count: 45, published_library_count: 1, pending_library_count: 1, enabled: true, updated_at: '2026-09-22T08:00:00Z' };
        await page.route('**/api/v1/**', async route => {
            const path = new URL(route.request().url()).pathname;
            if (path.endsWith('/me')) return route.fulfill({ json: { role, tenant: '测试企业' } });
            if (path.endsWith('/model-gateway/profiles')) return route.fulfill({ json: [{ id: 'embedding', name: 'Test Embedding', enabled: true, published_version: 1, spec: { operation: 'embed' } }] });
            if (path.endsWith('/knowledge-bases')) return route.fulfill({ json: [{ id: 'kb-1', name: '售后知识库', enabled: true, document_count: 1 }] });
            if (path.endsWith('/documents/doc-1/tasks')) return route.fulfill({ json: previews ? [{ id: 'preview-1', kind: 'preview', status: previewState, progress: {stage: 'semantic_embedding', completed: 16, total: 32} }] : [] });
            if (path.endsWith('/tasks/preview-1/cancel')) { previewState = 'cancelled'; return route.fulfill({json: {ok: true}}); }
            if (path.endsWith('/tasks/preview-1/result')) return route.fulfill({json: {total: 1, policy: previewPolicy, chunks: [{content: '重新分片结果', enabled: true}]}});
            if (path.endsWith('/folders') || path.endsWith('/tasks')) return route.fulfill({ json: [] });
            if (path.endsWith('/capabilities')) return route.fulfill({ json: { local_formats: ['.docx', '.pdf'], max_bytes: 20000000, external_parser: false } });
            if (path.endsWith('/documents/doc-1/preview-tasks')) { previews++; previewState = previews === 1 ? 'running' : 'completed'; const body = route.request().postDataJSON(); previewPolicy = body; if (body.strategy === 'semantic') { expect(body.semantic_profile_id).toBe('embedding'); expect(body.semantic_profile_version).toBe(1); } return route.fulfill({json: {task_id: 'preview-1', status: previewState}}); }
            if (path.endsWith('/libraries/kb-1/publish')) { publishes++; return route.fulfill({ json: { task_id: 'index-1', status: 'queued' } }); }
            if (path.endsWith('/documents/doc-1/draft')) {
                const data = route.request().postDataJSON();
                expect(data.revision).toBe(1);
                expect(data.offset).toBe(0); expect(data.replace_count).toBe(20); expect(data.chunks).toHaveLength(20);
                expect(data.chunks[0].content).toContain('补充说明');
                expect(data.chunks[0]).not.toHaveProperty('id');
                saved = true;
                return route.fulfill({ json: { id: 'doc-1', version: 2 } });
            }
            if (path.endsWith('/documents/doc-1')) {
                const offset = Number(new URL(route.request().url()).searchParams.get('offset') || 0);
                return route.fulfill({ json: { ...doc, total: 45, current_version: saved ? 2 : 1,
                    chunks: Array.from({ length: Math.min(20, 45 - offset) }, (_, i) => ({ id: `chunk-${offset + i}`, ordinal: offset + i, content: offset + i === 0 ? (saved ? '七天内可申请退款。补充说明' : '七天内可申请退款。') : `其他条款 ${offset + i + 1}`, enabled: true, source: { block_num: 1 } })),
                    version: { chunking: { strategy: 'hybrid', size: 800, overlap: 100 } }, libraries: [{kb_id: 'kb-1', name: '售后知识库', draft_version: 1, published_version: 1, enabled: true}] } });
            }
            if (path.endsWith('/documents')) return route.fulfill({ json: { items: [doc], total: 1 } });
            if (path.endsWith('/libraries/kb-1')) return route.fulfill({ json: { id: 'kb-1', revision: 1, name: '售后知识库', settings: { embedding: { id: 'embedding', version: 1 } }, documents: [{ document_id: 'doc-1', title: doc.title, current_version: 1, draft_version: 1, published_version: 1, chunk_count: 45 }], tasks: publishes ? [{id: 'index-1', status: 'queued'}] : [] } });
            if (path.endsWith('/search')) return route.fulfill({ json: { items: [{ chunk_id: 'c-1', title: '售后政策', version: 1, excerpt: '七天内可申请退款。', score: 0.03, channels: ['keyword', 'vector'], source: { block_num: 1 } }], total_ms: 36, timings: {}, context_chars: 10 } });
            return route.fulfill({ json: {} });
        });
        await page.goto('/#/knowledge');
        await expect(page.getByRole('tab', { name: '文档中心', exact: true })).toBeVisible({ timeout: 20000 });
        await expect(page.getByRole('button', { name: '售后政策.docx', exact: true })).toBeVisible();
        await page.screenshot({ path: `C:/Users/youdk/Documents/Codex/2026-09-20/wo-y/work/knowledge-ui/knowledge-documents-${role}.png`, fullPage: true });
        await page.getByRole('button', { name: '分片工作台', exact: true }).click();
        await expect(page.locator('.knowledge-editor .ant-card')).toHaveCount(20);
        await page.locator('.knowledge-editor .ant-pagination-item-2').first().click();
        await expect(page.getByText('分片 21 · 7 字符', { exact: true })).toBeVisible();
        await expect(page.locator('.knowledge-editor .ant-card')).toHaveCount(20);
        await page.locator('.knowledge-editor .ant-pagination-item-1').first().click();
        await expect(page.locator('.knowledge-document-page')).toBeVisible();
        await expect(page.getByRole('tab', {name: '文档中心', exact: true})).toHaveCount(0);
        const editor = page.getByRole('textbox').filter({ hasText: '七天内可申请退款。' });
        if (role === 'admin') {
            await page.getByRole('button', { name: '重新分片（生成预览）', exact: true }).click();
            await expect(page.getByRole('dialog', {name: '已有分片，确认重新生成？', exact: true})).toBeVisible();
            expect(previews).toBe(0);
            await page.getByRole('button', {name: /取\s*消/}).last().click();
            expect(previews).toBe(0);
            await editor.fill('七天内可申请退款。补充说明');
            await page.locator('.knowledge-editor .ant-pagination-item-2').first().click();
            await expect(page.getByText('请先保存本页修改，再翻页')).toBeVisible();
            await expect(editor).toHaveValue('七天内可申请退款。补充说明');
            await page.getByRole('button', { name: '保存分片草稿', exact: true }).click();
            await expect(page.getByText('分片草稿已保存，请在知识库更新版本并发布')).toBeVisible();
            expect(saved).toBe(true);
            await expect(page.getByText('已保存草稿 v2', {exact: true})).toBeVisible();
            await page.getByRole('button', {name: '前往知识库发布', exact: true}).click();
            await expect(page.getByText('文档最新为 v2，请先更新库内草稿。', {exact: true})).toBeVisible();
            await page.getByRole('tab', {name: '分片加工', exact: true}).click();
        } else {
            await expect(page.getByRole('button', { name: '保存分片草稿', exact: true })).toHaveCount(0);
            await expect(editor).toHaveAttribute('readonly', '');
        }
        await page.screenshot({ path: `C:/Users/youdk/Documents/Codex/2026-09-20/wo-y/work/knowledge-ui/knowledge-chunks-${role}.png`, fullPage: false });
        await page.setViewportSize({width: 390, height: 844});
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
        await page.screenshot({path: `C:/Users/youdk/Documents/Codex/2026-09-20/wo-y/work/knowledge-ui/knowledge-workbench-mobile-${role}.png`, fullPage: false});
        await page.setViewportSize({width: 1440, height: 1000});
        await page.getByRole('button', {name: '返回列表', exact: true}).click();
        if (role === 'admin') {
            await page.getByRole('tab', {name: '知识库', exact: true}).click();
            await page.screenshot({path: 'test-results-knowledge-library.png', fullPage: true});
            await page.getByRole('button', {name: '重新生成向量并发布', exact: true}).click();
            await expect(page.getByRole('dialog', {name: '已有发布版本，确认重新构建？', exact: true})).toBeVisible();
            expect(publishes).toBe(0);
            await page.getByRole('button', {name: /取\s*消/}).last().click();
            expect(publishes).toBe(0);
            await page.getByRole('button', {name: '重新生成向量并发布', exact: true}).click();
            await page.getByRole('button', {name: '确认生成向量并发布', exact: true}).click();
            await expect(page.getByRole('button', {name: /正在构建索引/})).toBeDisabled();
            expect(publishes).toBe(1);
        }
        await page.getByRole('tab', {name: '处理任务', exact: true}).click();
        await page.screenshot({path: `test-results-knowledge-tasks-${role}.png`, fullPage:true});
        await page.getByRole('tab', { name: '检索测试', exact: true }).click();
        await page.getByPlaceholder('输入一个真实的客户问题').fill('退款');
        await page.getByRole('button', { name: '测试检索', exact: true }).click();
        await expect(page.getByText('七天内可申请退款。', { exact: true })).toBeVisible();
        await page.setViewportSize({ width: 390, height: 844 });
        await page.screenshot({ path: `C:/Users/youdk/Documents/Codex/2026-09-20/wo-y/work/knowledge-ui/knowledge-search-mobile-${role}.png`, fullPage: true });
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
        if (role === 'admin') {
            await page.setViewportSize({width: 1440, height: 1000});
            await page.getByRole('tab', {name: '文档中心', exact: true}).click();
            await page.getByRole('button', {name: '分片工作台', exact: true}).click();
            await page.locator('.knowledge-editor label').filter({hasText: '分片规则'}).getByRole('combobox').click();
            await page.getByText('语义分片（Embedding）', {exact: true}).click();
            await page.locator('.knowledge-editor label').filter({hasText: '语义分析模型（固定发布版本）'}).getByRole('combobox').click();
            await page.getByText('Test Embedding · v1', {exact: true}).click();
            await page.getByRole('button', {name: '重新分片（生成预览）', exact: true}).click();
            await expect(page.getByText(/本次语义分析会调用所选 Embedding 模型/)).toBeVisible();
            await page.getByRole('button', {name: '确认重新生成', exact: true}).click();
            await expect(page.getByText('已分析语义单元 · 16 / 32', {exact: true})).toBeVisible();
            await expect(page.getByRole('button', {name: '重新分片（生成预览）', exact: true})).toBeDisabled();
            await page.getByRole('button', {name: '刷新进度', exact: true}).click();
            await page.getByRole('button', {name: '取消分片', exact: true}).click();
            await expect(page.getByText('已取消', {exact: true})).toBeVisible();
            await expect(page.locator('.knowledge-editor .ant-card')).toHaveCount(20);
            await page.getByRole('button', {name: '重新分片（生成预览）', exact: true}).click();
            await page.getByRole('button', {name: '确认重新生成', exact: true}).click();
            await page.getByRole('button', {name: '查看预览', exact: true}).click();
            await expect(page.locator('.knowledge-editor textarea').filter({hasText: '重新分片结果'})).toBeVisible();
            expect(previews).toBe(2);
        }

    });
}


test('search blocks missing vector index and offers keyword retrieval', async ({page}) => {
    let calls = 0;
    await page.route('**/api/v1/**', async route => {
        const path = new URL(route.request().url()).pathname;
        if (path.endsWith('/me')) return route.fulfill({json: {role: 'admin', tenant: 'test'}});
        if (path.endsWith('/knowledge-bases')) return route.fulfill({json: [{id: 'kb', name: '测试库', enabled: true}]});
        if (path.endsWith('/libraries/kb')) return route.fulfill({json: {id: 'kb', name: '测试库', settings: {}, published_settings: {}, vector_ready: false, published_document_count: 1, documents: [], tasks: []}});
        if (path.endsWith('/search')) { calls++; return route.fulfill({json: {items: [], timings: {}, total_ms: 1, context_chars: 0}}); }
        if (path.endsWith('/documents')) return route.fulfill({json: {items: [], total: 0}});
        if (path.endsWith('/capabilities')) return route.fulfill({json: {local_formats: [], max_bytes: 20000000}});
        return route.fulfill({json: []});
    });
    await page.goto('/#/knowledge');
    await page.getByRole('tab', {name: '检索测试', exact: true}).click();
    await page.getByPlaceholder('输入一个真实的客户问题').fill('退款');
    await page.getByRole('combobox').nth(1).click();
    await page.getByText('向量', {exact: true}).click();
    await expect(page.getByRole('button', {name: '测试检索', exact: true})).toBeDisabled();
    await expect(page.getByText('知识库尚未就绪', {exact: true})).toBeVisible();
    expect(calls).toBe(0);
    await page.getByRole('combobox').nth(1).click();
    await page.getByText('关键词', {exact: true}).click();
    await expect(page.getByRole('button', {name: '测试检索', exact: true})).toBeEnabled();
    await page.getByRole('button', {name: '测试检索', exact: true}).click();
    await expect(page.getByText('没有符合当前检索条件的已发布知识，可尝试降低分数阈值')).toBeVisible();
    expect(calls).toBe(1);
});


test('retrieval threshold controls send raw score filters', async ({page}) => {
    let submitted: Record<string, unknown> | undefined;
    await page.route('**/api/v1/**', async route => {
        const path = new URL(route.request().url()).pathname;
        if (path.endsWith('/me')) return route.fulfill({json: {role: 'admin', tenant: 'test'}});
        if (path.endsWith('/knowledge-bases')) return route.fulfill({json: [{id: 'kb', name: 'Threshold test'}]});
        if (path.endsWith('/libraries/kb')) return route.fulfill({json: {id: 'kb', name: 'Threshold test', settings: {}, published_settings: {mode: 'hybrid', rerank: {id: 'r', version: 1}}, vector_ready: true, published_document_count: 1, documents: [], tasks: []}});
        if (path.endsWith('/search')) { submitted = route.request().postDataJSON(); return route.fulfill({json: {items: [], total_ms: 1, context_chars: 0}}); }
        if (path.endsWith('/documents')) return route.fulfill({json: {items: [], total: 0}});
        if (path.endsWith('/capabilities')) return route.fulfill({json: {local_formats: []}});
        return route.fulfill({json: []});
    });
    await page.goto('/#/knowledge');
    await page.getByRole('tab', {name: '检索测试', exact: true}).click();
    await page.getByRole('spinbutton', {name: '最低向量相似度', exact: true}).fill('0.6');
    await page.getByRole('spinbutton', {name: '最低重排分数', exact: true}).fill('0.3');
    await page.getByPlaceholder('输入一个真实的客户问题').fill('部署');
    await page.getByRole('button', {name: '测试检索', exact: true}).click();
    await expect.poll(() => submitted?.min_vector_score).toBe(0.6);
    expect(submitted?.min_rerank_score).toBe(0.3);
    await page.getByRole('spinbutton', {name: '最低向量相似度', exact: true}).fill('');
    await page.getByRole('spinbutton', {name: '最低重排分数', exact: true}).fill('');
    await page.getByRole('button', {name: '测试检索', exact: true}).click();
    await expect.poll(() => submitted?.min_vector_score).toBe(null);
    expect(submitted?.min_rerank_score).toBe(null);
    await page.screenshot({path:'test-results-knowledge-search.png', fullPage:true});
    await page.getByRole('tab', {name: '知识库', exact: true}).click();
    await page.getByRole('button', {name: /设\s*置/, exact: true}).click();
    await expect(page.getByRole('dialog', {name: '知识库设置'})).toBeVisible();
    await page.screenshot({path:'test-results-knowledge-settings.png', fullPage:true});
    await page.getByRole('dialog', {name: '知识库设置'}).getByRole('button', {name: /取\s*消/}).click();

});
