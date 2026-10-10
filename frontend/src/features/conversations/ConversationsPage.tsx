import { useEffect, useMemo, useState } from 'react';
import { Button } from 'antd';
import { StopOutlined } from '@ant-design/icons';
import { t, errorText } from '../../i18n';
import { post, type Agent, type Conversation, type ConversationDetail, type Message, type Run } from '../../shared/api/client';
import { Alert, Badge, answerRound, useAccess, useAction, useResource, useRunStream } from '../../shared/components/ui';
import { ConversationSidebar } from './components/ConversationSidebar';
import { ConversationHeader } from './components/ConversationHeader';
import { MessageList } from './components/MessageList';
import { MessageComposer } from './components/MessageComposer';
import { ProposalPanel } from './components/ProposalPanel';
import { RunInspector } from './components/RunInspector';
import { AgentStatsPage } from './AgentStatsPage';
import './conversations.css';

interface SendResponse {
  conversation_id: string;
  run: { id: string; status: string } | null;
  handoff?: unknown;
}

interface ConversationPage {
  items: Conversation[];
  total: number;
  page: number;
  page_size: number;
}

const terminal: Record<string, boolean> = { completed: true, failed: true, cancelled: true };

interface Props {
  route: string;
  onNavigate: (path: string, replace?: boolean) => void;
}

export function ConversationsPage({ route, onNavigate }: Props) {
  const agentId = route.split('?')[0].split('/')[2];
  if (!agentId) {
    return (
      <AgentStatsPage
        onOpen={id => onNavigate(`/conversations/${id}`)}
        onAll={() => onNavigate('/conversations/_all')}
      />
    );
  }
  return (
    <ConversationWorkbench
      agentId={agentId === '_all' ? null : agentId}
      onBack={() => onNavigate('/conversations')}
    />
  );
}

