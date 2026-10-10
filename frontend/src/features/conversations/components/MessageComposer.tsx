import { useState, type FormEvent, type KeyboardEvent } from 'react';
import { Button, Checkbox } from 'antd';
import { SendOutlined, CustomerServiceOutlined } from '@ant-design/icons';
import { t } from '../../../i18n';
import type { ConversationDetail, Run } from '../../../shared/api/client';

interface Props {
  detail: ConversationDetail | null;
  active: Run | null;
  operator: boolean;
  actionBusy: boolean;
  onSend: (content: string, asCustomer: boolean) => Promise<boolean>;
}

export function MessageComposer({ detail, active, operator, actionBusy, onSend }: Props) {
  const [content, setContent] = useState('');
  const [asCustomer, setAsCustomer] = useState(false);

  if (!detail) {
    return (
      <form className="c-composer" onSubmit={event => event.preventDefault()}>
        <textarea disabled placeholder={t('选择会话后开始服务')} />
      </form>
    );
  }

  const humanMode = detail.mode === 'human' || detail.mode === 'waiting';
  const disabled = !operator || !!active || actionBusy || detail.mode === 'closed' || !!detail.deleted_agent;
  const isHumanReply = humanMode && !asCustomer;

  async function submit() {
    const value = content.trim();
    if (!value || disabled)
      return;
    const ok = await onSend(value, asCustomer);
    if (ok)
      setContent('');
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    void submit();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void submit();
    }
  }

  return (
    <form className="c-composer" onSubmit={handleSubmit}>
      <div className="c-composer-tools">
        <span>{detail.mode === 'closed' ? t('会话已关闭，无法发送消息') : detail.deleted_agent ? t('当前会话为只读') : t('Enter 发送')}<span> · </span>{t('Shift + Enter 换行')}</span>
        {humanMode && (
          <Checkbox checked={asCustomer} onChange={event => setAsCustomer(event.target.checked)}>
            {t('模拟客户发言')}
          </Checkbox>
        )}
      </div>
      <div className="c-composer-row">
        <textarea
          aria-label={t('消息内容')}
          value={content}
          disabled={disabled}
          onChange={event => setContent(event.target.value)}
          onKeyDown={handleKeyDown}
          maxLength={8000}
          rows={3}
          placeholder={isHumanReply ? t('输入人工回复…') : t('输入客户消息…')}
        />
        <Button
          type="primary"
          htmlType="submit"
          icon={isHumanReply ? <CustomerServiceOutlined aria-hidden /> : <SendOutlined aria-hidden />}
          disabled={disabled || !content.trim()}
          loading={actionBusy}
          aria-label={isHumanReply ? t('人工回复') : t('发送消息')}
        />
      </div>
      {!operator && <p className="hint">{t('当前角色无发送权限')}</p>}
    </form>
  );
}
