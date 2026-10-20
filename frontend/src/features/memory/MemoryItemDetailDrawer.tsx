import { Alert, Button, Descriptions, Drawer, Empty, Spin, Table, Tag, Tooltip, Typography } from 'antd';
import { t, errorText } from '../../i18n';
import type { MemoryItem, MemoryItemInjection, MemoryItemInjectionList, MemoryItemTimeline } from '../../shared/api/client';
import { time, useResource } from '../../shared/components/ui';
import { AuditTimeline } from './AuditTimeline';
import { conversationHref, SourceRef } from './MemoryItemTable';
import { sectionLabels, statusColors, statusLabels, typeLabels } from './labels';

/** Fields guaranteed by every caller; the rest degrade to placeholders (deleted items, C3). */
export type ItemDetailTarget = Partial<MemoryItem> & { id: string };

interface Props {
  item: ItemDetailTarget | null;
  open: boolean;
  onClose: () => void;
}

interface DiffLine {
  type: 'same' | 'del' | 'add';
  text: string;
}

/** Line-level LCS diff: removed lines only in `prev`, added lines only in `next`. */
export function diffLines(prev: string, next: string): DiffLine[] {
  const a = prev.split('\n');
  const b = next.split('\n');
  const m = a.length;
  const n = b.length;
  // lcs[i][j] = length of the longest common subsequence of a[i:] and b[j:].
  const lcs: number[][] = Array.from({ length: m + 1 }, () => new Array<number>(n + 1).fill(0));
  for (let i = m - 1; i >= 0; i--)
    for (let j = n - 1; j >= 0; j--)
      lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
  const lines: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < m && j < n) {
    if (a[i] === b[j]) {
      lines.push({ type: 'same', text: a[i] });
      i++;
      j++;
    }
    else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      lines.push({ type: 'del', text: a[i] });
      i++;
    }
    else {
      lines.push({ type: 'add', text: b[j] });
      j++;
    }
  }
  while (i < m) lines.push({ type: 'del', text: a[i++] });
  while (j < n) lines.push({ type: 'add', text: b[j++] });
  return lines;
}

function bool(value: boolean | undefined) {
  return value ? t('是') : t('否');
}

