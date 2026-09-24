import { t, dateTime, number } from '../../i18n/index';
import { useState } from 'react';
import { Drawer, Pagination } from 'antd';
import { api } from '../../shared/api/client';
import { Alert, Empty, Field, Panel, useAction, useResource } from '../../shared/components/ui';
const root = '/model-gateway';
interface Call {
    id: string;
    operation: string;
    status: string;
    error_code: string | null;
    duration_ms: number | null;
    profile_version: number;
    profile_name: string;
    key_name: string | null;
    created_at: string;
    estimated_cost: string | null;
    unknown_cost_attempts: number;
}
interface Statistics {
    summary: {
        requests: number;
        completed: number;
        failed: number;
        blocked: number;
        average_ms: number | null;
        estimated_cost: string | null;
        unknown_cost_attempts: number;
    };
    daily: {
        day: string;
        requests: number;
        failed: number;
        estimated_cost: string | null;
    }[];
    operations: {
        operation: string;
        requests: number;
    }[];
    models: {
        model_id: string;
        name: string;
        attempts: number;
        estimated_cost: string | null;
        unknown_cost_attempts: number;
    }[];
}
const statusName = (): Record<string, string> => ({ completed: t("已完成"), failed: t("失败"), running: t("执行中"), cancelled: t("已取消") });
const money = (value: string | null) => value == null ? t("未知") : number(Number(value),{style:'currency',currency:'CNY',minimumFractionDigits:6,maximumFractionDigits:6});
function exportCsv(name: string, rows: unknown[][]) {
    const csv = rows.map(row => row.map(value => { const raw = String(value ?? ''); const safe = /^[=+\-@\t\r]/.test(raw) ? `'${raw}` : raw; return `"${safe.replaceAll('"', '""')}"`; }).join(',')).join('\r\n');
    const url = URL.createObjectURL(new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = name;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function GatewayReports({ mode }: {
    mode: 'logs' | 'monitor';
}) {
    const [from, setFrom] = useState(new Date(Date.now() - 7 * 86400000).toISOString().slice(0, 10));
    const [to, setTo] = useState(new Date().toISOString().slice(0, 10));
    const [page, setPage] = useState(1);
    const [query, setQuery] = useState('');
    const [status, setStatus] = useState('all');
    const [detail, setDetail] = useState('');
    const action = useAction();
    const start = from ? new Date(`${from}T00:00:00Z`).getTime() : NaN;
    const end = to ? new Date(`${to}T00:00:00Z`).getTime() + 86400000 : NaN;
    const valid = Number.isFinite(start) && Number.isFinite(end) && start < end && end - start <= 366 * 86400000;
    const range = valid ? `since=${encodeURIComponent(new Date(start).toISOString())}&until=${encodeURIComponent(new Date(end).toISOString())}` : '';
    const calls = useResource<{
        items: Call[];
        total: number;
    }>(mode === 'logs' && valid ? `${root}/call-records?${range}&page=${page}&page_size=20&q=${encodeURIComponent(query)}${status === 'all' ? '' : `&status=${status}`}` : null, 10000);
    const stats = useResource<Statistics>(mode === 'monitor' && valid ? `${root}/statistics?${range}` : null, 10000);
    const summary = stats.data?.summary;
    return <Panel title={mode === 'logs' ? t("调用日志") : t("监控与费用")}>
    <Alert error={action.error || calls.error || stats.error} notice={action.notice}/>
    <div className="gateway-date-toolbar"><Field label={t("开始日期（UTC）")}><input type="date" value={from} onChange={e => { setFrom(e.target.value); setPage(1); }}/></Field><Field label={t("结束日期（UTC）")}><input type="date" value={to} onChange={e => { setTo(e.target.value); setPage(1); }}/></Field><button className="secondary" onClick={() => { calls.refresh(); stats.refresh(); }}>{t("刷新")}</button></div>{!valid && <Alert error={t("请选择有效日期范围，最多 366 天")}/>}
    {mode === 'logs' ? <>
      <div className="gateway-toolbar"><input aria-label={t("搜索调用日志")} value={query} placeholder={t("搜索调用 ID、方案名称、能力或错误码")} onChange={e => { setQuery(e.target.value); setPage(1); }}/><select aria-label={t("调用状态")} value={status} onChange={e => { setStatus(e.target.value); setPage(1); }}><option value="all">{t("全部状态")}</option>{Object.entries(statusName()).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><button disabled={!calls.data?.items.length} onClick={() => exportCsv(t("模型调用日志.csv"), [[t("时间"), t("调用 ID"), t("方案"), t("状态"), t("耗时 ms"), t("估算费用 CNY"), t("错误码")], ...calls.data!.items.map(call => [call.created_at, call.id, call.profile_name, call.status, call.duration_ms, call.estimated_cost, call.error_code])])}>{t("导出当前页")}</button></div>
      <div className="table-scroll"><table><thead><tr><th>{t("时间")}</th><th>{t("方案 / 版本")}</th><th>{t("能力")}</th><th>{t("密钥")}</th><th>{t("状态")}</th><th>{t("耗时")}</th><th>{t("估算费用")}</th><th>{t("错误码")}</th><th>{t("操作")}</th></tr></thead><tbody>{calls.data?.items.map(call => <tr key={call.id}><td>{dateTime(call.created_at)}</td><td>{call.profile_name || call.id} / v{call.profile_version}</td><td>{call.operation}</td><td>{call.key_name || t("内部调用")}</td><td><span className={`gateway-call-status ${call.status}`}>{statusName()[call.status] || call.status}</span></td><td>{call.duration_ms == null ? '—' : `${call.duration_ms} ms`}</td><td>{money(call.estimated_cost)}{call.unknown_cost_attempts > 0 && t("（不完整）")}</td><td>{call.error_code || '—'}</td><td><button className="text-button" disabled={action.busy} onClick={() => void action.run(async () => setDetail(JSON.stringify(await api(`${root}/calls/${call.id}`), null, 2)), '')}>{t("查看尝试与用量")}</button></td></tr>)}</tbody></table></div>
      {calls.loading && <Empty>{t("正在加载\u2026")}</Empty>}{calls.data?.total === 0 && <Empty>{t("所选范围内没有匹配的调用记录")}</Empty>}<Pagination current={page} pageSize={20} total={calls.data?.total || 0} showSizeChanger={false} onChange={setPage} showTotal={total => t("共 {{v0}} 条", { v0: total })}/>
    </> : <>
      <p className="hint">{t("统计所选日期内的全部记录，每 10 秒刷新。费用为配置价格 \u00D7 返回用量的估算，包含重试尝试；未知用量或未配置价格不会记作零费用。")}</p>
      <div className="gateway-statistics">{[[t("调用次数"), summary?.requests ?? '—'], [t("失败调用"), summary?.failed ?? '—'], [t("平均耗时"), summary?.average_ms == null ? '—' : `${Math.round(summary.average_ms)} ms`], [t("已知费用估算"), money(summary?.estimated_cost ?? null)], [t("内容拦截"), summary?.blocked ?? '—'], [t("费用未知的尝试"), summary?.unknown_cost_attempts ?? '—']].map(([label, value]) => <div className="gateway-stat" key={label}><span>{label}</span><strong>{value}</strong></div>)}</div>
      <h3>{t("每日调用趋势")}</h3>{stats.data?.daily.length ? <div className="gateway-distribution">{stats.data.daily.map(day => <div key={day.day}><span>{day.day}</span><progress aria-label={t("{{v0}}调用次数", { v0: day.day })} max={Math.max(1, ...stats.data!.daily.map(item => item.requests))} value={day.requests}/><strong>{day.requests}</strong></div>)}</div> : <Empty>{t("当前范围暂无调用数据")}</Empty>}
      <div className="table-scroll"><table><thead><tr><th>{t("模型")}</th><th>{t("尝试次数")}</th><th>{t("已知费用估算")}</th><th>{t("费用未知的尝试")}</th></tr></thead><tbody>{stats.data?.models.map(model => <tr key={model.model_id}><td>{model.name || model.model_id}</td><td>{model.attempts}</td><td>{money(model.estimated_cost)}</td><td>{model.unknown_cost_attempts}</td></tr>)}</tbody></table></div><button className="secondary" disabled={!stats.data?.daily.length} onClick={() => exportCsv(t("模型每日统计.csv"), [[t("日期 UTC"), t("调用次数"), t("失败次数"), t("已知费用估算 CNY")], ...stats.data!.daily.map(day => [day.day, day.requests, day.failed, day.estimated_cost])])}>{t("导出每日统计")}</button>
    </>}
    <Drawer title={t("执行诊断")} open={!!detail} onClose={() => setDetail('')} size={640}><pre>{detail}</pre></Drawer>
  </Panel>;
}
