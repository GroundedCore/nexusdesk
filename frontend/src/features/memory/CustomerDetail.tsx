import { useState } from 'react';
import { App, Alert, Badge, Button, Card, Empty, Form, Input, Modal, Popconfirm, Space, Table, Tabs, Tag, Typography } from 'antd';
import { ArrowLeftOutlined, ReloadOutlined } from '@ant-design/icons';
import { api, patch, post, put, type MemoryAuditList, type MemoryCustomerDetail as Detail, type MemoryIdentity, type MemoryInjectionDetail, type MemoryInjectionList, type MemoryItem } from '../../shared/api/client';
import { errorText, t } from '../../i18n';
import { time, useResource } from '../../shared/components/ui';
import { AuditTimeline } from './AuditTimeline';
import { InjectionLogPanel } from './InjectionLogPanel';
import { MemoryItemDetailDrawer, type ItemDetailTarget } from './MemoryItemDetailDrawer';
import { MemoryItemTable, SourceRef } from './MemoryItemTable';
import { flagKeyColors, flagKeyLabels, flagKeys, identitySourceLabels, typeLabels } from './labels';

interface Props {
  customerId: string;
  operator: boolean;
  initialTab?: string;
  onBack: () => void;
}

const memoryTypes = ['profile', 'preference', 'event'];
const typeTitles = (): Record<string, string> => ({ profile: t('客户画像'), preference: t('客户偏好'), event: t('事件') });
const tabKeys = ['overview', 'profile', 'preference', 'event', 'flags', 'pending', 'injections', 'audit'];

interface OverviewProps {
  detail: Detail;
}

/** FR-3 overview tab: identity + per-type stats + last update/injection. */
function OverviewTab({ detail }: OverviewProps) {
  const injections = useResource<MemoryInjectionList>(`/memory/customers/${detail.id}/injections?limit=1`);
  const items = detail.items || [];
  const live = items.filter(item => item.status !== 'deleted');
  const countOf = (type: string) => live.filter(item => item.type === type).length;
  const pending = items.filter(item => item.status === 'pending').length;
  const flags = items.filter(item => item.type === 'flag' && item.status === 'active').map(item => item.key);
  const lastUpdated = items.reduce<string | null>((max, item) => (item.updated_at && (!max || item.updated_at > max) ? item.updated_at : max), null);
  const lastInjected = injections.data?.items?.[0]?.injected_at || null;
  const stats: { label: string; value: number }[] = [
    { label: t('记忆总数'), value: live.length },
    { label: t('客户画像'), value: countOf('profile') },
    { label: t('客户偏好'), value: countOf('preference') },
    { label: t('事件'), value: countOf('event') },
    { label: t('待确认'), value: pending },
  ];
  return (
    <Space orientation="vertical" size="large" style={{ width: '100%' }}>
      <div className="memory-stats">
        {stats.map(stat => (
          <div key={stat.label} className="memory-stat">
            <span>{stat.label}</span>
            <strong>{stat.value}</strong>
          </div>
        ))}
      </div>
      <Card size="small" title={t('客户信息')}>
        <dl className="memory-overview-facts">
          <div>
            <dt>{t('服务标记')}</dt>
            <dd>{flags.length ? flags.map(key => <Tag key={key} color={flagKeyColors[key]}>{flagKeyLabels()[key] || key}</Tag>) : <Typography.Text type="secondary">{t('未标记')}</Typography.Text>}</dd>
          </div>
          <div>
            <dt>{t('最近更新')}</dt>
            <dd>{lastUpdated ? time(lastUpdated) : '—'}</dd>
          </div>
          <div>
            <dt>{t('最近注入时间')}</dt>
            <dd>{lastInjected ? time(lastInjected) : <Typography.Text type="secondary">{t('暂无注入记录')}</Typography.Text>}</dd>
          </div>
          <div>
            <dt>{t('创建时间')}</dt>
            <dd>{time(detail.created_at)}</dd>
          </div>
        </dl>
      </Card>
      <Card size="small" title={t('身份绑定')}>
        <Table<MemoryIdentity>
          rowKey="id"
          dataSource={detail.identities || []}
          pagination={false}
          locale={{ emptyText: <Empty description={t('暂无身份绑定')} /> }}
          columns={[
            { title: t('来源'), dataIndex: 'source', width: 140, render: (value: string) => identitySourceLabels()[value] || value },
            { title: t('客户标识'), dataIndex: 'external_key', render: (value: string) => <Typography.Text code>{value}</Typography.Text> },
            { title: t('绑定时间'), dataIndex: 'created_at', width: 200, render: (value: string) => time(value) },
          ]}
        />
      </Card>
    </Space>
  );
}