/** FR-5: full fields + prev_content diff + lifecycle timeline + recent injections. */
export function MemoryItemDetailDrawer({ item: target, open, onClose }: Props) {
  const itemId = open && target ? target.id : '';
  const lifecycle = useResource<MemoryItemTimeline>(itemId ? `/memory/items/${itemId}/timeline` : null);
  const injections = useResource<MemoryItemInjectionList>(itemId ? `/memory/items/${itemId}/injections` : null);
  if (!target)
    return null;
  // The timeline endpoint returns the live row; hydrate fields from it when the
  // caller only had the injection-log projection (null once physically deleted).
  const item: ItemDetailTarget = { ...target, ...(lifecycle.data?.item || {}) };
  const deleted = item.status === 'deleted' || (lifecycle.data !== null && lifecycle.data?.item === null);
  const hasContent = typeof item.content === 'string';
  const diff = item.prev_content != null && hasContent ? diffLines(item.prev_content, item.content || '') : [];
  return (
    <Drawer
      title={t('记忆详情')}
      open={open}
      onClose={onClose}
      size={640}
      className="memory-item-detail"
    >
      <Descriptions
        column={1}
        size="small"
        bordered
        className="memory-item-fields"
        items={[
          { key: 'key', label: t('键'), children: item.key ? <Typography.Text code>{item.key}</Typography.Text> : '—' },
          {
            key: 'type', label: t('类型'),
            children: item.type ? <Tag>{typeLabels()[item.type] || item.type}</Tag> : '—',
          },
          {
            key: 'status', label: t('状态'),
            children: item.status ? <Tag color={statusColors[item.status]}>{statusLabels()[item.status] || item.status}</Tag> : '—',
          },
          {
            key: 'content', label: t('内容'),
            children: hasContent
              ? <span className={deleted ? 'memory-item-deleted' : undefined}>{item.content}</span>
              : deleted
                ? <Typography.Text type="secondary">{t('条目已删除，内容不可还原')}</Typography.Text>
                : '—',
          },
          { key: 'confidence', label: t('置信度'), children: typeof item.confidence === 'number' ? item.confidence.toFixed(2) : '—' },
          { key: 'effective_confidence', label: t('有效置信度'), children: item.effective_confidence == null ? '—' : item.effective_confidence.toFixed(2) },
          {
            key: 'source', label: t('来源'),
            children: item.source_type
              ? <SourceRef item={{ source_type: item.source_type, source_ref: item.source_ref ?? null } as MemoryItem} />
              : '—',
          },
          { key: 'protected', label: t('人工保护'), children: bool(item.protected) },
          { key: 'reference_count', label: t('引用次数'), children: item.reference_count ?? '—' },
          { key: 'last_referenced_at', label: t('最近引用'), children: item.last_referenced_at ? time(item.last_referenced_at) : '—' },
          { key: 'revision', label: t('修订版本'), children: item.revision ?? '—' },
          { key: 'created_at', label: t('创建时间'), children: item.created_at ? time(item.created_at) : '—' },
          { key: 'updated_at', label: t('更新时间'), children: item.updated_at ? time(item.updated_at) : '—' },
        ]}
      />
      {item.prev_content != null && hasContent && (
        <section className="memory-drawer-section">
          <h3>{t('内容变更（上一版 → 当前）')}</h3>
          <div className="memory-diff">
            <div className="memory-diff-col" aria-label={t('上一版内容')}>
              <h4>{t('上一版')}</h4>
              {diff.filter(line => line.type !== 'add').map((line, index) => (
                <div key={index} className={`memory-diff-line${line.type === 'del' ? ' is-del' : ''}`}>{line.text || ' '}</div>
              ))}
            </div>
            <div className="memory-diff-col" aria-label={t('当前内容')}>
              <h4>{t('当前')}</h4>
              {diff.filter(line => line.type !== 'del').map((line, index) => (
                <div key={index} className={`memory-diff-line${line.type === 'add' ? ' is-add' : ''}`}>{line.text || ' '}</div>
              ))}
            </div>
          </div>
        </section>
      )}
      <section className="memory-drawer-section">
        <h3>{t('生命周期时间线')}</h3>
        {lifecycle.error && <Alert type="error" showIcon title={t('时间线加载失败')} description={errorText(lifecycle.error)} action={<Button onClick={lifecycle.refresh}>{t('重试')}</Button>} />}
        {lifecycle.loading && <Spin tip={t('正在加载')}><div style={{ minHeight: 80 }} /></Spin>}
        {lifecycle.data && <AuditTimeline events={lifecycle.data.timeline || []} emptyText={t('暂无生命周期事件')} />}
      </section>
      <section className="memory-drawer-section">
        <h3>{t('最近注入')}</h3>
        {injections.error && <Alert type="error" showIcon title={t('注入记录加载失败')} description={errorText(injections.error)} action={<Button onClick={injections.refresh}>{t('重试')}</Button>} />}
        <Table<MemoryItemInjection>
          rowKey={row => `${row.run_id}-${row.injected_at}`}
          size="small"
          dataSource={injections.data?.items || []}
          loading={injections.loading}
          pagination={false}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('该记忆近期未被注入')} /> }}
          columns={[
            { title: t('注入时间'), dataIndex: 'injected_at', width: 180, render: (value: string) => time(value) },
            {
              title: t('运行'), dataIndex: 'run_id', width: 120,
              render: (value: string) => (
                <Tooltip title={value}>
                  <Typography.Text code>{value.slice(0, 8)}</Typography.Text>
                </Tooltip>
              ),
            },
            { title: t('注入小节'), dataIndex: 'section', width: 90, render: (value: string) => sectionLabels()[value] || value },
            { title: t('排序分'), dataIndex: 'score', width: 80, render: (value: number | null) => (value == null ? '—' : value.toFixed(2)) },
            {
              title: t('会话'), dataIndex: 'conversation_id', width: 100,
              render: (value: string | null) => value
                ? <Typography.Link href={conversationHref(value)}>{t('查看会话')}</Typography.Link>
                : <Typography.Text type="secondary">—</Typography.Text>,
            },
          ]}
        />
      </section>
    </Drawer>
  );
}
