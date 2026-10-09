import { t } from '../../i18n/index';
import { useEffect, useRef, useState } from 'react';
import { Button, Modal } from 'antd';
import { ToolOutlined } from '@ant-design/icons';
import { post, type Agent, type Conversation, type ConversationDetail } from '../../shared/api/client';
import { Alert, Badge, answerRound, Empty, Field, Panel, Trace, time, useAccess, useAction, useResource, useRunStream } from '../../shared/components/ui';
export function ConversationsPage() {
    const conversations = useResource<Conversation[]>('/conversations', 5000);
    const agents = useResource<Agent[]>('/agents');
    const [selected, setSelected] = useState('');
    const detail = useResource<ConversationDetail>(selected ? `/conversations/${selected}` : null, 2000);
    const [agent, setAgent] = useState('');
    const [message, setMessage] = useState('');
    const [asCustomer, setAsCustomer] = useState(false);
    const action = useAction();
    const { operator } = useAccess();
    const bottom = useRef<HTMLDivElement>(null);
    const active = detail.data?.runs.find(r => ['queued', 'running'].includes(r.status));
    const runId = detail.data?.runs[0]?.id || null;
    // Which past answer's run the reader asked to inspect; each bubble carries its own.
    const [traceRun, setTraceRun] = useState('');
    // Watch the in-flight run so the answer types out instead of appearing at once.
    const stream = useRunStream(active?.id || null);
    const live = answerRound(stream.rounds);
    useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }, [detail.data?.messages.length, live?.text]);
    return <><Alert error={action.error || conversations.error || detail.error || agents.error} notice={action.notice}/><div className="conversation-layout"><Panel title={t("会话列表")}>
    {operator && <form className="new-conversation" onSubmit={e => { e.preventDefault(); void action.run(async () => { const row = await post<Conversation>('/conversations', { external_id: `console-${crypto.randomUUID()}`, agent_id: agent || null }); setSelected(row.id); conversations.refresh(); }, t("会话已创建")); }}><Field label={t("使用的 Agent")}><select value={agent} onChange={e => setAgent(e.target.value)}><option value="">{t("默认 Runtime")}</option>{agents.data?.filter(a => a.published_version).map(a => <option value={a.id} key={a.id}>{a.name} · v{a.published_version}</option>)}</select></Field><button disabled={action.busy}>{t("新建会话")}</button></form>}
    {conversations.data?.map(c => <button className={`list-item ${selected === c.id ? 'selected' : ''}`} key={c.id} onClick={() => { setSelected(c.id); setMessage(''); }}><strong title={c.external_id}>{c.external_id.slice(0, 28)}</strong><span><Badge value={c.mode}/> {time(c.updated_at)}</span></button>)}
  </Panel><div className="stack"><Panel title={t("客服工作台")} action={detail.data && <Badge value={detail.data.mode}/>}>
    {!detail.data ? <Empty>{t("选择会话，或创建一次对话")}</Empty> : <>
      {detail.data.deleted_agent && <p className="hint">{t("原智能体「")}{detail.data.deleted_agent.name}{t("」已永久删除。历史对话与运行记录保留，此会话为只读。")}</p>}<div className="chat-messages">{detail.data.messages.map(m => <div key={m.id} className={`message ${m.role}`}><div className="message-meta">{{ user: t("客户"), assistant: 'Agent', human: t("人工客服"), system: t("系统") }[m.role]} · {time(m.created_at)}</div><div className="message-content">{m.content}</div>{m.role === 'assistant' && m.run_id && <div className="message-trace"><Button type="text" size="small" icon={<ToolOutlined aria-hidden/>} aria-label={t("查看运行轨迹")} title={t("查看运行轨迹")} onClick={() => setTraceRun(m.run_id || '')}/></div>}</div>)}{live?.text ? <div className="message assistant"><div className="message-meta">Agent · {t("正在生成回答\u2026")}</div>{live.reasoning ? <details open><summary>{t("思维链")}</summary><pre className="mono">{live.reasoning}</pre></details> : null}<div className="message-content">{live.text}</div>{active?.id && <div className="message-trace"><Button type="text" size="small" icon={<ToolOutlined aria-hidden/>} aria-label={t("查看运行轨迹")} title={t("查看运行轨迹")} onClick={() => setTraceRun(active.id)}/></div>}</div> : null}<div ref={bottom}/></div>
      {detail.data.actions.filter(a => a.status === 'pending').map(a => <div className="proposal" key={a.id}><h3>{t("待确认工单 \u00B7 ")}{a.payload.title}</h3><p>{a.payload.description}</p><p className="hint">{t("有效期至 ")}{time(a.expires_at)}</p><div className="actions"><button disabled={!operator || !!active || action.busy || !!detail.data?.deleted_agent} onClick={() => void action.run(async () => { await post(`/actions/${a.id}/decision`, { approve: true }); detail.refresh(); }, t("工单已创建"))}>{t("确认创建")}</button><button className="secondary" disabled={!operator || !!active || action.busy || !!detail.data?.deleted_agent} onClick={() => void action.run(async () => { await post(`/actions/${a.id}/decision`, { approve: false }); detail.refresh(); }, t("已拒绝"))}>{t("拒绝")}</button></div></div>)}
      {active && <div className="row"><span className="muted"><Badge value={active.status}/>{t(" 运行 ")}{active.id.slice(0, 8)}</span><button className="text-button" disabled={!operator || action.busy} onClick={() => void action.run(async () => { await post(`/runs/${active.id}/cancel`); detail.refresh(); }, t("已请求取消"))}>{t("取消运行")}</button></div>}
      {detail.data.runs[0]?.status === 'failed' && <Alert error={t("运行失败：{{v0}}", { v0: detail.data.runs[0].error_code })}/>}
      {operator && detail.data.mode !== 'closed' && <form className="composer" onSubmit={e => { e.preventDefault(); void action.run(async () => { await post(`/conversations/${selected}/${detail.data?.mode === 'human' && !asCustomer ? 'human-replies' : 'messages'}`, { content: message }); setMessage(''); detail.refresh(); conversations.refresh(); }, ''); }}>
        {detail.data.mode === 'human' && <label className="check"><input type="checkbox" checked={asCustomer} onChange={e => setAsCustomer(e.target.checked)}/>{t("模拟客户发言（关闭时以人工客服回复）")}</label>}
        <textarea aria-label={t("消息内容")} rows={3} required value={message} onChange={e => setMessage(e.target.value)} placeholder={detail.data.mode === 'human' && !asCustomer ? t("输入人工客服回复\u2026") : t("输入客户问题\u2026")}/>
        <div className="actions"><button disabled={action.busy || !!active}>{detail.data.mode === 'human' && !asCustomer ? t("人工回复") : t("发送消息")}</button>{detail.data.mode === 'bot' && <button type="button" className="secondary" disabled={action.busy} onClick={() => void action.run(async () => { await post(`/conversations/${selected}/handoffs`, { reason: t("工作台转接") }); detail.refresh(); conversations.refresh(); }, t("已进入人工队列"))}>{t("转人工")}</button>}</div>
      </form>}
      {operator && !detail.data.deleted_agent && ['bot', 'closed'].includes(detail.data.mode) && <button className="secondary" disabled={!!active || action.busy} onClick={() => void action.run(async () => { await post(`/conversations/${selected}/transition`, { operation: detail.data?.mode === 'closed' ? 'reopen' : 'close', revision: detail.data?.revision }); detail.refresh(); conversations.refresh(); }, t("会话状态已更新"))}>{detail.data.mode === 'closed' ? t("重新打开会话") : t("关闭自动会话")}</button>}
      {detail.data.mode === 'waiting' && <p className="hint">{t("已进入人工队列。请在「人工协同」页面接管。")}</p>}
    </>}
  </Panel><Panel title={t("当前运行轨迹")}><Trace runId={runId}/></Panel></div><Modal open={!!traceRun} width={920} title={t("运行详情")} onCancel={() => setTraceRun('')} footer={<Button onClick={() => setTraceRun('')}>{t("关闭")}</Button>}><Trace runId={traceRun}/></Modal></div></>;
}
