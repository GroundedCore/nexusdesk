import { Alert, Button, Empty, Table, Tag, Tooltip, Typography } from 'antd';
import { t, errorText } from '../../i18n';
import type { MemoryInjection, MemoryInjectionDetail, MemoryInjectionList, MemoryRunInjections } from '../../shared/api/client';
import { time, useResource } from '../../shared/components/ui';
import { conversationHref } from './MemoryItemTable';
import { sectionLabels, statusColors, statusLabels, typeLabels } from './labels';

export const emptyRunInjectionsText = () => t('该会话无注入明细记录（功能上线前数据）');

interface DetailProps {
  runId: string;
  onOpenItem?: (detail: MemoryInjectionDetail) => void;
}

/** Item-level injection detail of a single run (GET /memory/runs/{run_id}/injections). */
export function RunInjectionItems({ runId, onOpenItem }: DetailProps) {
  const rows = useResource<MemoryRunInjections>(`/memory/runs/${runId}/injections`);
  const items = rows.data?.items || [];
  return (
    <>
      {rows.error && <Alert type="error" showIcon title={t('注入明细加载失败')} description={errorText(rows.error)} action={<Button onClick={rows.refresh}>{t('重试')}</Button>} />}
      <Table<MemoryInjectionDetail>
        rowKey="item_id"
        size="small"
        dataSource={items}
        loading={rows.loading}
        pagination={false}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={emptyRunInjectionsText()} /> }}
        columns={[
          {
            title: t('键'), dataIndex: 'key', width: 180,
            render: (value: string | null, row) => (
              <Tooltip title={row.item_id}>
                {row.item_deleted || value == null
                  ? <Typography.Text type="secondary" delete>{value || row.item_id.slice(0, 8)}</Typography.Text>
                  : onOpenItem
                    ? <Button type="link" size="small" onClick={() => onOpenItem(row)}><Typography.Text code>{value}</Typography.Text></Button>
                    : <Typography.Text code>{value}</Typography.Text>}
              </Tooltip>
            ),
          },
          {
            title: t('类型'), dataIndex: 'type', width: 100,
            render: (value: string | null, row) => row.item_deleted || value == null
              ? <Tag>{t('已删除')}</Tag>
              : <Tag>{typeLabels()[value] || value}</Tag>,
          },
          {
            title: t('注入小节'), dataIndex: 'section', width: 100,
            render: (value: string) => sectionLabels()[value] || value,
          },
          {
            title: t('排序分'), dataIndex: 'score', width: 90,
            render: (value: number | null) => (value == null ? '—' : value.toFixed(2)),
          },
          {
            title: t('状态'), dataIndex: 'status', width: 100,
            render: (value: string | null, row) => row.item_deleted || value == null
              ? <Typography.Text type="secondary">—</Typography.Text>
              : <Tag color={statusColors[value]}>{statusLabels()[value] || value}</Tag>,
          },
        ]}
      />
    </>
  );
}

interface PanelProps {
  customerId: string;
  onOpenItem?: (detail: MemoryInjectionDetail) => void;
}

/** Run-dimension injection history of a customer (FR-4, customer detail injections tab). */
export function InjectionLogPanel({ customerId, onOpenItem }: PanelProps) {
  const rows = useResource<MemoryInjectionList>(`/memory/customers/${customerId}/injections`);
  const items = rows.data?.items || [];
  return (
    <>
      {rows.error && <Alert type="error" showIcon title={t('注入记录加载失败')} description={errorText(rows.error)} action={<Button onClick={rows.refresh}>{t('重试')}</Button>} />}
      <Table<MemoryInjection>
        rowKey="run_id"
        dataSource={items}
        loading={rows.loading}
        pagination={false}
        scroll={{ x: 720 }}
        locale={{ emptyText: <Empty description={t('暂无注入记录')} /> }}
        expandable={{
          expandedRowRender: row => <RunInjectionItems runId={row.run_id} onOpenItem={onOpenItem} />,
          rowExpandable: () => true,
        }}
        columns={[
          { title: t('注入时间'), dataIndex: 'injected_at', width: 190, render: (value: string) => time(value) },
          {
            title: t('运行'), dataIndex: 'run_id', width: 140,
            render: (value: string) => (
              <Tooltip title={value}>
                <Typography.Text code>{value.slice(0, 8)}</Typography.Text>
              </Tooltip>
            ),
          },
          { title: t('注入条数'), dataIndex: 'item_count', width: 100 },
          {
            title: t('会话'), dataIndex: 'conversation_id', width: 120,
            render: (value: string | null) => value
              ? <Typography.Link href={conversationHref(value)}>{t('查看会话')}</Typography.Link>
              : <Typography.Text type="secondary">—</Typography.Text>,
          },
        ]}
      />
    </>
  );
}
