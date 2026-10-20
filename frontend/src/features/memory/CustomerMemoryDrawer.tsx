import { useState } from 'react';
import { Alert, Button, Collapse, Drawer, Empty, Spin, Typography } from 'antd';
import { t, errorText } from '../../i18n';
import type { MemoryCustomerDetail, MemoryInjectionDetail, Run } from '../../shared/api/client';
import { time, useResource } from '../../shared/components/ui';
import { RunInjectionItems } from './InjectionLogPanel';
import { MemoryItemDetailDrawer, type ItemDetailTarget } from './MemoryItemDetailDrawer';

interface Props {
  customerId: string;
  open: boolean;
  onClose: () => void;
  /** Conversation runs for the "this conversation" injection view (FR-6); newest first. */
  runs?: Run[];
}

const sections: { type: string; title: () => string }[] = [
  { type: 'profile', title: () => t('客户画像') },
  { type: 'preference', title: () => t('客户偏好') },
  { type: 'event', title: () => t('事件') },
];

/** Read-only profile/preference/event snapshot for the conversation workbench (US-3). */
export function CustomerMemoryDrawer({ customerId, open, onClose, runs }: Props) {
  const detail = useResource<MemoryCustomerDetail>(open ? `/memory/customers/${customerId}` : null);
  const [itemDetail, setItemDetail] = useState<ItemDetailTarget | null>(null);
  const [itemDetailOpen, setItemDetailOpen] = useState(false);
  const activeOf = (type: string) => (detail.data?.items || []).filter(item => item.type === type && item.status === 'active');

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

  const recentRuns = (runs || []).slice(0, 10);
  return (
    <Drawer
      title={t('客户画像与偏好')}
      open={open}
      onClose={onClose}
      size={480}
      extra={<Button type="link" href={`#/memory/customers/${customerId}`}>{t('查看客户记忆')}</Button>}
    >
      {detail.error && <Alert type="error" showIcon title={t('加载失败')} description={errorText(detail.error)} action={<Button onClick={detail.refresh}>{t('重试')}</Button>} />}
      {detail.loading && <Spin tip={t('正在加载')}><div style={{ minHeight: 120 }} /></Spin>}
      {runs && (
        <section className="memory-drawer-section">
          <h3>{t('本次对话注入')}</h3>
          {recentRuns.length === 0
            ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('本次对话暂无运行记录，无注入明细')} />
            : (
              <Collapse
                className="memory-run-injections"
                defaultActiveKey={recentRuns[0] ? [recentRuns[0].id] : []}
                items={recentRuns.map(run => ({
                  key: run.id,
                  label: (
                    <span className="memory-run-injection-label">
                      <Typography.Text code>{run.id.slice(0, 8)}</Typography.Text>
                      <Typography.Text type="secondary">{time(run.created_at)}</Typography.Text>
                    </span>
                  ),
                  children: <RunInjectionItems runId={run.id} onOpenItem={openInjectionItem} />,
                }))}
              />
            )}
        </section>
      )}
      {detail.data && sections.map(section => (
        <section key={section.type} className="memory-drawer-section">
          <h3>{section.title()}</h3>
          {activeOf(section.type).length === 0
            ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('暂无记录')} />
            : (
              <dl className="memory-drawer-list">
                {activeOf(section.type).map(item => (
                  <div key={item.id} className="memory-drawer-item">
                    <dt><Typography.Text code>{item.key}</Typography.Text></dt>
                    <dd>{item.content}</dd>
                  </div>
                ))}
              </dl>
            )}
        </section>
      ))}
      <MemoryItemDetailDrawer
        item={itemDetail}
        open={itemDetailOpen}
        onClose={() => setItemDetailOpen(false)}
      />
    </Drawer>
  );
}
