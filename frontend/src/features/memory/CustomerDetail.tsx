import { useState } from 'react';
import { App, Alert, Button, Card, Empty, Form, Input, Modal, Popconfirm, Space, Table, Tag, Typography } from 'antd';
import { ArrowLeftOutlined, ReloadOutlined } from '@ant-design/icons';
import { api, patch, post, put, type MemoryCustomerDetail as Detail, type MemoryIdentity, type MemoryItem } from '../../shared/api/client';
import { errorText, t } from '../../i18n';
import { time, useResource } from '../../shared/components/ui';
import { MemoryItemTable, SourceRef } from './MemoryItemTable';
import { flagKeyColors, flagKeyLabels, flagKeys, identitySourceLabels, typeLabels } from './labels';

interface Props {
  customerId: string;
  operator: boolean;
  onBack: () => void;
}

const memoryTypes = ['profile', 'preference', 'event'];
const typeTitles = (): Record<string, string> => ({ profile: t('客户画像'), preference: t('客户偏好'), event: t('事件') });

export function CustomerDetail({ customerId, operator, onBack }: Props) {
  const detail = useResource<Detail>(`/memory/customers/${customerId}`);
  const { message } = App.useApp();
  const [editing, setEditing] = useState<MemoryItem | null>(null);
  const [draft, setDraft] = useState('');
  const [flagEdit, setFlagEdit] = useState<{ key: string; content: string } | null>(null);
  const [merging, setMerging] = useState(false);
  const [mergeTarget, setMergeTarget] = useState('');
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

  async function deleteCustomer() {
    if (busy)
      return;
    setBusy(true);
    try {
      await api(`/memory/customers/${customerId}`, { method: 'DELETE' });
      void message.success(t('客户已删除'));
      onBack();
    }
    catch (e) {
      void message.error(errorText(e instanceof Error ? e.message : t('操作失败')));
      setBusy(false);
    }
  }

  async function saveFlag() {
    if (!flagEdit || busy)
      return;
    if (!flagEdit.content.trim()) {
      setError(t('标记说明不能为空'));
      return;
    }
    setBusy(true);
    setError('');
    try {
      await put(`/memory/customers/${customerId}/flags/${flagEdit.key}`, { content: flagEdit.content.trim() });
      setFlagEdit(null);
      detail.refresh();
      void message.success(t('标记已保存'));
    }
    catch (e) {
      setError(e instanceof Error ? e.message : t('保存失败，请重试'));
    }
    finally {
      setBusy(false);
    }
  }

  async function mergeCustomer() {
    if (busy)
      return;
    const target = mergeTarget.trim();
    if (!target) {
      setError(t('请输入目标客户 ID'));
      return;
    }
    setBusy(true);
    setError('');
    try {
      await post(`/memory/customers/${customerId}/merge`, { target_customer_id: target });
      setMerging(false);
      void message.success(t('客户已合并'));
      onBack();
    }
    catch (e) {
      setError(e instanceof Error ? e.message : t('合并失败，请重试'));
    }
    finally {
      setBusy(false);
    }
  }

  const items = detail.data?.items || [];
  const itemsOf = (type: string) => items.filter(item => item.type === type);
  const flagOf = (key: string) => items.find(item => item.type === 'flag' && item.key === key && item.status === 'active');
  return (
    <Space orientation="vertical" size="large" style={{ width: '100%' }}>
      {detail.error && <Alert type="error" showIcon title={t('客户详情加载失败')} description={errorText(detail.error)} action={<Button onClick={detail.refresh}>{t('重试')}</Button>} />}
      <div className="memory-detail-header">
        <Button icon={<ArrowLeftOutlined />} onClick={onBack}>{t('返回客户列表')}</Button>
        <Typography.Title level={4} style={{ margin: 0 }}>{t('客户详情')}</Typography.Title>
        {detail.data && <Typography.Text type="secondary" code>{detail.data.id}</Typography.Text>}
        <Button icon={<ReloadOutlined />} onClick={detail.refresh} loading={detail.loading}>{t('刷新')}</Button>
        {operator && (
          <>
            <Button danger disabled={busy} onClick={() => { setMerging(true); setMergeTarget(''); setError(''); }}>{t('合并到…')}</Button>
            <Popconfirm
              title={t('确认删除该客户及其全部记忆？此操作不可撤销')}
              okText={t('确认删除')}
              okButtonProps={{ danger: true }}
              cancelText={t('取消')}
              onConfirm={() => void deleteCustomer()}
            >
              <Button danger disabled={busy}>{t('删除客户')}</Button>
            </Popconfirm>
          </>
        )}
      </div>
      <Card title={t('服务标记')} data-type="flag">
        <Table
          rowKey="key"
          dataSource={flagKeys.map(key => ({ key, flag: flagOf(key) }))}
          pagination={false}
          locale={{ emptyText: <Empty description={t('暂无服务标记')} /> }}
          columns={[
            {
              title: t('标记'), dataIndex: 'key', width: 140,
              render: (value: string) => <Tag color={flagKeyColors[value]}>{flagKeyLabels()[value] || value}</Tag>,
            },
            {
              title: t('说明'), key: 'content',
              render: (_, row) => row.flag ? row.flag.content : <Typography.Text type="secondary">{t('未标记')}</Typography.Text>,
            },
            {
              title: t('来源'), key: 'source', width: 150,
              render: (_, row) => row.flag ? <SourceRef item={row.flag} /> : '—',
            },
            {
              title: t('更新时间'), key: 'updated_at', width: 180,
              render: (_, row) => row.flag ? time(row.flag.updated_at) : '—',
            },
            ...(operator ? [{
              title: t('操作'), key: 'actions', width: 170,
              render: (_: unknown, row: { key: string; flag?: MemoryItem }) => (
                <Space size={4} wrap>
                  <Button size="small" disabled={busy} onClick={() => { setFlagEdit({ key: row.key, content: row.flag?.content || '' }); setError(''); }}>
                    {row.flag ? t('编辑说明') : t('打标')}
                  </Button>
                  {row.flag && (
                    <Popconfirm
                      title={t('确认解除「{{v0}}」标记？', { v0: flagKeyLabels()[row.key] || row.key })}
                      okText={t('确认')}
                      cancelText={t('取消')}
                      onConfirm={() => void run(() => api(`/memory/customers/${customerId}/flags/${row.key}`, { method: 'DELETE' }), t('标记已解除'))}
                    >
                      <Button size="small" danger disabled={busy}>{t('解除')}</Button>
                    </Popconfirm>
                  )}
                </Space>
              ),
            }] : []),
          ]}
        />
      </Card>
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
        <Card key={type} title={typeTitles()[type] || type} data-type={type}>
          <MemoryItemTable
            items={itemsOf(type)}
            operator={operator}
            busy={busy}
            emptyText={t('暂无{{v0}}记忆', { v0: typeLabels()[type] || type })}
            onEdit={openEditor}
            onDelete={item => void run(() => api(`/memory/items/${item.id}`, { method: 'DELETE' }), t('记忆已删除'))}
            onRestore={item => void run(() => post(`/memory/items/${item.id}/restore`), t('记忆已恢复'))}
            onRelease={item => void run(() => post(`/memory/items/${item.id}/release`), t('已恢复自动管理'))}
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
      <Modal
        title={flagEdit ? t('编辑标记：{{v0}}', { v0: flagKeyLabels()[flagEdit.key] || flagEdit.key }) : ''}
        open={Boolean(flagEdit)}
        onOk={() => void saveFlag()}
        onCancel={() => { if (!busy) setFlagEdit(null); }}
        okText={t('保存')}
        cancelText={t('取消')}
        okButtonProps={{ loading: busy, disabled: !flagEdit?.content.trim() }}
        cancelButtonProps={{ disabled: busy }}
        maskClosable={!busy}
        keyboard={!busy}
        destroyOnHidden
      >
        {flagEdit && (
          <Form layout="vertical">
            <Form.Item label={t('标记说明')} required validateStatus={error ? 'error' : undefined} help={error ? errorText(error) : undefined}>
              <Input.TextArea
                aria-label={t('标记说明')}
                rows={3}
                maxLength={500}
                showCount
                value={flagEdit.content}
                onChange={e => setFlagEdit({ ...flagEdit, content: e.target.value })}
                disabled={busy}
              />
            </Form.Item>
          </Form>
        )}
      </Modal>
      <Modal
        title={t('合并客户')}
        open={merging}
        onOk={() => void mergeCustomer()}
        onCancel={() => { if (!busy) setMerging(false); }}
        okText={t('确认合并')}
        okButtonProps={{ danger: true, loading: busy, disabled: !mergeTarget.trim() }}
        cancelText={t('取消')}
        cancelButtonProps={{ disabled: busy }}
        maskClosable={!busy}
        keyboard={!busy}
        destroyOnHidden
      >
        <Form layout="vertical">
          <Alert type="warning" showIcon title={t('合并后当前客户的身份与记忆将迁移到目标客户，当前客户将被删除。此操作不可撤销')} style={{ marginBottom: 16 }} />
          <Form.Item label={t('目标客户 ID')} required validateStatus={error ? 'error' : undefined} help={error ? errorText(error) : undefined}>
            <Input
              aria-label={t('目标客户 ID')}
              value={mergeTarget}
              onChange={e => setMergeTarget(e.target.value)}
              disabled={busy}
              placeholder={t('请输入目标客户 ID')}
            />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
