import { Button, Popconfirm, Space, Table, Tag, Tooltip, Typography } from 'antd';
import { t } from '../../i18n';
import type { MemoryItem } from '../../shared/api/client';
import { time } from '../../shared/components/ui';
import { sourceTypeLabels, statusColors, statusLabels } from './labels';

interface Props {
  items: MemoryItem[];
  operator: boolean;
  busy: boolean;
  emptyText: string;
  onEdit: (item: MemoryItem) => void;
  onDelete: (item: MemoryItem) => void;
  onRestore: (item: MemoryItem) => void;
  onRelease?: (item: MemoryItem) => void;
}

/** Conversation deep link consumed by ConversationsPage (?focus=). */
export function conversationHref(conversationId: string) {
  return `#/conversations/_all?focus=${encodeURIComponent(conversationId)}`;
}

export function SourceRef({ item }: { item: MemoryItem }) {
  return (
    <Space size={4} wrap>
      <Typography.Text type="secondary">{sourceTypeLabels()[item.source_type] || item.source_type}</Typography.Text>
      {item.source_ref && (
        <Tooltip title={item.source_ref}>
          <Typography.Link href={conversationHref(item.source_ref)}>{t('来源会话')}</Typography.Link>
        </Tooltip>
      )}
    </Space>
  );
}

export function MemoryItemTable({ items, operator, busy, emptyText, onEdit, onDelete, onRestore, onRelease }: Props) {
  return (
    <Table<MemoryItem>
      rowKey="id"
      dataSource={items}
      pagination={{
        pageSize: 10,
        hideOnSinglePage: true,
        showSizeChanger: false,
        showTotal: total => t('共 {{v0}} 条', { v0: total }),
      }}
      scroll={{ x: 1080 }}
      locale={{ emptyText: emptyText }}
      columns={[
        {
          title: t('键'), dataIndex: 'key', width: 170,
          render: (value: string, row) => (
            <Space size={4} wrap>
              <Typography.Text code>{value}</Typography.Text>
              {row.protected && <Tag color="blue">{t('人工保护')}</Tag>}
            </Space>
          ),
        },
        {
          title: t('内容'), dataIndex: 'content',
          render: (value: string, row) => (
            <span className={row.status === 'deleted' ? 'memory-item-deleted' : undefined}>{value}</span>
          ),
        },
        { title: t('置信度'), dataIndex: 'confidence', width: 90, render: (value: number) => value.toFixed(2) },
        {
          title: t('有效置信度'), dataIndex: 'effective_confidence', width: 100,
          render: (value: number | null | undefined) => (value == null ? '—' : value.toFixed(2)),
        },
        {
          title: t('状态'), dataIndex: 'status', width: 100,
          render: (value: string) => <Tag color={statusColors[value]}>{statusLabels()[value] || value}</Tag>,
        },
        { title: t('来源'), key: 'source', width: 150, render: (_, row) => <SourceRef item={row} /> },
        {
          title: t('最近引用'), dataIndex: 'last_referenced_at', width: 160,
          render: (value: string | null | undefined) => (value ? time(value) : '—'),
        },
        { title: t('更新时间'), dataIndex: 'updated_at', width: 160, render: (value: string) => time(value) },
        ...(operator ? [{
          title: t('操作'), key: 'actions', width: 240,
          render: (_: unknown, row: MemoryItem) => (
            <Space size={4} wrap>
              {row.status !== 'deleted' && (
                <>
                  <Button size="small" disabled={busy} onClick={() => onEdit(row)}>{t('编辑')}</Button>
                  <Popconfirm
                    title={t('确认删除这条记忆？删除后可恢复')}
                    okText={t('确认')}
                    cancelText={t('取消')}
                    onConfirm={() => onDelete(row)}
                  >
                    <Button size="small" danger disabled={busy}>{t('删除')}</Button>
                  </Popconfirm>
                </>
              )}
              {(row.status === 'deleted' || row.status === 'expired') && (
                <Button size="small" disabled={busy} onClick={() => onRestore(row)}>{t('恢复')}</Button>
              )}
              {row.protected && row.status === 'active' && onRelease && (
                <Popconfirm
                  title={t('恢复自动管理后，自动抽取与衰退将再次作用于这条记忆')}
                  okText={t('确认')}
                  cancelText={t('取消')}
                  onConfirm={() => onRelease(row)}
                >
                  <Button size="small" disabled={busy}>{t('恢复自动管理')}</Button>
                </Popconfirm>
              )}
            </Space>
          ),
        }] : []),
      ]}
    />
  );
}
