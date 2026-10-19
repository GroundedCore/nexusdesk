import { useState } from 'react';
import {
  Alert,
  App,
  Button,
  Drawer,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd';
import type { TableColumnsType } from 'antd';
import {
  CheckOutlined,
  CloseOutlined,
  PlusOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  TeamOutlined,
} from '@ant-design/icons';
import { t } from '../../i18n';
import { patch, post } from '../../shared/api/client';
import { Alert as InlineAlert, time, useAccess, useResource } from '../../shared/components/ui';
import './access-control.css';

interface MemberIdentity {
  id: string;
  source: string;
  subject: string;
}

interface Member {
  id: string;
  name: string;
  role: string | null;
  enabled: boolean;
  revision: number;
  username: string | null;
  identities: MemberIdentity[];
  has_local_credential: boolean;
  is_emergency: boolean;
  last_login_at: string | null;
  last_active_at: string | null;
  disabled_reason: string | null;
  created_at: string;
}

interface MemberPage {
  items: Member[];
  total: number;
  page: number;
  page_size: number;
  counts: { total: number; enabled: number; disabled: number; pending: number };
}

interface Session {
  id: string;
  auth_source: string;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  ip: string | null;
  user_agent: string | null;
  current: boolean;
}

interface Role {
  name: string;
  permissions: string[];
}

interface Permission {
  code: string;
  description: string;
}

const ROLE_OPTIONS = [
  ['admin', '管理员'],
  ['operator', '客服操作员'],
  ['viewer', '只读查看'],
] as const;

const roleLabel = (role: string | null) =>
  role ? t(ROLE_OPTIONS.find(item => item[0] === role)?.[1] || role) : t('未授权');

const roleColor = (role: string | null) =>
  role === 'admin' ? 'purple' : role === 'operator' ? 'blue' : 'default';

const errorOf = (error: unknown) => (error instanceof Error ? error.message : String(error));

function AuthSourceTags({ member }: { member: Member }) {
  const sources = member.identities.map(identity => identity.source);
  if (member.has_local_credential) sources.push('local');
  if (!sources.length) return <Tag>{t('未认证')}</Tag>;
  return (
    <Space size={4} wrap>
      {sources.map(source => (
        <Tag key={source} color={source === 'local' ? 'default' : 'cyan'}>
          {source === 'local' ? t('本地账号') : t('企业 SSO')}
        </Tag>
      ))}
    </Space>
  );
}

function SessionTable({
  sessions,
  onRevoke,
}: {
  sessions: Session[];
  onRevoke?: (session: Session) => void;
}) {
  return (
    <Table<Session>
      rowKey="id"
      size="small"
      dataSource={sessions}
      pagination={false}
      scroll={{ x: 760 }}
      locale={{ emptyText: t('暂无活动会话') }}
      columns={[
        {
          title: t('登录方式'),
          dataIndex: 'auth_source',
          render: value => (
            <Tag color={value === 'local' ? 'default' : 'cyan'}>
              {value === 'local' ? t('账号登录') : t('企业 SSO')}
            </Tag>
          ),
        },
        {
          title: t('访问 IP'),
          dataIndex: 'ip',
          render: value => <span className="mono">{value || '—'}</span>,
        },
        {
          title: t('最近活动'),
          dataIndex: 'last_seen_at',
          render: value => time(value),
        },
        {
          title: t('过期时间'),
          dataIndex: 'expires_at',
          render: value => time(value),
        },
        {
          title: t('状态'),
          render: (_, row) =>
            row.current ? <Tag color="green">{t('当前会话')}</Tag> : null,
        },
        ...(onRevoke
          ? [
              {
                title: t('操作'),
                render: (_: unknown, row: Session) => (
                  <Button type="link" danger onClick={() => onRevoke(row)}>
                    {t('撤销')}
                  </Button>
                ),
              },
            ]
          : []),
      ]}
    />
  );
}

export function MembersPage() {
  const { admin } = useAccess();
  const { message, modal } = App.useApp();
  const [query, setQuery] = useState('');
  const [role, setRole] = useState<string>();
  const [status, setStatus] = useState<string>('all');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editing, setEditing] = useState<Member | null>(null);
  const [selected, setSelected] = useState<Member | null>(null);
  const [credential, setCredential] = useState<{ name: string; password: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [inviteForm] = Form.useForm();
  const [editForm] = Form.useForm();

  const params = [
    `page=${page}`,
    `page_size=${pageSize}`,
    `q=${encodeURIComponent(query)}`,
    role ? `role=${role}` : '',
    `status=${status}`,
  ].filter(Boolean).join('&');
  const members = useResource<MemberPage>(admin ? `/members?${params}` : null);
  const sessions = useResource<{ items: Session[] }>(
    admin && selected ? `/members/${selected.id}/sessions` : null,
  );

  if (!admin) return <Alert type="warning" showIcon title={t('仅管理员可管理成员')} />;

  const refresh = () => members.refresh();
  const toggle = async (member: Member) => {
    setError('');
    try {
      await post(`/members/${member.id}/enabled`, {
        enabled: !member.enabled,
        revision: member.revision,
      });
      message.success(member.enabled ? t('成员已停用') : t('成员已启用'));
      refresh();
    } catch (value) {
      setError(errorOf(value));
    }
  };

  const resetPassword = (member: Member) => {
    modal.confirm({
      title: t('重置成员密码？'),
      content: t('现有会话会立即失效，新密码只显示一次。'),
      okText: t('重置密码'),
      cancelText: t('取消'),
      onOk: async () => {
        try {
          const result = await post<{ temporary_password: string }>(
            `/members/${member.id}/password/reset`,
          );
          setCredential({ name: member.name, password: result.temporary_password });
          refresh();
        } catch (value) {
          setError(errorOf(value));
        }
      },
    });
  };

  const revoke = async (session: Session) => {
    if (!selected) return;
    try {
      await post(`/members/${selected.id}/sessions/revoke`, { session_id: session.id });
      message.success(t('会话已撤销'));
      sessions.refresh();
    } catch (value) {
      setError(errorOf(value));
    }
  };

  const columns: TableColumnsType<Member> = [
    {
      title: t('成员'),
      render: (_, member) => (
        <div className="access-member-cell">
          <strong>{member.name}</strong>
          <small>{member.username || member.id}</small>
        </div>
      ),
    },
    {
      title: t('角色'),
      dataIndex: 'role',
      render: value => <Tag color={roleColor(value)}>{roleLabel(value)}</Tag>,
    },
    {
      title: t('认证来源'),
      render: (_, member) => <AuthSourceTags member={member} />,
    },
    {
      title: t('状态'),
      render: (_, member) => (
        <Space size={4} wrap>
          <Tag color={member.enabled ? 'success' : 'default'}>
            {member.enabled ? t('已启用') : t('已停用')}
          </Tag>
          {member.is_emergency && <Tag color="red">{t('应急管理员')}</Tag>}
          {!member.role && <Tag color="orange">{t('待分配')}</Tag>}
        </Space>
      ),
    },
    {
      title: t('最近登录'),
      dataIndex: 'last_login_at',
      render: value => (value ? time(value) : '—'),
    },
    {
      title: t('操作'),
      width: 270,
      render: (_, member) => (
        <Space size={0} wrap>
          <Button
            type="link"
            onClick={() => {
              setEditing(member);
              editForm.setFieldsValue({
                name: member.name,
                role: member.role,
                enabled: member.enabled,
              });
              setEditOpen(true);
            }}
          >
            {t('编辑')}
          </Button>
          <Button type="link" onClick={() => setSelected(member)}>
            {t('会话')}
          </Button>
          <Button type="link" onClick={() => resetPassword(member)}>
            {t('重置密码')}
          </Button>
          <Button
            type="link"
            danger={member.enabled}
            disabled={member.is_emergency && member.enabled}
            onClick={() => void toggle(member)}
          >
            {member.enabled ? t('停用') : t('启用')}
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div className="access-control-page">
      <InlineAlert error={error || members.error} />
      {credential && (
        <Alert
          className="access-credential"
          type="info"
          showIcon
          closable
          onClose={() => setCredential(null)}
          title={t('临时密码已生成')}
          description={
            <Space direction="vertical" size={4}>
              <span>{t('请让 {{v0}} 登录后立即修改密码。该密码不会再次显示。', { v0: credential.name })}</span>
              <Typography.Text copyable code>{credential.password}</Typography.Text>
            </Space>
          }
        />
      )}
      <div className="access-toolbar">
        <Input.Search
          className="access-search"
          allowClear
          placeholder={t('搜索姓名、用户名、身份或 ID')}
          onSearch={value => {
            setQuery(value);
            setPage(1);
          }}
        />
        <Select
          allowClear
          className="access-filter"
          placeholder={t('全部角色')}
          value={role}
          onChange={value => {
            setRole(value);
            setPage(1);
          }}
          options={ROLE_OPTIONS.map(([value, label]) => ({ value, label: t(label) }))}
        />
        <Select
          className="access-filter"
          value={status}
          onChange={value => {
            setStatus(value);
            setPage(1);
          }}
          options={[
            { value: 'all', label: t('全部状态') },
            { value: 'enabled', label: t('已启用') },
            { value: 'disabled', label: t('已停用') },
            { value: 'pending', label: t('待分配') },
          ]}
        />
        <Button icon={<ReloadOutlined />} onClick={() => refresh()}>
          {t('刷新')}
        </Button>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setInviteOpen(true)}>
          {t('新增成员')}
        </Button>
      </div>
      <div className="access-counts">
        <span>{t('共 {{v0}} 名成员', { v0: members.data?.counts.total ?? '—' })}</span>
        <span className="ok">{t('启用 {{v0}}', { v0: members.data?.counts.enabled ?? '—' })}</span>
        <span className="danger">{t('停用 {{v0}}', { v0: members.data?.counts.disabled ?? '—' })}</span>
        <span className="warn">{t('待分配 {{v0}}', { v0: members.data?.counts.pending ?? '—' })}</span>
      </div>
      <Table<Member>
        rowKey="id"
        loading={members.loading}
        columns={columns}
        dataSource={members.data?.items || []}
        scroll={{ x: 1100 }}
        locale={{ emptyText: t('暂无成员') }}
        pagination={{
          current: page,
          pageSize,
          total: members.data?.total || 0,
          showSizeChanger: true,
          pageSizeOptions: [10, 20, 50],
          showTotal: total => t('共 {{v0}} 条', { v0: total }),
          onChange: (next, size) => {
            setPage(size === pageSize ? next : 1);
            setPageSize(size);
          },
        }}
      />

      <Modal
        open={inviteOpen}
        title={t('新增成员')}
        destroyOnHidden
        confirmLoading={busy}
        onCancel={() => setInviteOpen(false)}
        onOk={() => inviteForm.submit()}
      >
        <Form
          form={inviteForm}
          layout="vertical"
          initialValues={{ role: 'operator' }}
          onFinish={async values => {
            setBusy(true);
            setError('');
            try {
              const result = await post<{ temporary_password: string; member: Member }>(
                '/members/invite',
                values,
              );
              setCredential({ name: result.member.name, password: result.temporary_password });
              inviteForm.resetFields();
              setInviteOpen(false);
              refresh();
            } catch (value) {
              setError(errorOf(value));
            } finally {
              setBusy(false);
            }
          }}
        >
          <Alert
            type="info"
            showIcon
            title={t('创建本地成员账号')}
            description={t('适合未接入企业 SSO 的团队。首次登录后必须修改临时密码。')}
            style={{ marginBottom: 18 }}
          />
          <Form.Item name="name" label={t('姓名')} rules={[{ required: true }]}>
            <Input maxLength={100} />
          </Form.Item>
          <Form.Item
            name="username"
            label={t('登录账号')}
            rules={[{ required: true, min: 3, max: 64 }]}
            extra={t('仅支持小写字母、数字、点、下划线和短横线。')}
          >
            <Input autoComplete="off" maxLength={64} />
          </Form.Item>
          <Form.Item name="role" label={t('角色')} rules={[{ required: true }]}>
            <Select options={ROLE_OPTIONS.map(([value, label]) => ({ value, label: t(label) }))} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        open={editOpen}
        title={t('编辑成员')}
        destroyOnHidden
        confirmLoading={busy}
        onCancel={() => setEditOpen(false)}
        onOk={() => editForm.submit()}
      >
        <Form
          form={editForm}
          layout="vertical"
          onFinish={async values => {
            if (!editing) return;
            setBusy(true);
            setError('');
            try {
              await patch(`/members/${editing.id}`, {
                name: values.name,
                role: values.role || null,
                enabled: values.enabled,
                revision: editing.revision,
              });
              message.success(t('成员已更新'));
              setEditOpen(false);
              refresh();
            } catch (value) {
              setError(errorOf(value));
            } finally {
              setBusy(false);
            }
          }}
        >
          <Form.Item name="name" label={t('姓名')} rules={[{ required: true }]}>
            <Input maxLength={100} />
          </Form.Item>
          <Form.Item name="role" label={t('角色')}>
            <Select
              allowClear
              placeholder={t('未授权')}
              options={ROLE_OPTIONS.map(([value, label]) => ({ value, label: t(label) }))}
            />
          </Form.Item>
          <Form.Item name="enabled" label={t('启用')} valuePropName="checked">
            <Switch />
          </Form.Item>
          <Alert
            type="warning"
            showIcon
            title={t('修改角色或停用会立即撤销该成员现有会话。')}
          />
        </Form>
      </Modal>

      <Drawer
        title={selected ? t('{{v0}} 的活动会话', { v0: selected.name }) : t('活动会话')}
        width={860}
        open={!!selected}
        onClose={() => setSelected(null)}
        destroyOnHidden
      >
        <InlineAlert error={sessions.error} />
        <div className="access-drawer-actions">
          <Button
            danger
            onClick={async () => {
              if (!selected) return;
              try {
                await post(`/members/${selected.id}/sessions/revoke`, { all: true });
                message.success(t('全部会话已撤销'));
                sessions.refresh();
              } catch (value) {
                setError(errorOf(value));
              }
            }}
          >
            {t('撤销全部会话')}
          </Button>
        </div>
        <SessionTable sessions={sessions.data?.items || []} onRevoke={session => void revoke(session)} />
      </Drawer>
    </div>
  );
}

export function RolesPage() {
  const { admin } = useAccess();
  const roles = useResource<{ items: Role[] }>(admin ? '/roles' : null);
  const permissions = useResource<{ items: Permission[] }>(admin ? '/permissions' : null);
  if (!admin) return <Alert type="warning" showIcon title={t('仅管理员可查看角色与权限')} />;

  const byRole = (name: string) => roles.data?.items.find(item => item.name === name);
  const columns: TableColumnsType<Permission> = [
    {
      title: t('权限'),
      dataIndex: 'code',
      render: value => <Typography.Text code>{value}</Typography.Text>,
    },
    {
      title: t('管理员'),
      render: (_, row) => <PermissionMark value={!!byRole('admin')?.permissions.includes(row.code)} />,
    },
    {
      title: t('客服操作员'),
      render: (_, row) => <PermissionMark value={!!byRole('operator')?.permissions.includes(row.code)} />,
    },
    {
      title: t('只读查看'),
      render: (_, row) => <PermissionMark value={!!byRole('viewer')?.permissions.includes(row.code)} />,
    },
  ];

  return (
    <div className="access-control-page">
      <InlineAlert error={roles.error || permissions.error} />
      <Alert
        type="info"
        showIcon
        title={t('P0 固定角色权限矩阵')}
        description={t('当前阶段提供固定三角色。资源级授权、自定义角色和团队范围将在后续阶段开放。')}
        style={{ marginBottom: 20 }}
      />
      <Table<Permission>
        rowKey="code"
        loading={roles.loading || permissions.loading}
        dataSource={permissions.data?.items || []}
        pagination={{
          pageSize: 10,
          showSizeChanger: false,
          showTotal: total => t('共 {{v0}} 条', { v0: total }),
        }}
        columns={columns}
      />
    </div>
  );
}

function PermissionMark({ value }: { value: boolean }) {
  return value ? (
    <span className="permission-yes"><CheckOutlined /> {t('允许')}</span>
  ) : (
    <span className="permission-no"><CloseOutlined /> {t('不允许')}</span>
  );
}

export function SecurityPage() {
  const { message } = App.useApp();
  const sessions = useResource<{ items: Session[] }>('/sessions');
  const [error, setError] = useState('');

  const revoke = async (session: Session) => {
    try {
      await post('/sessions/revoke', { session_id: session.id });
      message.success(t('会话已撤销'));
      sessions.refresh();
    } catch (value) {
      setError(errorOf(value));
    }
  };

  return (
    <div className="access-control-page">
      <InlineAlert error={error || sessions.error} />
      <section className="access-security-card">
        <div className="access-security-heading">
          <div>
            <h3><SafetyCertificateOutlined /> {t('我的活动会话')}</h3>
            <p>{t('查看当前账号的登录设备，并在发现异常时立即撤销。')}</p>
          </div>
          <Button
            danger
            onClick={async () => {
              try {
                await post('/sessions/revoke', { all: true });
                message.success(t('全部会话已撤销'));
                sessions.refresh();
              } catch (value) {
                setError(errorOf(value));
              }
            }}
          >
            {t('撤销全部会话')}
          </Button>
        </div>
        <SessionTable sessions={sessions.data?.items || []} onRevoke={session => void revoke(session)} />
      </section>
    </div>
  );
}

export function AccessControlSummary() {
  return (
    <div className="access-control-summary">
      <TeamOutlined />
      <span>{t('成员与权限')}</span>
    </div>
  );
}
