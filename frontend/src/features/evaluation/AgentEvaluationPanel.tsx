import { Button, Descriptions, Modal, Pagination, Progress, Select, Space, Statistic, Table, Tag, Tooltip } from 'antd';
import type { TableColumnsType } from 'antd';
import { useRef, useState } from 'react';
import { errorText, t } from '../../i18n/index';
import type { ModelResource } from '../models/ModelsPage';
import {
    api,
    download,
    patch,
    post,
    upload,
    type EvaluationCase,
    type EvaluationCaseRun,
    type EvaluationCaseSpec,
    type EvaluationEnvironment,
    type EvaluationImport,
    type EvaluationReport,
    type EvaluationReportPage,
    type EvaluationResult,
    type EvaluationTrend,
} from '../../shared/api/client';
import { Alert, Empty, Field, Panel, time, useAccess, useAction, useResource } from '../../shared/components/ui';

interface Props {
    agentId: string;
    publishedVersion: number | null;
    disabled?: boolean;
}

interface CaseDraft {
    name: string;
    input: string;
    expected_contains: string;
    forbidden_contains: string;
    expected_tools: string;
    reference_answers: string;
    mock_tools: string;
}

interface CompareRow {
    name: string;
}

const DEFAULT_MOCKS = () => JSON.stringify({
    knowledge_search: {
        ok: true,
        data: [{ title: t("测试政策"), version: 1, excerpt: t("七天内可申请退货。") }],
    },
}, null, 2);

const blankDraft = (): CaseDraft => ({
    name: '',
    input: '',
    expected_contains: '',
    forbidden_contains: '',
    expected_tools: '',
    reference_answers: '',
    mock_tools: DEFAULT_MOCKS(),
});

const lines = (value: string) => value.split('\n').map(item => item.trim()).filter(Boolean);

function toDraft(testCase: EvaluationCase): CaseDraft {
    return {
        name: testCase.name,
        input: testCase.spec.input,
        expected_contains: (testCase.spec.expected_contains || []).join('\n'),
        forbidden_contains: (testCase.spec.forbidden_contains || []).join('\n'),
        expected_tools: (testCase.spec.expected_tools || []).join('\n'),
        reference_answers: (testCase.spec.reference_answers || []).join('\n'),
        mock_tools: JSON.stringify(testCase.spec.mock_tools || {}, null, 2),
    };
}

function payload(draft: CaseDraft) {
    const tools = lines(draft.expected_tools);
    return {
        name: draft.name,
        input: draft.input,
        expected_contains: lines(draft.expected_contains),
        forbidden_contains: lines(draft.forbidden_contains),
        expected_tools: tools.length ? tools : null,
        reference_answers: lines(draft.reference_answers),
        mock_tools: JSON.parse(draft.mock_tools || '{}') as unknown,
    };
}

function statusLabel(result: EvaluationResult) {
    if (result.status === 'needs_review') return t("待复核");
    if (result.status === 'error') return t("评分/执行错误");
    return result.passed ? t("通过") : t("失败");
}

/** `error` outranks a rule failure, matching the backend summary buckets. */
function statusColor(result: EvaluationResult) {
    if (result.status === 'error' || result.error) return 'red';
    if (result.status === 'needs_review') return 'gold';
    return result.passed ? 'green' : 'red';
}

/** Import errors arrive as stable codes from the API; only the limit one has a friendlier label. */
function importError(code: string) {
    return code === 'maximum_20_cases_per_agent' ? t("每个 Agent 最多 20 条用例") : code;
}

function environmentText(environment?: EvaluationEnvironment | null) {
    if (!environment) return t("未知");
    const published = environment.knowledge.reduce((sum, item) => sum + item.published, 0);
    return [
        environment.model.version ? t("模型方案 v{{v0}}", { v0: environment.model.version }) : t("未绑定模型方案"),
        environment.knowledge.length ? t("知识库 {{v0}} 个 / 已发布 {{v1}} 篇", { v0: environment.knowledge.length, v1: published }) : t("无知识库"),
        t("记忆{{v0}}", { v0: environment.memory.enabled ? t("开启") : t("关闭") }),
        t("工具 {{v0}} 个", { v0: environment.tools.length }),
    ].join(' · ');
}