function ConversationWorkbench({ agentId, onBack }: {
  agentId: string | null;
  onBack: () => void;
}) {
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState('all');
  const [pageSize] = useState(10);
  const conversations = useResource<ConversationPage>(
    `/conversations?page=${page}&page_size=${pageSize}${agentId ? `&agent_id=${encodeURIComponent(agentId)}` : ''}&status=${status}`,
    5000,
  );
  const agents = useResource<Agent[]>('/agents?include_archived=true');
  const action = useAction();
  const { operator } = useAccess();

  const [selected, setSelected] = useState('');
  const [traceRun, setTraceRun] = useState('');
  const [pendingRun, setPendingRun] = useState<Run | null>(null);
  const [echoes, setEchoes] = useState<Message[]>([]);
  const [sendError, setSendError] = useState('');

  const detail = useResource<ConversationDetail>(selected ? `/conversations/${selected}` : null, 2000);
  const activeServer = detail.data?.runs.find(run => run.status === 'queued' || run.status === 'running') || null;
  const active = activeServer || pendingRun;
  const busy = !!active || action.busy;
  const stream = useRunStream(active?.id || null);
  const live = answerRound(stream.rounds);

  const agentMap = useMemo(() => new Map((agents.data || []).map(item => [item.id, item])), [agents.data]);
  const detailAgent = selected && detail.data?.agent_id ? agentMap.get(detail.data.agent_id) || null : null;

  useEffect(() => {
    if (pendingRun && detail.data?.runs.some(run => run.id === pendingRun.id && terminal[run.status])) {
      setPendingRun(null);
    }
  }, [pendingRun, detail.data]);

  useEffect(() => {
    setEchoes(prev => prev.filter(echo => {
      const matched = detail.data?.messages.some(item =>
        item.role === echo.role &&
        item.content === echo.content &&
        Math.abs(new Date(item.created_at).getTime() - new Date(echo.created_at).getTime()) < 120000
      );
      return !matched;
    }));
  }, [detail.data]);

  function selectConversation(id: string) {
    setSelected(id);
    setSendError('');
    setEchoes([]);
    setPendingRun(null);
  }

  function createConversation(agentId: string | null): Promise<void> {
    return action.run(async () => {
      const row = await post<Conversation>('/conversations', {
        external_id: `console-${crypto.randomUUID()}`,
        agent_id: agentId || null,
        source: 'business',
      });
      setSelected(row.id);
      conversations.refresh();
    }, t('会话已创建'));
  }

  function transitionConversation() {
    if (!selected || !detail.data)
      return;
    const current = detail.data;
    void action.run(async () => {
      await post(`/conversations/${selected}/transition`, {
        operation: current.mode === 'closed' ? 'reopen' : 'close',
        revision: current.revision,
      });
      detail.refresh();
      conversations.refresh();
    }, t('会话状态已更新'));
  }

  function handoffConversation() {
    if (!selected)
      return;
    void action.run(async () => {
      await post(`/conversations/${selected}/handoffs`, { reason: t('工作台转人工') });
      detail.refresh();
      conversations.refresh();
    }, t('已进入人工队列'));
  }

  async function cancelRun() {
    if (!active)
      return;
    await post<Run>(`/runs/${active.id}/cancel`);
    detail.refresh();
  }

  async function sendMessage(content: string, asCustomerValue: boolean): Promise<boolean> {
    if (!selected || !content.trim() || action.busy)
      return false;
    setSendError('');
    const echoId = `echo-${crypto.randomUUID()}`;
    const lastSeq = detail.data?.messages.at(-1)?.seq || 0;
    const nextSeq = typeof lastSeq === 'number' ? lastSeq + 0.5 : 0.5;
    const echo: Message = {
      id: echoId,
      seq: nextSeq,
      role: detail.data?.mode === 'human' && !asCustomerValue ? 'human' : 'user',
      content: content.trim(),
      created_at: new Date().toISOString(),
      run_id: null,
    };
    setEchoes(prev => [...prev, echo]);
    try {
      const isHumanReply = detail.data?.mode === 'human' && !asCustomerValue;
      const path = isHumanReply ? `/conversations/${selected}/human-replies` : `/conversations/${selected}/messages`;
      const response = await post<SendResponse>(path, { content: content.trim() });
      if (response.run?.id) {
        setPendingRun({
          id: response.run.id,
          conversation_id: selected,
          status: response.run.status,
          error_code: null,
          created_at: new Date().toISOString(),
          output: '',
        });
      }
      detail.refresh();
      conversations.refresh();
      return true;
    } catch (e) {
      setEchoes(prev => prev.filter(item => item.id !== echoId));
      setSendError(e instanceof Error ? errorText(e.message) : t('发送失败'));
      return false;
    }
  }

  return (
    <>
      <Alert error={action.error || conversations.error || detail.error || agents.error} notice={action.notice} />
      <div className="conversation-workspace">
        <ConversationSidebar
          conversations={conversations.data?.items || null}
          selected={selected}
          operator={operator}
          agents={agents.data}
          busy={action.busy}
          total={conversations.data?.total || 0}
          page={page}
          pageSize={pageSize}
          status={status}
          activeAgentId={agentId}
          onBack={onBack}
          onStatusChange={next => { setPage(1); setStatus(next); }}
          onCreate={createConversation}
          onSelect={selectConversation}
          onPageChange={setPage}
        />
        <section className="c-panel c-chat-card">
          <ConversationHeader
            detail={detail.data}
            agent={detailAgent}
            active={active}
            operator={operator}
            actionBusy={action.busy}
            onRefresh={detail.refresh}
            onTrace={setTraceRun}
            onTransition={transitionConversation}
            onHandoff={handoffConversation}
          />
          {active && (
            <div className="c-run-status">
              <Badge value={active.status} />
              <span className="mono">{active.id.slice(0, 8)}</span>
              <span>{active.status === 'queued' ? t('正在排队…') : t('正在生成回答…')}</span>
              <Button type="text" size="small" icon={<StopOutlined aria-hidden />} disabled={!operator} onClick={() => void cancelRun()}>
                {t('取消运行')}
              </Button>
            </div>
          )}
          {sendError && <Alert error={sendError} />}
          <ProposalPanel
            actions={detail.data?.actions || []}
            disabled={!operator || !!active || !!detail.data?.deleted_agent}
            busy={action.busy}
            onConfirm={async id => { await post(`/actions/${id}/decision`, { approve: true }); detail.refresh(); }}
            onReject={async id => { await post(`/actions/${id}/decision`, { approve: false }); detail.refresh(); }}
          />
          <MessageList
            detail={detail.data}
            liveRunId={active?.id || ''}
            live={live}
            echoes={echoes}
            onTrace={setTraceRun}
          />
          <MessageComposer
            detail={detail.data}
            active={active}
            operator={operator}
            actionBusy={action.busy}
            onSend={sendMessage}
          />
        </section>
      </div>
      <RunInspector runId={traceRun} open={!!traceRun} onClose={() => setTraceRun('')} />
    </>
  );
}