interface PendingTabProps {
  items: MemoryItem[];
  operator: boolean;
  busy: boolean;
  onDecide: (item: MemoryItem, action: 'confirm' | 'reject') => void;
  busyId: string;
}

/** FR-3 pending tab: this customer's pending queue (confirm/reject as in PendingQueue). */
function CustomerPendingTab({ items, operator, busy, onDecide, busyId }: PendingTabProps) {
  return (
    <Table<MemoryItem>
      rowKey="id"
      dataSource={items}
      pagination={false}
      scroll={{ x: 960 }}
      locale={{ emptyText: <Empty description={t('暂无待确认的记忆')} /> }}
      columns={[
        {
          title: t('类型'), dataIndex: 'type', width: 90,
          render: (value: string) => <Tag>{typeLabels()[value] || value}</Tag>,
        },
        {
          title: t('键'), dataIndex: 'key', width: 200,
          render: (value: string) => {
            const suggested = value.endsWith(':suggested');
            return (
              <Space size={4} wrap>
                <Typography.Text code>{suggested ? value.slice(0, -':suggested'.length) : value}</Typography.Text>
                {suggested && <Tag color="blue">{t('建议更新（人工保护）')}</Tag>}
              </Space>
            );
          },
        },
        { title: t('内容'), dataIndex: 'content' },
        { title: t('置信度'), dataIndex: 'confidence', width: 90, render: (value: number) => value.toFixed(2) },
        { title: t('来源'), key: 'source', width: 160, render: (_, row) => <SourceRef item={row} /> },
        { title: t('更新时间'), dataIndex: 'updated_at', width: 180, render: (value: string) => time(value) },
        ...(operator ? [{
          title: t('操作'), key: 'actions', width: 170,
          render: (_: unknown, row: MemoryItem) => (
            <Space size={4} wrap>
              <Popconfirm
                title={row.key.endsWith(':suggested') ? t('确认后将更新人工维护的原条目') : t('确认后这条记忆将对客户生效')}
                okText={t('确认')}
                cancelText={t('取消')}
                onConfirm={() => onDecide(row, 'confirm')}
              >
                <Button size="small" type="primary" loading={busyId === row.id} disabled={busy}>{t('确认')}</Button>
              </Popconfirm>
              <Popconfirm
                title={t('拒绝后这条记忆将被删除')}
                okText={t('确认')}
                cancelText={t('取消')}
                onConfirm={() => onDecide(row, 'reject')}
              >
                <Button size="small" danger disabled={busy || busyId === row.id}>{t('拒绝')}</Button>
              </Popconfirm>
            </Space>
          ),
        }] : []),
      ]}
    />
  );
}

/** FR-3 audit tab: customer-scoped memory.* timeline (lazy: mounts with the tab). */
function CustomerAuditTab({ customerId }: { customerId: string }) {
  const audit = useResource<MemoryAuditList>(`/memory/customers/${customerId}/audit`);
  return (
    <>
      {audit.error && <Alert type="error" showIcon title={t('审计记录加载失败')} description={errorText(audit.error)} action={<Button onClick={audit.refresh}>{t('重试')}</Button>} />}
      {audit.loading && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('正在加载')} />}
      {audit.data && <AuditTimeline events={audit.data.items || []} />}
    </>
  );
}

