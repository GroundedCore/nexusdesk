import { t } from '../../i18n/index';
import { useState } from 'react';
import { Select, Table } from 'antd';
import type { TableColumnsType } from 'antd';
import { type Audit, type MemoryExtractionDay, type MemorySummary, type Run, type Summary } from '../../shared/api/client';
import { Alert, Badge, Panel, Trace, time, useAccess, useResource } from '../../shared/components/ui';

interface Paged<T> {
    items: T[];
    total: number;
    page: number;
    page_size: number;
}

interface AuditPaged extends Paged<Audit> {
    action_types: { action: string; count: number }[];
}

interface GatewayModel {
    operation: string;
    status: string;
    calls: number;
    average_ms: number;
    p95_ms: number;
}

interface GatewayTool {
    adapter_type: string;
    effect: string;
    status: string;
    calls: number;
    average_ms: number;
}

interface GatewayUsage {
    operation: string;
    unit: string;
    amount: number;
}

interface GatewaySummary {
    models: GatewayModel[];
    tools: GatewayTool[];
    known_usage: GatewayUsage[];
    window_hours: number;
}

const PAGE_SIZE = 10;
const PAGE_SIZE_OPTIONS = [10, 20, 50];

/** Aggregate tables (memory, gateway billing) are small, so paging happens in the browser. */
const clientPagination = {
    pageSize: PAGE_SIZE,
    showSizeChanger: false,
    showTotal: (total: number) => t("总共 {{v0}} 条", { v0: total }),
};

/** Runs and audit records grow without bound, so their paging happens in SQL. */
function serverPagination(page: number, pageSize: number, total: number, onChange: (page: number, pageSize: number) => void) {
    return {
        current: page,
        pageSize,
        total,
        showSizeChanger: true,
        pageSizeOptions: PAGE_SIZE_OPTIONS,
        showTotal: (value: number) => t("总共 {{v0}} 条", { v0: value }),
        onChange,
    };
}

export function Metrics({ value }: { value: Summary | null }) {
    const cards = [[t("近 24h 运行"), value?.total], [t("执行中 / 排队"), value ? `${value.running} / ${value.queued}` : '—'], [t("成功完成"), value?.completed], [t("失败运行"), value?.failed], [t("P95 执行时间"), value?.p95_seconds == null ? '—' : `${Number(value.p95_seconds).toFixed(2)}s`], [t("P50 首字延迟"), value?.ttfc?.p50_ms == null ? '—' : `${Math.round(Number(value.ttfc.p50_ms))}ms`], [t("模型 Tokens"), value?.tokens]];
    return <div className="metrics">{cards.map(([label, count]) => <div className="metric" key={label}><span>{label}</span><strong>{count ?? '—'}</strong></div>)}</div>;
}

/** Phase 4 memory governance observability (FR-7): rates, backlog, decay and audit aggregation. */
export function MemoryMetrics({ value }: { value: MemorySummary | null }) {
    const rate = value && value.hit_rate.customer_runs > 0 ? `${Math.round((value.hit_rate.injected_runs / value.hit_rate.customer_runs) * 100)}%` : '—';
    const oldest = value?.backlog.oldest_age_seconds == null ? '—' : `${Math.round(Number(value.backlog.oldest_age_seconds) / 3600)}h`;
    const cards = [
        [t("记忆注入命中率"), `${rate} (${value?.hit_rate.injected_runs ?? '—'}/${value?.hit_rate.customer_runs ?? '—'})`],
        [t("待确认积压"), value ? `${value.backlog.pending} · ${t("最久 {{v0}}", { v0: oldest })}` : '—'],
        [t("PII 脱敏命中（24h）"), value?.pii_masked],
        [t("强化任务（完成 / 失败）"), value ? `${value.reinforce.tasks_done} / ${value.reinforce.tasks_failed}` : '—'],
        [t("强化刷新条目"), value?.reinforce.ids_refreshed],
        [t("衰退迁移（待确认 / 过期）"), value ? `${value.decay.decay_pending} / ${value.decay.decay_expired}` : '—'],
        [t("容量淘汰（7 天）"), value?.decay.evicted],
        [t("过期恢复（7 天）"), value?.decay.expired_restored],
        [t("记忆检索（调用 / 空结果）"), value ? `${value.search.calls} / ${value.search.empty}` : '—'],
    ];
    return <div className="metrics">{cards.map(([label, count]) => <div className="metric" key={label}><span>{label}</span><strong>{count ?? '—'}</strong></div>)}</div>;
}

