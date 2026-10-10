import { Button } from 'antd';
import { CloseOutlined, CheckOutlined } from '@ant-design/icons';
import { t, dateTime } from '../../../i18n';
import type { Action } from '../../../shared/api/client';

interface Props {
  actions: Action[];
  disabled: boolean;
  busy: boolean;
  onConfirm: (id: string) => Promise<void>;
  onReject: (id: string) => Promise<void>;
}

export function ProposalPanel({ actions, disabled, busy, onConfirm, onReject }: Props) {
  const pending = actions.filter(action => action.status === 'pending');
  if (pending.length === 0)
    return null;

  return (
    <div className="c-proposals">
      {pending.map(action => (
        <article className="c-proposal" key={action.id}>
          <h4>{t('待确认工单')} · {action.payload.title}</h4>
          <p>{action.payload.description}</p>
          <p className="hint">{t('有效期至')} {dateTime(action.expires_at)}</p>
          <div className="c-proposal-actions">
            <Button icon={<CloseOutlined aria-hidden />} disabled={disabled || busy} onClick={() => void onReject(action.id)}>
              {t('拒绝')}
            </Button>
            <Button type="primary" icon={<CheckOutlined aria-hidden />} disabled={disabled || busy} loading={busy} onClick={() => void onConfirm(action.id)}>
              {t('确认创建')}
            </Button>
          </div>
        </article>
      ))}
    </div>
  );
}

