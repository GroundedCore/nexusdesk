import { useState } from 'react';
import { App, Alert, Button, Empty, Popconfirm, Space, Table, Tag, Typography } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { post, type MemoryPendingItem, type MemoryPendingPage } from '../../shared/api/client';
import { errorText, t } from '../../i18n';
import { time, useResource } from '../../shared/components/ui';
import { SourceRef } from './MemoryItemTable';
import { typeLabels } from './labels';

const pageSize = 20;

interface Props {
  operator: boolean;
  onOpenCustomer: (customerId: string) => void;
}

export function PendingQueue({ operator, onOpenCustomer }: Props) {
  const [page, setPage] = useState(1);
  const rows = useResource<MemoryPendingPage>(`/memory/pending?page=${page}&page_size=${pageSize}`);
  const { message } = App.useApp();
  const [busyId, setBusyId] = useState('');

  async function decide(item: MemoryPendingItem, action: 'confirm' | 'reject') {
    if (busyId)
      return;
    setBusyId(item.id);
    try {
      await post(`/memory/items/${item.id}/${action}`);
      rows.refresh();
      void message.success(action === 'confirm' ? t('已确认') : t('已拒绝'));
    }
    catch (e) {
      void message.error(errorText(e instanceof Error ? e.message : t('操作失败')));
    }
    finally {
      setBusyId('');
    }
  }

  return (
    <>
      {rows.error && <Alert type="error" showIcon title={t('待确认队列加载失败')} description={errorText(rows.error)} action={<Button onClick={rows.refresh}>{t('重试')}</Button>} />}
      <div className="memory-toolbar">
        <Typography.Text type="secondary">{t('自动抽取的低置信记忆，确认后才会对客户生效')}</Typography.Text>
        <Button icon={<ReloadOutlined />} onClick={rows.refresh} loading={rows.loading}>{t('刷新')}</Button>
      </div>
      <Table<MemoryPendingItem>
        rowKey="id"
        dataSource={rows.data?.items || []}
        loading={rows.loading}
        scroll={{ x: 960 }}
        pagination={{
          current: page,
          onChange: setPage,
          pageSize,
          total: rows.data?.total || 0,
          showSizeChanger: false,
          showTotal: total => t('共 {{v0}} 条待确认', { v0: total }),
        }}
        locale={{ emptyText: <Empty description={t('暂无待确认的记忆')} /> }}
        columns={[
          {
            title: t('客户'), key: 'customer', width: 220,
            render: (_, row) => (
              <>
                <Button type="link" onClick={() => onOpenCustomer(row.customer_id)}>{row.customer_external_keys?.[0] || row.customer_id.slice(0, 8)}</Button>
              </>
            ),
          },
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
            render: (_: unknown, row: MemoryPendingItem) => (
              <Space size={4} wrap>
                <Popconfirm
                  title={row.key.endsWith(':suggested') ? t('确认后将更新人工维护的原条目') : t('确认后这条记忆将对客户生效')}
                  okText={t('确认')}
                  cancelText={t('取消')}
                  onConfirm={() => void decide(row, 'confirm')}
                >
                  <Button size="small" type="primary" loading={busyId === row.id}>{t('确认')}</Button>
                </Popconfirm>
                <Popconfirm
                  title={t('拒绝后这条记忆将被删除')}
                  okText={t('确认')}
                  cancelText={t('取消')}
                  onConfirm={() => void decide(row, 'reject')}
                >
                  <Button size="small" danger disabled={busyId === row.id}>{t('拒绝')}</Button>
                </Popconfirm>
              </Space>
            ),
          }] : []),
        ]}
      />
    </>
  );
}
