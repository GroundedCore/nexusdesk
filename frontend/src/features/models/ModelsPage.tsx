import { t } from '../../i18n/index';
import { useEffect, useState } from 'react';
import { api, patch, post, put, token } from '../../shared/api/client';
import { Alert, Empty, Field, Panel, useAccess, useAction, useResource } from '../../shared/components/ui';
import { AppstoreOutlined, SearchOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { Alert as AntAlert, Drawer, Pagination, Popconfirm } from 'antd';
import { GatewayGovernance, ModelPolicyForm, type ManagementSection } from './GatewayGovernance';
import { GatewayReports } from './GatewayReports';
import { useCatalogOptions } from './useCatalogOptions';
import './models.css';
import { ProviderModelField } from './ProviderModelField';
export interface ModelResource {
    id: string;
    name: string;
    spec: Record<string, unknown>;
    revision: number;
    enabled: boolean;
    published_version: number | null;
    credential_configured?: boolean | null;
    credential_source?: 'stored' | 'environment' | 'none';
}
type Kind = 'connections' | 'models' | 'profiles';
type Workspace = Kind | 'playground' | 'logs' | 'monitor' | ManagementSection;
const root = '/model-gateway';
const templates = (): Record<Kind, object> => ({
    connections: { name: t('演示连接'), protocol: 'demo', base_url: '', credential_ref: null, concurrency: 8 },
    models: { name: t('演示 Chat'), connection_id: '', model_name: '', operations: ['chat'], tool_calling: true },
    profiles: { name: t('客服快速问答'), operation: 'chat', model_id: '', fallback_model_ids: [], timeout_seconds: 30, retries: 0, parameters: { temperature: null, max_tokens: null, thinking: null }, require_tools: true },
});
const examples = (): Record<string, object> => ({
    chat: { operation: 'chat', messages: [{ role: 'user', content: t('你好') }], tools: [] },
    embed: { operation: 'embed', inputs: [{ id: 'text-1', text: t('售后服务') }] },
    rerank: { operation: 'rerank', query: t('如何退货'), candidates: [{ id: 'doc-1', text: t('退货流程') }, { id: 'doc-2', text: t('物流查询') }], top_n: 2 },
    transcribe: { operation: 'transcribe', media_id: t('请先上传音频') },
    synthesize: { operation: 'synthesize', text: t('您好，请问有什么可以帮您？'), voice_id: 'demo', format: 'wav' },
    recognize: { operation: 'recognize', pages: [{ source_id: 'page-1', media_id: t('请先上传图片') }] },
});
const pretty = (value: unknown) => JSON.stringify(value, null, 2);
function editorSpec(kind: Kind, spec: Record<string, unknown>) {
    if (kind !== 'profiles') return spec;
    const parameters = spec.parameters && typeof spec.parameters === 'object' && !Array.isArray(spec.parameters) ? spec.parameters as Record<string, unknown> : {};
    return {
        fallback_model_ids: [], timeout_seconds: 30, retries: 0, require_tools: false,
        ...spec,
        parameters: spec.operation === 'chat' ? { temperature: null, max_tokens: null, thinking: null, ...parameters } : parameters,
    };
}
const labels = (): Record<Workspace, string> => ({ connections: t("渠道管理"), models: t("模型广场"), profiles: t("配置方案"), playground: t("模型体验"), logs: t("调用日志"), monitor: t("监控概览"), access_keys: t("访问密钥"), sensitive_words: t("敏感词库"), alert_rules: t("告警管理"), quota: t("配额管理") });
const resourceNames = (): Record<Kind, string> => ({ connections: t("渠道"), models: t("模型"), profiles: t("配置方案") });
const operations = (): Record<string, string> => ({ chat: t("对话生成"), embed: t("文本向量"), rerank: t("重排序"), transcribe: t("语音识别"), synthesize: t("语音合成"), recognize: t("图像识别") });
function ResourceTable({ rows, kind, admin, onSelect }: {
    rows: ModelResource[];
    kind: Kind;
    admin: boolean;
    onSelect: (row: ModelResource) => void;
}) {
    return <div className="table-scroll"><table className="gateway-resource-table"><thead><tr><th>{kind === 'connections' ? t("渠道名称") : t("方案名称")}</th><th>{kind === 'connections' ? t("服务地址") : t("调用能力")}</th><th>{kind === 'connections' ? t("协议") : t("发布版本")}</th><th>{t("状态")}</th><th>{kind === 'connections' ? t("凭据") : t("主模型 ID")}</th><th>{t("修订")}</th><th>{t("操作")}</th></tr></thead><tbody>{rows.map(row => <tr key={row.id}><td><button className="text-button" onClick={() => onSelect(row)}>{row.name}</button></td><td className="gateway-url" title={String(row.spec.base_url || '')}>{kind === 'connections' ? String(row.spec.base_url || '—') : operations()[String(row.spec.operation)] || String(row.spec.operation)}</td><td>{kind === 'connections' ? String(row.spec.protocol) : row.published_version ? `v${row.published_version}` : t("未发布")}</td><td><span className={`gateway-state ${row.enabled ? 'enabled' : ''}`}>{row.enabled ? t("已启用") : t("已停用")}</span></td><td>{kind === 'connections' ? row.credential_configured == null ? '—' : row.credential_configured ? t("已配置") : t("待配置") : String(row.spec.model_id)}</td><td>r{row.revision}</td><td><button className="text-button" onClick={() => onSelect(row)}>{admin ? t("配置") : t("查看")}</button></td></tr>)}</tbody></table></div>;
}
function ResourceFields({ kind, editor, onChange, connections, models }: {
    kind: Kind;
    editor: string;
    onChange: (value: string) => void;
    connections: ModelResource[];
    models: ModelResource[];
}) {
    let draft: Record<string, unknown>;
    try {
        draft = JSON.parse(editor);
        if (!draft || Array.isArray(draft) || typeof draft !== 'object')
            return null;
    }
    catch {
        return <p className="hint">{t("请先修正下方 JSON，再使用表单编辑。")}</p>;
    }
    const update = (key: string, value: unknown) => onChange(pretty({ ...draft, [key]: value }));
    const selectedModel = models.find(row => row.id === draft.model_id);
    const selectedConnection = connections.find(row => row.id === selectedModel?.spec.connection_id);
    let thinkingSupported = false;
    try { thinkingSupported = selectedConnection?.spec.protocol === 'openai_compatible' && new URL(String(selectedConnection.spec.base_url)).hostname === 'api.deepseek.com' && ['deepseek-flash', 'deepseek-v4-pro', 'deepseek-v4-flash'].includes(String(selectedModel?.spec.model_name)); } catch { /* Incomplete form. */ }
    const parameters = draft.parameters && typeof draft.parameters === 'object' && !Array.isArray(draft.parameters) ? draft.parameters as Record<string, unknown> : {};

    return <div className="gateway-fields">
    <Field label={t("资源名称")}><input required value={String(draft.name ?? '')} onChange={e => update('name', e.target.value)} placeholder={t("为资源设置易于识别的名称")}/></Field>
    {kind === 'connections' && <>
      <Field label={t("接入协议")}><select value={String(draft.protocol ?? 'demo')} onChange={e => update('protocol', e.target.value)}><option value="demo">{t("Demo \u00B7 演示连接")}</option><option value="openai_compatible">{t("OpenAI 兼容协议")}</option><option value="gateway_http">{t("HTTP 模型网关")}</option></select></Field>
      <Field label={t("服务地址")}><input value={String(draft.base_url ?? '')} onChange={e => update('base_url', e.target.value)} placeholder="https://api.example.com/v1"/></Field>

    </>}
    {kind === 'models' && <>
      <Field label={t("供应商连接")}><select required value={String(draft.connection_id ?? '')} onChange={e => onChange(pretty({ ...draft, connection_id: e.target.value, model_name: '' }))}><option value="">{t("选择连接")}</option>{connections.map(row => <option key={row.id} value={row.id}>{row.name}{!row.enabled && t("（已停用）")}</option>)}</select></Field>
      <ProviderModelField key={String(draft.connection_id || '')} connection={connections.find(row => row.id === draft.connection_id)} value={String(draft.model_name ?? '')} onChange={value => update('model_name', value)} />
      <div><span className="label">{t("模型能力")}</span><div className="checks">{Object.entries(operations()).map(([key, label]) => <label key={key}><input type="checkbox" checked={Array.isArray(draft.operations) && draft.operations.includes(key)} onChange={e => update('operations', e.target.checked ? [...(Array.isArray(draft.operations) ? draft.operations : []), key] : (Array.isArray(draft.operations) ? draft.operations : []).filter(value => value !== key))}/>{label}</label>)}</div></div>
      <label className="check"><input type="checkbox" checked={draft.tool_calling === true} onChange={e => update('tool_calling', e.target.checked)}/>{t("支持工具调用")}</label>
      {Array.isArray(draft.operations) && draft.operations.includes('embed') && <><Field label={t("向量维度")}><input type="number" required min={1} max={8192} value={draft.embedding_dimension == null ? '' : Number(draft.embedding_dimension)} onChange={e => update('embedding_dimension', e.target.value ? Number(e.target.value) : null)}/></Field><Field label={t("向量空间版本")}><input required value={String(draft.vector_space ?? '')} onChange={e => update('vector_space', e.target.value)} placeholder={t("如 embedding-v1")}/></Field></>}
    </>}
    {kind === 'profiles' && <>
      <Field label={t("主模型")}><select required value={String(draft.model_id ?? '')} onChange={e => update('model_id', e.target.value)}><option value="">{t("选择模型")}</option>{models.map(row => <option key={row.id} value={row.id}>{row.name}{!row.enabled && t("（已停用）")}</option>)}</select></Field>
      <Field label={t("调用能力")}><select value={String(draft.operation ?? 'chat')} onChange={e => onChange(pretty({ ...draft, operation: e.target.value, ...(e.target.value !== 'chat' ? { require_tools: false, parameters: {} } : { parameters: { temperature: null, max_tokens: null, thinking: null, ...parameters } }) }))}>{Object.entries(operations()).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></Field>
      {draft.operation === 'chat' && <div><Field label={t('思考模式')}><select aria-label={t('思考模式')} value={String(parameters.thinking ?? 'default')} onChange={e => update('parameters', { ...parameters, thinking: e.target.value === 'default' ? null : e.target.value })}><option value="default">{t('跟随模型默认')}</option><option value="enabled" disabled={!thinkingSupported}>{t('开启思考')}</option><option value="disabled" disabled={!thinkingSupported}>{t('关闭思考')}</option></select></Field><p className="hint">{thinkingSupported ? t('思考模式会增加耗时和 Token 消耗，保存后需重新发布方案。') : t('当前模型尚未适配思考开关，请使用跟随模型默认。')}</p></div>}
      <Field label={t("超时时间（秒）")}><input type="number" min={1} max={120} value={Number(draft.timeout_seconds ?? 30)} onChange={e => update('timeout_seconds', Number(e.target.value))}/></Field>
    </>}
  </div>;
}
export function ModelsPage({ routeSection = 'models', onNavigate }: {
    routeSection?: Workspace;
    onNavigate: (section: Workspace) => void;
}) {
    const { admin, operator } = useAccess();
    const action = useAction();
    const [kind, setKind] = useState<Kind>(['connections', 'models', 'profiles'].includes(routeSection) ? routeSection as Kind : 'models');
    const [workspace, setWorkspace] = useState<Workspace>(routeSection);
    const [editorOpen, setEditorOpen] = useState(false);
    const [apiKey, setApiKey] = useState('');
    const [credentialMode, setCredentialMode] = useState('direct');
    const [credentialRef, setCredentialRef] = useState('');
    const [clearKey, setClearKey] = useState(false);
    useEffect(() => { if (!editorOpen) { setApiKey(''); setClearKey(false); } }, [editorOpen]);
    const [connectionFilter, setConnectionFilter] = useState('all');
    const [page, setPage] = useState(1);
    const [diagnostic, setDiagnostic] = useState('');
    const [query, setQuery] = useState('');
    const [status, setStatus] = useState('all');
    const [capability, setCapability] = useState('all');
    const list = useResource<{
        items: ModelResource[];
        total: number;
    }>(`${root}/catalog/${kind}?page=${page}&page_size=12&q=${encodeURIComponent(query)}${status === 'all' ? '' : `&enabled=${status === 'enabled'}`}${capability === 'all' ? '' : `&operation=${capability}`}${connectionFilter === 'all' ? '' : `&connection_id=${connectionFilter}`}`);
    const connections = useCatalogOptions('connections');
    const models = useCatalogOptions('models');
    const profiles = useCatalogOptions('profiles');
    const [selected, setSelected] = useState<ModelResource | null>(null);
    const [editor, setEditor] = useState(pretty(templates().connections));
    const [versions, setVersions] = useState<{
        version: number;
    }[]>([]);
    const [profile, setProfile] = useState('');
    const [version, setVersion] = useState(1);
    const [payload, setPayload] = useState(pretty(examples().chat));
    const [result, setResult] = useState('');
    const [audio, setAudio] = useState('');
    useEffect(() => () => { if (audio)
        URL.revokeObjectURL(audio); }, [audio]);
    function updateCredentialRef(value: string) {
        setCredentialRef(value);
        try { setEditor(pretty({ ...JSON.parse(editor), credential_ref: value || null })); } catch { /* Keep invalid JSON editable. */ }
    }
    function editJson(value: string) {
        setEditor(value);
        try {
            const ref = JSON.parse(value).credential_ref || '';
            setCredentialRef(String(ref));
            setCredentialMode(ref ? 'environment' : 'direct');
        } catch { /* Validation is shown by ResourceFields. */ }
    }
    function refresh() { list.refresh(); connections.refresh(); models.refresh(); profiles.refresh(); }
    function reset(next: Kind) {
        setKind(next);
        setSelected(null);
        setApiKey(''); setCredentialRef(''); setCredentialMode('direct'); setClearKey(false);
        setVersions([]);
        setQuery('');
        setStatus('all');
        setCapability('all');
        setConnectionFilter('all');
        setPage(1);
        const draft = { ...templates()[next] } as Record<string, unknown>;
        if (next === 'models')
            draft.connection_id = connections.data?.find(row => row.enabled)?.id || '';
        if (next === 'profiles')
            draft.model_id = models.data?.[0]?.id || '';
        setEditor(pretty(draft));
    }
    async function select(row: ModelResource) { setApiKey(''); setClearKey(false); setCredentialRef(String(row.spec.credential_ref || '')); setCredentialMode(row.spec.credential_ref ? 'environment' : 'direct'); setSelected(row); setEditor(pretty(editorSpec(kind, row.spec))); setEditorOpen(true); setVersions([]); setVersions(kind === 'profiles' ? await api(`${root}/profiles/${row.id}/versions`) : []); }
    function navigate(next: Workspace) { onNavigate(next); }
    useEffect(() => { action.clear(); setWorkspace(routeSection); setEditorOpen(false); if (routeSection === 'connections' || routeSection === 'models' || routeSection === 'profiles')
        reset(routeSection); }, [routeSection]);
    useEffect(() => setPage(1), [query, status, capability, connectionFilter]);
    const visible = list.data?.items;
    const currentPage = page;
    const pageRows = visible;
    const managing = workspace === 'connections' || workspace === 'models' || workspace === 'profiles';
    return <div className="model-gateway">
    {profiles.data && !profiles.error && !profiles.data.some(row => row.enabled && row.published_version && row.spec.operation === 'chat') && <AntAlert showIcon type="info" style={{marginBottom: 16}} title={t('尚未配置可用的对话模型')} description={t('平台可以先启动。请添加供应商连接和 Chat 模型，发布配置方案后绑定到 Agent，即可开始对话。')} />}
    {!editorOpen && <Alert error={action.error || list.error || connections.error || models.error || profiles.error} notice={action.notice}/>}
    <div className="gateway-nav" aria-label={t("模型网关功能")}>{(['models', 'connections', 'profiles', 'playground', 'logs', 'monitor', ...(admin ? ['access_keys', 'sensitive_words', 'alert_rules', 'quota'] : [])] as Workspace[]).map(k => <button aria-pressed={workspace === k} className={workspace === k ? 'active' : ''} key={k} disabled={action.busy} onClick={() => navigate(k)}>{labels()[k]}</button>)}<button className="gateway-refresh" aria-label={t("刷新资源")} onClick={refresh}><ReloadOutlined /></button></div>
    {managing && <div className={`gateway-catalog ${kind === 'models' ? 'with-filters' : ''}`}>
    {kind === 'models' && <section className="gateway-filters" aria-label={t("模型筛选")}><div className="row"><h2>{t("模型筛选")}</h2><button className="text-button" onClick={() => { setStatus('all'); setCapability('all'); setConnectionFilter('all'); setQuery(''); }}>{t("重置")}</button></div><h3>{t("模型能力")}</h3><div className="gateway-filter-chips">{Object.entries({ all: t("全部"), ...operations() }).map(([key, label]) => <button key={key} aria-pressed={capability === key} className={capability === key ? 'active' : ''} onClick={() => setCapability(key)}>{label}</button>)}</div><h3>{t("接入渠道")}</h3><select aria-label={t("筛选接入渠道")} value={connectionFilter} onChange={e => setConnectionFilter(e.target.value)}><option value="all">{t("全部渠道")}</option>{connections.data?.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select><h3>{t("工具调用")}</h3><p className="hint">{t("支持工具调用的模型会在卡片中显示能力标签。")}</p><div className="gateway-guide"><strong>{t("接入模型的三个步骤")}</strong><p>{t("创建渠道 \u2192 登记模型 \u2192 发布配置方案")}</p><button className="text-button" onClick={() => navigate('connections')}>{t("前往渠道管理 \u2192")}</button></div></section>}
    <div className="gateway-catalog-content">
    <div className="gateway-toolbar"><label className="gateway-search"><SearchOutlined /><input aria-label={t("搜索资源")} placeholder={t("搜索名称、模型标识或资源 ID")} value={query} onChange={e => setQuery(e.target.value)}/></label><select aria-label={t("资源状态")} value={status} onChange={e => setStatus(e.target.value)}><option value="all">{t("全部状态")}</option><option value="enabled">{t("已启用")}</option><option value="disabled">{t("已停用")}</option></select><button disabled={!admin || action.busy} onClick={() => { reset(kind); setEditorOpen(true); }}><PlusOutlined aria-hidden="true"/>{t(" 新建")}{resourceNames()[kind]}</button></div>
    <div className="gateway-workspace"><Panel title={`${labels()[kind]} · ${visible?.length ?? 0}`}>
      {list.loading && <p className="empty">{t("正在加载资源\u2026")}</p>}
      {kind !== 'models' ? <ResourceTable rows={pageRows || []} kind={kind} admin={admin} onSelect={row => void action.run(() => select(row), '')}/> : <div className="gateway-cards">{pageRows?.map(row => <button aria-pressed={selected?.id === row.id} className={`gateway-card ${selected?.id === row.id ? 'selected' : ''}`} key={row.id} onClick={() => void action.run(() => select(row), '')}><div className="gateway-card-top"><span className="gateway-resource-icon"><AppstoreOutlined /></span><span className={`gateway-state ${row.enabled ? 'enabled' : ''}`}>{row.enabled ? t("已启用") : t("已停用")}</span></div><strong>{row.name}</strong><p>{String(row.spec.model_name || row.spec.protocol || operations()[String(row.spec.operation)] || t("模型配置"))}</p><div className="gateway-tags">{(Array.isArray(row.spec.operations) ? row.spec.operations : []).map(value => <span key={String(value)}>{operations()[String(value)] || String(value)}</span>)}{row.spec.tool_calling === true && <span>{t("工具调用")}</span>}{row.credential_configured != null && <span>{row.credential_configured ? t("凭据已配置") : t("凭据待配置")}</span>}</div><div className="gateway-card-bottom"><span>{t("修订 r")}{row.revision}{row.published_version ? t(" \u00B7 发布 v{{v0}}", { v0: row.published_version }) : ''}</span><span>{admin ? t("配置 \u2192") : t("查看 \u2192")}</span></div></button>)}</div>}{visible?.length === 0 && <Empty>{query || status !== 'all' || capability !== 'all' ? t("没有匹配的资源，请调整筛选条件") : t("尚未添加{{v0}}，点击右上角添加第一个资源", { v0: labels()[kind] })}</Empty>}
    </Panel></div>
    <div className="gateway-pagination"><span>{t("共 ")}{list.data?.total ?? 0}{t(" 条资源")}</span><Pagination current={currentPage} pageSize={12} total={list.data?.total ?? 0} showSizeChanger={false} onChange={setPage} showTotal={total => t("筛选结果 {{v0}} 条", { v0: total })}/></div>
    </div></div>}
    <Drawer title={selected ? `${admin ? t("编辑") : t("查看")} · ${selected.name}` : t("新建{{v0}}", { v0: resourceNames()[kind] })} open={editorOpen} onClose={() => setEditorOpen(false)} size={560} className="gateway-editor" destroyOnHidden>
    <Alert error={action.error} notice={action.notice}/>
      <form onSubmit={e => { e.preventDefault(); void action.run(async () => { const body = JSON.parse(editor) as Record<string, unknown>;
          if (kind === 'connections') {
            if ('api_key' in body || 'clear_api_key' in body) throw new Error(t('请在 API Key 输入框填写密钥'));
            body.credential_ref = credentialMode === 'environment' ? credentialRef : null;
            if (credentialMode === 'direct' && apiKey) body.api_key = apiKey;
            if (credentialMode === 'direct' && clearKey) body.clear_api_key = true;
          } const saved = selected ? await put<ModelResource>(`${root}/${kind}/${selected.id}?revision=${selected.revision}`, body) : await post<ModelResource>(`${root}/${kind}`, body); await select(saved); refresh(); }, t("已保存；修改配置方案后需重新发布")); }}>
        <fieldset disabled={!admin || action.busy}><ResourceFields kind={kind} editor={editor} onChange={setEditor} connections={connections.data || []} models={models.data || []}/>
          {kind === 'connections' && <div className="gateway-fields">
            <Field label={t('凭据方式')}><select value={credentialMode} onChange={e => { setCredentialMode(e.target.value); updateCredentialRef(''); setApiKey(''); setClearKey(false); }}><option value="direct">{t('直接填写 API Key')}</option><option value="environment">{t('环境变量')}</option></select></Field>
            {credentialMode === 'direct' ? <>
              <Field label="API Key"><input type="password" value={apiKey} disabled={clearKey} onChange={e => setApiKey(e.target.value)} autoComplete="new-password" placeholder={selected?.credential_source === 'stored' ? t('已配置，留空保留原密钥') : t('填写供应商的 API Key')} /></Field>
              <p className="hint">{t('密钥加密保存，保存后不再显示原文。')}</p>
              {selected?.credential_source === 'stored' && <label className="check"><input type="checkbox" checked={clearKey} onChange={e => { setClearKey(e.target.checked); setApiKey(''); }}/>{t('清除已保存的 API Key')}</label>}
            </> : <Field label={t('凭据环境变量')}><input required value={credentialRef} onChange={e => updateCredentialRef(e.target.value)} placeholder="AGENT_MODEL_SECRET_…" autoComplete="off" /></Field>}
          </div>}
          {kind === 'models' && <p className="hint">{t('温度、最大输出 Token、思考模式等调用参数在「配置方案」中设置；此处仅配置模型信息。')}</p>}
          <details open className="gateway-advanced"><summary>{t("高级配置 \u00B7 JSON")}</summary><Field label={t("配置 JSON")}><textarea rows={7} required value={editor} onChange={e => editJson(e.target.value)} spellCheck={false}/></Field></details>
          <p className="hint">{t("支持直接填写 API Key，也可使用环境变量。Embedding 模型需声明 embedding_dimension、vector_space；语音合成需声明 voices。")}</p>
          <div className="actions"><button>{t("保存")}</button>{selected && <><button type="button" className="secondary" onClick={() => void action.run(async () => { await patch(`${root}/${kind}/${selected.id}`, { revision: selected.revision, enabled: !selected.enabled }); setSelected(null); setEditorOpen(false); refresh(); }, t("启停状态已更新"))}>{selected.enabled ? t("停用") : t("启用")}</button>{kind === 'connections' && <button type="button" className="secondary" onClick={() => void action.run(async () => setDiagnostic(pretty(await post(`${root}/connections/${selected.id}/probe`))), t("连接检测完成"))}>{t("检测连接")}</button>}{kind === 'profiles' && <button type="button" onClick={() => void action.run(async () => { await post(`${root}/profiles/${selected.id}/publish`, { revision: selected.revision }); setVersions(await api(`${root}/profiles/${selected.id}/versions`)); refresh(); }, t("已发布新版本"))}>{t("发布已保存方案")}</button>}</>}</div>
        </fieldset>
      </form>
      {versions.map(v => <div className="row" key={v.version}><span>{t("版本 ")}{v.version}</span><button disabled={!admin || action.busy} className="text-button" onClick={() => void action.run(async () => { await post(`${root}/profiles/${selected!.id}/rollback`, v); refresh(); }, t("发布指针已切换；已绑定的固定版本保持不变"))}>{t("切换发布版本")}</button></div>)}
      {kind !== 'connections' && <details><summary>{t("可引用资源 ID")}</summary>{(kind === 'models' ? connections.data : models.data)?.map(row => <p key={row.id}>{row.name}<br /><code>{row.id}</code></p>)}</details>}
      {selected && kind === 'models' && <ModelPolicyForm key={selected.id} modelId={selected.id} admin={admin}/>}
      {selected && admin && <div className="gateway-extra"><Popconfirm title={t("移除资源？被其他资源或发布版本引用时无法移除。")} onConfirm={() => action.run(async () => { await api(`${root}/resources/${kind}/${selected.id}?revision=${selected.revision}`, { method: 'DELETE' }); setEditorOpen(false); setSelected(null); refresh(); }, t("资源已移除"))}><button disabled={action.busy} className="secondary">{t("删除资源")}</button></Popconfirm></div>}
    </Drawer>
    {workspace === 'playground' && <Panel title={t("模型试用台")}>
      <form onSubmit={e => {
                e.preventDefault();
                void action.run(async () => {
                    setResult('');
                    setAudio('');
                    const response = await post<{
                        payload: {
                            id?: string;
                            mime_type?: string;
                        };
                    }>(`${root}/invoke`, { profile_id: profile, version, payload: JSON.parse(payload) });
                    setResult(pretty(response));
                    if (response.payload.id && response.payload.mime_type?.startsWith('audio/')) {
                        const download = await fetch(`/api/v1${root}/media/${response.payload.id}`, { headers: token() ? { Authorization: `Bearer ${token()}` } : {} });
                        if (!download.ok)
                            throw new Error(t("音频下载失败"));
                        setAudio(URL.createObjectURL(await download.blob()));
                    }
                }, t("调用完成"));
            }}>
        <fieldset disabled={!operator || action.busy}><div className="split"><Field label={t("已发布方案")}><select required value={profile} onChange={e => { setProfile(e.target.value); const row = profiles.data?.find(p => p.id === e.target.value); if (row) {
            setVersion(row.published_version || 1);
            setPayload(pretty(examples()[String(row.spec.operation)]));
        } }}><option value="">{t("选择方案")}</option>{profiles.data?.filter(p => p.enabled && p.published_version).map(p => <option key={p.id} value={p.id}>{p.name} · {String(p.spec.operation)} · v{p.published_version}</option>)}</select></Field><Field label={t("固定版本")}><input type="number" min={1} value={version} onChange={e => setVersion(Number(e.target.value))}/></Field></div>
          <Field label={t("调用参数 JSON")}><textarea rows={9} value={payload} onChange={e => setPayload(e.target.value)} spellCheck={false}/></Field>
          <Field label={t("上传音频或图片（有效期 24 小时）")}><input type="file" accept="audio/wav,audio/mpeg,audio/ogg,audio/webm,image/png,image/jpeg" onChange={e => { const file = e.target.files?.[0]; if (!file)
            return; void action.run(async () => { const response = await fetch(`/api/v1${root}/media`, { method: 'POST', headers: { 'Content-Type': file.type, ...(token() ? { Authorization: `Bearer ${token()}` } : {}) }, body: file }); if (!response.ok)
            throw new Error(t("上传失败：{{v0}}", { v0: response.status })); const media = await response.json() as {
            id: string;
        }; const body = JSON.parse(payload) as {
            operation: string;
            media_id?: string;
            pages?: object[];
        }; if (body.operation === 'transcribe')
            body.media_id = media.id;
        else if (body.operation === 'recognize')
            body.pages = [{ source_id: 'page-1', media_id: media.id }]; setPayload(pretty(body)); }, t("媒体已上传")); }}/></Field>
          <button disabled={!profile}>{t("调用模型")}</button>
        </fieldset>
      </form>{audio && <audio controls src={audio}/>}<pre>{result}</pre>
    </Panel>}
    {(workspace === 'logs' || workspace === 'monitor') && <GatewayReports key={workspace} mode={workspace}/>}
    {admin && (workspace === 'access_keys' || workspace === 'sensitive_words' || workspace === 'alert_rules' || workspace === 'quota') && <GatewayGovernance key={workspace} section={workspace} profiles={profiles.data || []}/>}
    <Drawer title={t("执行诊断")} open={!!diagnostic} onClose={() => setDiagnostic('')} size={640}><pre>{diagnostic}</pre></Drawer>
  </div>;
}
