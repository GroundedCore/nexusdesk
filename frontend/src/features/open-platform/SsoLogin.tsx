import {useEffect,useState} from 'react';
import {Alert,Button,Space} from 'antd';
import {t} from '../../i18n';
import {setToken,token} from '../../shared/api/client';

export interface LoginProvider {id:string;name:string;kind:string;platform_origin:string}
export interface LoginSession {access_token:string;expires_in:number;external_user_id:string;user:{id:string;name:string;role:string|null}}
export function ssoLogin(provider:LoginProvider,appId?:string,parentOrigin?:string):Promise<LoginSession> {
  if(provider.platform_origin!==location.origin) return Promise.reject(new Error(t('登录服务地址与当前平台地址不一致')));
  const channel=crypto.randomUUID().replaceAll('-','')+crypto.randomUUID().replaceAll('-','');
  const params=new URLSearchParams({channel,...(appId?{app_id:appId,parent_origin:parentOrigin||''}:{})});
  const popup=window.open(`/api/v1/sso/start/${provider.id}?${params}`,'nexusdesk-sso-'+channel,'popup,width=600,height=760');
  if(!popup) return Promise.reject(new Error(t('请允许浏览器打开登录窗口')));
  return new Promise((resolve,reject)=>{
    const cleanup=()=>{clearInterval(timer);window.removeEventListener('message',receive);popup.close();};
    const receive=(event:MessageEvent)=>{
      if(event.origin!==location.origin||event.source!==popup||event.data?.type!=='nexusdesk.sso'||event.data.channel!==channel)return;
      cleanup();
      if(event.data.error)reject(new Error(event.data.error==='workbench_access_pending'?t('身份已登记，请联系管理员分配工作台角色'):event.data.error));
      else if(typeof event.data.session?.access_token==='string')resolve(event.data.session);
      else reject(new Error(t('登录响应无效')));
    };
    window.addEventListener('message',receive);
    const deadline=Date.now()+300000;
    const timer=setInterval(()=>{if(popup.closed||Date.now()>deadline){cleanup();reject(new Error(t('登录已取消或超时')));}},500);
  });
}

export function WorkbenchSso({onChange}:{onChange:()=>void}) {
  const [providers,setProviders]=useState<LoginProvider[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState('');
  useEffect(()=>{fetch('/api/v1/sso/providers').then(r=>r.ok?r.json():[]).then(setProviders).catch(()=>{});},[]);
  return <div style={{margin:'20px 0'}}><h3>{t('企业单点登录')}</h3>{error&&<Alert type="error" title={error} showIcon/>}
    <Space wrap>{providers.map(p=><Button key={p.id} loading={busy===p.id} disabled={!!busy} onClick={async()=>{setBusy(p.id);setError('');try{const session=await ssoLogin(p);setToken(session.access_token);onChange();}catch(e){setError(String((e as Error).message));}finally{setBusy('');}}}>{p.name}</Button>)}
    {token().startsWith('ssow_')&&<Button onClick={async()=>{try{await fetch('/api/v1/sso/logout',{method:'POST',headers:{Authorization:'Bearer '+token()}});setToken('');onChange();}catch(e){setError(String(e));}}}>{t('退出企业登录')}</Button>}</Space>
    {!providers.length&&<p className="op-muted">{t('管理员可在开放平台中配置企业登录。')}</p>}
  </div>;
}
