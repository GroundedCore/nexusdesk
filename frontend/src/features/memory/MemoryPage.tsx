import { Card, Tabs } from 'antd';
import { t } from '../../i18n';
import { useAccess } from '../../shared/components/ui';
import { CustomerList } from './CustomerList';
import { PendingQueue } from './PendingQueue';
import { CustomerDetail } from './CustomerDetail';

interface Props {
  route: string;
  onNavigate: (path: string) => void;
}

export function MemoryPage({ route, onNavigate }: Props) {
  const { operator } = useAccess();
  const base = route.split('?')[0];
  const detail = base.match(/^\/memory\/customers\/([0-9a-zA-Z_-]+)$/);
  if (detail) {
    return (
      <CustomerDetail
        customerId={detail[1]}
        operator={operator}
        onBack={() => onNavigate('/memory')}
      />
    );
  }
  const tab = base === '/memory/pending' ? 'pending' : 'customers';
  return (
    <Card className="memory-workspace">
      <Tabs
        activeKey={tab}
        onChange={key => onNavigate(key === 'pending' ? '/memory/pending' : '/memory')}
        items={[
          { key: 'customers', label: t('客户列表'), children: <CustomerList onOpen={id => onNavigate(`/memory/customers/${id}`)} /> },
          { key: 'pending', label: t('待确认队列'), children: <PendingQueue operator={operator} onOpenCustomer={id => onNavigate(`/memory/customers/${id}`)} /> },
        ]}
      />
    </Card>
  );
}
