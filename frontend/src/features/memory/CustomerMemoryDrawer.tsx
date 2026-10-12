import { Alert, Button, Drawer, Empty, Spin, Typography } from 'antd';
import { t, errorText } from '../../i18n';
import type { MemoryCustomerDetail } from '../../shared/api/client';
import { useResource } from '../../shared/components/ui';

interface Props {
  customerId: string;
  open: boolean;
  onClose: () => void;
}

const sections: { type: string; title: () => string }[] = [
  { type: 'profile', title: () => t('客户画像') },
  { type: 'preference', title: () => t('客户偏好') },
];

/** Read-only profile/preference snapshot for the conversation workbench (US-3). */
export function CustomerMemoryDrawer({ customerId, open, onClose }: Props) {
  const detail = useResource<MemoryCustomerDetail>(open ? `/memory/customers/${customerId}` : null);
  const activeOf = (type: string) => (detail.data?.items || []).filter(item => item.type === type && item.status === 'active');
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
    </Drawer>
  );
}
