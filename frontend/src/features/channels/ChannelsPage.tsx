import { t } from '../../i18n/index';
import { useState } from 'react';
import { patch, post, type Agent, type Channel } from '../../shared/api/client';
import { Alert, Empty, Field, Panel, useAccess, useAction, useResource } from '../../shared/components/ui';
export function ChannelsPage() {
    const rows = useResource<Channel[]>('/channels');
    const agents = useResource<Agent[]>('/agents');
    const action = useAction();
    const { admin } = useAccess();
    const [signing, setSigning] = useState('');
    const [name, setName] = useState('');
    const [agent, setAgent] = useState('');
    const [created, setCreated] = useState<Channel | null>(null);
    return <div className="channel-workspace"><Alert error={rows.error || agents.error || action.error} notice={action.notice}/><div className="split"><Panel title={t("接入渠道")}>{rows.data?.map(c => <article className="tool-item" key={c.id}><div className="row"><strong>{c.name}</strong><span className="badge">{c.enabled ? t("已启用") : t("已停用")}</span></div><p className="mono small">{c.id}</p><p>Agent：{agents.data?.find(a => a.id === c.agent_id)?.name || c.agent_id}</p><button className="secondary" disabled={!admin || action.busy} onClick={() => void action.run(async () => { await patch(`/channels/${c.id}`, { enabled: !c.enabled }); rows.refresh(); })}>{c.enabled ? t("停用") : t("启用")}</button><button className="secondary" disabled={!admin || action.busy} onClick={() => void action.run(async () => setCreated(await post<Channel>(`/channels/${c.id}/rotate-token`)), t("已轮换，旧令牌立即失效"))}>{t("轮换渠道令牌")}</button></article>)}{rows.data?.length === 0 && <Empty>{t("创建标准 Webhook 渠道接入业务系统")}</Empty>}</Panel><div className="stack"><Panel title={t("创建渠道")}><form onSubmit={e => { e.preventDefault(); void action.run(async () => { setCreated(await post<Channel>('/channels', { name, agent_id: agent, signing_secret_ref: signing || null })); rows.refresh(); setName(''); }, t("渠道已创建，请保存本次显示的令牌")); }}><fieldset disabled={!admin || action.busy}><Field label={t("渠道名称")}><input required value={name} onChange={e => setName(e.target.value)}/></Field><Field label={t("已发布 Agent")}><select required value={agent} onChange={e => setAgent(e.target.value)}><option value="">{t("请选择")}</option>{agents.data?.filter(a => a.published_version).map(a => <option key={a.id} value={a.id}>{a.name}</option>)}</select></Field><Field label={t("签名密钥环境变量引用（可选）")}><input value={signing} onChange={e => setSigning(e.target.value)} placeholder="AGENT_CHANNEL_SECRET_WEBHOOK"/></Field><button>{t("创建渠道")}</button></fieldset></form></Panel>
    {created && <Panel title={t("本次创建的访问令牌")}><p className="hint">{t("令牌仅显示这一次。服务端只保存摘要。")}</p><code className="secret">{created.token}</code><p className="small mono">POST /api/v1/ingress/channels/{created.id}/messages</p></Panel>}
    <Panel title={t("接入协议")}><p>{t("请求头：")}<code>X-Channel-Token</code></p><pre>{JSON.stringify({ message_id: t("唯一消息编号"), session_id: t("稳定客户会话标识"), text: t("你好") }, null, 2)}</pre><p className="hint">{t("重发同一消息编号会返回原运行；不同内容复用编号会被拒绝。通过 replies 接口按 seq 拉取 Agent 和人工回复。渠道凭据应由可信服务端持有。")}</p></Panel>
  </div></div></div>;
}
