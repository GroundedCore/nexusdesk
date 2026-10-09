import { t } from '../../i18n/index';
import { useEffect, useRef, useState } from 'react';
import { Avatar, Button, Empty, Input, Pagination, Select, Space, Spin, Table, Tag } from 'antd';
import { PlusOutlined, RobotOutlined, SendOutlined } from '@ant-design/icons';
import { api, post, type Agent, type Conversation, type ConversationDetail, type Message } from '../../shared/api/client';
import { Alert, Badge, answerRound, time, useAccess, useAction, useResource, useRunStream } from '../../shared/components/ui';
import { type PageResult } from './AgentsPage';
function Messages({ detail, follow = false, pending = '', reasoning = '' }: {
    detail: ConversationDetail;
    follow?: boolean;
    pending?: string;
    reasoning?: string;
}) {
    const [older, setOlder] = useState<Message[]>([]);
    const [more, setMore] = useState(true);
    const action = useAction();
    const bottom = useRef<HTMLDivElement>(null);
    const scroller = useRef<HTMLDivElement>(null);
    const messages = [...new Map([...older, ...detail.messages].map(m => [m.seq, m])).values()].sort((a, b) => a.seq - b.seq);
    useEffect(() => { if (follow)
        bottom.current?.scrollIntoView({ block: 'nearest' }); }, [detail.messages.at(-1)?.seq, pending, follow]);
    async function loadOlder() {
        const row = await api<{
            items: Message[];
            has_more: boolean;
        }>(`/conversations/${detail.id}/history${messages.length ? `?before=${messages[0].seq}` : ''}`);
        const height = scroller.current?.scrollHeight || 0;
        setOlder(old => [...row.items, ...old]);
        setMore(row.has_more);
        requestAnimationFrame(() => { if (scroller.current)
            scroller.current.scrollTop += scroller.current.scrollHeight - height; });
    }
    return <div className="agent-messages" ref={scroller}><Alert error={action.error}/>{more && messages.length > 0 && <Button className="agent-load-history" type="text" loading={action.busy} onClick={() => void action.run(loadOlder, '')}>{t("加载更早消息")}</Button>}{messages.length === 0 && !pending && <Empty description={t("暂无消息")}/>}{messages.map(message => <div key={message.id} className={`agent-message ${message.role}`}><div className="agent-message-meta">{{ user: t("用户"), assistant: 'Agent', human: t("人工客服"), system: t("系统") }[message.role]} · {time(message.created_at)}</div><div className="agent-message-bubble">{message.content}</div></div>)}{pending ? <div className="agent-message assistant"><div className="agent-message-meta">Agent · {t("正在生成回答\u2026")}</div>{reasoning ? <details open><summary>{t("思维链")}</summary><pre className="mono">{reasoning}</pre></details> : null}<div className="agent-message-bubble">{pending}</div></div> : null}<div ref={bottom}/></div>;
}
export function AgentChat({ agent }: {
    agent: Agent | null;
}) {
    const [cid, setCid] = useState('');
    const [input, setInput] = useState('');
    const [pendingRun, setPendingRun] = useState('');
    const action = useAction();
    const { operator } = useAccess();
    const detail = useResource<ConversationDetail>(cid ? `/conversations/${cid}` : null, 1500);
    const active = detail.data?.runs.find(r => ['queued', 'running'].includes(r.status));
    const currentRun = detail.data?.runs[0];
    const available = !!agent?.published_version && !agent.archived && operator;
    useEffect(() => { if (pendingRun && detail.data?.runs.some(r => r.id === pendingRun && !['queued', 'running'].includes(r.status)))
        setPendingRun(''); }, [pendingRun, detail.data]);
    const busy = !!active || !!pendingRun;
    // Watch the in-flight run so the answer types out instead of appearing at once.
    // Once the run settles the polled message carries the same text, so the live
    // bubble steps aside in the same refresh and nothing is duplicated.
    const stream = useRunStream(busy ? (active?.id || pendingRun) : null);
    const live = answerRound(stream.rounds);
    const canSend = available && !action.busy && !busy && !detail.loading && !detail.error && (!detail.data || detail.data.mode === 'bot');
    async function send() {
        let conversationId = cid;
        if (!conversationId) {
            const created = await post<Conversation>('/conversations', { external_id: `playground-${crypto.randomUUID()}`, agent_id: agent!.id, source: 'playground' });
            conversationId = created.id;
            setCid(created.id);
        }
        const response = await post<{
            run: {
                id: string;
            } | null;
        }>(`/conversations/${conversationId}/messages`, { content: input.trim() });
        setPendingRun(response.run?.id || '');
        setInput('');
        detail.refresh();
    }
    return <div className="agent-chat"><div className="agent-chat-heading"><div><strong>{t("试聊")}<span className="agent-preview-label">PREVIEW</span></strong><p>{agent?.published_version ? t("当前使用发布版本 v{{v0}} \u00B7 草稿修改不影响本次试聊", { v0: agent.published_version }) : t("发布后即可开始试聊")}</p></div><Button type="text" icon={<PlusOutlined aria-hidden/>} disabled={busy || action.busy} onClick={() => { setCid(''); setPendingRun(''); setInput(''); action.clear(); }}>{t("新会话")}</Button></div>
    <Alert error={action.error.includes('agent_model_required') ? t("已发布版本尚未配置模型，请在 Agent 配置中选择模型方案并重新发布。") : action.error || detail.error}/>{detail.error && <Button onClick={detail.refresh}>{t("重试加载")}</Button>}
    {!cid ? <div className="agent-chat-welcome"><Avatar size={72} shape="square" icon={<RobotOutlined aria-hidden/>}/><h2>{agent?.name || t("你的 Agent")}</h2><p>{t("从一个问题开始，看看 Agent 如何回应。")}</p><div className="agent-chat-suggestions">{[t("介绍一下你能做什么"), t("我需要你的帮助")].map(text => <Button key={text} disabled={!available} onClick={() => setInput(text)}>{text}<span aria-hidden>↗</span></Button>)}</div></div> : detail.data ? <Messages key={cid} detail={detail.data} follow pending={live?.text || ''} reasoning={live?.reasoning || ''}/> : <div className="agent-chat-welcome"><Spin /></div>}
    {busy && <div className="agent-run-state"><Space><Spin size="small"/><span>{active?.status === 'queued' ? t("正在排队\u2026") : t("正在生成回答\u2026")}</span></Space><Button type="text" disabled={action.busy} onClick={() => void action.run(async () => { await post(`/runs/${active?.id || pendingRun}/cancel`); detail.refresh(); }, '')}>{t("停止生成")}</Button></div>}
    {currentRun?.status === 'failed' && <Alert error={t("运行失败：{{v0}}", { v0: currentRun.error_code || t("请重试") })}/>}{currentRun?.status === 'cancelled' && <p className="agent-muted">{t("本次运行已取消")}</p>}
    {detail.data?.actions.filter(a => a.status === 'pending').map(a => <div className="agent-pending-action" key={a.id}><strong>{t("待确认工单：")}{a.payload.title}</strong><p>{a.payload.description}</p><Space><Button disabled={!operator || busy || action.busy} onClick={() => void action.run(async () => { await post(`/actions/${a.id}/decision`, { approve: true }); detail.refresh(); }, '')}>{t("确认创建")}</Button><Button disabled={!operator || busy || action.busy} onClick={() => void action.run(async () => { await post(`/actions/${a.id}/decision`, { approve: false }); detail.refresh(); }, '')}>{t("拒绝")}</Button></Space></div>)}
    {detail.data && detail.data.mode !== 'bot' && <p className="agent-chat-note">{t("当前会话为「")}<Badge value={detail.data.mode}/>{t("」，请在会话工作台继续处理，或新建试聊。")}</p>}
    <form className="agent-composer" onSubmit={e => { e.preventDefault(); if (canSend && input.trim())
        void action.run(send, ''); }}><Input.TextArea aria-label={t("试聊消息")} placeholder={!operator ? t("当前角色无试聊权限") : !available ? t("请先保存并发布 Agent") : t("输入你的问题\u2026")} value={input} onChange={e => setInput(e.target.value)} maxLength={8000} disabled={!available || action.busy} autoSize={{ minRows: 2, maxRows: 5 }} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
        e.preventDefault();
        if (canSend && input.trim())
            void action.run(send, '');
    } }}/><div><span>{t("Enter 发送 ")}<span className="agent-composer-divider">·</span>{t(" Shift + Enter 换行")}</span><Button type="primary" htmlType="submit" aria-label={t("发送试聊消息")} shape="circle" icon={<SendOutlined aria-hidden/>} disabled={!canSend || !input.trim()} loading={action.busy}/></div></form><p className="agent-chat-disclaimer">{t("试聊使用实际模型与工具，回答内容请核实。")}</p>
  </div>;
}
export function AgentRecords({ agentId }: {
    agentId: string;
}) {
    const [source, setSource] = useState('all');
    const [query, setQuery] = useState('');
    const [search, setSearch] = useState('');
    const [page, setPage] = useState(1);
    const [pageSize, setPageSize] = useState(12);
    const [selected, setSelected] = useState<Conversation | null>(null);
    const rows = useResource<PageResult<Conversation>>(`/agents/${agentId}/conversations?source=${source}&q=${encodeURIComponent(search)}&page=${page}&page_size=${pageSize}`);
    const detail = useResource<ConversationDetail>(selected ? `/conversations/${selected.id}` : null, selected ? 3000 : 0);
    useEffect(() => { const timer = setTimeout(() => { setSearch(query); setPage(1); }, 300); return () => clearTimeout(timer); }, [query]);
    return <div className="agent-records"><div className="agent-records-heading"><div><h2>{t("对话记录")}</h2><p className="agent-muted">{t("查看当前 Agent 的试聊与业务会话")}</p></div>{selected && <Button onClick={() => setSelected(null)}>{t("返回会话列表")}</Button>}</div><Alert error={rows.error || detail.error}/>
  {!selected ? <><div className="agent-toolbar"><Select aria-label={t("会话来源")} value={source} onChange={v => { setSource(v); setPage(1); }} options={[{ value: 'all', label: t("全部来源") }, { value: 'playground', label: t("试聊") }, { value: 'business', label: t("业务会话") }]}/><Input.Search aria-label={t("搜索会话")} placeholder={t("搜索会话标识")} value={query} maxLength={128} onChange={e => setQuery(e.target.value)}/><Button onClick={rows.refresh}>{t("刷新")}</Button></div><Table rowKey="id" loading={rows.loading} dataSource={rows.data?.items || []} pagination={false} scroll={{ x: 760 }} locale={{ emptyText: t("暂无对话记录") }} columns={[{ title: t("会话"), dataIndex: 'external_id', width: 250, render: (value: string, row: Conversation) => <Button className="agent-record-link" type="link" onClick={() => setSelected(row)} title={value}>{value}</Button> }, { title: t("最近消息"), dataIndex: 'last_message', ellipsis: true, render: (value: string) => value || t("暂无消息") }, { title: t("来源"), dataIndex: 'source', width: 100, render: (value: string) => <Tag color={value === 'playground' ? 'purple' : 'blue'}>{value === 'playground' ? t("试聊") : t("业务")}</Tag> }, { title: t("状态"), dataIndex: 'mode', width: 110, render: (value: string) => <Badge value={value}/> }, { title: t("最近消息时间"), dataIndex: 'updated_at', width: 190, render: (value: string) => time(value) }]}/><Pagination className="agent-pagination" current={page} pageSize={pageSize} total={rows.data?.total || 0} showSizeChanger pageSizeOptions={[12, 24, 48]} showTotal={total => t("总共 {{v0}} 条", { v0: total })} onChange={(p, size) => { setPage(size === pageSize ? p : 1); setPageSize(size); }}/></> : <div className="agent-record-detail"><div className="agent-record-meta"><strong>{selected.external_id}</strong><Tag>{selected.source === 'playground' ? t("试聊") : t("业务会话")}</Tag><Badge value={detail.data?.mode || selected.mode}/></div>{detail.data ? <Messages key={selected.id} detail={detail.data}/> : <Spin />}{detail.error && <Button onClick={detail.refresh}>{t("重试加载")}</Button>}</div>}
  </div>;
}
