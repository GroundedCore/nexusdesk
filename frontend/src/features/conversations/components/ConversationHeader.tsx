import { useState } from 'react';
import { Button, Space, Tooltip } from 'antd';
import { CopyOutlined, CustomerServiceOutlined, ReloadOutlined, ToolOutlined, UnlockOutlined, LockOutlined } from '@ant-design/icons';
import { t, dateTime } from '../../../i18n';
import { Badge } from '../../../shared/components/ui';
import type { Agent, ConversationDetail, Run } from '../../../shared/api/client';

interface Props {
  detail: ConversationDetail | null;
  agent: Agent | null;
  active: Run | null;
  operator: boolean;
  actionBusy: boolean;
  onRefresh: () => void;
  onTrace: (runId: string) => void;
  onTransition: () => void;
  onHandoff: () => void;
}

export function ConversationHeader({
  detail,
  agent,
  active,
  operator,
  actionBusy,
  onRefresh,
  onTrace,
  onTransition,
  onHandoff,
}: Props) {
  const [copied, setCopied] = useState(false);
  if (!detail) {
    return (
      <div className="c-chat-header">
        <div className="c-chat-meta">{t('选择会话开始服务')}</div>
      </div>
    );
  }

  const agentName = detail.deleted_agent ? detail.deleted_agent.name : (agent?.name || t('默认 Runtime'));
  const disabled = actionBusy || !operator;
  const current = detail;

  async function copyId() {
    try {
      await navigator.clipboard.writeText(current.external_id);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      // Clipboard is optional; keep the selector quiet.
    }
  }

  return (
    <div className="c-chat-header">
      <div className="c-chat-title-row">
        <h3 title={detail.external_id}>{detail.external_id}</h3>
        <Badge value={detail.mode} />
        <div className="c-chat-actions">
          {detail.runs[0]?.id && (
            <Tooltip title={t('查看运行轨迹')}>
              <Button icon={<ToolOutlined aria-hidden />} disabled={actionBusy} onClick={() => onTrace(detail.runs[0].id)} />
            </Tooltip>
          )}
          <Tooltip title={t('刷新')}>
            <Button icon={<ReloadOutlined aria-hidden />} disabled={actionBusy} onClick={onRefresh} />
          </Tooltip>
          {operator && detail.mode !== 'closed' && detail.mode !== 'human' && (
            <Button size="small" icon={<CustomerServiceOutlined aria-hidden />} disabled={disabled || !!active} onClick={onHandoff}>
              {t('转人工')}
            </Button>
          )}
          {operator && !detail.deleted_agent && ['bot', 'closed'].includes(detail.mode) && (
            <Button size="small" icon={detail.mode === 'closed' ? <UnlockOutlined aria-hidden /> : <LockOutlined aria-hidden />} disabled={disabled || !!active} onClick={onTransition}>
              {detail.mode === 'closed' ? t('重新打开会话') : t('关闭自动会话')}
            </Button>
          )}
        </div>
      </div>
      <div className="c-chat-meta">
        <span><strong>{agentName}</strong></span>
        <span>{t('会话来源')}: {detail.source || t('业务会话')}</span>
        <span>{t('最近更新')}: {dateTime(detail.updated_at)}</span>
        <button type="button" className="text-button c-copy-id" onClick={() => void copyId()}>
          <code>{copied ? t('已复制') : current.external_id}</code>
          <CopyOutlined aria-hidden />
        </button>
        {active && <Badge value={active.status} />}
      </div>
      {detail.deleted_agent && (
        <p className="hint">「{detail.deleted_agent.name}」{t('已永久删除。历史对话与运行记录保留，此会话为只读。')}</p>
      )}
    </div>
  );
}
