import { t, errorText } from '../../i18n/index';
import { useState } from 'react';
import { Alert, Button, Card, Descriptions, Drawer, Empty, Form, Input, Tabs, Space, Table, Tag, Typography, App } from 'antd';
import { ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import { api, patch, type Ticket } from '../../shared/api/client';
import { time, useAccess, useResource } from '../../shared/components/ui';
const labels = (): Record<string, string> => ({ open: t("待处理"), in_progress: t("处理中"), waiting_customer: t("待客户补充"), resolved: t("已解决"), closed: t("已关闭") });
const colors: Record<string, string> = { open: 'default', in_progress: 'processing', waiting_customer: 'warning', resolved: 'success', closed: 'default' };
const transitions: Record<string, string[]> = { open: ['in_progress', 'closed'], in_progress: ['waiting_customer', 'resolved', 'closed'], waiting_customer: ['in_progress', 'closed'], resolved: ['in_progress', 'closed'], closed: [] };
const verbs = (): Record<string, string> => ({ in_progress: t("开始处理"), waiting_customer: t("等待客户补充"), resolved: t("解决工单"), closed: t("关闭工单") });
export function TicketsPage() {
    const rows = useResource<Ticket[]>('/tickets');
    const { operator } = useAccess();
    const { message } = App.useApp();
    const [selected, setSelected] = useState<Ticket | null>(null);
    const [query, setQuery] = useState('');
    const [filter, setFilter] = useState<string>();
    const [page, setPage] = useState(1);
    const [note, setNote] = useState('');
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const filtered = (rows.data || []).filter(t => (!filter || t.status === filter) && `${t.ticket_no || t.id} ${t.title}`.toLowerCase().includes(query.toLowerCase()));
    function open(ticket: Ticket) { setSelected(ticket); setNote(''); setError(''); }
    async function reloadSelected() {
        if (!selected || busy)
            return;
        setBusy(true);
        try {
            const latest = await api<Ticket[]>('/tickets');
            const found = latest.find(t => t.id === selected.id);
            if (found) {
                setSelected(found);
                setError('');
                rows.refresh();
            }
            else
                setError(t("当前工单已不在最近记录中，请重新查询"));
        }
        catch (e) {
            setError(e instanceof Error ? e.message : t("刷新失败"));
        }
        finally {
            setBusy(false);
        }
    }
    async function update(status: string) {
        if (!selected || busy)
            return;
        if ((status === 'closed' || status === 'resolved' || status === selected.status) && !note.trim()) {
            setError(t("请填写处理说明后再提交"));
            return;
        }
        setBusy(true);
        setError('');
        try {
            const saved = await patch<Ticket>(`/tickets/${selected.id}`, { status, note: note.trim(), revision: selected.revision });
            setSelected(saved);
            setNote('');
            rows.refresh();
            void message.success(t("工单已更新"));
        }
        catch (e) {
            setError(e instanceof Error ? e.message : t("更新失败，请重试"));
        }
        finally {
            setBusy(false);
        }
    }
    return <>
    {rows.error && <Alert type="error" showIcon title={t("工单加载失败")} description={errorText(rows.error)} action={<Button onClick={rows.refresh}>{t("重试")}</Button>}/>}
    <Card className="ticket-workspace">
      <Tabs activeKey={filter || 'all'} onChange={key => { setFilter(key === 'all' ? undefined : key); setPage(1); }} items={[{ key: 'all', label: t("全部工单") }, ...Object.entries(labels()).map(([key, label]) => ({ key, label }))]}/>
      <div className="ticket-toolbar"><Input className="ticket-search" prefix={<SearchOutlined />} aria-label={t("搜索工单")} placeholder={t("搜索工单编号或标题")} allowClear value={query} onChange={e => { setQuery(e.target.value); setPage(1); }}/><Typography.Text type="secondary">{t("仅筛选最近 100 条工单")}</Typography.Text><Button icon={<ReloadOutlined />} onClick={rows.refresh} loading={rows.loading}>{t("刷新")}</Button></div>
      <Table<Ticket> rowKey="id" dataSource={filtered} loading={rows.loading} scroll={{ x: 850 }} pagination={{ current: page, onChange: setPage, pageSize: 10, showSizeChanger: false, showTotal: total => t("本次加载 {{v0}} 条", { v0: total }) }} locale={{ emptyText: <Empty description={query || filter ? t("没有符合条件的工单") : t("在会话中拟定并确认后，工单会出现在这里")}/> }} columns={[
            { title: t("工单"), key: 'ticket', width: 320, render: (_, t) => <><Button type="link" onClick={() => open(t)}>{t.title}</Button><div><Typography.Text type="secondary" code>{t.ticket_no || t.id}</Typography.Text></div></> },
            { title: t("状态"), dataIndex: 'status', render: (s: string) => <Tag color={colors[s]}>{labels()[s] || s}</Tag> },
            { title: t("优先级"), dataIndex: 'priority', render: (p?: number) => ['—', t("低"), t("普通"), t("高"), t("紧急")][p || 0] },
            { title: t("负责人"), dataIndex: 'assignee_id', render: (id?: string | null) => id ? <Typography.Text code>{id.slice(0, 8)}</Typography.Text> : t("未分配") },
            { title: t("更新时间"), dataIndex: 'updated_at', render: (value?: string) => value ? time(value) : '—' },
        ]}/>
    </Card>
    <Drawer className="ticket-drawer" title={t("工单详情")} open={Boolean(selected)} size={600} onClose={() => { if (!busy)
        setSelected(null); }} maskClosable={!busy} keyboard={!busy} footer={selected && operator && selected.status !== 'closed' ? <div className="ticket-actions"><Button disabled={busy} onClick={() => void update(selected.status)}>{t("保存备注")}</Button>{transitions[selected.status]?.map(status => <Button key={status} type={status === (selected.status === 'in_progress' ? 'resolved' : 'in_progress') ? 'primary' : 'default'} danger={status === 'closed'} disabled={busy} onClick={() => void update(status)}>{verbs()[status]}</Button>)}</div> : null}>
      {selected && <Space orientation="vertical" size="large" style={{ width: '100%' }}>
        <div><Typography.Title level={4}>{selected.title}</Typography.Title><Tag color={colors[selected.status]}>{labels()[selected.status]}</Tag></div>
        <Descriptions column={1} size="small" items={[{ key: 'id', label: t("工单编号"), children: selected.ticket_no || selected.id }, { key: 'updated', label: t("最近更新"), children: selected.updated_at ? time(selected.updated_at) : '—' }]}/>
        <section className="ticket-detail-section"><h3>{t("问题描述")}</h3><p className="ticket-description">{selected.description}</p></section>
        <section className="ticket-detail-section"><h3>{t("最近处理信息")}</h3><p className="ticket-description">{selected.note || t("暂无处理备注")}</p>{selected.resolution && <><h4>{t("解决说明")}</h4><p className="ticket-description">{selected.resolution}</p></>}</section>
        {error && <Alert type="error" showIcon title={t("提交未完成")} description={errorText(error)} action={<Button loading={busy} onClick={() => void reloadSelected()}>{t("加载最新版本")}</Button>}/>}
        {!operator ? <Alert type="info" title={t("当前为只读角色")}/> : selected.status === 'closed' ? <Alert type="success" title={t("工单已关闭，详情只读")}/> : <Form layout="vertical"><Form.Item label={t("处理说明")} required><Input.TextArea aria-label={t("处理说明")} rows={5} maxLength={8000} showCount value={note} onChange={e => setNote(e.target.value)} disabled={busy} placeholder={t("记录本次处理；解决或关闭时必须填写说明")}/></Form.Item></Form>}
      </Space>}
    </Drawer>
  </>;
}
