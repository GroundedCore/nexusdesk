import {consoleTheme} from '../../styles/theme';
import { LanguageSelector } from '../../i18n/LanguageSelector';
import { t, number, errorText } from '../../i18n/index';
import { useEffect, useRef, useState } from 'react';
import { Avatar, Button, Card, Checkbox, ConfigProvider, Drawer, Dropdown, Empty, Input, InputNumber, Modal, Pagination, Radio, Select, Space, Spin, Splitter, Switch, Tag, Tooltip } from 'antd';
import { ArrowLeftOutlined, CommentOutlined, EditOutlined, ExperimentOutlined, HistoryOutlined, MoreOutlined, PlusOutlined, RobotOutlined, SaveOutlined, SendOutlined } from '@ant-design/icons';
import { api, patch, post, put, type Agent, type AgentConfig, type EvaluationReportPage, type KnowledgeBase, type MemoryConfigCaps, type Tool } from '../../shared/api/client';
import { Alert, Field, time, toast, useAccess, useAction, useResource } from '../../shared/components/ui';
import { useCatalogOptions } from '../models/useCatalogOptions';
import { AgentModelPicker } from './AgentModelPicker';
import { AgentChat, AgentRecords } from './Conversations';
import { AgentEvaluationPanel } from '../evaluation/AgentEvaluationPanel';
import { AgentFilters, INDUSTRIES, PURPOSES, industryLabel, purposeLabel, readFilters, filterSuffix, type Filters } from './AgentFilters';
import './agents.css';
import './workspace.css';
export interface PageResult<T> {
    items: T[];
    total: number;
    page: number;
    page_size: number;
}
const initialConfig = (): AgentConfig => withMemoryDefaults({ system_prompt: t('你是一个专业、友好的智能助手。请准确理解用户需求，并提供清晰可靠的帮助。'), tool_names: ['knowledge_search', 'propose_ticket', 'memory_search'], knowledge_base_ids: [], max_model_rounds: 6, model_profile_id: null, model_profile_version: null, reply_language: "auto" });
const groups = () => ([['basic', t("基本信息")], ['model', t("模型配置")], ['prompt', t("行为指令")], ['tools', t("工具")], ['knowledge', t("知识库")], ['memory', t("记忆")], ['runtime', t("运行参数")]] as const);
const labels = (): Record<string, string> => ({ system_prompt: t("行为指令"), tool_names: t("工具"), knowledge_base_ids: t("知识库"), max_model_rounds: t("最大模型轮次"), model_profile_id: t("模型方案"), model_profile_version: t("模型方案版本"), reply_language: t("回复语言"), summary_enabled: t("滚动摘要"), memory_enabled: t("启用客户长期记忆"), memory_read_enabled: t("读取记忆"), memory_write: t("自动写入记忆"), memory_types: t("记忆类型"), memory_search_enabled: t("启用 memory_search 检索"), memory_write_mode: t("写入策略"), memory_inject_max_items: t("单次注入上限"), memory_search_limit: t("检索返回上限"), memory_blacklist_auto_handoff: t("黑名单客户自动转人工") });
type MemoryType = "profile" | "preference" | "event";
const MEMORY_TYPES: MemoryType[] = ["profile", "preference", "event"];
const memoryTypeLabel = (type: MemoryType) => type === 'profile' ? t("客户画像") : type === 'preference' ? t("客户偏好") : t("事件记忆");
const withMemoryDefaults = (config: AgentConfig): AgentConfig => ({ ...config, summary_enabled: config.summary_enabled ?? true, memory_enabled: config.memory_enabled ?? true, memory_read_enabled: config.memory_read_enabled ?? true, memory_write: config.memory_write ?? true, memory_types: config.memory_types?.length ? config.memory_types : [...MEMORY_TYPES], memory_search_enabled: config.memory_search_enabled ?? true, memory_write_mode: config.memory_write_mode ?? "auto", memory_inject_max_items: config.memory_inject_max_items ?? null, memory_search_limit: config.memory_search_limit ?? null, memory_blacklist_auto_handoff: config.memory_blacklist_auto_handoff ?? false });
interface MemoryPreset { key: string; label: string; summary_enabled: boolean; memory_enabled: boolean; memory_read_enabled: boolean; memory_write: boolean; memory_write_mode: "auto" | "review"; memory_types: MemoryType[]; memory_search_enabled: boolean; memory_inject_max_items: number | null; memory_search_limit: number | null; memory_blacklist_auto_handoff: boolean; }
const memoryPresets = (): MemoryPreset[] => [
    { key: 'standard', label: t("普通客服"), summary_enabled: true, memory_enabled: true, memory_read_enabled: true, memory_write: true, memory_write_mode: "auto", memory_types: [...MEMORY_TYPES], memory_search_enabled: true, memory_inject_max_items: null, memory_search_limit: null, memory_blacklist_auto_handoff: false },
    { key: 'compliance', label: t("高合规客服"), summary_enabled: true, memory_enabled: true, memory_read_enabled: true, memory_write: true, memory_write_mode: "review", memory_types: [...MEMORY_TYPES], memory_search_enabled: true, memory_inject_max_items: null, memory_search_limit: null, memory_blacklist_auto_handoff: false },
    { key: 'knowledge', label: t("知识问答"), summary_enabled: true, memory_enabled: false, memory_read_enabled: false, memory_write: false, memory_write_mode: "auto", memory_types: [...MEMORY_TYPES], memory_search_enabled: false, memory_inject_max_items: null, memory_search_limit: null, memory_blacklist_auto_handoff: false },
    { key: 'short_term', label: t("短期问答"), summary_enabled: true, memory_enabled: true, memory_read_enabled: false, memory_write: false, memory_write_mode: "auto", memory_types: [...MEMORY_TYPES], memory_search_enabled: false, memory_inject_max_items: null, memory_search_limit: null, memory_blacklist_auto_handoff: false },
    { key: 'demo', label: t("演示 Agent"), summary_enabled: false, memory_enabled: false, memory_read_enabled: false, memory_write: false, memory_write_mode: "auto", memory_types: [...MEMORY_TYPES], memory_search_enabled: false, memory_inject_max_items: null, memory_search_limit: null, memory_blacklist_auto_handoff: false },
];
function MemoryToggle({ label, hint, checked, onChange, disabled, tooltip }: { label: string; hint?: string; checked: boolean; onChange: (checked: boolean) => void; disabled: boolean; tooltip?: string }) {
    const tile = <label className={`agent-memory-toggle${checked ? ' is-on' : ''}${disabled ? ' is-disabled' : ''}`}><Switch size="small" aria-label={label} checked={checked} disabled={disabled} onChange={onChange}/><span className="agent-memory-toggle-text"><span className="agent-memory-toggle-label">{label}</span>{hint && <span className="agent-memory-toggle-hint">{hint}</span>}</span></label>;
    return tooltip ? <Tooltip title={tooltip}><span className="agent-memory-toggle-wrap">{tile}</span></Tooltip> : tile;
}
interface Props {
    route: string;
    onNavigate: (path: string, replace?: boolean) => void;
    onConfigureModels: () => void;
    onWorkspaceChange: (active: boolean) => void;
    onGuardChange: (guard: ((destination?: string) => boolean) | null) => void;
}
export function AgentsPage({ onWorkspaceChange, onGuardChange, onConfigureModels, route, onNavigate }: Props) {
    const { admin } = useAccess();
    const action = useAction();
    const [deleteTarget, setDeleteTarget] = useState<Agent | null>(null);
    const filters = readFilters(route);
    const suffix = filterSuffix(filters);
    const { q: search, status, archived, page, pageSize } = filters;
    const [query, setQuery] = useState(search);
    const listFilters = useRef(suffix);
    const targetId = route.split('?')[0].split('/')[2];
    const editor = !!targetId;
    if (!editor)
        listFilters.current = suffix;
    function changeFilters(patch: Partial<Filters>, replace = false) { onNavigate('/agents' + filterSuffix({ ...filters, page: 1, ...patch }), replace); }
    const extra = new URLSearchParams();
    filters.industries.forEach(v => extra.append('industry', v));
    filters.tags.forEach(v => extra.append('tag', v));
    const rows = useResource<PageResult<Agent>>(`/agents/catalog?q=${encodeURIComponent(search)}&status=${status}&include_archived=${archived}&page=${page}&page_size=${pageSize}&examples_only=${filters.scope === 'examples'}&mine_only=${filters.scope === 'mine'}&${extra}`);
    const [selected, setSelected] = useState<Agent | null>(null);
    const routePath = route.split('?')[0];
    const view = routePath.endsWith('/conversations') ? 'records' : routePath.endsWith('/evaluation') ? 'evaluation' : 'config';
    const [routeError, setRouteError] = useState('');
    const [loadRevision, setLoadRevision] = useState(0);
    function setView(next: string) { onNavigate(`/agents/${targetId}/${next === 'records' ? 'conversations' : next === 'evaluation' ? 'evaluation' : 'config'}${suffix}`); }
    const [name, setName] = useState('');
    const [description, setDescription] = useState('');
    const [config, setConfig] = useState<AgentConfig>(initialConfig);
    const [industry, setIndustry] = useState('');
    const [tags, setTags] = useState<string[]>([]);
    const [isExample, setIsExample] = useState(false);
    const value = JSON.stringify({ name, description, config, industry, tags, is_example: isExample });
    const [saved, setSaved] = useState(value);
    const dirty = editor && value !== saved;
    const [savedAt, setSavedAt] = useState('');
    const [history, setHistory] = useState(false);
    const [picker, setPicker] = useState<'tools' | 'knowledge' | null>(null);
    const [pickQuery, setPickQuery] = useState('');
    const [activeSection, setActiveSection] = useState('basic');
    const [mobileTab, setMobileTab] = useState('config');
    const [preset, setPreset] = useState<string | null>(null);
    const form = useRef<HTMLFormElement>(null);
    const models = useCatalogOptions('profiles', editor);
    const tools = useResource<Tool[]>(editor ? '/tools' : null);
    const bases = useResource<KnowledgeBase[]>(editor ? '/knowledge-bases' : null);
    const memoryCaps = useResource<MemoryConfigCaps>(editor ? '/agents/memory-config-caps' : null);
    const caps = memoryCaps.data;
    const formDisabled = !admin || action.busy || !!selected?.archived;
    const summaryPlatformOff = caps ? !caps.summary_enabled : false;
    const injectPlatformOff = caps ? !caps.memory_inject_enabled : false;
    const extractPlatformOff = caps ? !caps.memory_extract_enabled : false;
    const blacklistForced = caps ? !!caps.memory_blacklist_auto_handoff : false;
    // 上限字段初始化为平台默认值（caps 未加载时回退到后端内置默认 20/5）。
    const defaultInjectLimit = caps?.memory_inject_max_items ?? 20;
    const defaultSearchLimit = caps?.memory_search_limit ?? 5;
    const memoryEnabled = config.memory_enabled !== false;
    const readEffective = !injectPlatformOff && memoryEnabled && config.memory_read_enabled !== false;
    const writeEffective = !extractPlatformOff && memoryEnabled && config.memory_write !== false;
    const searchEffective = readEffective && config.memory_search_enabled !== false;
    const summaryEffective = !summaryPlatformOff && config.summary_enabled !== false;
    const blacklistEffective = blacklistForced || config.memory_blacklist_auto_handoff === true;
    const versions = useResource<{
        version: number;
        created_at: string;
    }[]>(history && selected ? `/agents/${selected.id}/versions` : null);
    const differences = useResource<{
        changes: {
            field: string;
            published: unknown;
            draft: unknown;
        }[];
    }>(history && selected?.published_version ? `/agents/${selected.id}/diff?version=${selected.published_version}` : null);
    const evaluationReports = useResource<EvaluationReportPage>(selected ? `/agents/${selected.id}/evaluation-reports?page=1&page_size=1` : null);
    const latestEvaluation = evaluationReports.data?.items[0];
    const latestEvaluationPassed = latestEvaluation?.results.filter(result => result.passed).length ?? 0;
    const latestEvaluationTotal = latestEvaluation?.results.length ?? 0;
    const guard = (destination?: string) => !action.busy && (destination?.startsWith(`/agents/${targetId}/`) || !dirty || window.confirm(t("有尚未保存的修改，确定放弃修改并离开吗？")));
    useEffect(() => { onGuardChange(editor ? guard : null); });
    useEffect(() => setQuery(search), [search]);
    useEffect(() => { if (editor || query === search)
        return; const timer = setTimeout(() => changeFilters({ q: query }, true), 300); return () => clearTimeout(timer); }, [query, search, editor, suffix]);
    useEffect(() => { if (rows.data && page > 1 && !rows.data.items.length)
        changeFilters({ page: Math.max(1, Math.ceil(rows.data.total / pageSize)) }, true); }, [rows.data, page, pageSize]);
    useEffect(() => { const listener = (e: BeforeUnloadEvent) => { if (dirty || action.busy) {
        e.preventDefault();
        e.returnValue = '';
    } }; window.addEventListener('beforeunload', listener); return () => window.removeEventListener('beforeunload', listener); }, [dirty, action.busy]);
    useEffect(() => {
        if (!editor || view !== 'config')
            return;
        const root = form.current?.closest('.ant-splitter-panel');
        if (!root)
            return;
        const update = () => {
            const top = root.getBoundingClientRect().top;
            let active = 'basic';
            for (const [id] of groups()) {
                const section = document.getElementById(`agent-${id}`);
                if (section && section.getBoundingClientRect().top - top < 150)
                    active = id;
            }
            setActiveSection(active);
        };
        root.addEventListener('scroll', update, { passive: true });
        update();
        return () => root.removeEventListener('scroll', update);
    }, [editor, view]);
    function apply(agent: Agent) { const draft = withMemoryDefaults(agent.draft); setSelected(agent); setName(agent.name); setDescription(agent.description); setConfig(draft); setIndustry(agent.industry || ''); setTags(agent.tags || []); setIsExample(!!agent.is_example); setPreset(null); setSaved(JSON.stringify({ name: agent.name, description: agent.description, config: draft, industry: agent.industry || '', tags: agent.tags || [], is_example: !!agent.is_example })); setSavedAt(agent.updated_at || ''); }
    function applyMemoryPreset(key: string) {
        const presetValue = memoryPresets().find(p => p.key === key);
        if (!presetValue)
            return;
        setPreset(key);
        setConfig(c => ({ ...c, summary_enabled: presetValue.summary_enabled, memory_enabled: presetValue.memory_enabled, memory_read_enabled: presetValue.memory_read_enabled, memory_write: presetValue.memory_write, memory_write_mode: presetValue.memory_write_mode, memory_types: [...presetValue.memory_types], memory_search_enabled: presetValue.memory_search_enabled, memory_inject_max_items: presetValue.memory_inject_max_items, memory_search_limit: presetValue.memory_search_limit, memory_blacklist_auto_handoff: presetValue.memory_blacklist_auto_handoff }));
    }
    function setMemoryTypes(types: MemoryType[]) {
        if (!types.length) {
            toast.error(t("请至少选择一种记忆类型"));
            return;
        }
        setConfig({ ...config, memory_types: types });
    }
    function memoryOverCap(): string | null {
        if (!caps)
            return null;
        const inject = config.memory_inject_max_items;
        if (inject != null && inject > caps.memory_inject_max_items)
            return t("{{v0}} 超过平台上限 {{v1}}，保存后将被截断为平台上限 {{v1}}。确定继续吗？", { v0: t("单次注入上限"), v1: caps.memory_inject_max_items });
        const search = config.memory_search_limit;
        if (search != null && search > caps.memory_search_limit)
            return t("{{v0}} 超过平台上限 {{v1}}，保存后将被截断为平台上限 {{v1}}。确定继续吗？", { v0: t("检索返回上限"), v1: caps.memory_search_limit });
        return null;
    }
    useEffect(() => { onWorkspaceChange(editor); }, [editor]);
    useEffect(() => {
        setRouteError('');
        setHistory(false);
        setPicker(null);
        setMobileTab('config');
        action.clear();
        setPreset(null);
        if (!targetId) {
            setSelected(null);
            rows.refresh();
            return;
        }
        if (targetId === 'new') {
            setSelected(null);
            setName('');
            setDescription('');
            setConfig({ ...initialConfig() });
            setIndustry('');
            setTags([]);
            setIsExample(false);
            setSaved(JSON.stringify({ name: '', description: '', config: initialConfig(), industry: '', tags: [], is_example: false }));
            setSavedAt('');
            return;
        }
        if (selected?.id === targetId && !loadRevision)
            return;
        const controller = new AbortController();
        api<Agent>(`/agents/${encodeURIComponent(targetId)}`, { signal: controller.signal })
            .then(agent => { if (!controller.signal.aborted)
            apply(agent); })
            .catch(error => { if (!controller.signal.aborted)
            setRouteError(error instanceof Error ? error.message : t("加载 Agent 失败")); });
        return () => controller.abort();
    }, [targetId, loadRevision]);
    function open(agent: Agent | null, records = false) { onNavigate(`/agents/${agent?.id || 'new'}/${records ? 'conversations' : 'config'}${suffix}`); }
    function back() { onNavigate('/agents' + listFilters.current); }
    async function save() {
        const body = { name: name.trim(), description: description.trim(), config, industry, tags, is_example: isExample };
        const agent = selected ? await put<Agent>(`/agents/${selected.id}`, { ...body, revision: selected.draft_revision }) : await post<Agent>('/agents', body);
        apply(agent);
        setSavedAt(new Date().toISOString());
        if (targetId === 'new')
            onNavigate(`/agents/${agent.id}/config${suffix}`, true);
        return agent;
    }
    function submit(publish = false) {
        if (publish && (!config.model_profile_id || !config.model_profile_version)) {
            setView('config');
            setMobileTab('config');
            setActiveSection('model');
            requestAnimationFrame(() => document.getElementById('agent-model')?.scrollIntoView({ block: 'start' }));
            void action.run(async () => { throw new Error(t("请先选择已发布的 Chat 配置方案；没有可选方案时，请前往模型网关配置并发布。")); }, '');
            return;
        }
        if (publish && latestEvaluation?.results.length) {
            const failed = latestEvaluationPassed < latestEvaluationTotal;
            const outdated = !!selected?.published_version && latestEvaluation.agent_version !== selected.published_version;
            if (failed || outdated) {
                const message = failed
                    ? t("最近评测 v{{v0}} 未全部通过（{{v1}}/{{v2}}），确定继续发布吗？", { v0: latestEvaluation.agent_version, v1: latestEvaluationPassed, v2: latestEvaluationTotal })
                    : t("最近评测对应 v{{v0}}，与当前发布版本不一致，确定继续发布吗？", { v0: latestEvaluation.agent_version });
                if (!window.confirm(message))
                    return;
            }
        }
        if (!(config.memory_types?.length)) {
            setView('config');
            setMobileTab('config');
            setActiveSection('memory');
            requestAnimationFrame(() => document.getElementById('agent-memory')?.scrollIntoView({ block: 'start' }));
            void action.run(async () => { throw new Error(t("请至少选择一种记忆类型")); }, '');
            return;
        }
        const overCap = memoryOverCap();
        if (overCap && !window.confirm(overCap))
            return;
        setView('config');
        setMobileTab('config');
        requestAnimationFrame(() => {
            if (!form.current?.reportValidity())
                return;
            void action.run(async () => {
                const agent = dirty || !selected ? await save() : selected;
                if (publish) {
                    await post(`/agents/${agent.id}/publish`, { revision: agent.draft_revision });
                    apply(await api<Agent>(`/agents/${agent.id}`));
                }
                rows.refresh();
                versions.refresh();
                differences.refresh();
            }, publish ? t("当前配置已保存并发布") : t("草稿已保存"));
        });
    }
    async function archive(agent: Agent) { await patch(`/agents/${agent.id}/archive`, { archived: !agent.archived, revision: agent.draft_revision }); rows.refresh(); }
    function deleteAgent() {
        if (!deleteTarget || !admin)
            return;
        void action.run(async () => {
            try {
                await api(`/agents/${deleteTarget.id}?revision=${deleteTarget.draft_revision}`, { method: 'DELETE' });
                setDeleteTarget(null);
                rows.refresh();
            }
            catch (error) {
                const detail = error instanceof Error ? error.message : t("删除失败，请重试。");
                const reasons: Record<string, string> = {
                    agent_referenced_by_channels: t("该智能体仍被渠道引用，暂不能删除。停用渠道不会解除绑定。"),
                    agent_referenced_by_evaluations: t("该智能体仍有关联的评测用例或报告，暂不能删除，以保留评测追溯关系。"),
                    agent_has_active_runs: t("该智能体还有排队或运行中的任务，请等待结束或取消任务后重试。"),
                    agent_has_open_handoffs: t("该智能体还有未完成的人工接管，请先结束人工协同。"),
                    agent_has_pending_actions: t("该智能体还有待确认操作，请先完成处理后重试。"),
                    agent_must_be_archived: t("只能删除已归档的智能体，请刷新列表后检查状态。"),
                    agent_revision_conflict: t("智能体已被修改或恢复，请关闭弹窗并刷新列表后重新操作。"),
                    agent_not_found: t("该智能体已不存在，请关闭弹窗并刷新列表。"),
                };
                throw new Error(Object.entries(reasons).find(([code]) => detail.includes(code))?.[1] || detail);
            }
        }, t("智能体已永久删除，历史对话和运行记录已保留"));
    }
    function toggle(key: 'tool_names' | 'knowledge_base_ids', id: string) { setConfig(c => ({ ...c, [key]: c[key].includes(id) ? c[key].filter(x => x !== id) : [...c[key], id] })); }
    function showValue(field: string, value: unknown) { if (value == null)
        return t("未设置"); if (Array.isArray(value))
        return value.map(id => field === 'knowledge_base_ids' ? bases.data?.find(b => b.id === id)?.name || id : id).join('、') || t("无"); if (field === 'model_profile_id')
        return models.data?.find(m => m.id === value)?.name || String(value); return String(value); }
    if (editor && targetId !== 'new' && selected?.id !== targetId)
        return <div className="agent-workspace"><Button onClick={back}>{t("返回列表")}</Button>{routeError ? <><Alert error={t("无法打开 Agent：{{v0}}", { v0: errorText(routeError) })}/><Button onClick={() => setLoadRevision(v => v + 1)}>{t("重试加载")}</Button></> : <Spin tip={t("正在加载 Agent")}><div style={{ minHeight: 160 }}/></Spin>}</div>;
    if (!editor)
        return <div className="agent-list-page">
    <div className="agent-toolbar"><Button type="primary" icon={<PlusOutlined aria-hidden/>} disabled={!admin} onClick={() => open(null)}>{t("新增 Agent")}</Button><Button onClick={() => { setQuery(''); onNavigate('/agents'); }}>{t("重置")}</Button><Select aria-label={t("发布状态")} value={status} onChange={v => changeFilters({ status: v })} options={[{ value: 'all', label: t("全部发布状态") }, { value: 'published', label: t("已发布") }, { value: 'unpublished', label: t("未发布") }]}/><Input.Search aria-label={t("搜索 Agent")} placeholder={t("搜索 Agent 名称")} value={query} maxLength={100} onChange={e => setQuery(e.target.value)}/><Checkbox checked={archived} onChange={e => changeFilters({ archived: e.target.checked })}>{t("显示已归档")}</Checkbox></div>
    <AgentFilters value={filters} onChange={changeFilters}/>
    {!admin && <p className="agent-muted">{t("当前为只读角色，可查看配置和对话记录。")}</p>}
    <Alert error={rows.error || action.error} notice={action.notice}/>{rows.error && <Button onClick={rows.refresh}>{t("重新加载")}</Button>}
    <Spin spinning={rows.loading}><div className="agent-card-grid">{rows.data?.items.map(agent => <Card key={agent.id} className={`agent-card ${agent.archived ? 'is-archived' : ''}`}>
      <button className="agent-card-main" onClick={() => open(agent)} aria-label={t("配置 {{v0}}", { v0: agent.name })}><Avatar size={48} shape="square" icon={<RobotOutlined aria-hidden/>}/><div><h2>{agent.name}</h2><p>{agent.description || t("这个 Agent 还没有填写介绍")}</p></div></button>
      <div className="agent-classification">{agent.industry && <Tag color="purple">{industryLabel(agent.industry)}</Tag>}{agent.tags?.map(tag => <Tag key={tag}>{purposeLabel(tag)}</Tag>)}{agent.is_example && <Tag color="blue">{t("案例")}</Tag>}</div>
      <div className="agent-card-footer"><Space size={4}><Tag color={agent.archived ? 'default' : agent.published_version ? 'green' : 'default'}>{agent.archived ? t("已归档") : agent.published_version ? t("已发布") : t("未发布")}</Tag>{agent.published_version && <span>v{agent.published_version}</span>}</Space><Dropdown trigger={['click']} menu={{ items: [{ key: 'edit', label: t("查看配置") }, { key: 'records', label: t("对话记录") }, { key: 'archive', label: agent.archived ? t("恢复 Agent") : t("归档 Agent"), disabled: !admin || action.busy }, ...(agent.archived && admin ? [{ key: 'delete', label: t("永久删除"), danger: true, disabled: action.busy }] : [])], onClick: ({ key }) => { if (key === 'delete') {
                    action.clear();
                    setDeleteTarget(agent);
                }
                else if (key === 'archive')
                    void action.run(() => archive(agent), t("归档状态已更新"));
                else
                    open(agent, key === 'records'); } }}><Button type="text" aria-label={t("{{v0}} 更多操作", { v0: agent.name })} icon={<MoreOutlined aria-hidden/>}/></Dropdown></div>
    </Card>)}</div>{!rows.loading && !rows.error && !rows.data?.items.length && <Empty description={t("暂无符合条件的 Agent")}/>}</Spin>
    <Modal title={t("永久删除智能体")} open={!!deleteTarget} onCancel={() => { if (!action.busy) {
            setDeleteTarget(null);
            action.clear();
            rows.refresh();
        } }} onOk={deleteAgent} okText={t("确认永久删除")} cancelText={t("取消")} confirmLoading={action.busy} okButtonProps={{ danger: true, disabled: !admin }} cancelButtonProps={{ disabled: action.busy }} closable={!action.busy} maskClosable={!action.busy} keyboard={!action.busy}>
      <p>{t("确定永久删除「")}<strong>{deleteTarget?.name}</strong>{t("」吗？此操作无法恢复。")}</p>
      <p>{t("智能体配置和发布版本将被删除。历史对话与运行记录会保留，相关会话将转为只读，可在「会话工作台」查看。")}</p>
      <p className="agent-muted">{t("存在渠道、评测引用或未完成的任务时，系统会阻止删除。")}</p>
      <Alert error={action.error}/>
    </Modal>
    <Pagination className="agent-pagination" current={page} pageSize={pageSize} total={rows.data?.total || 0} showSizeChanger pageSizeOptions={[12, 24, 48]} showTotal={total => t("总共 {{v0}} 条", { v0: total })} onChange={(p, size) => { changeFilters({ page: size === pageSize ? p : 1, pageSize: size }); }}/>
  </div>;
    return <ConfigProvider theme={consoleTheme}><div className="agent-workspace">
    <header className="agent-topbar"><Space><Button type="text" icon={<ArrowLeftOutlined aria-hidden/>} onClick={back} disabled={action.busy}>{t("返回列表")}</Button><strong>{name || t("新建 Agent")}</strong><Tag color={selected?.published_version ? 'green' : 'default'}>{selected?.archived ? t("已归档") : selected?.published_version ? t("已发布 v{{v0}}", { v0: selected.published_version }) : t("未发布")}</Tag><span className={`agent-save-state ${dirty ? 'is-dirty' : ''}`}>{dirty ? t("有未保存修改") : savedAt ? t("已保存 {{v0}}", { v0: time(savedAt) }) : t("尚未保存")}</span></Space><Space><LanguageSelector /><Button icon={<HistoryOutlined aria-hidden/>} disabled={!selected} onClick={() => setHistory(true)}>{t("版本历史")}</Button><Button icon={<SaveOutlined aria-hidden/>} disabled={!admin || selected?.archived} loading={action.busy} onClick={() => submit()}>{t("保存草稿")}</Button><Button type="primary" icon={<SendOutlined aria-hidden/>} disabled={!admin || selected?.archived} loading={action.busy} onClick={() => submit(true)}>{t("发布")}</Button></Space></header>
    <Alert error={action.error || evaluationReports.error} notice={action.notice}/>
    <div className="agent-workspace-body"><aside className="agent-local-nav"><span className="agent-nav-eyebrow">AGENT WORKSPACE</span><Avatar size={46} shape="square" icon={<RobotOutlined aria-hidden/>}/><h3 title={name}>{name || t("新建 Agent")}</h3><Button block type={view === 'config' ? 'primary' : 'text'} icon={<EditOutlined aria-hidden/>} onClick={() => setView('config')}>{t("Agent 配置")}</Button>{view === 'config' && <div className="agent-section-links">{groups().map(([id, label]) => <Button type="text" block key={id} className={activeSection === id ? 'section-active' : ''} aria-current={activeSection === id ? 'location' : undefined} onClick={() => { setActiveSection(id); setMobileTab('config'); requestAnimationFrame(() => document.getElementById(`agent-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })); }}>{label}</Button>)}</div>}<Button block type={view === 'records' ? 'primary' : 'text'} icon={<CommentOutlined aria-hidden/>} disabled={!selected} onClick={() => setView('records')}>{t("对话记录")}</Button><Button block type={view === 'evaluation' ? 'primary' : 'text'} icon={<ExperimentOutlined aria-hidden/>} disabled={!selected} onClick={() => setView('evaluation')}>{t("评测")}</Button><div className="agent-nav-footer"><span className="agent-nav-dot"/>{t("独立配置 \u00B7 即时验证")}</div></aside>
    <main className="agent-work-area"><div className="agent-config-view" style={{ display: view === 'config' ? undefined : 'none' }}><div className="agent-mobile-tabs"><Button type={mobileTab === 'config' ? 'primary' : 'default'} onClick={() => setMobileTab('config')}>{t("配置")}</Button><Button type={mobileTab === 'chat' ? 'primary' : 'default'} onClick={() => setMobileTab('chat')}>{t("试聊")}</Button></div>
    {selected && <div className="agent-evaluation-banner"><div><span className="agent-nav-eyebrow">{t("评测概览")}</span><strong>{latestEvaluation ? t("最近评测：v{{v0}} · {{v1}}/{{v2}} 通过", { v0: latestEvaluation.agent_version, v1: latestEvaluationPassed, v2: latestEvaluationTotal }) : t("尚未运行评测")}</strong><small>{latestEvaluation ? time(latestEvaluation.created_at) : t("运行评测后查看发布版本的质量结果")}</small>{dirty && <small>{t("评测运行于已发布版本 v{{v0}}，不包含当前草稿修改。", { v0: selected.published_version ?? '—' })}</small>}{latestEvaluation && selected.published_version && latestEvaluation.agent_version !== selected.published_version && <small>{t("评测结果对应 v{{v0}}，与当前发布版本不一致，建议重新评测。", { v0: latestEvaluation.agent_version })}</small>}</div><Button onClick={() => setView('evaluation')}>{t("查看评测")}</Button></div>}
    <Splitter className={`agent-splitter mobile-${mobileTab}`}><Splitter.Panel defaultSize="55%" min="30%" className="agent-config-panel"><form ref={form} onSubmit={e => { e.preventDefault(); submit(); }} className="agent-config-form"><div className="agent-form-intro"><span>{t("配置工作台")}</span><h2>{t("定义你的 Agent")}</h2><p>{t("设置角色、连接能力，让每一次回答更符合预期。")}</p></div><Alert error={models.error || tools.error || bases.error}/>{!admin && <p className="agent-muted">{t("当前为只读角色")}</p>}{selected?.archived && <p className="agent-muted">{t("此 Agent 已归档，请返回列表恢复后编辑。")}</p>}<fieldset disabled={!admin || action.busy || selected?.archived}>
    <section id="agent-basic"><h2>{t("基本信息")}</h2><div className="agent-setting-card"><Field label={t("名称")}><input aria-label={t("Agent 名称")} required maxLength={100} value={name} onChange={e => setName(e.target.value)}/></Field><Field label={t("简介")}><textarea aria-label={t("Agent 简介")} rows={2} maxLength={1000} value={description} onChange={e => setDescription(e.target.value)} placeholder={t("介绍这个 Agent 的职责和擅长的工作")}/></Field></div><div className="agent-setting-card"><Field label={t("所属行业")}><select aria-label={t("所属行业")} value={industry} onChange={e => setIndustry(e.target.value)}><option value="">{t("未分类")}</option>{INDUSTRIES.map(v => <option key={v} value={v}>{industryLabel(v)}</option>)}</select></Field><Field label={t("用途标签")}><Select mode="multiple" virtual={false} aria-label={t("用途标签")} value={tags} onChange={setTags} options={PURPOSES.map(v => ({ value: v, label: purposeLabel(v) }))} disabled={!admin || action.busy || selected?.archived} style={{ width: '100%' }} placeholder={t("选择用途标签")}/></Field><Checkbox checked={isExample} disabled={!admin || action.busy || selected?.archived} onChange={e => setIsExample(e.target.checked)}>{t("案例数据")}</Checkbox></div></section>
    <section id="agent-model"><h2>{t("模型配置")}</h2><AgentModelPicker key={selected?.id || 'new'} config={config} onChange={setConfig} profiles={models.data} refresh={models.refresh} onConfigure={onConfigureModels} disabled={!admin || action.busy || !!selected?.archived}/></section>
    <section id="agent-prompt"><h2>{t("行为指令")}</h2><p className="agent-section-description">{t("告诉 Agent 应该扮演什么角色、如何思考和回答。")}</p><div className="agent-setting-card agent-prompt-card"><textarea aria-label={t("行为指令")} required maxLength={16000} rows={6} value={config.system_prompt} onChange={e => setConfig({ ...config, system_prompt: e.target.value })} placeholder={t("描述角色、任务目标与回答要求\u2026")}/><div className="agent-input-footer"><span>{t("清晰的指令有助于获得稳定的回答")}</span><span>{number(config.system_prompt.length)} / 16,000</span></div></div></section>
    {(['tools', 'knowledge'] as const).map(kind => <section id={`agent-${kind}`} key={kind}><div className="agent-section-heading"><h2>{kind === 'tools' ? t("工具") : t("知识库")}</h2><Button type="text" icon={<PlusOutlined aria-hidden/>} disabled={!admin || action.busy || selected?.archived} onClick={() => { setPickQuery(''); setPicker(kind); }}>{t("添加")}</Button></div><div className="agent-setting-card">{(kind === 'tools' ? config.tool_names : config.knowledge_base_ids).length ? (kind === 'tools' ? config.tool_names : config.knowledge_base_ids).map(id => <div className="agent-binding" key={id}><span>{kind === 'tools' ? id : bases.data?.find(b => b.id === id)?.name || id}</span><Button type="text" disabled={!admin || action.busy || selected?.archived} onClick={() => toggle(kind === 'tools' ? 'tool_names' : 'knowledge_base_ids', id)}>{t("移除")}</Button></div>) : <div className="agent-binding-empty">{t("点击「添加」选择")}{kind === 'tools' ? t("可调用的工具") : t("可访问的知识库")}</div>}</div></section>)}
    <section id="agent-memory"><h2>{t("记忆")}</h2><p className="agent-section-description">{t("配置 Agent 的对话记忆与客户长期记忆，保存后新会话生效。")}</p>
      <div className="agent-setting-card"><Field label={t("应用预设")}><select aria-label={t("应用预设")} value={preset || ''} disabled={formDisabled} onChange={e => applyMemoryPreset(e.target.value)}><option value="">{t("选择预设模板")}</option>{memoryPresets().map(p => <option key={p.key} value={p.key}>{p.label}</option>)}</select></Field><p className="agent-muted">{t("预设仅填充表单，不会自动保存，可继续调整后点击保存。")}</p></div>
      <div className="agent-setting-card"><h3 className="agent-memory-group">{t("会话记忆")}</h3><MemoryToggle label={t("滚动摘要")} hint={t("长对话自动压缩为摘要，保持上下文")} checked={summaryEffective} onChange={v => setConfig({ ...config, summary_enabled: v })} disabled={formDisabled || summaryPlatformOff} tooltip={summaryPlatformOff ? t("平台已禁用") : undefined}/></div>
      <div className="agent-setting-card"><h3 className="agent-memory-group">{t("客户长期记忆")}</h3>
        <MemoryToggle label={t("启用客户长期记忆")} hint={t("跨会话记住客户的画像、偏好与事件")} checked={memoryEnabled} onChange={v => setConfig({ ...config, memory_enabled: v })} disabled={formDisabled}/>
        {!memoryEnabled && <p className="agent-muted agent-memory-off">{t("关闭后，此 Agent 不再读取、写入或检索客户记忆。")}</p>}
        {memoryEnabled && <div className="agent-memory-reveal">
          <div className="agent-memory-grid">
            <MemoryToggle label={t("读取记忆")} hint={t("会话中注入该客户的既有记忆")} checked={readEffective} onChange={v => setConfig({ ...config, memory_read_enabled: v })} disabled={formDisabled || injectPlatformOff} tooltip={injectPlatformOff ? t("平台已禁用") : undefined}/>
            <MemoryToggle label={t("自动写入记忆")} hint={t("对话结束后自动抽取新记忆")} checked={writeEffective} onChange={v => setConfig({ ...config, memory_write: v })} disabled={formDisabled || extractPlatformOff} tooltip={extractPlatformOff ? t("平台已禁用") : undefined}/>
          </div>
          {(readEffective || writeEffective) && <div className="agent-memory-types"><span className="agent-memory-caption">{t("记忆类型")}</span><Checkbox.Group aria-label={t("记忆类型")} value={config.memory_types ?? []} disabled={formDisabled} options={MEMORY_TYPES.map(type => ({ value: type, label: memoryTypeLabel(type) }))} onChange={(vals) => setMemoryTypes(vals as MemoryType[])}/></div>}
          {writeEffective && <div className="agent-memory-types"><span className="agent-memory-caption">{t("写入策略")}</span><Radio.Group value={config.memory_write_mode ?? 'auto'} disabled={formDisabled} onChange={e => setConfig({ ...config, memory_write_mode: e.target.value as "auto" | "review" })}><Radio value="auto">{t("自动写入")}</Radio><Radio value="review">{t("全部人工确认")}</Radio></Radio.Group></div>}
          <div className="agent-memory-grid">
            {readEffective && <MemoryToggle label={t("启用 memory_search 检索")} hint={t("允许 Agent 主动检索客户记忆")} checked={searchEffective} onChange={v => setConfig({ ...config, memory_search_enabled: v })} disabled={formDisabled}/>}
            <MemoryToggle label={t("黑名单客户自动转人工")} hint={t("命中黑名单的客户直接转人工处理")} checked={blacklistEffective} onChange={v => setConfig({ ...config, memory_blacklist_auto_handoff: v })} disabled={formDisabled || blacklistForced} tooltip={blacklistForced ? t("平台已开启自动转人工") : undefined}/>
          </div>
          {readEffective && <div className="agent-memory-grid agent-memory-limits">
            <Field label={t("单次注入上限")}><InputNumber aria-label={t("单次注入上限")} min={1} precision={0} value={config.memory_inject_max_items ?? defaultInjectLimit} disabled={formDisabled} onChange={v => setConfig({ ...config, memory_inject_max_items: v ?? defaultInjectLimit })}/><small className="agent-memory-cap">{t("平台上限 {{v0}}", { v0: defaultInjectLimit })}</small></Field>
            {searchEffective && <Field label={t("检索返回上限")}><InputNumber aria-label={t("检索返回上限")} min={1} precision={0} value={config.memory_search_limit ?? defaultSearchLimit} disabled={formDisabled} onChange={v => setConfig({ ...config, memory_search_limit: v ?? defaultSearchLimit })}/><small className="agent-memory-cap">{t("平台上限 {{v0}}", { v0: defaultSearchLimit })}</small></Field>}
          </div>}
        </div>}
      </div>
    </section>
    <section id="agent-runtime"><h2>{t("运行参数")}</h2><div className="agent-setting-card"><Field label={t("回复语言")}><select aria-label={t("回复语言")} value={config.reply_language || 'auto'} onChange={e => setConfig({ ...config, reply_language: e.target.value as AgentConfig['reply_language'] })}><option value="auto">{t("跟随用户语言")}</option><option value="zh-CN">{t("简体中文")}</option><option value="zh-TW">{t("繁體中文")}</option><option value="en">English</option><option value="hi">हिन्दी</option></select></Field><p className="agent-muted">{t("回复语言独立于界面语言，保存并发布后生效。")}</p><Field label={t("最大模型轮次")}><InputNumber aria-label={t("最大模型轮次")} min={1} max={30} precision={0} value={config.max_model_rounds} disabled={!admin || action.busy || selected?.archived} onChange={v => setConfig({ ...config, max_model_rounds: v || 1 })}/></Field><p className="agent-muted">{t("限制单次运行的模型调用轮数，范围 1\u201330。")}</p></div></section>
    </fieldset></form></Splitter.Panel><Splitter.Panel min="25%" className="agent-chat-panel"><AgentChat key={selected?.id || 'new'} agent={selected}/></Splitter.Panel></Splitter></div>
    {view === 'records' && selected && <AgentRecords agentId={selected.id}/>}
    {view === 'evaluation' && selected && <div className="agent-evaluation-view"><AgentEvaluationPanel key={selected.id} agentId={selected.id} publishedVersion={selected.published_version} disabled={!!selected.archived}/></div>}
    </main></div>
    <Drawer title={picker === 'tools' ? t("选择工具") : t("选择知识库")} open={!!picker} onClose={() => setPicker(null)} size={460} footer={<Button type="primary" onClick={() => setPicker(null)}>{t("完成选择")}</Button>}><Input.Search placeholder={t("搜索名称")} value={pickQuery} onChange={e => setPickQuery(e.target.value)}/><p className="agent-muted">{t("选择后点击顶部「保存草稿」保存绑定。")}</p><Spin spinning={tools.loading || bases.loading}>{picker === 'tools' ? tools.data?.filter(t => t.enabled && t.name.toLowerCase().includes(pickQuery.toLowerCase())).map(t => <div className="agent-picker-row" key={t.name}><Checkbox checked={config.tool_names.includes(t.name)} disabled={!config.tool_names.includes(t.name) && config.tool_names.length >= 30} onChange={() => toggle('tool_names', t.name)}>{t.name}</Checkbox><Tag>{t.source}</Tag></div>) : bases.data?.filter(b => b.enabled && b.name.toLowerCase().includes(pickQuery.toLowerCase())).map(b => <div className="agent-picker-row" key={b.id}><Checkbox checked={config.knowledge_base_ids.includes(b.id)} disabled={!config.knowledge_base_ids.includes(b.id) && config.knowledge_base_ids.length >= 30} onChange={() => toggle('knowledge_base_ids', b.id)}>{b.name}</Checkbox></div>)}</Spin></Drawer>
    <Drawer title={t("版本历史与差异")} open={history} onClose={() => setHistory(false)} size={640}><Alert error={versions.error || differences.error || action.error} notice={action.notice}/><Spin spinning={versions.loading}><h3>{t("发布历史")}</h3>{!versions.data?.length && <Empty description={t("尚无发布版本")}/>}{versions.data?.map(v => <div className="agent-version" key={v.version}><div><strong>{t("版本 ")}{v.version}</strong>{v.version === selected?.published_version && <Tag color="green">{t("当前发布")}</Tag>}<p className="agent-muted">{time(v.created_at)}</p></div><Button disabled={!admin || action.busy || selected?.archived || v.version === selected?.published_version} onClick={() => void action.run(async () => { await post(`/agents/${selected!.id}/rollback`, { version: v.version }); setSelected(previous => previous ? { ...previous, published_version: v.version } : previous); versions.refresh(); differences.refresh(); rows.refresh(); }, t("已切换发布版本，草稿保持不变"))}>{t("切换到此版本")}</Button></div>)}</Spin>{selected?.published_version && <><h3>{t("已保存草稿与当前发布版本")}</h3>{dirty && <p className="agent-muted">{t("下方比较不包含尚未保存的修改。")}</p>}{differences.data?.changes.length === 0 && <Empty description={t("配置一致")}/>}{differences.data?.changes.map(c => <div className="agent-diff" key={c.field}><strong>{labels()[c.field] || c.field}</strong><div><span>{t("发布版本")}</span><p>{showValue(c.field, c.published)}</p></div><div><span>{t("已保存草稿")}</span><p>{showValue(c.field, c.draft)}</p></div></div>)}</>}</Drawer>
  </div></ConfigProvider>;
}
