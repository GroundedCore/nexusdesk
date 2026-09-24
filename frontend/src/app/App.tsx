import {AuthBoundary} from '../features/auth/AuthBoundary';
import {AccountMenu} from '../features/auth/AccountMenu';
import { useTranslation } from 'react-i18next';
import { LanguageSelector } from '../i18n/LanguageSelector';
import { t } from '../i18n/index';
import { lazy, Suspense, useState, useRef, useEffect } from 'react';
import { Layout, Button, Avatar, Tag, Spin, Alert as AntAlert } from 'antd';
import { MenuFoldOutlined, MenuUnfoldOutlined, BarChartOutlined, MessageOutlined, FileTextOutlined, CustomerServiceOutlined, RobotOutlined, ApiOutlined, BookOutlined, ToolOutlined, SafetyOutlined, ExperimentOutlined, LineChartOutlined } from '@ant-design/icons';
import { type PlatformModule, type Summary, setToken, token } from '../shared/api/client';
import { Alert, Badge, Field, Panel, Role, useResource } from '../shared/components/ui';
import { AgentsPage } from '../features/agents/AgentsPage';
const KnowledgePage = lazy(() => import('../features/knowledge/KnowledgePage').then(module => ({ default: module.KnowledgePage })));
import { ConversationsPage } from '../features/conversations/ConversationsPage';
import { ToolsPage } from '../features/tools/ToolsPage';
import { HandoffPage } from '../features/handoff/HandoffPage';
const TicketsPage = lazy(() => import('../features/tickets/TicketsPage').then(module => ({ default: module.TicketsPage })));
import { EvaluationPage } from '../features/evaluation/EvaluationPage';
import { Metrics, ObservabilityPage } from '../features/observability/ObservabilityPage';
import { WorkbenchSso } from '../features/open-platform/SsoLogin';
import { StaffPanel } from '../features/handoff/StaffPanel';
import { useHashRouter, type ModelSection } from './hashRouter';
import { ModelsPage } from '../features/models/ModelsPage';
const OpenPlatformPage = lazy(() => import('../features/open-platform/OpenPlatformPage').then(module => ({ default: module.OpenPlatformPage })));
const sections = () => ([['overview', t("平台总览")], ['conversations', t("会话工作台")], ['agents', t("Agent 管理")], ['models', t("模型网关")], ['knowledge', t("知识库")], ['tools', t("工具目录")], ['handoff', t("人工协同")], ['tickets', t("服务工单")], ['open-platform', t("开放平台")], ['evaluation', t("质量评测")], ['observability', t("运行与审计")], ['access', t("访问与策略")]] as const);
type Page = ReturnType<typeof sections>[number][0];
const icons = { 'open-platform': <ApiOutlined/>, overview: <BarChartOutlined />, conversations: <MessageOutlined />, tickets: <FileTextOutlined />, handoff: <CustomerServiceOutlined />, agents: <RobotOutlined />, models: <ApiOutlined />, knowledge: <BookOutlined />, tools: <ToolOutlined />, access: <SafetyOutlined />, evaluation: <ExperimentOutlined />, observability: <LineChartOutlined /> };
function Overview() {
    const summary = useResource<Summary>('/observability/summary', 5000);
    const modules = useResource<PlatformModule[]>('/modules');
    return <><Alert error={summary.error || modules.error}/><Metrics value={summary.data}/><section className="welcome"><p>{t("在一个工作空间中连接知识、配置 Agent，并协同处理客户问题。")}</p></section><div className="module-grid">{modules.data?.map((m, index) => <article key={m.id} className="module-card"><div className="row"><span className="mono muted">{String(index + 1).padStart(2, '0')}</span><Badge value={m.status}/></div><h3>{t(m.name)}</h3><p>{t(m.description)}</p></article>)}</div></>;
}
function Access({ onChange }: {
    onChange: () => void;
}) {
    const [value, setValue] = useState(token());
    const me = useResource<{
        role: string;
        tenant: string;
        actor: string;
    }>('/me');
    const policy = useResource<{
        model_backend: string;
        model_name: string;
        max_model_rounds: number;
        max_tool_calls: number;
        worker_concurrency: number;
        tool_allowed_hosts: string[];
    }>('/policy');
    return <div className="split"><Panel title={t("工作台访问凭据")}><Alert error={me.error || policy.error}/><WorkbenchSso onChange={onChange}/><form onSubmit={e => { e.preventDefault(); setToken(value.trim()); onChange(); }}><Field label={t("Bearer 服务令牌")}><input type="password" autoComplete="off" value={value} onChange={e => setValue(e.target.value)} placeholder={t("本地开发模式可留空")}/></Field><button>{t("应用凭据")}</button></form><p className="hint">{t("凭据仅保存在当前浏览器会话。模型密钥在后端配置，不通过此页面传入。")}</p>{me.data && <p>{t("租户：")}{me.data.tenant}{t(" \u00B7 当前角色：")}{me.data.role}</p>}</Panel><Panel title={t("权限与运行策略")}><table><thead><tr><th>{t("角色")}</th><th>{t("能力")}</th></tr></thead><tbody><tr><td>admin</td><td>{t("配置、发布、接入、评测及客服操作")}</td></tr><tr><td>operator</td><td>{t("会话、接管、工单确认与处理")}</td></tr><tr><td>viewer</td><td>{t("只读查看")}</td></tr></tbody></table>{policy.data && <dl><dt>{t("模型模式")}</dt><dd>{policy.data.model_backend} / {policy.data.model_name || t("演示模型")}</dd><dt>{t("单 Worker 并发")}</dt><dd>{policy.data.worker_concurrency}</dd><dt>{t("单次运行预算")}</dt><dd>{policy.data.max_model_rounds}{t(" 轮模型 / ")}{policy.data.max_tool_calls}{t(" 次工具")}</dd><dt>{t("可注册 API 主机")}</dt><dd>{policy.data.tool_allowed_hosts.join('、')}</dd></dl>}<p className="hint">{t("服务令牌提供基础角色划分；员工账号、SSO 和客户身份系统需要按实际部署接入。")}</p></Panel></div>;
}
function Shell({ reset }: {
    reset: () => void;
}) {
    const leaveGuard = useRef<((destination?: string) => boolean) | null>(null);
    const router = useHashRouter(destination => !leaveGuard.current || leaveGuard.current(destination));
    const page = router.path.split('?')[0].split('/')[1] as Page;
    const me = useResource<{
        role: string;
        tenant: string;
    }>('/me');
    const deployment = useResource<{quickstart: boolean}>('/deployment');
    const [collapsed, setCollapsed] = useState(false);
    const [agentWorkspace, setAgentWorkspace] = useState(false);
    const previousCollapsed = useRef(false);
    function navigate(next: Page, entry: 'models' | 'profiles' = 'models') {
        router.navigate(next === 'models' ? `/models/${entry}` : `/${next}`);
    }
    useEffect(() => {
        if (page !== 'agents') {
            leaveGuard.current = null;
            setAgentWorkspace(false);
            setCollapsed(window.innerWidth < 992 || previousCollapsed.current);
        }
    }, [page]);
    function workspaceChanged(active: boolean) {
        setAgentWorkspace(active);
        if (active) {
            previousCollapsed.current = collapsed;
            setCollapsed(true);
        }
        else
            setCollapsed(window.innerWidth < 992 || previousCollapsed.current);
    }
    const role = me.data?.role || 'viewer';
    const groups: {
        title: string;
        ids: Page[];
    }[] = [{ title: t("客服工作"), ids: ['overview', 'conversations', 'tickets', 'handoff'] }, { title: t("配置管理"), ids: ['agents', 'models', 'knowledge', 'tools', 'open-platform', 'access'] }, { title: t("质量运营"), ids: ['evaluation', 'observability'] }];
    const pages = { 'open-platform': <OpenPlatformPage route={router.path} onNavigate={router.navigate}/>, models: <ModelsPage routeSection={router.path.split('/')[2] as ModelSection} onNavigate={section => router.navigate(`/models/${section}`)}/>, overview: <Overview />, conversations: <ConversationsPage />, agents: <AgentsPage route={router.path} onNavigate={router.navigate} onConfigureModels={() => navigate('models', 'profiles')} onWorkspaceChange={workspaceChanged} onGuardChange={guard => { leaveGuard.current = guard; }}/>, knowledge: <KnowledgePage />, tools: <ToolsPage route={router.path} onNavigate={router.navigate}/>, handoff: <HandoffPage />, tickets: <TicketsPage />, evaluation: <EvaluationPage />, observability: <ObservabilityPage />, access: <><Access onChange={reset}/><StaffPanel /></> };
    return <Role.Provider value={role}><Layout className={`console-layout ${page === 'agents' ? 'agents-shell' : page === 'tools' ? 'tools-shell' : page === 'open-platform' ? 'open-platform-shell' : ''} ${agentWorkspace ? 'agent-focus' : ''}`}><Layout.Sider className="console-sider" theme="light" width={248} collapsedWidth={0} collapsed={collapsed} breakpoint="lg" onBreakpoint={setCollapsed} trigger={null}>
    <div className="console-brand"><div className="brand-wordmark">nexus<span>desk</span><small>{t("智能客服平台")}</small></div></div>
    <nav aria-label={t("主导航")} className="console-nav">{groups.map(group => <div key={group.title}><div className="nav-group-title">{group.title}</div>{group.ids.map(id => <Button block type="text" icon={icons[id]} className={page === id ? 'nav-selected' : ''} aria-current={page === id ? 'page' : undefined} key={id} onClick={() => navigate(id)}>{sections().find(item => item[0] === id)?.[1]}</Button>)}</div>)}</nav>
  </Layout.Sider><Layout className="console-body"><Layout.Header className="console-header"><div className="console-heading"><Button type="text" aria-label={collapsed ? t("展开导航") : t("收起导航")} icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />} onClick={() => setCollapsed(!collapsed)}/><div><h1>{sections().find(([id]) => id === page)?.[1]}</h1><p className="page-description">{page === 'tickets' ? t("跟进每一个问题，记录每一次处理。") : page === 'overview' ? t("了解服务运行情况，管理你的客服工作空间。") : page === 'models' ? t("统一接入模型服务，管理模型能力与发布方案。") : t("管理服务配置与协作，让客户问题得到持续跟进。")}</p></div></div><div className="console-header-actions"><LanguageSelector /><AccountMenu access={() => navigate('access')}><Button type="text" aria-label={t("账户菜单")}><Avatar size="small">{role[0].toUpperCase()}</Avatar>{me.data?.tenant || t("未连接")}<Tag>{role}</Tag></Button></AccountMenu></div></Layout.Header>
    <Layout.Content className="console-content">{deployment.data?.quickstart && <AntAlert style={{marginBottom: 20}} showIcon type="warning" title="nexusdesk · 快速体验模式" description={<span>默认使用模拟模型，仅供本机体验，不代表真实模型回答或业务处理结果。打开“会话工作台”的 sample-support，尝试“发货需要多久？”或“帮我创建工单”。</span>} />}{me.error && page !== 'access' && <div role="alert" className="alert error">{t("无法连接后端或读取权限：")}{me.error} <Button onClick={() => me.refresh()}>{t("重试")}</Button> <Button onClick={() => navigate('access')}>{t("设置凭据")}</Button></div>}<div className={`page module-page module-${page}`} key={page}><Suspense fallback={<Spin tip={t("正在加载页面")}><div style={{ minHeight: 160 }}/></Spin>}>{pages[page]}</Suspense></div></Layout.Content>
  </Layout></Layout></Role.Provider>;
}
export function App() { useTranslation(); const [revision, setRevision] = useState(0); return <AuthBoundary key={revision}><Shell reset={() => setRevision(v => v + 1)}/></AuthBoundary>; }
