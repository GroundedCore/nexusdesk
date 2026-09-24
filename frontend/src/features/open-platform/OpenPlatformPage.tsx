import {consoleTheme} from '../../styles/theme';
import { useState } from 'react';
import { Alert, App, Button, Card, Checkbox, ConfigProvider, Empty, Form, Input, InputNumber, Modal, Pagination, Select, Space, Spin, Switch, Table, Tabs, Tag, Typography } from 'antd';
import { ApiOutlined, ArrowLeftOutlined, ArrowRightOutlined, CopyOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { t, dateTime } from '../../i18n';
import { post, put } from '../../shared/api/client';
import { useAccess, useResource } from '../../shared/components/ui';
import { IntegrationDocs, Debugger } from './Integration';
import {Integrations,WebhookPanel,appBody} from './Enterprise';
import './open-platform.css';
import {IdentityPage,EmbedSettings} from './Identity';
import { ChannelsPage } from '../channels/ChannelsPage';

export const ROOT='/open-platform/applications';
export interface Application { id:string;name:string;description:string;enabled:boolean;agent_ids:string[];rpm:number;max_concurrency:number;revision:number;created_at:string;agent_versions?:Record<string,number>;knowledge_base_ids?:string[] }
interface Agent { id:string;name:string;published_version:number }
interface Key { id:string;prefix:string;active:boolean;created_at:string;expires_at:string|null;revoked_at:string|null }
interface Log { id:string;route:string;method:string;status:number;duration_ms:number;error_code:string|null;run_id:string|null;run_status:string|null;run_error:string|null;run_duration_ms:number|null;created_at:string }
const errorOf=(e:unknown)=>e instanceof Error?e.message:String(e);

function Editor({value,agents,close,saved}:{value:Application|null;agents:Agent[];close:()=>void;saved:(app:Application)=>void}) {
  const [form]=Form.useForm();const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  return <Modal open title={value?t('编辑应用'):t('创建应用')} onCancel={close} onOk={()=>form.submit()} confirmLoading={busy} width={640} destroyOnHidden>
    {error&&<Alert type="error" title={error} showIcon/>}<Form form={form} layout="vertical" initialValues={value||{enabled:true,agent_ids:[],rpm:120,max_concurrency:5}} onFinish={async values=>{setBusy(true);setError('');try{const result=value?await put<Application>(`${ROOT}/${value.id}`,{...appBody(value),...values,agent_versions:Object.fromEntries(Object.entries(value.agent_versions||{}).filter(([id])=>values.agent_ids.includes(id)))}):await post<Application>(ROOT,values);saved(result);}catch(e){setError(errorOf(e));}finally{setBusy(false);}}}>
      <Form.Item name="name" label={t('应用名称')} rules={[{required:true,whitespace:true}]}><Input maxLength={100} showCount/></Form.Item>
      <Form.Item name="description" label={t('描述')}><Input.TextArea rows={3} maxLength={1000}/></Form.Item>
      <Form.Item name="agent_ids" label={t('授权 Agent')} extra={t('仅可授权已发布的 Agent，可在集成配置中固定版本。')}><Select mode="multiple" showSearch optionFilterProp="label" options={agents.map(a=>({value:a.id,label:`${a.name} · v${a.published_version}`}))}/></Form.Item>
      <div className="op-form-row"><Form.Item name="rpm" label={t('每分钟请求上限')} rules={[{required:true}]}><InputNumber min={1} max={10000} precision={0}/></Form.Item><Form.Item name="max_concurrency" label={t('最大并发运行数')} rules={[{required:true}]}><InputNumber min={1} max={100} precision={0}/></Form.Item><Form.Item name="enabled" label={t('启用应用')} valuePropName="checked"><Switch/></Form.Item></div>
    </Form></Modal>;
}

function Keys({app}:{app:Application}) {
  const {admin}=useAccess();const {modal,message}=App.useApp();const keys=useResource<Key[]>(`${ROOT}/${app.id}/keys`);
  const [issue,setIssue]=useState<{replace?:string}|null>(null);const [days,setDays]=useState<number|null>(90);const [grace,setGrace]=useState<number|null>(60);const [secret,setSecret]=useState('');const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  return <Card title={t('应用密钥')} extra={<Button type="primary" disabled={!admin} icon={<PlusOutlined/>} onClick={()=>{setError('');setIssue({});}}>{t('创建密钥')}</Button>}>
    <Alert showIcon type="info" title={t('密钥仅在创建时展示一次，请妥善保存。')} description={t('密钥只授权当前应用，不具备后台管理权限。轮换时可设置旧密钥的过渡有效期。')}/>
    {(error||keys.error)&&<Alert type="error" title={error||keys.error}/>}
    <Table<Key> rowKey="id" dataSource={keys.data||[]} loading={keys.loading} pagination={{pageSize:10}} scroll={{x:760}} columns={[
      {title:t('密钥'),dataIndex:'prefix',render:v=><code>{v}</code>},{title:t('状态'),render:(_,k)=><Tag color={k.active?'success':'default'}>{k.active?t('有效'):k.revoked_at?t('已撤销'):t('已过期')}</Tag>},
      {title:t('创建时间'),dataIndex:'created_at',render:dateTime},{title:t('过期时间'),dataIndex:'expires_at',render:v=>v?dateTime(v):t('永不过期')},
      {title:t('操作'),render:(_,k)=><Space><Button type="link" disabled={!admin||!k.active} onClick={()=>{setError('');setIssue({replace:k.id});}}>{t('轮换')}</Button><Button danger type="link" disabled={!admin||!k.active} onClick={()=>modal.confirm({title:t('撤销此密钥？'),content:t('使用此密钥的后续请求将被拒绝。'),onOk:async()=>{await post(`${ROOT}/${app.id}/keys/${k.id}/revoke`);keys.refresh();}})}>{t('撤销')}</Button></Space>}
    ]}/>
    <Modal open={!!issue} title={issue?.replace?t('轮换密钥'):t('创建密钥')} onCancel={()=>setIssue(null)} confirmLoading={busy} onOk={async()=>{setBusy(true);setError('');try{const result=await post<{key:string}>(`${ROOT}/${app.id}/keys`,{expires_at:days?new Date(Date.now()+days*86400000).toISOString():null,replace_id:issue?.replace||null,grace_minutes:issue?.replace?grace||0:0});setSecret(result.key);setIssue(null);keys.refresh();}catch(e){setError(errorOf(e));}finally{setBusy(false);}}}>
      {error&&<Alert type="error" title={error}/>}<Form layout="vertical"><Form.Item label={t('有效天数（留空为永不过期）')}><InputNumber value={days} onChange={setDays} min={1} max={3650} precision={0}/></Form.Item>{issue?.replace&&<Form.Item label={t('旧密钥过渡期（分钟）')}><InputNumber value={grace} onChange={setGrace} min={0} max={1440} precision={0}/></Form.Item>}</Form>
    </Modal>
    <Modal destroyOnHidden open={!!secret} title={t('请保存新密钥')} onCancel={()=>setSecret('')} footer={<Button type="primary" onClick={()=>setSecret('')}>{t('我已保存')}</Button>}>
      <Alert showIcon type="warning" title={t('关闭后无法再次查看完整密钥。')}/><Typography.Paragraph className="op-secret" code>{secret}</Typography.Paragraph><Button icon={<CopyOutlined/>} onClick={async()=>{try{await navigator.clipboard.writeText(secret);message.success(t('已复制'));}catch{message.error(t('复制失败'));}}}>{t('复制密钥')}</Button>
    </Modal>
  </Card>;
}

function Logs({app}:{app:Application}) {
  const [page,setPage]=useState(1);const [errors,setErrors]=useState(false);const [search,setSearch]=useState('');const [id,setId]=useState('');
  const rows=useResource<{items:Log[];total:number}>(`${ROOT}/${app.id}/logs?page=${page}&errors_only=${errors}${id?'&request_id='+encodeURIComponent(id):''}`);
  return <Card title={t('调用日志')} extra={<Button icon={<ReloadOutlined/>} onClick={rows.refresh}>{t('刷新')}</Button>}>
    <div className="op-toolbar"><Input.Search value={search} onChange={e=>setSearch(e.target.value)} placeholder="Request ID" onSearch={()=>{setPage(1);setId(search.trim());}} allowClear/><Checkbox checked={errors} onChange={e=>{setErrors(e.target.checked);setPage(1);}}>{t('只看失败请求')}</Checkbox></div>
    <p className="op-muted">{t('响应耗时统计到响应头生成；SSE 的完整执行耗时请查看运行耗时。日志不记录密钥和请求正文。')}</p>
    {rows.error&&<Alert type="error" title={rows.error}/>}<Table<Log> rowKey="id" dataSource={rows.data?.items||[]} loading={rows.loading} pagination={false} scroll={{x:1200}} expandable={{expandedRowRender:r=><div className="op-log-detail"><Typography.Text copyable>Request ID: {r.id}</Typography.Text><Typography.Text copyable={!!r.run_id}>Run ID: {r.run_id||'—'}</Typography.Text><span>{r.error_code||r.run_error||'—'}</span></div>}} columns={[
      {title:t('时间'),dataIndex:'created_at',render:dateTime,width:180},{title:t('接口'),render:(_,r)=><code>{r.method} {r.route}</code>},
      {title:'HTTP',dataIndex:'status',render:v=><Tag color={v>=400?'error':'success'}>{v}</Tag>,width:80},{title:t('响应耗时'),dataIndex:'duration_ms',render:v=>`${v} ms`,width:110},{title:t('运行状态'),dataIndex:'run_status',render:v=>v||'—',width:110},{title:t('运行耗时'),dataIndex:'run_duration_ms',render:v=>v==null?'—':`${Math.round(v)} ms`,width:110}
    ]}/><Pagination current={page} pageSize={20} total={rows.data?.total||0} onChange={setPage} showSizeChanger={false}/>
  </Card>;
}

function ApplicationWorkspace({route,onNavigate}:{route:string;onNavigate:(path:string)=>void}) {
  const {admin}=useAccess();const appId=route.split('/')[2]||'';const tab=route.split('/')[3]||'settings';
  const [page,setPage]=useState(1);const [status,setStatus]=useState('all');const [q,setQ]=useState('');const [editing,setEditing]=useState<{value:Application|null}|null>(null);
  const catalog=useResource<{items:Application[];total:number}>(!appId?`${ROOT}?page=${page}&q=${encodeURIComponent(q)}${status!=='all'?'&enabled='+(status==='enabled'): ""}`:null);
  const detail=useResource<Application>(appId?`${ROOT}/${appId}`:null);const agents=useResource<Agent[]>('/open-platform/agents');
  const app=detail.data;const error=catalog.error||detail.error||agents.error;
  return <ConfigProvider theme={consoleTheme}><div className="op-workspace">
    {error&&<Alert type="error" showIcon title={error} action={<Button onClick={()=>{catalog.refresh();detail.refresh();agents.refresh();}}>{t('重试')}</Button>}/>}
    {!appId?<div className="catalog-layout"><aside className="catalog-filter"><h3>{t('企业应用')}</h3><p>{t('状态')}</p><Space wrap size={6}>{[{value:'all',label:t('全部')},{value:'enabled',label:t('已启用')},{value:'disabled',label:t('已停用')}].map(item=><Button size="small" key={item.value} type={status===item.value?'primary':'default'} onClick={()=>{setStatus(item.value);setPage(1);}}>{item.label}</Button>)}</Space><div className="catalog-help"><p>{t('将 Agent 接入企业业务系统，独立管理授权、密钥与调用记录。')}</p><Button type="link" disabled={!admin} onClick={()=>onNavigate('/open-platform/identity')}>{t('企业身份与 SSO')} →</Button></div></aside><div className="catalog-content">
      <div className="catalog-toolbar"><Input.Search aria-label={t('搜索应用名称')} placeholder={t('搜索应用名称')} allowClear onSearch={v=>{setQ(v);setPage(1);}}/><Button aria-label={t('创建应用')} type="primary" icon={<PlusOutlined aria-hidden/>} disabled={!admin} onClick={()=>setEditing({value:null})}>{t('创建应用')}</Button></div>
      <p className="op-muted">{t('共 {{count}} 个应用',{count:catalog.data?.total||0})}</p>
      {catalog.loading?<Spin/>:catalog.data?.items.length?<div className="op-grid">{catalog.data.items.map(a=><Card key={a.id} className="op-app-card"><div className="op-card-top"><div className="op-icon"><ApiOutlined/></div><div><h3>{a.name}</h3></div><span className={'catalog-state'+(a.enabled?' enabled':'')}>{a.enabled?t('已启用'):t('已停用')}</span></div><p className="op-description">{a.description||t('暂无描述')}</p><div className="op-card-footer"><span>{t('授权 {{count}} 个 Agent',{count:a.agent_ids.length})}</span><Button type="link" icon={<ArrowRightOutlined/>} onClick={()=>onNavigate(`/open-platform/${a.id}/settings`)}>{t('管理')}</Button></div></Card>)}</div>:<Empty description={t('创建第一个应用，开始企业集成。')}/>}
      <Pagination current={page} pageSize={20} total={catalog.data?.total||0} onChange={setPage} showSizeChanger={false}/></div></div>:detail.loading?<Spin/>:app?<>
      <div className="op-heading"><Space><Button icon={<ArrowLeftOutlined/>} aria-label={t('返回应用列表')} onClick={()=>onNavigate('/open-platform')}/><div><h2>{app.name}</h2><Typography.Text type="secondary" copyable>App ID: {app.id}</Typography.Text></div></Space><Tag color={app.enabled?'success':'default'}>{app.enabled?t('已启用'):t('已停用')}</Tag></div>
      <Tabs destroyOnHidden activeKey={tab} onChange={key=>onNavigate(`/open-platform/${app.id}/${key}`)} items={[{key:'settings',label:t('应用配置')},{key:'integrations',label:t('集成配置')},{key:'embed',label:t('嵌入聊天')},{key:'webhook',label:t('Webhook 回调')},{key:'keys',label:t('应用密钥')},{key:'docs',label:t('接入文档')},{key:'debug',label:t('在线调试')},{key:'logs',label:t('调用日志')}]} />
      {tab==='settings'&&<Card title={t('应用配置')} extra={<Button disabled={!admin} onClick={()=>setEditing({value:app})}>{t('编辑应用')}</Button>}><p>{app.description||t('暂无描述')}</p><div className="op-stats"><div><strong>{app.agent_ids.length}</strong><span>{t('授权 Agent')}</span></div><div><strong>{app.rpm}</strong><span>{t('每分钟请求上限')}</span></div><div><strong>{app.max_concurrency}</strong><span>{t('最大并发运行数')}</span></div></div><Space wrap>{app.agent_ids.map(id=><Tag key={id}>{agents.data?.find(a=>a.id===id)?.name||id}</Tag>)}</Space><p className="op-muted">{t('仅可授权已发布的 Agent，可在集成配置中固定版本。')}</p></Card>}
      {tab==='embed'&&<EmbedSettings app={app}/>}{tab==='integrations'&&<Integrations key={`${app.id}:${app.revision}`} app={app} agents={agents.data||[]} saved={detail.refresh}/>}{tab==='webhook'&&<WebhookPanel key={app.id} app={app}/>}{tab==='keys'&&<Keys key={app.id} app={app}/>}{tab==='docs'&&<IntegrationDocs app={app}/ >}{tab==='debug'&&<Debugger key={app.id} app={app} agents={agents.data||[]}/ >}{tab==='logs'&&<Logs key={app.id} app={app}/>}
    </>:null}
    {editing&&<Editor value={editing.value} agents={agents.data||[]} close={()=>setEditing(null)} saved={a=>{setEditing(null);catalog.refresh();detail.refresh();onNavigate(`/open-platform/${a.id}/settings`);}}/>}
  </div></ConfigProvider>;
}

export function OpenPlatformPage(props:{route:string;onNavigate:(path:string)=>void}) {
  const identity = props.route === '/open-platform/identity';
  const channels = props.route === '/open-platform/channels';
  return <div className="op-workspace">
    <Tabs aria-label={t('开放平台接入方式')} activeKey={identity ? 'identity' : channels ? 'channels' : 'applications'} onChange={key=>props.onNavigate(key === 'identity' ? '/open-platform/identity' : key === 'channels' ? '/open-platform/channels' : '/open-platform')} items={[
      {key:'applications',label:t('应用接入')},
      {key:'identity',label:t('企业身份与 SSO')},
      {key:'channels',label:t('通用消息接入')},
    ]}/>
    {identity ? <IdentityPage back={()=>props.onNavigate('/open-platform')}/> : channels ? <>
      <div className="op-heading"><div><h2>{t('通用消息接入')}</h2><p>{t('接收客服消息，映射客户会话，并拉取 Agent 与人工回复。')}</p></div></div>
      <Alert showIcon type="info" title={t('如何选择接入方式')} description={t('自研网站、App 和企业系统调用 Agent，请使用“应用接入”。需要接入客服消息并衔接人工回复时，可使用通用消息协议。企微、飞书等原生渠道适配尚未提供。')}/>
      <ChannelsPage/>
    </> : <ApplicationWorkspace {...props}/>}
  </div>;
}
