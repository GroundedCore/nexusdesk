import { useEffect, useMemo, useRef, useState } from 'react';
import { Button, Tooltip } from 'antd';
import { ArrowDownOutlined, ToolOutlined } from '@ant-design/icons';
import { t, dateTime } from '../../../i18n';
import type { RunRound } from '../../../shared/components/ui';
import type { ConversationDetail, Message } from '../../../shared/api/client';

interface Props {
  detail: ConversationDetail | null;
  liveRunId: string;
  live: RunRound | null;
  echoes: Message[];
  onTrace: (runId: string) => void;
}

const roleLabel = (role: string) => ({ user: t('客户'), assistant: t('Agent'), human: t('人工客服'), system: t('系统') }[role] || role);

export function MessageList({ detail, liveRunId, live, echoes, onTrace }: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const [showJump, setShowJump] = useState(false);

  const messages = useMemo(() => {
    const source = detail?.messages || [];
    const deduped = echoes.filter(echo => {
      const match = source.some(item => item.role === echo.role && item.content === echo.content && Math.abs(new Date(item.created_at).getTime() - new Date(echo.created_at).getTime()) < 120000);
      return !match;
    });
    return [...source, ...deduped].sort((a, b) => a.seq - b.seq);
  }, [detail?.messages, echoes]);

  function scrollToBottom(smooth = true) {
    requestAnimationFrame(() => {
      scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
    });
  }

  function handleScroll() {
    const el = scroller.current;
    if (!el)
      return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    stick.current = distance < 100;
    setShowJump(!stick.current);
  }

  useEffect(() => {
    if (live?.text && stick.current)
      scrollToBottom();
  }, [live?.text, messages.length]);

  useEffect(() => {
    if (detail?.messages.length && stick.current)
      scrollToBottom(false);
  }, [detail?.messages.length]);

  if (!detail) {
    return (
      <div className="c-messages">
        <div className="c-composer-empty">
          <h3>{t('咨询台')}</h3>
          <p>{t('选择会话，或创建一次对话')}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="c-messages" ref={scroller} onScroll={handleScroll} aria-live="polite">
      {messages.map(message => (
        <div key={message.id} className={`c-message ${message.role === 'user' ? 'is-user' : message.role === 'human' ? 'is-human' : message.role === 'system' ? 'is-system' : ''}`}>
          <div className="c-message-meta">
            <span>{roleLabel(message.role)}</span>
            <span>{dateTime(message.created_at)}</span>
            {message.role === 'assistant' && message.run_id && (
              <Tooltip title={t('查看运行轨迹')}>
                <Button type="text" icon={<ToolOutlined aria-hidden />} aria-label={t('查看运行轨迹')} onClick={() => onTrace(message.run_id || '')} />
              </Tooltip>
            )}
          </div>
          <div className="c-message-content">{message.content}</div>
        </div>
      ))}
      {live?.text ? (
        <div className="c-message">
          <div className="c-message-meta">
            <span>Agent</span>
            <span>{t('正在生成回答…')}</span>
            {liveRunId && (
              <Tooltip title={t('查看运行轨迹')}>
                <Button type="text" icon={<ToolOutlined aria-hidden />} aria-label={t('查看运行轨迹')} onClick={() => onTrace(liveRunId)} />
              </Tooltip>
            )}
          </div>
          {live.reasoning && (
            <details open>
              <summary>{t('思维链')}</summary>
              <pre className="mono">{live.reasoning}</pre>
            </details>
          )}
          <div className="c-message-content c-live-cursor">{live.text}</div>
        </div>
      ) : null}
      {showJump && (
        <button className="c-jump-bottom" onClick={() => scrollToBottom(true)}>
          <ArrowDownOutlined aria-hidden /> {t('回到最新')}
        </button>
      )}
    </div>
  );
}

