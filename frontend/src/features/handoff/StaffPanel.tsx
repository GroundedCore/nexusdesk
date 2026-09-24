import { t } from '../../i18n/index';
import { useState } from 'react';
import { patch, post } from '../../shared/api/client';
import { Alert, Field, Panel, useAccess, useAction, useResource } from '../../shared/components/ui';
interface Staff {
    id: string;
    name: string;
    role: string;
    enabled: boolean;
    token?: string;
}
export function StaffPanel() {
    const { admin } = useAccess();
    const rows = useResource<Staff[]>(admin ? '/staff' : null);
    const action = useAction();
    const [name, setName] = useState('');
    const [role, setRole] = useState('operator');
    const [credential, setCredential] = useState('');
    if (!admin)
        return null;
    return <Panel title={t("员工访问凭据")}><Alert error={rows.error || action.error} notice={action.notice}/><p className="hint">{t("每名员工使用独立凭据，操作记录关联固定主体 ID。令牌仅在创建时显示；停用立即阻止新请求。")}</p>
    <form onSubmit={e => { e.preventDefault(); void action.run(async () => { const staff = await post<Staff>('/staff', { name, role }); setCredential(staff.token || ''); rows.refresh(); }, t("员工凭据已创建，请安全保存")); }}><fieldset disabled={action.busy}><Field label={t("员工姓名")}><input value={name} onChange={e => setName(e.target.value)} required/></Field><Field label={t("员工角色")}><select value={role} onChange={e => setRole(e.target.value)}><option value="operator">{t("客服操作员")}</option><option value="viewer">{t("只读查看")}</option></select></Field><button>{t("创建员工凭据")}</button></fieldset></form>
    {credential && <code className="secret">{credential}</code>}{rows.data?.map(row => <div className="tool-item" key={row.id}><strong>{row.name} · {row.role}</strong><p className="mono small">staff:{row.id}</p><button className="secondary" disabled={action.busy} onClick={() => void action.run(async () => { await patch(`/staff/${row.id}`, { enabled: !row.enabled }); rows.refresh(); })}>{row.enabled ? t("停用") : t("启用")}</button></div>)}
  </Panel>;
}