export function CustomerDetail({ customerId, operator, initialTab, onBack }: Props) {
  const detail = useResource<Detail>(`/memory/customers/${customerId}`);
  const { message } = App.useApp();
  const firstTab = tabKeys.includes(initialTab || '') ? (initialTab as string) : 'overview';
  const [tab, setTab] = useState(firstTab);
  const [visited, setVisited] = useState<Set<string>>(() => new Set([firstTab]));
  const [editing, setEditing] = useState<MemoryItem | null>(null);
  const [draft, setDraft] = useState('');
  const [flagEdit, setFlagEdit] = useState<{ key: string; content: string } | null>(null);
  const [merging, setMerging] = useState(false);
  const [mergeTarget, setMergeTarget] = useState('');
  const [busy, setBusy] = useState(false);
  const [decidingId, setDecidingId] = useState('');
  const [error, setError] = useState('');
  const [itemDetail, setItemDetail] = useState<ItemDetailTarget | null>(null);
  const [itemDetailOpen, setItemDetailOpen] = useState(false);

  function changeTab(key: string) {
    setTab(key);
    setVisited(previous => (previous.has(key) ? previous : new Set(previous).add(key)));
  }

  function openItemDetail(item: MemoryItem) {
    setItemDetail(item);
    setItemDetailOpen(true);
  }

  /** Injection rows only carry the join projection; prefer the full item when loaded. */
  function openInjectionItem(entry: MemoryInjectionDetail) {
    const full = (detail.data?.items || []).find(item => item.id === entry.item_id);
    setItemDetail(full || {
      id: entry.item_id,
      key: entry.key ?? undefined,
      type: entry.type ?? undefined,
      status: entry.item_deleted ? 'deleted' : entry.status ?? undefined,
    });
    setItemDetailOpen(true);
  }

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

  async function decide(item: MemoryItem, action: 'confirm' | 'reject') {
    if (decidingId || busy)
      return;
    setDecidingId(item.id);
    try {
      await post(`/memory/items/${item.id}/${action}`);
      detail.refresh();
      void message.success(action === 'confirm' ? t('已确认') : t('已拒绝'));
    }
    catch (e) {
      void message.error(errorText(e instanceof Error ? e.message : t('操作失败')));
    }
    finally {
      setDecidingId('');
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
  const pendingItems = items.filter(item => item.status === 'pending');
  const flagOf = (key: string) => items.find(item => item.type === 'flag' && item.key === key && item.status === 'active');
  const flagTable = (
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
  );
  const tabs = [
    {
      key: 'overview',
      label: t('概览'),
      children: detail.data ? <OverviewTab detail={detail.data} /> : null,
    },
    ...memoryTypes.map(type => ({
      key: type,
      label: typeTitles()[type] || type,
      children: (
        <MemoryItemTable
          items={itemsOf(type)}
          operator={operator}
          busy={busy}
          emptyText={t('暂无{{v0}}记忆', { v0: typeLabels()[type] || type })}
          onEdit={openEditor}
          onDelete={item => void run(() => api(`/memory/items/${item.id}`, { method: 'DELETE' }), t('记忆已删除'))}
          onRestore={item => void run(() => post(`/memory/items/${item.id}/restore`), t('记忆已恢复'))}
          onRelease={item => void run(() => post(`/memory/items/${item.id}/release`), t('已恢复自动管理'))}
          onOpenDetail={openItemDetail}
        />
      ),
    })),
    { key: 'flags', label: t('服务标记'), children: flagTable },
    {
      key: 'pending',
      label: pendingItems.length
        ? <span>{t('待确认')} <Badge count={pendingItems.length} size="small" /></span>
        : t('待确认'),
      children: (
        <CustomerPendingTab
          items={pendingItems}
          operator={operator}
          busy={busy}
          busyId={decidingId}
          onDecide={(item, action) => void decide(item, action)}
        />
      ),
    },
    {
      key: 'injections',
      label: t('注入记录'),
      children: <InjectionLogPanel customerId={customerId} onOpenItem={openInjectionItem} />,
    },
    {
      key: 'audit',
      label: t('审计记录'),
      children: <CustomerAuditTab customerId={customerId} />,
    },
  ];
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
      <Card className="memory-workspace">
        <Tabs
          activeKey={tab}
          onChange={changeTab}
          items={tabs.map(entry => ({
            key: entry.key,
            label: entry.label,
            children: visited.has(entry.key) ? entry.children : null,
          }))}
        />
      </Card>
      <MemoryItemDetailDrawer
        item={itemDetail}
        open={itemDetailOpen}
        onClose={() => setItemDetailOpen(false)}
      />
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
