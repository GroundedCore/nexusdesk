import { useState } from 'react';
import { Alert, Button, Empty, Input, Select, Table, Tag, Typography } from 'antd';
import { ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import type { SorterResult } from 'antd/es/table/interface';
import { t, errorText } from '../../i18n';
import type { MemoryCustomer, MemoryCustomerPage } from '../../shared/api/client';
import { time, useResource } from '../../shared/components/ui';
import { flagKeyColors, flagKeyLabels, flagKeys } from './labels';

const pageSize = 20;

type SortKey = 'total' | 'pending';

interface Props {
  onOpen: (customerId: string, tab?: string) => void;
}

export function CustomerList({ onOpen }: Props) {
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState<SortKey | ''>('');
  const [flag, setFlag] = useState('');
  const path = `/memory/customers?page=${page}&page_size=${pageSize}&with_stats=true`
    + `${search ? `&q=${encodeURIComponent(search)}` : ''}`
    + `${sort ? `&sort=${sort}` : ''}`
    + `${flag ? `&flag=${encodeURIComponent(flag)}` : ''}`;
  const rows = useResource<MemoryCustomerPage>(path);

  function changeSort(_pagination: unknown, _filters: unknown, sorter: SorterResult<MemoryCustomer> | SorterResult<MemoryCustomer>[]) {
    const active = Array.isArray(sorter) ? sorter[0] : sorter;
    const field = active?.order ? (active.columnKey as SortKey) : '';
    setSort(field === 'total' || field === 'pending' ? field : '');
    setPage(1);
  }

  const flagLabels = flagKeyLabels();
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
        <Select
          className="memory-flag-filter"
          aria-label={t('按服务标记筛选')}
          placeholder={t('按服务标记筛选')}
          allowClear
          value={flag || undefined}
          onChange={value => { setFlag(value || ''); setPage(1); }}
          options={flagKeys.map(key => ({ value: key, label: flagLabels[key] || key }))}
        />
        <Button icon={<ReloadOutlined />} onClick={rows.refresh} loading={rows.loading}>{t('刷新')}</Button>
      </div>
      <Table<MemoryCustomer>
        rowKey="id"
        dataSource={rows.data?.items || []}
        loading={rows.loading}
        scroll={{ x: 1080 }}
        onChange={changeSort}
        pagination={{
          current: page,
          onChange: setPage,
          pageSize,
          total: rows.data?.total || 0,
          showSizeChanger: false,
          showTotal: total => t('共 {{v0}} 位客户', { v0: total }),
        }}
        locale={{ emptyText: <Empty description={search || flag ? t('没有符合条件的客户') : t('渠道或开放平台会话建立客户档案后，会出现在这里')} /> }}
        columns={[
          {
            title: t('客户'), key: 'customer', width: 300,
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
          {
            title: t('记忆总数'), key: 'total', dataIndex: ['memory_stats', 'total'], width: 110,
            sorter: true,
            sortOrder: sort === 'total' ? 'descend' : null,
            render: (_: unknown, row) => row.memory_stats?.total ?? row.item_count,
          },
          {
            title: t('分类条数'), key: 'kinds', width: 220,
            render: (_, row) => row.memory_stats
              ? (
                <span className="memory-type-counts">
                  <span>{t('画像')} {row.memory_stats.profile}</span>
                  <span>{t('偏好')} {row.memory_stats.preference}</span>
                  <span>{t('事件')} {row.memory_stats.event}</span>
                </span>
              )
              : '—',
          },
          {
            title: t('待确认'), key: 'pending', dataIndex: ['memory_stats', 'pending'], width: 110,
            sorter: true,
            sortOrder: sort === 'pending' ? 'descend' : null,
            render: (_: unknown, row) => {
              const pending = row.memory_stats?.pending ?? 0;
              return pending > 0
                ? <Button type="link" className="memory-pending-link" onClick={() => onOpen(row.id, 'pending')}>{t('{{v0}} 条待确认', { v0: pending })}</Button>
                : <Typography.Text type="secondary">0</Typography.Text>;
            },
          },
          {
            title: t('服务标记'), key: 'flags', width: 200,
            render: (_, row) => row.memory_stats?.flags?.length
              ? row.memory_stats.flags.map(key => <Tag key={key} color={flagKeyColors[key]}>{flagKeyLabels()[key] || key}</Tag>)
              : <Typography.Text type="secondary">—</Typography.Text>,
          },
          {
            title: t('最近更新'), key: 'last_updated_at', width: 180,
            render: (_, row) => {
              const value = row.memory_stats?.last_updated_at ?? row.last_active_at;
              return value ? time(value) : '—';
            },
          },
          { title: t('操作'), key: 'actions', width: 110, render: (_, row) => <Button size="small" onClick={() => onOpen(row.id)}>{t('查看')}</Button> },
        ]}
      />
    </>
  );
}
