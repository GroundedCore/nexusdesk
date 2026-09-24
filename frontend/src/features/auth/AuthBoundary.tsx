import {useEffect, useState, type ReactNode} from 'react';
import {Alert, Button, Spin} from 'antd';
import {token,setToken} from '../../shared/api/client';
import {normalizeRoute} from '../../app/hashRouter';
import {t} from '../../i18n';
import {LoginPage} from './LoginPage';
import {InitialPassword} from './InitialPassword';

const destinationKey='nexusdesk-return-route';
export function showLogin(expired=false) {
  if(location.hash!=='#/login')sessionStorage.setItem(destinationKey,normalizeRoute(location.hash));
  if(expired)sessionStorage.setItem('nexusdesk-session-expired','1');
  setToken('');location.hash='/login';
  window.dispatchEvent(new Event('nexusdesk:login-required'));
}

export function AuthBoundary({children}:{children:ReactNode}) {
  const [login,setLogin]=useState(!location.hash||location.hash==='#/login');
  const [ready,setReady]=useState(false),[error,setError]=useState(false),[revision,setRevision]=useState(0);
  const [mustChange,setMustChange]=useState(false);
  useEffect(()=>{const changed=()=>{const next=!location.hash||location.hash==='#/login';setLogin(next);if(!next)setRevision(v=>v+1);};
    const expired=()=>showLogin(true);const required=()=>{setLogin(true);setReady(false);};
    window.addEventListener('hashchange',changed);window.addEventListener('nexusdesk:session-expired',expired);window.addEventListener('nexusdesk:login-required',required);
    return()=>{window.removeEventListener('hashchange',changed);window.removeEventListener('nexusdesk:session-expired',expired);window.removeEventListener('nexusdesk:login-required',required);};},[]);
  useEffect(()=>{if(login){if(!location.hash)history.replaceState(null,'','#/login');return;}const controller=new AbortController();const credential=token();setError(false);
    async function check(){try{const r=await fetch('/api/v1/me',{signal:controller.signal,headers:credential?{Authorization:'Bearer '+credential}:{}});
      if(controller.signal.aborted||credential!==token())return;
      if(r.status===403){const data=await r.json();if(data.detail==='default_password_change_required'){setMustChange(true);setReady(false);return;}}
      if(r.status===401||r.status===403){showLogin(!!credential);return;}if(!r.ok)throw Error();setMustChange(false);setReady(true);
    }catch{if(!controller.signal.aborted)setError(true);}}
    void check();const focus=()=>{if(token())void check();};window.addEventListener('focus',focus);
    return()=>{controller.abort();window.removeEventListener('focus',focus);};},[login,revision]);
  if(login)return <LoginPage expired={sessionStorage.getItem('nexusdesk-session-expired')==='1'} complete={()=>{
    sessionStorage.removeItem('nexusdesk-session-expired');const target=normalizeRoute(sessionStorage.getItem(destinationKey)||'/overview');sessionStorage.removeItem(destinationKey);
    history.replaceState(null,'','#'+(target==='/login'?'/overview':target));setLogin(false);setReady(false);setMustChange(false);setRevision(v=>v+1);
  }}/>;
  if(mustChange)return <InitialPassword/>;
  if(!ready)return <div className="auth-loading"><div className="login-brand">nexus<span>desk</span></div>{error?<Alert type="error" showIcon title={t('登录服务暂不可用，请稍后重试')} action={<Button onClick={()=>setRevision(v=>v+1)}>{t('重试')}</Button>}/>:<Spin size="large"/>}</div>;
  return children;
}
