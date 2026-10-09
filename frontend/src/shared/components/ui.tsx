import { t, dateTime, errorText } from '../../i18n/index';
import { message } from 'antd';
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { api, token } from '../api/client';
export const Role = createContext('viewer');

// Toasts are raised through one holder mounted at the app root, so callers that
// are not React components (useAction's run, for example) can raise them too.
let toastApi: ReturnType<typeof message.useMessage>[0] | null = null;

/** Mount once inside the Ant Design App; enables toasts for non-component callers. */
export function ToastHolder() {
    const [api, holder] = message.useMessage();
    useEffect(() => { toastApi = api; return () => { toastApi = null; }; }, [api]);
    return holder;
}

export const toast = {
    success: (text: string) => { toastApi?.success(text); },
    error: (text: string) => { toastApi?.error(text); },
};
export function useAccess() { const role = useContext(Role); return { admin: role === 'admin', operator: role !== 'viewer', role }; }
export function useResource<T>(path: string | null, interval = 0) {
    const [data, setData] = useState<T | null>(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);
    const [revision, setRevision] = useState(0);
    const refresh = useCallback(() => setRevision(v => v + 1), []);
    useEffect(() => {
        if (!path) {
            setData(null);
            return;
        }
        const controller = new AbortController();
        let timer: ReturnType<typeof setTimeout>;
        setData(null);
        setError('');
        setLoading(true);
        const load = async () => {
            try {
                const value = await api<T>(path, { signal: controller.signal });
                if (!controller.signal.aborted) {
                    setData(value);
                    setError('');
                }
            }
            catch (e) {
                if (!controller.signal.aborted)
                    setError(e instanceof Error ? e.message : t("加载失败"));
            }
            finally {
                if (!controller.signal.aborted) {
                    setLoading(false);
                    if (interval)
                        timer = setTimeout(load, interval);
                }
            }
        };
        void load();
        return () => { controller.abort(); clearTimeout(timer); };
    }, [path, interval, revision]);
    return { data, error, loading, refresh };
}
export function useAction() {
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const [notice, setNotice] = useState('');
    const guard = useRef(false);
    async function run(action: () => Promise<void>, message = t("已保存")) {
        if (guard.current)
            return;
        guard.current = true;
        setBusy(true);
        setError('');
        setNotice('');
        try {
            await action();
            setNotice(message);
            // Raised alongside the inline notice: the toast surfaces the outcome
            // immediately, the inline line stays on screen afterwards. An empty
            // message means the caller reports its own way, so stay quiet.
            if (message)
                toast.success(message);
        }
        catch (e) {
            const raw = e instanceof Error ? e.message : t("操作失败");
            setError(raw);
            toast.error(errorText(raw));
        }
        finally {
            guard.current = false;
            setBusy(false);
        }
    }
    function clear() { if (!guard.current) {
        setError('');
        setNotice('');
    } }
    return { busy, error, notice, run, clear };
}
export function Alert({ error, notice }: {
    error?: string;
    notice?: string;
}) {
    return <>{error && <p role="alert" className="alert error">{errorText(error)}</p>}{notice && <p role="status" className="alert success">{notice}</p>}</>;
}
export function Empty({ children = t("暂无记录") }: {
    children?: ReactNode;
}) { return <p className="empty">{children}</p>; }
export function Badge({ value }: {
    value: string;
}) {
    const labels: Record<string, string> = { bot: t("自动服务"), waiting: t("等待接管"), human: t("人工服务"), closed: t("已关闭"), queued: t("排队中"), running: t("执行中"), completed: t("已完成"), failed: t("失败"), cancelled: t("已取消"), active: t("处理中"), resolved: t("已解决"), open: t("待处理"), in_progress: t("处理中"), waiting_customer: t("待客户补充"), pending: t("待确认"), confirmed: t("已确认"), rejected: t("已拒绝"), mvp: t("基础可用"), partial: t("部分实现"), planned: t("待实现") };
    return <span className={`badge ${value}`}>{labels[value] || value}</span>;
}
export function Panel({ title, children, action }: {
    title: string;
    children: ReactNode;
    action?: ReactNode;
}) { return <section className="panel"><div className="panel-heading"><h2>{title}</h2>{action}</div>{children}</section>; }
export function Field({ label, children }: {
    label: string;
    children: ReactNode;
}) { return <label className="field"><span>{label}</span>{children}</label>; }
export function time(value: string) { return dateTime(value); }
interface StreamEvent {
    id: number;
    type: string;
    data: Record<string, unknown>;
}
export interface RunRound {
    round: number;
    text: string;
    reasoning: string;
    /** Set once model.completed for this round arrives; >0 means the round only produced tool calls. */
    toolCalls?: number;
}
/**
 * Subscribe to a run's event stream. Durable events come from the log and carry a
 * seq cursor; token deltas arrive live and are transient, so they are accumulated
 * per round and never advance the cursor.
 */
