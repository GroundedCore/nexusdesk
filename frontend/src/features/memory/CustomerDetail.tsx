import { useState } from 'react';
import { App, Alert, Button, Card, Empty, Form, Input, Modal, Space, Table, Typography } from 'antd';
import { ArrowLeftOutlined, ReloadOutlined } from '@ant-design/icons';
import { api, patch, type MemoryCustomerDetail as Detail, type MemoryIdentity, type MemoryItem } from '../../shared/api/client';
import { errorText, t } from '../../i18n';
import { time, useResource } from '../../shared/components/ui';
import { MemoryItemTable } from './MemoryItemTable';
import { identitySourceLabels, typeLabels } from './labels';

interface Props {
  customerId: string;
  operator: boolean;
  onBack: () => void;
}

const memoryTypes = ['profile', 'preference'];

export function CustomerDetail({ customerId, operator, onBack }: Props) {
  const detail = useResource<Detail>(`/memory/customers/${customerId}`);
  const { message } = App.useApp();
  const [editing, setEditing] = useState<MemoryItem | null>(null);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  function openEditor(item: MemoryItem) {
    setEditing(item);
    setDraft(item.content);
    setError('');
  }

  async function saveEdit() {
    if (!editing || busy)
      return;
    if (!draft.trim()) {
      setError(t('记忆内容不能为空'));
      return;
    }
    setBusy(true);
    setError('');
    try {
      await patch<MemoryItem>(`/memory/items/${editing.id}`, { content: draft.trim() });
      setEditing(null);
      detail.refresh();
      void message.success(t('记忆已更新'));
    }
    catch (e) {
      setError(e instanceof Error ? e.message : t('更新失败，请重试'));
    }
    finally {
      setBusy(false);
    }
  }

  async function run(action: () => Promise<unknown>, done: string) {
    if (busy)
      return;
    setBusy(true);
    try {
      await action();
      detail.refresh();
      void message.success(done);
    }
    catch (e) {
      void message.error(errorText(e instanceof Error ? e.message : t('操作失败')));
    }
    finally {
      setBusy(false);
    }
  }

  const itemsOf = (type: string) => (detail.data?.items || []).filter(item => item.type === type);
  return (
    <Space orientation="vertical" size="large" style={{ width: '100%' }}>
      {detail.error && <Alert type="error" showIcon title={t('客户详情加载失败')} description={errorText(detail.error)} action={<Button onClick={detail.refresh}>{t('重试')}</Button>} />}
      <div className="memory-detail-header">
        <Button icon={<ArrowLeftOutlined />} onClick={onBack}>{t('返回客户列表')}</Button>
        <Typography.Title level={4} style={{ margin: 0 }}>{t('客户详情')}</Typography.Title>
        {detail.data && <Typography.Text type="secondary" code>{detail.data.id}</Typography.Text>}
        <Button icon={<ReloadOutlined />} onClick={detail.refresh} loading={detail.loading}>{t('刷新')}</Button>
      </div>
      <Card title={t('身份绑定')}>
        <Table<MemoryIdentity>
          rowKey="id"
          dataSource={detail.data?.identities || []}
          loading={detail.loading}
          pagination={false}
          locale={{ emptyText: <Empty description={t('暂无身份绑定')} /> }}
          columns={[
            { title: t('来源'), dataIndex: 'source', width: 140, render: (value: string) => identitySourceLabels()[value] || value },
            { title: t('客户标识'), dataIndex: 'external_key', render: (value: string) => <Typography.Text code>{value}</Typography.Text> },
            { title: t('绑定时间'), dataIndex: 'created_at', width: 200, render: (value: string) => time(value) },
          ]}
        />
      </Card>
      {memoryTypes.map(type => (
        <Card key={type} title={type === 'profile' ? t('客户画像') : t('客户偏好')} data-type={type}>
          <MemoryItemTable
            items={itemsOf(type)}
            operator={operator}
            busy={busy}
            emptyText={t('暂无{{v0}}记忆', { v0: typeLabels()[type] || type })}
            onEdit={openEditor}
            onDelete={item => void run(() => api(`/memory/items/${item.id}`, { method: 'DELETE' }), t('记忆已删除'))}
            onRestore={item => void run(() => api(`/memory/items/${item.id}/restore`, { method: 'POST' }), t('记忆已恢复'))}
          />
        </Card>
      ))}
      <Modal
        title={t('编辑记忆')}
        open={Boolean(editing)}
        onOk={() => void saveEdit()}
        onCancel={() => { if (!busy) setEditing(null); }}
        okText={t('保存')}
        cancelText={t('取消')}
        okButtonProps={{ loading: busy, disabled: !draft.trim() }}
        cancelButtonProps={{ disabled: busy }}
        maskClosable={!busy}
        keyboard={!busy}
        destroyOnHidden
      >
        {editing && (
          <Form layout="vertical">
            <Form.Item label={t('键')}>
              <Typography.Text code>{editing.key}</Typography.Text>
            </Form.Item>
            <Form.Item label={t('记忆内容')} required validateStatus={error ? 'error' : undefined} help={error ? errorText(error) : undefined}>
              <Input.TextArea
                aria-label={t('记忆内容')}
                rows={4}
                maxLength={4000}
                showCount
                value={draft}
                onChange={e => setDraft(e.target.value)}
                disabled={busy}
              />
            </Form.Item>
          </Form>
        )}
      </Modal>
    </Space>
  );
}