export function AgentEvaluationPanel({ agentId, publishedVersion, disabled = false }: Props) {
    const profiles = useResource<ModelResource[]>('/model-gateway/profiles');
    // Judge needs a real chat model: resolving profile → model → connection is the only
    // way to tell a deterministic demo profile from one that can return the rubric JSON.
    const models = useResource<ModelResource[]>('/model-gateway/models');
    const connections = useResource<ModelResource[]>('/model-gateway/connections');
    const [judge, setJudge] = useState('');
    const [embedding, setEmbedding] = useState('');
    const [reportPage, setReportPage] = useState(1);
    const [reportPageSize, setReportPageSize] = useState(5);
    const [reportVersion, setReportVersion] = useState<'all' | number>('all');
    const [compareIds, setCompareIds] = useState<string[]>([]);
    const [compareOpen, setCompareOpen] = useState(false);
    const [addDraft, setAddDraft] = useState<CaseDraft>(blankDraft);
    const [addOpen, setAddOpen] = useState(false);
    const [editing, setEditing] = useState<EvaluationCase | null>(null);
    const [editDraft, setEditDraft] = useState<CaseDraft>(blankDraft);
    const [caseResults, setCaseResults] = useState<Record<string, EvaluationResult>>({});
    const [expandedCaseKeys, setExpandedCaseKeys] = useState<readonly string[]>([]);
    const [runningCaseId, setRunningCaseId] = useState('');
    const [importing, setImporting] = useState(false);
    const [importNotice, setImportNotice] = useState('');
    const [importDetail, setImportDetail] = useState('');
    const fileInput = useRef<HTMLInputElement>(null);
    const cases = useResource<EvaluationCase[]>(`/agents/${agentId}/evaluation-cases`);
    const reportQuery = `/agents/${agentId}/evaluation-reports?page=${reportPage}&page_size=${reportPageSize}${reportVersion === 'all' ? '' : `&agent_version=${reportVersion}`}`;
    const reports = useResource<EvaluationReportPage>(reportQuery);
    const latestReports = useResource<EvaluationReportPage>(`/agents/${agentId}/evaluation-reports?page=1&page_size=1`);
    const trend = useResource<EvaluationTrend>(`/agents/${agentId}/evaluation-trend?limit=20`);
    const action = useAction();
    const { admin } = useAccess();
    const locked = !admin || action.busy || disabled;

    const latest = latestReports.data?.items[0];
    const latestPassed = latest?.results.filter(result => result.passed).length ?? 0;
    const latestTotal = latest?.results.length ?? 0;
    const latestReview = latest?.results.filter(result => result.status === 'needs_review').length ?? 0;
    const latestErrors = latest?.results.filter(result => result.status === 'error' || result.error).length ?? 0;
    const latestRate = latestTotal ? Math.round((latestPassed / latestTotal) * 100) : 0;
    const canRun = !locked && !!publishedVersion && !!cases.data?.length;
    const pageReports = reports.data?.items ?? [];
    const selectedReports = pageReports.filter(report => compareIds.includes(report.id));

    function profileRef(id: string) {
        const profile = profiles.data?.find(item => item.id === id);
        return profile ? { id: profile.id, version: profile.published_version } : null;
    }

    function demoProfile(id: string) {
        const profile = profiles.data?.find(item => item.id === id);
        const modelId = profile?.spec.model_id as string | undefined;
        const model = models.data?.find(item => item.id === modelId);
        const connectionId = model?.spec.connection_id as string | undefined;
        const connection = connections.data?.find(item => item.id === connectionId);
        return connection?.spec.protocol === 'demo';
    }

    const options = () => ({
        judge: profileRef(judge),
        embedding: profileRef(embedding),
        budget_seconds: 180,
    });

    function refresh() {
        reports.refresh();
        latestReports.refresh();
        trend.refresh();
    }

    function submitAdd(event: React.FormEvent) {
        event.preventDefault();
        void action.run(async () => {
            await post(`/agents/${agentId}/evaluation-cases`, payload(addDraft));
            cases.refresh();
            setAddDraft(blankDraft());
            setAddOpen(false);
        }, t("用例已保存"));
    }

    function submitEdit(event: React.FormEvent) {
        event.preventDefault();
        if (!editing) return;
        void action.run(async () => {
            await patch(`/agents/${agentId}/evaluation-cases/${editing.id}`, payload(editDraft));
            cases.refresh();
            setEditing(null);
        }, t("用例已更新"));
    }

    function removeCase(testCase: EvaluationCase) {
        Modal.confirm({
            title: t("删除用例"),
            content: t("确认删除用例「{{v0}}」吗？", { v0: testCase.name }),
            okText: t("删除"),
            cancelText: t("取消"),
            onOk: () => action.run(async () => {
                await api(`/agents/${agentId}/evaluation-cases/${testCase.id}`, { method: 'DELETE' });
                cases.refresh();
                setCaseResults(previous => {
                    const next = { ...previous };
                    delete next[testCase.id];
                    return next;
                });
            }, t("用例已删除")),
        });
    }

    function runCase(testCase: EvaluationCase) {
        setRunningCaseId(testCase.id);
        void action.run(async () => {
            try {
                const run = await post<EvaluationCaseRun>(`/agents/${agentId}/evaluation-cases/${testCase.id}/run`, options());
                setCaseResults(previous => ({ ...previous, [testCase.id]: run.result }));
                setExpandedCaseKeys(previous => previous.includes(testCase.id) ? previous : [...previous, testCase.id]);
            } finally {
                setRunningCaseId('');
            }
        }, '');
    }

    function downloadTemplate() {
        void action.run(async () => {
            const file = await download(`/agents/${agentId}/evaluation-cases/template`);
            const url = URL.createObjectURL(file.blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = file.filename;
            link.click();
            URL.revokeObjectURL(url);
        }, t("模板已下载"));
    }

    function importCases(file: File) {
        setImporting(true);
        setImportNotice('');
        setImportDetail('');
        void action.run(async () => {
            try {
                const result = await upload<EvaluationImport>(`/agents/${agentId}/evaluation-cases/import?filename=${encodeURIComponent(file.name)}`, file);
                setImportNotice(t("导入完成：新增 {{v0}} 条，覆盖 {{v1}} 条，当前共 {{v2}} 条", { v0: result.created, v1: result.updated, v2: result.total }));
                setImportDetail(result.errors.map(item => t("第 {{v0}} 行（{{v1}}）：{{v2}}", { v0: item.row, v1: item.name || t("未命名"), v2: importError(item.error) })).join('；'));
                cases.refresh();
            } finally {
                setImporting(false);
            }
        }, '');
    }

    const caseColumns: TableColumnsType<EvaluationCase> = [
        { title: t("用例名称"), dataIndex: 'name', width: 200, render: (value: string) => <strong>{value}</strong> },
        { title: t("用户输入"), key: 'input', ellipsis: true, render: (_, row) => row.spec.input },
        { title: t("断言"), key: 'assertions', width: 220, render: (_, row) => <AssertionTags spec={row.spec}/> },
        { title: t("最近结果"), key: 'result', width: 120, render: (_, row) => <LatestResult result={caseResults[row.id]}/> },
        {
            title: t("操作"), key: 'actions', width: 220,
            render: (_, row) => <Space size={4} wrap>
                <Button size="small" loading={runningCaseId === row.id} disabled={locked || !publishedVersion} onClick={() => runCase(row)}>{t("运行此用例")}</Button>
                <Button size="small" disabled={locked} onClick={() => { setEditing(row); setEditDraft(toDraft(row)); }}>{t("编辑")}</Button>
                <Button size="small" danger disabled={locked} onClick={() => removeCase(row)}>{t("删除")}</Button>
            </Space>,
        },
    ];

    const reportColumns: TableColumnsType<EvaluationReport> = [
        { title: t("版本"), key: 'version', width: 90, render: (_, row) => <strong>v{row.agent_version}</strong> },
        {
            title: t("通过率"), key: 'pass_rate', width: 180,
            render: (_, row) => {
                const passed = row.results.filter(result => result.passed).length;
                const total = row.results.length;
                const percent = total ? Math.round((passed / total) * 100) : 0;
                return <div className="agent-evaluation-rate">
                    <Progress percent={percent} size="small" showInfo={false} strokeColor={percent === 100 ? '#52a06b' : percent >= 50 ? '#d9a441' : '#d1635f'}/>
                    <span>{passed}/{total}</span>
                </div>;
            },
        },
        { title: t("待复核"), key: 'review', width: 90, render: (_, row) => row.results.filter(result => result.status === 'needs_review').length },
        { title: t("评分/执行错误"), key: 'error', width: 130, render: (_, row) => row.results.filter(result => result.status === 'error' || result.error).length },
        { title: t("运行时间"), dataIndex: 'created_at', width: 180, render: (value: string) => time(value) },
        { title: t("运行环境"), key: 'environment', ellipsis: true, render: (_, row) => <Tooltip title={environmentText(row.environment)}><span>{environmentText(row.environment)}</span></Tooltip> },
    ];

    const compareRows: CompareRow[] = Array.from(new Set(selectedReports.flatMap(report => report.results.map(result => result.name)))).map(name => ({ name }));
    const compareColumns: TableColumnsType<CompareRow> = [
        { title: t("用例"), dataIndex: 'name', ellipsis: true },
        ...selectedReports.map(report => ({
            title: `v${report.agent_version} · ${time(report.created_at)}`,
            key: report.id,
            width: 200,
            render: (_: unknown, row: CompareRow) => {
                const result = report.results.find(item => item.name === row.name);
                return result ? <Tag color={statusColor(result)}>{statusLabel(result)}</Tag> : <span className="hint">{t("无")}</span>;
            },
        })),
    ];

    return <>
        <Alert error={profiles.error || models.error || connections.error || cases.error || reports.error || latestReports.error || trend.error || action.error} notice={action.notice}/>
        <div className="agent-evaluation-overview">
            <div>
                <span className="agent-nav-eyebrow">{t("评测概览")}</span>
                <strong>{latest ? t("最近评测：v{{v0}} · {{v1}}/{{v2}} 通过", { v0: latest.agent_version, v1: latestPassed, v2: latestTotal }) : t("尚未运行评测")}</strong>
                <p className="agent-muted">{latest ? time(latest.created_at) : t("运行评测后查看发布版本的质量结果")}</p>
                {latest && <p className="agent-muted">{t("待复核")}: {latestReview} · {t("评分/执行错误")}: {latestErrors}</p>}
            </div>
            <div className="agent-evaluation-actions">
                <p className="hint">{publishedVersion ? t("评测运行于已发布版本 v{{v0}}，不包含当前草稿修改。", { v0: publishedVersion }) : t("请先发布 Agent 后再运行评测。")}</p>
                <Button type="primary" disabled={!canRun} loading={action.busy} onClick={() => void action.run(async () => {
                    await post(`/agents/${agentId}/evaluate`, options());
                    refresh();
                }, t("评测完成"))}>{action.busy ? t("处理中…") : t("运行评测")}</Button>
            </div>
        </div>
        <div className="agent-evaluation-stats">
            <div className="agent-evaluation-stat">
                <Statistic title={t("最近通过率")} value={latestRate} suffix="%"/>
                <span className="hint">{latest ? t("最近一次：v{{v0}} · {{v1}}", { v0: latest.agent_version, v1: time(latest.created_at) }) : t("尚未运行评测")}</span>
            </div>
            <div className="agent-evaluation-stat"><Statistic title={t("待复核")} value={latestReview}/></div>
            <div className="agent-evaluation-stat agent-evaluation-stat-error"><Statistic title={t("评分/执行错误")} value={latestErrors}/></div>
            <div className="agent-evaluation-stat"><Statistic title={t("用例总数")} value={cases.data?.length ?? 0}/></div>
        </div>
        {latest && publishedVersion && latest.agent_version !== publishedVersion && <p className="hint">{t("评测结果对应 v{{v0}}，与当前发布版本不一致，建议重新评测。", { v0: latest.agent_version })}</p>}
        <div className="split">
            <Field label={t("Judge 方案（分析模式）")}>
                <select value={judge} disabled={locked} onChange={event => setJudge(event.target.value)}>
                    <option value="">{t("不启用")}</option>
                    {profiles.data?.filter(profile => profile.enabled && profile.published_version && profile.spec.operation === 'chat').map(profile => <option key={profile.id} value={profile.id}>{profile.name} · v{profile.published_version}{demoProfile(profile.id) ? `（${t("演示模型")}）` : ''}</option>)}
                </select>
            </Field>
            <Field label={t("相似度 Embedding 方案")}>
                <select value={embedding} disabled={locked} onChange={event => setEmbedding(event.target.value)}>
                    <option value="">{t("不启用")}</option>
                    {profiles.data?.filter(profile => profile.enabled && profile.published_version && profile.spec.operation === 'embed').map(profile => <option key={profile.id} value={profile.id}>{profile.name} · v{profile.published_version}</option>)}
                </select>
            </Field>
        </div>
        <p className="hint">{t("Judge 尚未使用人工标注集完成校准，只输出分析并进入待复核；向量相似度不替代业务规则。演示 Chat 不提供有效 Judge 评分。")}</p>
        {demoProfile(judge) && <p className="hint">{t("演示模型不能提供 Judge 评分，请改选真实 Chat 方案。")}</p>}
        {Boolean(trend.data?.points.length) && <div className="agent-evaluation-trend">
            <div className="agent-evaluation-trend-head">
                <span className="agent-nav-eyebrow">{t("质量趋势")}</span>
                <span className="hint">{t("最近 {{v0}} 次评测通过率", { v0: trend.data?.points.length ?? 0 })}</span>
            </div>
            <div className="agent-evaluation-spark">
                {trend.data?.points.map(point => <div className="agent-evaluation-spark-item" key={point.id} title={`v${point.agent_version} · ${point.passed}/${point.total} · ${time(point.created_at)}`}>
                    <div className="agent-evaluation-spark-track">
                        <div className={`agent-evaluation-spark-fill ${point.pass_rate === 1 ? 'ok' : point.pass_rate >= 0.5 ? 'warn' : 'bad'}`} style={{ height: `${Math.round(point.pass_rate * 100)}%` }}/>
                    </div>
                    <small>v{point.agent_version}</small>
                </div>)}
            </div>
        </div>}
        <div className="agent-evaluation-sections">
            <Panel title={t("测试用例")} action={<Space size={4}>
                <Button size="small" disabled={action.busy} onClick={downloadTemplate}>{t("下载模板")}</Button>
                <Button size="small" loading={importing} disabled={locked} onClick={() => fileInput.current?.click()}>{t("导入用例")}</Button>
                <Button size="small" disabled={locked} onClick={() => { setAddDraft(blankDraft()); setAddOpen(true); }}>{t("新增用例")}</Button>
                <input ref={fileInput} type="file" accept=".xlsx" hidden onChange={event => {
                    const file = event.target.files?.[0];
                    event.target.value = '';
                    if (file) importCases(file);
                }}/>
            </Space>}>
                <p className="hint">{t("业务工具全部使用模拟结果，不会调用真实业务接口。真实模型模式仍会调用模型并消耗额度。")}</p>
                <p className="hint">{t("先下载模板，填写后导入；同名的用例会被覆盖，新名称会新增。")}</p>
                <Alert error={importDetail} notice={importNotice}/>
                <Table<EvaluationCase>
                    className="agent-evaluation-table agent-evaluation-case-table"
                    rowKey="id"
                    size="small"
                    dataSource={cases.data ?? []}
                    columns={caseColumns}
                    scroll={{ x: 980 }}
                    locale={{ emptyText: <Empty>{t("暂无记录")}</Empty> }}
                    expandable={{
                        expandedRowKeys: expandedCaseKeys,
                        onExpandedRowsChange: keys => setExpandedCaseKeys(keys.map(String)),
                        expandedRowRender: testCase => <CaseRunResult result={caseResults[testCase.id]}/>,
                    }}
                    pagination={{ pageSize: 10, size: 'small', showSizeChanger: false, showTotal: total => t("共 {{v0}} 条用例", { v0: total }) }}
                />
            </Panel>
            <Panel title={t("评测报告")} action={<Space size={8}>
                <Select size="small" value={reportVersion} style={{ minWidth: 130 }} disabled={locked} onChange={value => { setReportVersion(value); setReportPage(1); setCompareIds([]); }} options={[{ value: 'all' as const, label: t("全部版本") }, ...(reports.data?.versions || []).map(version => ({ value: version, label: `v${version}` }))]}/>
                <Button size="small" disabled={selectedReports.length !== 2} onClick={() => setCompareOpen(true)}>{t("对比")}</Button>
                {compareIds.length > 0 && <Button size="small" type="text" onClick={() => setCompareIds([])}>{t("清空选择")}</Button>}
            </Space>}>
                {selectedReports.length > 0 && <p className="hint">{t("已选择 {{v0}}/2 份报告", { v0: selectedReports.length })}</p>}
                <Table<EvaluationReport>
                    className="agent-evaluation-table agent-evaluation-report-table"
                    rowKey="id"
                    size="small"
                    dataSource={pageReports}
                    columns={reportColumns}
                    scroll={{ x: 760 }}
                    pagination={false}
                    locale={{ emptyText: <Empty>{t("运行后查看发布版本的评测结果")}</Empty> }}
                    rowSelection={{
                        selectedRowKeys: compareIds,
                        onChange: keys => setCompareIds(keys.map(String).slice(-2)),
                        columnWidth: 40,
                    }}
                    expandable={{
                        expandedRowRender: report => <ReportCasesTable report={report}/>,
                    }}
                />
                {(reports.data?.total || 0) > 0 && <Pagination className="agent-pagination" size="small" current={reportPage} pageSize={reportPageSize} total={reports.data?.total || 0} showSizeChanger pageSizeOptions={[5, 10, 20]} showTotal={total => t("共 {{v0}} 条报告", { v0: total })} onChange={(page, size) => { setReportPage(size === reportPageSize ? page : 1); setReportPageSize(size); setCompareIds([]); }}/>}
            </Panel>
        </div>
        <Modal open={addOpen} title={t("新增用例")} onCancel={() => setAddOpen(false)} footer={null} destroyOnClose>
            <form onSubmit={submitAdd}>
                <fieldset disabled={locked}>
                    <CaseFields draft={addDraft} onChange={setAddDraft}/>
                    <Button htmlType="submit" type="primary" disabled={locked}>{t("保存用例")}</Button>
                </fieldset>
            </form>
        </Modal>
        <Modal open={!!editing} title={t("编辑用例")} onCancel={() => setEditing(null)} footer={null} destroyOnClose>
            <form onSubmit={submitEdit}>
                <fieldset disabled={locked}>
                    <CaseFields draft={editDraft} onChange={setEditDraft}/>
                    <Button htmlType="submit" type="primary" disabled={locked}>{t("保存")}</Button>
                </fieldset>
            </form>
        </Modal>
        <Modal open={compareOpen} title={t("报告对比")} width={900} footer={<Button onClick={() => setCompareOpen(false)}>{t("关闭")}</Button>} onCancel={() => setCompareOpen(false)}>
            <h4 className="agent-evaluation-compare-title">{t("运行环境")}</h4>
            <Descriptions size="small" bordered column={1} items={[
                { key: 'model', label: t("模型方案"), children: compareEnvironment(selectedReports, report => report.environment?.model.version ? `v${report.environment.model.version}` : t("无")) },
                { key: 'memory', label: t("记忆"), children: compareEnvironment(selectedReports, report => report.environment ? (report.environment.memory.enabled ? t("开启") : t("关闭")) : t("未知")) },
                { key: 'knowledge', label: t("知识库"), children: compareEnvironment(selectedReports, report => report.environment?.knowledge.length ? t("{{v0}} 个", { v0: report.environment.knowledge.length }) : t("无")) },
            ]}/>
            <h4 className="agent-evaluation-compare-title">{t("用例结果")}</h4>
            <Table<CompareRow>
                rowKey="name"
                size="small"
                dataSource={compareRows}
                columns={compareColumns}
                pagination={false}
                scroll={{ x: 560 }}
                locale={{ emptyText: <Empty>{t("暂无记录")}</Empty> }}
                rowClassName={row => {
                    const states = selectedReports.map(report => report.results.find(item => item.name === row.name)?.passed);
                    return states.length === 2 && states[0] !== states[1] ? 'agent-evaluation-compare-diff' : '';
                }}
            />
        </Modal>
    </>;
}

function compareEnvironment(reports: EvaluationReport[], value: (report: EvaluationReport) => string) {
    return <Space size={16} wrap>{reports.map(report => <span key={report.id}><strong>v{report.agent_version}</strong>：{value(report)}</span>)}</Space>;
}

function AssertionTags({ spec }: { spec: EvaluationCaseSpec }) {
    const tags = [
        spec.expected_contains?.length ? <Tag key="include" color="blue">{t("含 {{v0}}", { v0: spec.expected_contains.length })}</Tag> : null,
        spec.forbidden_contains?.length ? <Tag key="forbid" color="red">{t("禁 {{v0}}", { v0: spec.forbidden_contains.length })}</Tag> : null,
        spec.expected_tools?.length ? <Tag key="tools" color="purple">{t("工具 {{v0}}", { v0: spec.expected_tools.length })}</Tag> : null,
    ].filter(Boolean);
    if (!tags.length) return <span className="hint">{t("无")}</span>;
    return <Space size={4} wrap>{tags}</Space>;
}

function LatestResult({ result }: { result?: EvaluationResult }) {
    if (!result) return <span className="hint">—</span>;
    return <Tag color={statusColor(result)}>{statusLabel(result)}</Tag>;
}

function CaseRunResult({ result }: { result?: EvaluationResult }) {
    if (!result) return <p className="hint">{t("暂无记录")}</p>;
    return <div className="agent-evaluation-result">
        <Tag color={statusColor(result)}>{statusLabel(result)}</Tag>
        <span className="hint">{t("单条运行结果")}</span>
        <pre>{result.error || result.output || t("无")}</pre>
        <p className="hint">{t("工具：")}{result.tools.join(' → ') || t("无")}</p>
    </div>;
}

function ReportCasesTable({ report }: { report: EvaluationReport }) {
    const rows = report.results.map((result, index) => ({ ...result, key: result.case_id || `${report.id}-${index}` }));
    const columns: TableColumnsType<EvaluationResult & { key: string }> = [
        { title: t("用例"), dataIndex: 'name', width: 200, ellipsis: true },
        { title: t("最近结果"), key: 'status', width: 120, render: (_, row) => <Tag color={statusColor(row)}>{statusLabel(row)}</Tag> },
        { title: t("工具"), key: 'tools', width: 180, render: (_, row) => <span className="hint">{row.tools.join(' → ') || t("无")}</span> },
        { title: t("输出"), key: 'output', ellipsis: true, render: (_, row) => row.error || row.output || t("无") },
    ];
    return <div className="agent-evaluation-report-detail">
        <p className="agent-evaluation-env hint">{environmentText(report.environment)}</p>
        <Table<EvaluationResult & { key: string }>
            className="agent-evaluation-table"
            rowKey="key"
            size="small"
            dataSource={rows}
            columns={columns}
            pagination={false}
            scroll={{ x: 700 }}
            expandable={{
                rowExpandable: row => Boolean(row.judge || row.judge_error || row.similarity),
                expandedRowRender: row => <pre>{JSON.stringify({ judge: row.judge, judge_error: row.judge_error ? errorText(row.judge_error) : null, similarity: row.similarity }, null, 2)}</pre>,
            }}
        />
    </div>;
}

function CaseFields({ draft, onChange }: { draft: CaseDraft; onChange: (draft: CaseDraft) => void }) {
    return <>
        <Field label={t("用例名称")}><input required value={draft.name} onChange={event => onChange({ ...draft, name: event.target.value })}/></Field>
        <Field label={t("用户输入")}><textarea required rows={2} value={draft.input} onChange={event => onChange({ ...draft, input: event.target.value })}/></Field>
        <Field label={t("预期包含文本（每行一项）")}><textarea rows={2} value={draft.expected_contains} onChange={event => onChange({ ...draft, expected_contains: event.target.value })}/></Field>
        <Field label={t("禁止包含文本（每行一项，可选）")}><textarea rows={2} value={draft.forbidden_contains} onChange={event => onChange({ ...draft, forbidden_contains: event.target.value })}/></Field>
        <Field label={t("预期工具（每行一个，可选）")}><textarea rows={2} placeholder={t("例如：knowledge_search")} value={draft.expected_tools} onChange={event => onChange({ ...draft, expected_tools: event.target.value })}/></Field>
        <Field label={t("参考答案（每行一条，可选）")}><textarea value={draft.reference_answers} onChange={event => onChange({ ...draft, reference_answers: event.target.value })}/></Field>
        <Field label={t("模拟工具结果 JSON")}><textarea className="mono" rows={6} value={draft.mock_tools} onChange={event => onChange({ ...draft, mock_tools: event.target.value })}/></Field>
        <p className="hint">{t("至少填写一项断言：预期包含、禁止包含或预期工具。")}</p>
    </>;
}