export function useRunStream(runId: string | null) {
    const [events, setEvents] = useState<StreamEvent[]>([]);
    const [rounds, setRounds] = useState<RunRound[]>([]);
    const [error, setError] = useState('');
    const [finished, setFinished] = useState(false);
    // Deltas are accumulated here and revealed on a timer. Rendering straight from
    // the stream would paint them in whatever lumps arrive: the reader drains every
    // buffered chunk inside one macrotask, and React batches those updates into a
    // single render, so a burst of token frames lands as one jump of text.
    const target = useRef<RunRound[]>([]);
    useEffect(() => {
        const timer = setInterval(() => {
            setRounds(prev => {
                const rows = target.current;
                if (!rows.length)
                    return prev;
                let changed = false;
                const next = rows.map((row, i) => {
                    const shown = prev[i];
                    const same = shown && shown.round === row.round;
                    const shownText = same ? shown.text.length : 0;
                    const shownReason = same ? shown.reasoning.length : 0;
                    // Reveal a few characters per tick so the answer looks typed, but
                    // catch up proportionally when a burst left a large backlog.
                    const take = (have: number, want: number) => {
                        const backlog = want - have;
                        if (backlog <= 0)
                            return 0;
                        return Math.min(backlog, Math.max(4, Math.ceil(backlog / 8)));
                    };
                    const text = take(shownText, row.text.length) ? row.text.slice(0, shownText + take(shownText, row.text.length)) : row.text;
                    const reasoning = take(shownReason, row.reasoning.length) ? row.reasoning.slice(0, shownReason + take(shownReason, row.reasoning.length)) : row.reasoning;
                    if (!same || text !== shown.text || reasoning !== shown.reasoning || row.toolCalls !== shown.toolCalls)
                        changed = true;
                    return { round: row.round, text, reasoning, toolCalls: row.toolCalls };
                });
                return changed ? next : prev;
            });
        }, 20);
        return () => clearInterval(timer);
    }, []);
    useEffect(() => {
        target.current = [];
        setEvents([]);
        setRounds([]);
        setError('');
        setFinished(false);
        if (!runId)
            return;
        const controller = new AbortController();
        let cursor = 0;
        let timer: ReturnType<typeof setTimeout>;
        async function connect() {
            try {
                const response = await fetch(`/api/v1/runs/${runId}/events?after=${cursor}`, { signal: controller.signal,
                    headers: token() ? { Authorization: `Bearer ${token()}` } : {} });
                if (!response.ok || !response.body)
                    throw new Error(t("事件流连接失败：{{v0}}", { v0: response.status }));
                setError('');
                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';
                let done = false;
                while (true) {
                    const { done: end, value } = await reader.read();
                    if (end)
                        break;
                    buffer += decoder.decode(value, { stream: true });
                    let boundary: number;
                    while ((boundary = buffer.indexOf('\n\n')) >= 0) {
                        const block = buffer.slice(0, boundary);
                        buffer = buffer.slice(boundary + 2);
                        const lines = block.split('\n');
                        const id = Number(lines.find(l => l.startsWith('id: '))?.slice(4));
                        const kind = lines.find(l => l.startsWith('event: '))?.slice(7);
                        const data = lines.find(l => l.startsWith('data: '))?.slice(6);
                        if (kind === 'model.delta' && data) {
                            const p = JSON.parse(data) as { round?: number; text?: string; reasoning?: string };
                            const round = p.round ?? 0;
                            const rows = target.current;
                            const i = rows.findIndex(r => r.round === round);
                            if (i >= 0)
                                target.current[i] = { ...rows[i], text: rows[i].text + (p.text || ''), reasoning: rows[i].reasoning + (p.reasoning || '') };
                            else
                                target.current = [...rows, { round, text: p.text || '', reasoning: p.reasoning || '' }];
                            continue;
                        }
                        if (kind && data && id > cursor) {
                            cursor = id;
                            setEvents(old => [...old, { id, type: kind, data: JSON.parse(data) as Record<string, unknown> }].slice(-150));
                            if (kind === 'model.completed') {
                                const p = JSON.parse(data) as { round?: number; tool_calls?: number };
                                if (typeof p.round === 'number')
                                    target.current = target.current.map(r => (r.round === p.round ? { ...r, toolCalls: p.tool_calls ?? 0 } : r));
                            }
                        }
                        if (kind && ['run.completed', 'run.failed', 'run.cancelled'].includes(kind))
                            done = true;
                    }
                }
                if (done)
                    setFinished(true);
                else if (!controller.signal.aborted)
                    timer = setTimeout(connect, 1500);
            }
            catch (e) {
                if (!controller.signal.aborted) {
                    setError(e instanceof Error ? e.message : t("事件流中断"));
                    timer = setTimeout(connect, 3000);
                }
            }
        }
        void connect();
        return () => { controller.abort(); clearTimeout(timer); };
    }, [runId]);
    return { events, rounds, error, finished };
}
/**
 * The round a reader should watch: the newest one that did not end in tool calls.
 * While the loop is still running, rounds with no model.completed yet are candidates.
 */
export function answerRound(rounds: RunRound[]): RunRound | null {
    const settled = rounds.filter(r => r.toolCalls === 0);
    if (settled.length)
        return settled[settled.length - 1];
    const streaming = rounds.filter(r => r.toolCalls === undefined);
    if (streaming.length)
        return streaming[streaming.length - 1];
    // Between rounds (a tool call is running, or the next round has not started):
    // keep the last text on screen rather than blanking the bubble.
    return rounds[rounds.length - 1] || null;
}
export function Trace({ runId }: {
    runId: string | null;
}) {
    const { events, rounds, error } = useRunStream(runId);
    if (!runId)
        return <Empty>{t("选择一次运行查看事件")}</Empty>;
    return <div className="trace"><Alert error={error}/>{rounds.map(r => <div key={r.round} className="trace-round"><small className="mono muted">{t("第 {{v0}} 轮", { v0: r.round })}{r.toolCalls ? t("（过程）") : ''}</small>{r.reasoning ? <details open><summary>{t("思维链")}</summary><pre className="mono">{r.reasoning}</pre></details> : null}{r.text ? <pre className="mono">{r.text}</pre> : null}</div>)}{events.map(event => <details key={event.id}><summary><span className="mono">{String(event.id).padStart(2, '0')}</span> {event.type}</summary><pre>{JSON.stringify(event.data, null, 2)}</pre></details>)}{events.length === 0 && rounds.length === 0 && <Empty>{t("等待执行事件\u2026")}</Empty>}</div>;
}
