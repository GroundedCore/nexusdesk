import { useState } from 'react';
import { Alert, Button, Empty, Input, Table, Typography } from 'antd';
import { ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import { t, errorText } from '../../i18n';
import type { MemoryCustomer, MemoryCustomerPage } from '../../shared/api/client';
import { time, useResource } from '../../shared/components/ui';

const pageSize = 20;

interface Props {
  onOpen: (customerId: string) => void;
}

export function CustomerList({ onOpen }: Props) {
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const rows = useResource<MemoryCustomerPage>(`/memory/customers?page=${page}&page_size=${pageSize}${search ? `&q=${encodeURIComponent(search)}` : ''}`);
  return (
    <>
      {rows.error && <Alert type="error" showIcon title={t('客户列表加载失败')} description={errorText(rows.error)} action={<Button onClick={rows.refresh}>{t('重试')}</Button>} />}
      <div className="memory-toolbar">
        <Input.Search
          className="memory-search"
          prefix={<SearchOutlined />}
          aria-label={t('搜索客户标识')}
          placeholder={t('按 external_key 搜索客户')}
          allowClear
          value={query}
          onChange={e => setQuery(e.target.value)}
          onSearch={value => { setSearch(value.trim()); setPage(1); }}
        />
        <Button icon={<ReloadOutlined />} onClick={rows.refresh} loading={rows.loading}>{t('刷新')}</Button>
      </div>
      <Table<MemoryCustomer>
        rowKey="id"
        dataSource={rows.data?.items || []}
        loading={rows.loading}
        scroll={{ x: 760 }}
        pagination={{
          current: page,
          onChange: setPage,
          pageSize,
          total: rows.data?.total || 0,
          showSizeChanger: false,
          showTotal: total => t('共 {{v0}} 位客户', { v0: total }),
        }}
        locale={{ emptyText: <Empty description={search ? t('没有符合条件的客户') : t('渠道或开放平台会话建立客户档案后，会出现在这里')} /> }}
        columns={[
          {
            title: t('客户'), key: 'customer', width: 320,
            render: (_, row) => (
              <>
                <Button type="link" onClick={() => onOpen(row.id)}>{row.external_keys[0] || row.id}</Button>
                <div>
                  <Typography.Text type="secondary" code>{row.id.slice(0, 8)}</Typography.Text>
                  {row.external_keys.length > 1 && <Typography.Text type="secondary"> {t('等 {{v0}} 个身份', { v0: row.external_keys.length })}</Typography.Text>}
                </div>
              </>
            ),
          },
          { title: t('记忆条数'), dataIndex: 'item_count', width: 110 },
          { title: t('最近活跃时间'), dataIndex: 'last_active_at', render: (value?: string | null) => value ? time(value) : '—' },
          { title: t('创建时间'), dataIndex: 'created_at', render: (value?: string) => value ? time(value) : '—' },
          { title: t('操作'), key: 'actions', width: 110, render: (_, row) => <Button size="small" onClick={() => onOpen(row.id)}>{t('查看')}</Button> },
        ]}
      />
    </>
  );
}
