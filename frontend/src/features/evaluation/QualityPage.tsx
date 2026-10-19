import { useState } from 'react';
import { Pagination } from 'antd';
import { errorText, t } from '../../i18n/index';
import type {
    QualityAgentRow,
    QualityOverview,
    QualityRegressionItem,
    QualityRegressions,
    QualityReviewItem,
    QualityReviews,
} from '../../shared/api/client';
import { Alert, Badge, Empty, Panel, time, useResource } from '../../shared/components/ui';
import './quality.css';

/** Review queues are ordered by urgency, so the chips double as the priority list. */
const FILTERS = [
    ['needs_review', '待复核'],
    ['error', '评分/执行错误'],
    ['failed', '失败'],
    ['all', '全部'],
] as const;

const percent = (value: number | null | undefined) =>
    value == null ? '—' : `${Math.round(value * 100)}%`;

const PAGE_SIZE = 10;

function count(value: number, kind = '') {
    return <span className={`quality-count ${value === 0 ? 'zero' : kind}`}>{value}</span>;
}

/**
 * Only regressions carry a previous value, and the first evaluation of an agent
 * has nothing to compare against, so the arrow stays hidden rather than showing
 * a meaningless zero.
 */
function Delta({ delta }: { delta: number | null }) {
    if (delta == null) return null;
    // An unchanged pass rate is not information; the arrow is only for movement.
    if (delta === 0) return null;
    const tone = delta > 0 ? 'up' : delta < 0 ? 'down' : 'flat';
    return <span className={`quality-delta ${tone}`}>{delta > 0 ? '↑' : '↓'} {percent(Math.abs(delta))}</span>;
}

function AgentCell({ name, version }: { name: string; version: number | null }) {
    return <div className="quality-agent">
        <strong>{name}</strong>
        <span>{version == null ? t("未评测") : `v${version}`}</span>
    </div>;
}

function BoardPagination({ current, total, onChange }: {
    current: number;
    total: number;
    onChange: (page: number) => void;
}) {
    return <div className="quality-pagination">
        <Pagination
            current={current}
            pageSize={PAGE_SIZE}
            total={total}
            showSizeChanger={false}
            hideOnSinglePage
            showTotal={value => t("共 {{v0}} 条", { v0: value })}
            onChange={onChange}
        />
    </div>;
}

function OverviewTable({ rows, onOpen }: { rows: QualityAgentRow[]; onOpen: (agentId: string) => void }) {
    return <div className="table-scroll"><table>
        <thead><tr>
            <th>{t("Agent")}</th>
            <th>{t("用例数")}</th>
            <th>{t("最近评测")}</th>
            <th>{t("通过率")}</th>
            <th>{t("待复核")}</th>
            <th>{t("错误")}</th>
            <th>{t("回归")}</th>
            <th/>
        </tr></thead>
        <tbody>{rows.map(row => <tr key={row.agent_id}>
            <td><AgentCell name={row.agent_name} version={row.agent_version}/></td>
            <td>{count(row.cases)}</td>
            <td className="quality-muted">{row.created_at ? time(row.created_at) : <span>{t("暂无评测报告")}</span>}</td>
            <td><span className="quality-rate"><strong>{percent(row.pass_rate)}</strong><Delta delta={row.delta}/></span></td>
            <td>{count(row.needs_review, 'warn')}</td>
            <td>{count(row.errors, 'alert')}</td>
            <td>{count(row.regressions, 'alert')}</td>
            <td><button className="text-button" onClick={() => onOpen(row.agent_id)}>{t("查看")}</button></td>
        </tr>)}</tbody>
    </table></div>;
}

function ReviewTable({ items, onOpen }: { items: QualityReviewItem[]; onOpen: (agentId: string) => void }) {
    return <div className="table-scroll"><table>
        <thead><tr>
            <th>{t("Agent")}</th>
            <th>{t("用例")}</th>
            <th>{t("状态")}</th>
            <th>{t("原因")}</th>
            <th>{t("最近评测")}</th>
            <th/>
        </tr></thead>
        <tbody>{items.map((item, index) => <tr key={`${item.report_id}-${item.case_id ?? item.name ?? index}`}>
            <td><AgentCell name={item.agent_name} version={item.agent_version}/></td>
            <td>{item.name || '—'}</td>
            <td><Badge value={item.status}/></td>
            <td className={item.error ? 'quality-reason mono' : 'quality-reason'}>
                {item.error || (item.judge_error ? errorText(item.judge_error) : null)
                    || item.rule_violations.join('、') || t("待人工确认")}
            </td>
            <td className="quality-muted">{time(item.created_at)}</td>
            <td><button className="text-button" onClick={() => onOpen(item.agent_id)}>{t("处理")}</button></td>
        </tr>)}</tbody>
    </table></div>;
}