export function ObservabilityPage() {
    const summary = useResource<Summary>('/observability/summary', 5000);
    const { admin } = useAccess();
    const [runPage, setRunPage] = useState(1);
    const [runPageSize, setRunPageSize] = useState(PAGE_SIZE);
    const runs = useResource<Paged<Run>>(`/observability/runs?page=${runPage}&page_size=${runPageSize}`, 3000);
    const [auditPage, setAuditPage] = useState(1);
    const [auditPageSize, setAuditPageSize] = useState(PAGE_SIZE);
    const [auditActionType, setAuditActionType] = useState<string>();
    const auditTypeQuery = auditActionType ? `&action_type=${encodeURIComponent(auditActionType)}` : '';
    const audit = useResource<AuditPaged>(admin ? `/audit?page=${auditPage}&page_size=${auditPageSize}${auditTypeQuery}` : null, 5000);
    const memory = useResource<MemorySummary>('/observability/memory', 5000);
    const [selected, setSelected] = useState<string | null>(null);
    const gateways = useResource<GatewaySummary>('/observability/gateways', 5000);
    const correlation = useResource<Record<string, unknown>>(selected ? `/observability/runs/${selected}/trace` : null);

    const runColumns: TableColumnsType<Run> = [
        {
            title: t("运行"), dataIndex: 'id', width: 150,
            render: (id: string, run) => <>
                <button className="text-button mono" onClick={() => setSelected(id)}>{id.slice(0, 8)}</button>
                {run.error_code && <><br/><small className="error-text">{run.error_code}</small></>}
            </>,
        },
        { title: t("状态"), dataIndex: 'status', width: 100, render: (value: string) => <Badge value={value}/> },
        {
            title: t("首字延迟"), dataIndex: 'ttfc_ms', width: 110,
            render: (value: number | null | undefined) => value == null ? t("未测量") : `${Math.round(Number(value))}ms`,
        },
        { title: t("创建时间"), dataIndex: 'created_at', width: 175, render: (value: string) => time(value) },
    ];

    const auditColumns: TableColumnsType<Audit> = [
        { title: t("操作"), dataIndex: 'action', render: (value: string) => <span className="mono">{value}</span> },
        { title: t("操作者"), dataIndex: 'actor', ellipsis: true },
        { title: t("访问 IP"), dataIndex: 'ip', width: 150, render: (value?: string | null) => <span className="mono">{value || '—'}</span> },
        { title: t("资源"), dataIndex: 'resource', width: 150, render: (value: string) => <span className="mono">{value.slice(0, 12)}</span> },
        { title: t("时间"), dataIndex: 'created_at', width: 175, render: (value: string) => time(value) },
    ];

    const extractionColumns: TableColumnsType<MemoryExtractionDay> = [
        { title: t("日期"), dataIndex: 'day', render: (value: string) => value.slice(0, 10) },
        { title: t("运行"), dataIndex: 'runs' },
        { title: t("新增"), dataIndex: 'added' },
        { title: t("更新"), dataIndex: 'updated' },
        { title: t("待确认"), dataIndex: 'pending' },
        { title: t("丢弃"), dataIndex: 'dropped' },
        { title: t("脱敏"), dataIndex: 'pii_masked' },
    ];

    const governanceColumns: TableColumnsType<{ action: string; n: number }> = [
        { title: t("操作"), dataIndex: 'action', render: (value: string) => <span className="mono">{value}</span> },
        { title: t("次数"), dataIndex: 'n' },
    ];

    const modelColumns: TableColumnsType<GatewayModel> = [
        { title: t("操作"), dataIndex: 'operation' },
        { title: t("状态"), dataIndex: 'status', render: (value: string) => <Badge value={value}/> },
        { title: t("次数"), dataIndex: 'calls' },
        { title: t("平均耗时"), dataIndex: 'average_ms', render: (value: number) => `${Math.round(Number(value))}ms` },
        { title: t("P95 耗时"), dataIndex: 'p95_ms', render: (value: number) => `${Math.round(Number(value))}ms` },
    ];

    const toolColumns: TableColumnsType<GatewayTool> = [
        { title: t("适配器"), dataIndex: 'adapter_type' },
        { title: t("效果"), dataIndex: 'effect' },
        { title: t("状态"), dataIndex: 'status', render: (value: string) => <Badge value={value}/> },
        { title: t("次数"), dataIndex: 'calls' },
        { title: t("平均耗时"), dataIndex: 'average_ms', render: (value: number) => `${Math.round(Number(value))}ms` },
    ];

    const usageColumns: TableColumnsType<GatewayUsage> = [
        { title: t("操作"), dataIndex: 'operation' },
        { title: t("单位"), dataIndex: 'unit' },
        { title: t("数量"), dataIndex: 'amount', render: (value: number) => Number(value).toLocaleString() },
    ];

    return <>
        <Alert error={gateways.error || correlation.error || summary.error || runs.error || audit.error || memory.error}/>
        <Metrics value={summary.data}/>
        <Panel title={t("记忆观测")}>
            <MemoryMetrics value={memory.data}/>
            {memory.data && memory.data.extraction_daily.length > 0 && <details>
                <summary>{t("记忆抽取量（近 7 天）")}</summary>
                <Table<MemoryExtractionDay> rowKey="day" size="small" columns={extractionColumns}
                    dataSource={memory.data.extraction_daily} pagination={clientPagination} scroll={{ x: 700 }}/>
            </details>}
            {memory.data && memory.data.governance.length > 0 && <details>
                <summary>{t("治理操作（近 24 小时）")}</summary>
                <Table<{ action: string; n: number }> rowKey="action" size="small" columns={governanceColumns}
                    dataSource={memory.data.governance} pagination={clientPagination}/>
            </details>}
        </Panel>
        <div className="split">
            <Panel title={t("最近运行")}>
                <Table<Run> rowKey="id" size="small" loading={runs.loading} columns={runColumns}
                    dataSource={runs.data?.items || []} scroll={{ x: 560 }} locale={{ emptyText: t("暂无数据") }}
                    pagination={serverPagination(runPage, runPageSize, runs.data?.total || 0, (page, size) => {
                        setRunPage(size === runPageSize ? page : 1);
                        setRunPageSize(size);
                    })}/>
            </Panel>
            <Panel title={t("运行详情")}>
                <Trace runId={selected}/>
                {correlation.data && <details><summary>{t("模型与工具调用关联")}</summary><pre>{JSON.stringify(correlation.data, null, 2)}</pre></details>}
            </Panel>
        </div>
        <Panel title={t("近 24 小时网关计量")}>
            <p>{t("按操作、结果和计量单位分别统计；未返回的用量不按零计算。")}</p>
            <h3 className="gateway-group-title">{t("模型调用")}</h3>
            <Table<GatewayModel> rowKey={row => `${row.operation}-${row.status}`} size="small" columns={modelColumns}
                dataSource={gateways.data?.models || []} pagination={clientPagination} scroll={{ x: 640 }} locale={{ emptyText: t("暂无数据") }}/>
            <h3 className="gateway-group-title">{t("工具调用")}</h3>
            <Table<GatewayTool> rowKey={row => `${row.adapter_type}-${row.effect}-${row.status}`} size="small" columns={toolColumns}
                dataSource={gateways.data?.tools || []} pagination={clientPagination} scroll={{ x: 640 }} locale={{ emptyText: t("暂无数据") }}/>
            <h3 className="gateway-group-title">{t("用量")}</h3>
            <Table<GatewayUsage> rowKey={row => `${row.operation}-${row.unit}`} size="small" columns={usageColumns}
                dataSource={gateways.data?.known_usage || []} pagination={clientPagination} locale={{ emptyText: t("暂无数据") }}/>
        </Panel>
        {admin && <Panel title={t("操作审计")} action={
            <Select
                aria-label={t("操作类型")}
                className="audit-type-filter"
                allowClear
                showSearch
                optionFilterProp="label"
                placeholder={t("全部类型")}
                value={auditActionType}
                onChange={(value?: string) => {
                    setAuditActionType(value);
                    setAuditPage(1);
                }}
                options={(audit.data?.action_types || []).map(item => ({
                    value: item.action,
                    label: `${item.action} (${item.count})`,
                }))}
            />
        }>
            <Table<Audit> rowKey="id" size="small" loading={audit.loading} columns={auditColumns}
                dataSource={audit.data?.items || []} scroll={{ x: 920 }}
                locale={{ emptyText: auditActionType ? t("暂无符合条件的审计记录") : t("暂无记录") }}
                pagination={serverPagination(auditPage, auditPageSize, audit.data?.total || 0, (page, size) => {
                    setAuditPage(size === auditPageSize ? page : 1);
                    setAuditPageSize(size);
                })}/>
        </Panel>}
    </>;
}
