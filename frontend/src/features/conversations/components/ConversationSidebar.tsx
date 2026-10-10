import { useState } from 'react';
import { Pagination } from 'antd';
import { t } from '../../../i18n';
import { Badge, Empty, Field, Panel, time } from '../../../shared/components/ui';
import type { Agent, Conversation } from '../../../shared/api/client';

interface Props {
  conversations: Conversation[] | null;
  total: number;
  page: number;
  pageSize: number;
  status: string;
  activeAgentId?: string | null;
  selected: string;
  operator: boolean;
  agents: Agent[] | null;
  busy: boolean;
  onBack?: () => void;
  onStatusChange: (status: string) => void;
  onCreate: (agentId: string | null) => Promise<void>;
  onSelect: (id: string) => void;
  onPageChange: (page: number) => void;
}

export function ConversationSidebar({
  conversations,
  total,
  page,
  pageSize,
  status,
  activeAgentId,
  selected,
  operator,
  agents,
  busy,
  onBack,
  onStatusChange,
  onCreate,
  onSelect,
  onPageChange,
}: Props) {
  const [agentId, setAgentId] = useState('');
  const locked = !!activeAgentId;
  const lockedName = locked ? agents?.find(agent => agent.id === activeAgentId)?.name || t('默认 Runtime') : '';
  const effectiveAgentId = locked ? activeAgentId : agentId;
  const statusOptions = [
    ['all', t('全部')],
    ['bot', t('自动服务')],
    ['waiting', t('待接管')],
    ['human', t('人工服务')],
    ['closed', t('已关闭')],
  ] as const;

  return (
    <Panel title={t('会话列表')} action={onBack ? <button type="button" className="text-button" onClick={onBack}>{t('返回')}</button> : undefined}>
      {operator && (
        <form
          className="new-conversation"
          onSubmit={event => {
            event.preventDefault();
            void onCreate(effectiveAgentId || null);
            setAgentId('');
          }}
        >
          <Field label={t('使用的 Agent')}>
            <select value={effectiveAgentId || ''} onChange={event => setAgentId(event.target.value)} disabled={locked}>
              {!locked && <option value="">{t('默认 Runtime')}</option>}
              {(agents || []).filter(agent => agent.published_version).map(agent => (
                <option value={agent.id} key={agent.id}>{agent.name} · v{agent.published_version}</option>
              ))}
            </select>
          </Field>
          <button disabled={busy}>{t('新建会话')}</button>
        </form>
      )}
      {locked && <p className="hint">{t('当前仅查看 {{v0}} 的会话', { v0: lockedName })}</p>}
      <div className="c-filter-tabs">
        {statusOptions.map(([value, label]) => (
          <button
            key={value}
            type="button"
            className={`c-filter-tab ${status === value ? 'is-active' : ''}`}
            onClick={() => {
              if (onStatusChange) onStatusChange(value);
            }}
          >
            {label}
          </button>
        ))}
      </div>
      {(conversations || []).map(item => (
        <button
          className={`list-item ${selected === item.id ? 'selected' : ''}`}
          key={item.id}
          onClick={() => onSelect(item.id)}
        >
          <strong title={item.external_id}>{item.external_id.slice(0, 28)}</strong>
          <span><Badge value={item.mode} /> {time(item.updated_at)}</span>
        </button>
      ))}
      {!conversations?.length && <Empty>{t('暂无会话')}</Empty>}
      {total > pageSize && (
        <div className="c-conversation-pagination">
          <Pagination
            size="small"
            current={page}
            pageSize={pageSize}
            total={total}
            showSizeChanger={false}
            onChange={onPageChange}
          />
        </div>
      )}
    </Panel>
  );
}
