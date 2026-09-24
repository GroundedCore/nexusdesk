import { t, dateTime } from '../../i18n/index';
import { useEffect, useState } from 'react';
import { Drawer, Pagination, Popconfirm } from 'antd';
import { api, patch, post, put } from '../../shared/api/client';
import { Alert, Empty, Field, Panel, useAction, useResource } from '../../shared/components/ui';
import type { ModelResource } from './ModelsPage';
const root = '/model-gateway';
export type ManagementSection = 'access_keys' | 'sensitive_words' | 'alert_rules' | 'quota';
type Spec = Record<string, unknown>;
interface ManagedResource {
    id: string;
    name: string;
    spec: Spec;
    revision: number;
    enabled: boolean;
    prefix?: string;
}
interface Page<T> {
    items: T[];
    total: number;
    page: number;
    page_size: number;
}
const names = () => ({ access_keys: t("访问密钥"), sensitive_words: t("敏感词库"), alert_rules: t("告警规则"), quota: t("配额管理") });
const defaultSpec = (section: ManagementSection): Spec => section === 'sensitive_words' ? { name: '', category: '其他' } : section === 'alert_rules' ? { name: '', metric: 'failure', profile_id: null, threshold_ms: 5000, cooldown_seconds: 60 } : { name: '', profile_ids: [], expires_at: new Date(Date.now() + 30 * 86400000).toISOString(), requests_per_minute: 60, requests_per_day: 10000, concurrency: 4 };
export function GatewayGovernance({ section, profiles }: {
    section: ManagementSection;
    profiles: ModelResource[];
}) {
    const [query, setQuery] = useState('');
    const [page, setPage] = useState(1);
    const [open, setOpen] = useState(false);
    const [selected, setSelected] = useState<ManagedResource | null>(null);
    const [spec, setSpec] = useState<Spec>(() => defaultSpec(section));
    const [secret, setSecret] = useState('');
    const [bulk, setBulk] = useState('');
    const [sample, setSample] = useState('');
    const [review, setReview] = useState<{
        blocked: boolean;
        matches: {
            category: string;
        }[];
    } | null>(null);
    const [showEvents, setShowEvents] = useState(false);
    const [eventPage, setEventPage] = useState(1);
    const [eventStatus, setEventStatus] = useState('all');
    const action = useAction();
    const list = useResource<Page<ManagedResource>>(section === 'quota' ? null : `${root}/manage/${section}?page=${page}&page_size=20&q=${encodeURIComponent(query)}`);
    const events = useResource<Page<{
        id: string;
        rule_name: string;
        metric: string;
        call_id: string;
        created_at: string;
        acknowledged: boolean;
        detail: {
            error_code: string;
            duration_ms: number;
        };
    }>>(section === 'alert_rules' && showEvents ? `${root}/alert-events?page=${eventPage}&page_size=20${eventStatus === 'all' ? '' : `&acknowledged=${eventStatus}`}` : null, 10000);
    const change = (key: string, value: unknown) => setSpec(old => ({ ...old, [key]: value }));
    if (section === 'quota')
        return <QuotaForm />;
    function edit(row?: ManagedResource) { setSelected(row || null); setSpec(row?.spec || defaultSpec(section)); setOpen(true); }
    return <>
    {!open && <Alert error={action.error || list.error || events.error} notice={action.notice}/>}
    <Panel title={names()[section]} action={section === 'alert_rules' ? <div className="actions"><button className={!showEvents ? '' : 'secondary'} onClick={() => setShowEvents(false)}>{t("规则管理")}</button><button className={showEvents ? '' : 'secondary'} onClick={() => setShowEvents(true)}>{t("告警事件")}</button></div> : undefined}>
      {showEvents && section === 'alert_rules' ? <>
        <p className="hint">{t("调用结束时按规则生成站内事件。输入输出拦截、失败和耗时阈值可分别配置；冷却期间不重复通知。")}</p>
        <Field label={t("事件状态")}><select value={eventStatus} onChange={e => { setEventStatus(e.target.value); setEventPage(1); }}><option value="all">{t("全部事件")}</option><option value="false">{t("待确认")}</option><option value="true">{t("已确认")}</option></select></Field>
        <div className="table-scroll"><table><thead><tr><th>{t("时间")}</th><th>{t("规则")}</th><th>{t("调用 ID")}</th><th>{t("详情")}</th><th>{t("状态")}</th><th>{t("操作")}</th></tr></thead><tbody>{events.data?.items.map(event => <tr key={event.id}><td>{dateTime(event.created_at)}</td><td>{event.rule_name}</td><td>{event.call_id}</td><td>{event.detail.error_code || `${event.detail.duration_ms} ms`}</td><td>{event.acknowledged ? t("已确认") : t("待确认")}</td><td><button className="text-button" disabled={event.acknowledged || action.busy} onClick={() => void action.run(async () => { await post(`${root}/alert-events/${event.id}/acknowledge`); events.refresh(); }, t("已确认告警"))}>{t("确认")}</button></td></tr>)}</tbody></table></div>
        {events.data?.total === 0 && <Empty>{t("暂无告警事件")}</Empty>}<Pagination current={eventPage} total={events.data?.total || 0} pageSize={20} showSizeChanger={false} onChange={setEventPage}/>
      </> : <>
        <div className="gateway-toolbar"><input aria-label={t("搜索管理资源")} placeholder={t("搜索名称或配置")} value={query} onChange={e => { setQuery(e.target.value); setPage(1); }}/><button onClick={() => edit()}>{t("新增")}{section === 'sensitive_words' ? t("敏感词") : names()[section]}</button></div>
        {section === 'access_keys' && <p className="hint">{t("密钥仅创建或轮换时展示一次。仅可调用所选方案，受有效期和配额约束。调用入口：POST /api/v1/model-gateway/external/invoke。")}</p>}
        {section === 'sensitive_words' && <p className="hint">{t("使用 Unicode 规范化和不区分大小写的包含匹配。需在模型的「审核与定价」中启用，才会拦截调用中的文本。")}</p>}
        <div className="table-scroll"><table><thead><tr><th>{t("名称")}</th><th>{section === 'sensitive_words' ? t("类别") : section === 'access_keys' ? t("前缀 / 到期时间") : t("触发条件")}</th><th>{t("状态")}</th><th>{t("操作")}</th></tr></thead><tbody>{list.data?.items.map(row => <tr key={row.id}><td>{row.name}</td><td>{section === 'sensitive_words' ? String(row.spec.category) : section === 'access_keys' ? `${row.prefix}… / ${dateTime(String(row.spec.expires_at))}` : `${({ failure: t("调用失败"), latency: t("耗时超限"), content_blocked: t("内容拦截") } as Record<string, string>)[String(row.spec.metric)]}${row.spec.metric === 'latency' ? ` ≥ ${row.spec.threshold_ms} ms` : ''}`}</td><td>{row.enabled ? t("已启用") : t("已停用")}</td><td><div className="actions"><button className="text-button" disabled={action.busy} onClick={() => edit(row)}>{t("编辑")}</button><button className="text-button" disabled={action.busy} onClick={() => void action.run(async () => { await patch(`${root}/manage/${section}/${row.id}`, { revision: row.revision, enabled: !row.enabled }); list.refresh(); }, t("状态已更新"))}>{row.enabled ? t("停用") : t("启用")}</button>{section === 'access_keys' && <Popconfirm title={t("轮换后旧密钥立即失效，继续？")} onConfirm={() => action.run(async () => { const response = await post<{
            secret: string;
        }>(`${root}/keys/${row.id}/rotate`, { revision: row.revision }); setSecret(response.secret); list.refresh(); }, t("密钥已轮换"))}><button className="text-button" disabled={action.busy}>{t("轮换")}</button></Popconfirm>}<Popconfirm title={t("移除此资源？历史记录将保留。")} onConfirm={() => action.run(async () => { await api(`${root}/resources/${section}/${row.id}?revision=${row.revision}`, { method: 'DELETE' }); list.refresh(); }, t("资源已移除"))}><button className="text-button" disabled={action.busy}>{t("删除")}</button></Popconfirm></div></td></tr>)}</tbody></table></div>
        {list.loading && <Empty>{t("正在加载\u2026")}</Empty>}{list.data?.total === 0 && <Empty>{t("暂无匹配记录")}</Empty>}<Pagination current={page} total={list.data?.total || 0} pageSize={20} showSizeChanger={false} onChange={setPage}/>
        {section === 'sensitive_words' && <div className="split gateway-extra"><div><h3>{t("批量导入")}</h3><Field label={t("敏感词列表")}><textarea rows={4} value={bulk} placeholder={t("每行一个敏感词，可用制表符分隔类别")} onChange={e => setBulk(e.target.value)}/></Field><button disabled={!bulk.trim() || action.busy} onClick={() => void action.run(async () => { const words = bulk.split('\n').filter(line => line.trim()).map(line => { const [name, category = t("其他")] = line.split('\t'); return { name: name.trim(), category: category.trim() }; }); const result = await post<{
            imported: number;
            skipped: number;
        }>(`${root}/words/import`, { words }); setBulk(''); list.refresh(); setReview(null); if (result.skipped)
            setBulk(''); }, t("导入完成，重复词已跳过"))}>{t("导入敏感词")}</button></div><div><h3>{t("效果测试")}</h3><Field label={t("待检测文本")}><textarea rows={4} value={sample} onChange={e => setSample(e.target.value)}/></Field><button disabled={action.busy || !sample} onClick={() => void action.run(async () => setReview(await post(`${root}/words/test`, { text: sample })), t("检测完成"))}>{t("检测文本")}</button>{review && <p role="status">{review.blocked ? t("命中 {{v0}} 项：{{v1}}", { v0: review.matches.length, v1: review.matches.map(item => item.category).join('、') }) : t("未命中敏感词")}</p>}</div></div>}
      </>}
    </Panel>
    <Drawer title={`${selected ? t("编辑") : t("新增")}${names()[section]}`} open={open} onClose={() => setOpen(false)} size={560} destroyOnHidden className="gateway-editor">
      <Alert error={action.error} notice={action.notice}/>
      <form onSubmit={e => { e.preventDefault(); void action.run(async () => { const saved = selected ? await put<ManagedResource>(`${root}/manage/${section}/${selected.id}?revision=${selected.revision}`, spec) : await post<ManagedResource & {
        secret?: string;
    }>(`${root}/manage/${section}`, spec); if ('secret' in saved && typeof saved.secret === 'string')
        setSecret(saved.secret); setOpen(false); list.refresh(); }, t("已保存")); }}><fieldset disabled={action.busy}>
        <Field label={section === 'sensitive_words' ? t("敏感词") : t("名称")}><input required maxLength={100} value={String(spec.name || '')} onChange={e => change('name', e.target.value)}/></Field>
        {section === 'sensitive_words' && <Field label={t("敏感词类别")}><input required maxLength={100} value={String(spec.category || '')} onChange={e => change('category', e.target.value)}/></Field>}
        {section === 'access_keys' && <><Field label={t("允许调用的方案")}><select multiple required value={(spec.profile_ids || []) as string[]} onChange={e => change('profile_ids', Array.from(e.target.selectedOptions, option => option.value))}>{profiles.filter(p => p.enabled).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></Field><Field label={t("到期时间（ISO 8601，含时区）")}><input required value={String(spec.expires_at)} onChange={e => change('expires_at', e.target.value)} placeholder="2026-12-31T23:59:59+08:00"/></Field>{[['requests_per_minute', t("每分钟请求上限")], ['requests_per_day', t("每日请求上限")], ['concurrency', t("并发上限")]].map(([key, label]) => <Field key={key} label={label}><input type="number" required min={1} value={Number(spec[key])} onChange={e => change(key, Number(e.target.value))}/></Field>)}</>}
        {section === 'alert_rules' && <><Field label={t("触发条件")}><select value={String(spec.metric)} onChange={e => change('metric', e.target.value)}><option value="failure">{t("调用失败")}</option><option value="latency">{t("耗时超限")}</option><option value="content_blocked">{t("内容拦截")}</option></select></Field><Field label={t("监控方案")}><select value={String(spec.profile_id || '')} onChange={e => change('profile_id', e.target.value || null)}><option value="">{t("全部方案")}</option>{profiles.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></Field>{spec.metric === 'latency' && <Field label={t("耗时阈值（毫秒）")}><input type="number" min={1} max={600000} value={Number(spec.threshold_ms)} onChange={e => change('threshold_ms', Number(e.target.value))}/></Field>}<Field label={t("冷却时间（秒）")}><input type="number" min={0} max={86400} value={Number(spec.cooldown_seconds)} onChange={e => change('cooldown_seconds', Number(e.target.value))}/></Field></>}
        <button>{t("保存")}</button>
      </fieldset></form>
    </Drawer>
    <Drawer title={t("保存新密钥")} open={!!secret} onClose={() => setSecret('')} size={560}><p>{t("密钥仅本次显示。关闭后不能再次查看，请保存到安全位置。")}</p><Field label={t("新访问密钥")}><textarea readOnly value={secret} rows={3}/></Field><button onClick={() => void action.run(() => navigator.clipboard.writeText(secret), t("已复制密钥"))}>{t("复制密钥")}</button><Alert error={action.error} notice={action.notice}/></Drawer>
  </>;
}
function QuotaForm() {
    const resource = useResource<{
        revision: number;
        spec: Record<string, number>;
    }>(`${root}/quota`);
    const [draft, setDraft] = useState<Record<string, number>>({});
    const action = useAction();
    useEffect(() => { if (resource.data)
        setDraft(resource.data.spec); }, [resource.data]);
    return <Panel title={t("配额管理")}><Alert error={action.error || resource.error} notice={action.notice}/><p className="hint">{t("租户内所有方案和进程共享配额；访问密钥同时受自身配额限制。每日和每分钟计数按 UTC 自然日、自然分钟重置。运行中的请求占用并发槽，结束后释放。")}</p><form onSubmit={e => { e.preventDefault(); void action.run(async () => { await put(`${root}/quota`, { revision: resource.data?.revision ?? 0, spec: draft }); resource.refresh(); }, t("配额已更新")); }}><fieldset disabled={action.busy || !resource.data}>{[['requests_per_minute', t("每分钟请求上限"), 1000000], ['requests_per_day', t("每日请求上限"), 100000000], ['concurrency', t("并发上限"), 10000]].map(([key, label, max]) => <Field key={key} label={String(label)}><input type="number" required min={1} max={Number(max)} value={draft[key] ?? ''} onChange={e => setDraft(old => ({ ...old, [key]: Number(e.target.value) }))}/></Field>)}<button>{t("保存配额")}</button></fieldset></form></Panel>;
}
interface Policy {
    input_review: string;
    output_review: string;
    pricing: Record<string, string | number | null>;
}
export function ModelPolicyForm({ modelId, admin }: {
    modelId: string;
    admin: boolean;
}) {
    const resource = useResource<{
        revision: number;
        spec: Policy;
    }>(`${root}/models/${modelId}/settings`);
    const action = useAction();
    const [draft, setDraft] = useState<Policy | null>(null);
    useEffect(() => setDraft(resource.data?.spec || null), [resource.data]);
    return <section className="gateway-extra"><h3>{t("审核与定价")}</h3><Alert error={action.error || resource.error} notice={action.notice}/><p className="hint">{t("文本敏感词规则即时生效。启用输出审核时，流式内容先完整审核后返回。价格单位为人民币，留空表示未知；费用按每次尝试的返回用量估算。")}</p>{draft && <form onSubmit={e => { e.preventDefault(); void action.run(async () => { await put(`${root}/models/${modelId}/settings`, { revision: resource.data!.revision, spec: draft }); resource.refresh(); }, t("审核与定价已保存")); }}><fieldset disabled={!admin || action.busy}>{(['input_review', 'output_review'] as const).map(key => <Field key={key} label={key === 'input_review' ? t("输入文本审核") : t("输出文本审核")}><select value={draft[key]} onChange={e => setDraft({ ...draft, [key]: e.target.value })}><option value="off">{t("关闭")}</option><option value="sensitive_words">{t("敏感词拦截")}</option></select></Field>)}{[['input_per_million', t("输入价格（元 / 百万 Token）")], ['output_per_million', t("输出价格（元 / 百万 Token）")], ['audio_per_second', t("音频价格（元 / 秒）")], ['per_thousand_characters', t("文本价格（元 / 千字符）")], ['per_page', t("识别价格（元 / 页）")]].map(([key, label]) => <Field key={key} label={label}><input type="number" min={0} step="any" value={draft.pricing[key] ?? ''} onChange={e => setDraft({ ...draft, pricing: { ...draft.pricing, [key]: e.target.value === '' ? null : e.target.value } })}/></Field>)}<button>{t("保存审核与定价")}</button></fieldset></form>}</section>;
}
