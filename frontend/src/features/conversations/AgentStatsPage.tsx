import { ArrowRightOutlined, RobotOutlined } from '@ant-design/icons';
import { Pagination } from 'antd';
import { useEffect, useState } from 'react';
import { t } from '../../i18n';
import { Alert, useResource } from '../../shared/components/ui';
import type { Agent } from '../../shared/api/client';

export interface AgentCount {
  agent_id: string | null;
  name: string | null;
  total: number;
  bot: number;
  waiting: number;
  human: number;
  closed: number;
}

export interface AgentCounts {
  items: AgentCount[];
  total: number;
}

interface Props {
  onOpen: (agentId: string) => void;
  onAll: () => void;
}

const PAGE_SIZE = 12;

export function AgentStatsPage({ onOpen, onAll }: Props) {
  const agents = useResource<Agent[]>('/agents?include_archived=true');
  const counts = useResource<AgentCounts>('/agents/counts', 10000);
  const rows = (counts.data?.items || [])
    .filter(item => item.agent_id)
    .map(item => ({
      counts: item,
      agent: (agents.data || []).find(agent => agent.id === item.agent_id) || null,
    }));
  const grandTotal = (counts.data?.items || []).reduce((sum, item) => sum + (item.total || 0), 0);
  const [page, setPage] = useState(1);
  const totalPages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const paged = rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);

  return (
    <div className="agent-stats-page">
      <Alert error={agents.error || counts.error} />
      <div className="agent-stats-toolbar">
        <h2>{t('会话工作台')}</h2>
        <p>{t('选择一个 Agent 进入其会话工作台，下方展示各状态的会话数量。')}</p>
      </div>
      <button className="agent-stats-all" onClick={onAll}>
        <span>
          <strong>{t('全部会话')}</strong>
          <small>{t('查看所有 Agent 的会话')}</small>
        </span>
        <span className="agent-stats-count">
          <b>{grandTotal}</b>
          <i>{t('会话总数')}</i>
          <ArrowRightOutlined aria-hidden />
        </span>
      </button>
      <div className="agent-stats-grid">
        {paged.map(({ agent, counts: value }) => (
          <button
            key={value.agent_id || ''}
            className="agent-stats-card"
            onClick={() => onOpen(value.agent_id || '')}
            aria-label={t('进入 {{v0}} 会话工作台', { v0: agent?.name || value.name || value.agent_id || '' })}
          >
            <span className="agent-stats-main">
              <span className="agent-stats-avatar"><RobotOutlined aria-hidden /></span>
              <span> 
                <strong>{agent?.name || value.name || value.agent_id || ''}</strong>
                {agent?.description && <small>{agent.description}</small>}
              </span>
            </span>
            <span className="agent-stats-counts">
              <span className="agent-stats-total"><b>{value.total}</b> {t('会话')}</span>
              <span className="agent-stat is-waiting"><i className="agent-stat-dot" aria-hidden /><b>{value.waiting}</b> {t('待接管')}</span>
              <span className="agent-stat is-human"><i className="agent-stat-dot" aria-hidden /><b>{value.human}</b> {t('人工服务')}</span>
              <span className="agent-stat is-closed"><i className="agent-stat-dot" aria-hidden /><b>{value.closed}</b> {t('已关闭')}</span>
            </span>
          </button>
        ))}
      </div>
      {rows.length > PAGE_SIZE && (
        <div className="agent-stats-pagination">
          <Pagination
            size="small"
            current={page}
            pageSize={PAGE_SIZE}
            total={rows.length}
            showSizeChanger={false}
            showTotal={value => t('总共 {{v0}} 条', { v0: value })}
            onChange={setPage}
          />
        </div>
      )}
      {!counts.loading && !counts.error && !rows.length && (
        <p className="empty">{t('暂无会话数据')}</p>
      )}
    </div>
  );
}