function RegressionTable({ items, onOpen }: { items: QualityRegressionItem[]; onOpen: (agentId: string) => void }) {
    return <div className="table-scroll"><table>
        <thead><tr>
            <th>{t("Agent")}</th>
            <th>{t("用例")}</th>
            <th>{t("较上次")}</th>
            <th>{t("原因")}</th>
            <th>{t("最近评测")}</th>
            <th/>
        </tr></thead>
        <tbody>{items.map((item, index) => <tr key={`${item.report_id}-${item.case_id ?? item.name ?? index}`}>
            <td><AgentCell name={item.agent_name} version={item.agent_version}/></td>
            <td>{item.name || '—'}</td>
            <td><span className="quality-route">
                v{item.previous_agent_version} {t("通过")} <span>→</span> v{item.agent_version} {item.status === 'error' ? t("错误") : t("失败")}
            </span></td>
            <td className={item.error ? 'quality-reason mono' : 'quality-reason'}>
                {item.error || (item.judge_error ? errorText(item.judge_error) : null) || t("待人工确认")}
            </td>
            <td className="quality-muted">{time(item.created_at)}</td>
            <td><button className="text-button" onClick={() => onOpen(item.agent_id)}>{t("处理")}</button></td>
        </tr>)}</tbody>
    </table></div>;
}

export function QualityPage({ onNavigate }: { onNavigate: (path: string) => void }) {
    const [status, setStatus] = useState<string>('needs_review');
    const [overviewPage, setOverviewPage] = useState(1);
    const [reviewPage, setReviewPage] = useState(1);
    const [regressionPage, setRegressionPage] = useState(1);
    const overview = useResource<QualityOverview>('/evaluation/overview');
    const reviews = useResource<QualityReviews>(`/evaluation/reviews?status=${status}&page=${reviewPage}&page_size=${PAGE_SIZE}`);
    const regressions = useResource<QualityRegressions>(`/evaluation/regressions?page=${regressionPage}&page_size=${PAGE_SIZE}`);
    const summary = overview.data?.summary;
    const openAgent = (agentId: string) => onNavigate(`/agents/${agentId}/evaluation`);
    const cards: [string, string, string][] = [
        [t("评测覆盖"), summary ? `${summary.evaluated} / ${summary.agents}` : '—', t("已跑过评测的 Agent")],
        [t("用例总数"), summary ? String(summary.cases) : '—', t("已编写的评测用例")],
        [t("通过率"), percent(summary?.pass_rate), t("所有 Agent 最近一次评测")],
        [t("待复核"), summary ? String(summary.needs_review) : '—', t("需要人工确认的判断")],
        [t("评分/执行错误"), summary ? String(summary.errors) : '—', t("判分失败或运行出错")],
        [t("回归"), regressions.data ? String(regressions.data.total) : '—', t("较上次由通过转为未通过")],
    ];
    const counts = reviews.data?.counts || {};
    const overviewRows = (overview.data?.agents || []).slice((overviewPage - 1) * PAGE_SIZE, overviewPage * PAGE_SIZE);

    return <div className="quality-board">
        <Alert error={overview.error || reviews.error || regressions.error}/>
        <div className="metrics quality-metrics">
            {cards.map(([label, value, sub]) => <div className="metric" key={label}>
                <span>{label}</span>
                <strong>{value}</strong>
                <div className="quality-metric-sub">{sub}</div>
            </div>)}
        </div>
        <Panel title={t("质量概览")}>
            {overview.data && overview.data.agents.length
                ? <>
                    <OverviewTable rows={overviewRows} onOpen={openAgent}/>
                    <BoardPagination current={overviewPage} total={overview.data.agents.length} onChange={setOverviewPage}/>
                </>
                : <Empty>{t("还没有 Agent 配置评测用例，可在 Agent 的评测页添加。")}</Empty>}
        </Panel>
        <Panel title={t("待复核队列")} action={<div className="quality-filters">
            {FILTERS.map(([key, label]) => <button
                key={key}
                className={status === key ? 'selected' : ''}
                aria-pressed={status === key}
                onClick={() => { setStatus(key); setReviewPage(1); }}>{t(label)}<small>{counts[key] ?? 0}</small></button>)}
        </div>}>
            {reviews.data && reviews.data.items.length
                ? <>
                    <ReviewTable items={reviews.data.items} onOpen={openAgent}/>
                    <BoardPagination current={reviewPage} total={reviews.data.total} onChange={setReviewPage}/>
                </>
                : <Empty>{status === 'needs_review' ? t("所有待复核项都已处理") : t("暂无数据")}</Empty>}
        </Panel>
        <Panel title={t("回归用例")}>
            <p className="hint">{t("仅比较每个 Agent 最近两次评测：上次通过、这次没通过的用例。")}</p>
            {regressions.data && regressions.data.items.length
                ? <>
                    <RegressionTable items={regressions.data.items} onOpen={openAgent}/>
                    <BoardPagination current={regressionPage} total={regressions.data.total} onChange={setRegressionPage}/>
                </>
                : <Empty>{t("最近两次评测之间没有出现回退。")}</Empty>}
        </Panel>
    </div>;
}
