import { t } from '../../i18n/index';
import { useState } from 'react';
import { type Audit, type MemorySummary, type Run, type Summary } from '../../shared/api/client';
import { Alert, Badge, Panel, Trace, time, useAccess, useResource } from '../../shared/components/ui';
export function Metrics({ value }: {
    value: Summary | null;
}) {
    const cards = [[t("近 24h 运行"), value?.total], [t("执行中 / 排队"), value ? `${value.running} / ${value.queued}` : '—'], [t("成功完成"), value?.completed], [t("失败运行"), value?.failed], [t("P95 执行时间"), value?.p95_seconds == null ? '—' : `${Number(value.p95_seconds).toFixed(2)}s`], [t("P50 首字延迟"), value?.ttfc?.p50_ms == null ? '—' : `${Math.round(Number(value.ttfc.p50_ms))}ms`], [t("模型 Tokens"), value?.tokens]];
    return <div className="metrics">{cards.map(([label, count]) => <div className="metric" key={label}><span>{label}</span><strong>{count ?? '—'}</strong></div>)}</div>;
}

/** Phase 4 memory governance observability (FR-7): rates, backlog, decay and audit aggregation. */
export function MemoryMetrics({ value }: {
    value: MemorySummary | null;
}) {
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
    const runs = useResource<Run[]>('/observability/runs', 3000);
    const { admin } = useAccess();
    const audit = useResource<Audit[]>(admin ? '/audit' : null, 5000);
    const memory = useResource<MemorySummary>('/observability/memory', 5000);
    const [selected, setSelected] = useState<string | null>(null);
    const gateways = useResource<{
        models: Record<string, unknown>[];
        tools: Record<string, unknown>[];
        known_usage: Record<string, unknown>[];
    }>('/observability/gateways', 5000);
    const correlation = useResource<Record<string, unknown>>(selected ? `/observability/runs/${selected}/trace` : null);
    return <><Alert error={gateways.error || correlation.error || summary.error || runs.error || audit.error || memory.error}/><Metrics value={summary.data}/><Panel title={t("记忆观测")}><MemoryMetrics value={memory.data}/>{memory.data && memory.data.extraction_daily.length > 0 && <details><summary>{t("记忆抽取量（近 7 天）")}</summary><div className="table-scroll"><table><thead><tr><th>{t("日期")}</th><th>{t("运行")}</th><th>{t("新增")}</th><th>{t("更新")}</th><th>{t("待确认")}</th><th>{t("丢弃")}</th><th>{t("脱敏")}</th></tr></thead><tbody>{memory.data.extraction_daily.map(day => <tr key={day.day}><td>{day.day.slice(0, 10)}</td><td>{day.runs}</td><td>{day.added}</td><td>{day.updated}</td><td>{day.pending}</td><td>{day.dropped}</td><td>{day.pii_masked}</td></tr>)}</tbody></table></div></details>}{memory.data && memory.data.governance.length > 0 && <details><summary>{t("治理操作（近 24 小时）")}</summary><div className="table-scroll"><table><thead><tr><th>{t("操作")}</th><th>{t("次数")}</th></tr></thead><tbody>{memory.data.governance.map(row => <tr key={row.action}><td className="mono">{row.action}</td><td>{row.n}</td></tr>)}</tbody></table></div></details>}</Panel><div className="split"><Panel title={t("最近运行")}><div className="table-scroll"><table><thead><tr><th>{t("运行")}</th><th>{t("状态")}</th><th>{t("首字延迟")}</th><th>{t("创建时间")}</th></tr></thead><tbody>{runs.data?.map(run => <tr key={run.id}><td><button className="text-button mono" onClick={() => setSelected(run.id)}>{run.id.slice(0, 8)}</button>{run.error_code && <small className="error-text">{run.error_code}</small>}</td><td><Badge value={run.status}/></td><td>{run.ttfc_ms == null ? t("未测量") : `${Math.round(Number(run.ttfc_ms))}ms`}</td><td>{time(run.created_at)}</td></tr>)}</tbody></table></div></Panel><Panel title={t("运行详情")}><Trace runId={selected}/>{correlation.data && <details><summary>{t("模型与工具调用关联")}</summary><pre>{JSON.stringify(correlation.data, null, 2)}</pre></details>}</Panel></div><Panel title={t("近 24 小时网关计量")}><p>{t("按操作、结果和计量单位分别统计；未返回的用量不按零计算。")}</p><pre>{JSON.stringify(gateways.data, null, 2)}</pre></Panel>{admin && <Panel title={t("操作审计")}><div className="table-scroll"><table><thead><tr><th>{t("操作")}</th><th>{t("操作者")}</th><th>{t("资源")}</th><th>{t("时间")}</th></tr></thead><tbody>{audit.data?.map(a => <tr key={a.id}><td>{a.action}</td><td>{a.actor}</td><td className="mono">{a.resource.slice(0, 12)}</td><td>{time(a.created_at)}</td></tr>)}</tbody></table></div></Panel>}</>;
}
