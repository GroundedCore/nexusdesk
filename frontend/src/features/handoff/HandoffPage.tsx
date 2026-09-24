import { t } from '../../i18n/index';
import { useState } from 'react';
import { post, type Handoff } from '../../shared/api/client';
import { Alert, Badge, Empty, Field, Panel, useAccess, useAction, useResource } from '../../shared/components/ui';
export function HandoffPage() {
    const rows = useResource<Handoff[]>('/handoffs', 3000);
    const action = useAction();
    const { operator, admin } = useAccess();
    const [assignee, setAssignee] = useState('');
    return <Panel title={t("人工服务队列")}><Alert error={rows.error || action.error} notice={action.notice}/><Field label={t("管理员分配主体（留空为本人）")}><input disabled={!admin} value={assignee} onChange={e => setAssignee(e.target.value)} maxLength={100}/></Field><div className="cards">{rows.data?.map(h => <article className="service-card" key={h.id}><div className="row"><strong>{h.external_id.slice(0, 32)}</strong><Badge value={h.status}/></div><p>{h.reason}</p><details><summary>{t("交接摘要")}</summary><pre>{h.summary || t("暂无历史消息")}</pre></details><p className="hint">{t("处理人：")}{h.assignee || t("未分配")}</p><div className="actions">{h.status === 'waiting' && <button disabled={!operator || action.busy} onClick={() => void action.run(async () => { await post(`/handoffs/${h.id}/transition`, { operation: 'claim', assignee: assignee || null, revision: h.revision }); rows.refresh(); }, t("已接管，请到会话工作台回复"))}>{t("接管")}</button>}{h.status === 'active' && <><button disabled={!operator || action.busy} onClick={() => void action.run(async () => { await post(`/handoffs/${h.id}/transition`, { operation: 'resume', revision: h.revision }); rows.refresh(); }, t("已恢复自动服务"))}>{t("结束并恢复 Agent")}</button><button className="secondary" disabled={!operator || action.busy} onClick={() => void action.run(async () => { await post(`/handoffs/${h.id}/transition`, { operation: 'close', revision: h.revision }); rows.refresh(); }, t("会话已结束"))}>{t("关闭会话")}</button></>}</div></article>)}</div>{rows.data?.length === 0 && <Empty>{t("当前没有人工转接记录")}</Empty>}</Panel>;
}
