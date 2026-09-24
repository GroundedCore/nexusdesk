import {useEffect, useState} from 'react';
import {Alert, Button, Form, Input, Tabs} from 'antd';
import {ArrowRightOutlined, LockOutlined, UserOutlined, SafetyOutlined} from '@ant-design/icons';
import {t} from '../../i18n';
import {LanguageSelector} from '../../i18n/LanguageSelector';
import {setToken} from '../../shared/api/client';
import {ssoLogin, type LoginProvider} from '../open-platform/SsoLogin';
import './login.css';

export function loginError(code:string) {
  if(code==='invalid_local_credentials') return t('账号或密码不正确');
  if(code==='local_login_rate_limited') return t('尝试次数过多，请在 15 分钟后重试');
  if(code==='new_password_must_differ') return t('新密码不能与原密码相同');
  if(code==='password_requires_12_to_128_characters') return t('密码需要 12–128 个字符');
  if(code==='workbench_access_pending') return t('身份已登记，请联系管理员分配工作台角色');
  return t('登录服务暂不可用，请稍后重试');
}

export function LoginPage({complete, expired=false}:{complete:()=>void;expired?:boolean}) {
  const [tab,setTab]=useState('local'),[busy,setBusy]=useState(''),[error,setError]=useState('');
  const [providers,setProviders]=useState<LoginProvider[]>([]),[loading,setLoading]=useState(true),[retry,setRetry]=useState(0);
  const [status,setStatus]=useState<{initialized:boolean;development_access:boolean}|null>(null);
  const [serviceToken,setServiceToken]=useState('');
  const [form]=Form.useForm();
  useEffect(()=>{const controller=new AbortController();setLoading(true);setError('');
    Promise.all([fetch('/api/v1/sso/providers',{signal:controller.signal}),fetch('/api/v1/auth/local/status',{signal:controller.signal})])
      .then(async ([p,s])=>{if(!p.ok||!s.ok)throw Error();const [ps,st]=await Promise.all([p.json(),s.json()]);if(!controller.signal.aborted){setProviders(ps);setStatus(st);}})
      .catch(()=>{if(!controller.signal.aborted)setError(t('登录服务暂不可用，请稍后重试'));})
      .finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    return()=>controller.abort();},[retry]);
  async function local(values:{username:string;password:string}) {
    setBusy('local');setError('');
    try {const response=await fetch('/api/v1/auth/local/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)});
      const data=await response.json();if(!response.ok){setError(loginError(data.detail));form.setFieldValue('password','');return;}
      setToken(data.access_token);form.resetFields();complete();
    }catch{setError(t('登录服务暂不可用，请稍后重试'));}finally{setBusy('');}
  }
  async function enterprise(provider:LoginProvider){setBusy(provider.id);setError('');try{const session=await ssoLogin(provider);setToken(session.access_token);complete();}catch(e){setError((e as Error).message);}finally{setBusy('');}}
  return <main className="login-page">
    <section className="login-brand-panel" aria-label="nexusdesk"><div className="login-brand">nexus<span>desk</span><small>{t('智能客服平台')}</small></div>
      <div className="login-story"><h1>{t('让智能，融入每一次服务')}</h1><p>{t('连接企业知识、业务工具与服务团队')}</p></div>
      <div className="login-brand-footer">Agent · {t('知识库')} · {t('模型网关')}</div>
    </section>
    <section className="login-form-panel"><div className="login-language"><LanguageSelector/></div>
      <div className="login-form-inner"><header><h2>{t('欢迎回来')}</h2><p>{t('登录你的企业智能服务平台')}</p></header>
        <Tabs activeKey={tab} onChange={key=>{setTab(key);setError('');}} items={[{key:'local',label:t('账号登录')},{key:'enterprise',label:t('企业登录')}]}/>
        {expired&&<Alert className="login-notice" type="info" showIcon title={t('登录已过期，请重新登录')}/>}
        {error&&<Alert className="login-notice" type="error" showIcon title={error} action={<Button type="text" size="small" onClick={()=>setRetry(v=>v+1)}>{t('重试')}</Button>}/>}
        {tab==='local'?<><Form form={form} layout="vertical" requiredMark={false} onFinish={local} disabled={!!busy}>
          <Form.Item name="username" label={t('账号')} rules={[{required:true,message:t('请输入账号')}]}><Input prefix={<UserOutlined/>} autoComplete="username" maxLength={64} placeholder={t('请输入账号')}/></Form.Item>
          <Form.Item name="password" label={t('密码')} rules={[{required:true,message:t('请输入密码')}]}><Input.Password prefix={<LockOutlined/>} autoComplete="current-password" maxLength={128} placeholder={t('请输入密码')}/></Form.Item>
          <Button className="login-submit" aria-label={t('登录')} type="primary" block htmlType="submit" loading={busy==='local'}>{t('登录')} <ArrowRightOutlined aria-hidden/></Button>
        </Form><p className="login-help">{t('本地管理员可在企业登录不可用时使用')}</p>
        {status&&!status.initialized&&<Alert className="login-notice" type="info" showIcon title={t('本地管理员尚未初始化，请联系部署管理员完成设置。')}/>}</>
        :<div className="login-providers"><p>{t('选择企业身份，安全进入工作台')}</p>{providers.map(p=><Button key={p.id} block icon={<SafetyOutlined aria-hidden/>} loading={busy===p.id} disabled={!!busy} onClick={()=>enterprise(p)}>{p.name}<ArrowRightOutlined aria-hidden/></Button>)}
          {!providers.length&&!loading&&<Alert showIcon type="info" title={t('暂未配置企业登录，请使用账号登录')}/>}
          {loading&&<Button loading block>{t('加载中')}</Button>}</div>}
        <details className="login-service"><summary>{t('使用服务令牌')}</summary><Input.Password aria-label={t('Bearer 服务令牌')} autoComplete="off" value={serviceToken} onChange={e=>setServiceToken(e.target.value)}/><Button block disabled={!serviceToken.trim()||!!busy} onClick={async()=>{setBusy('token');setError('');try{const r=await fetch('/api/v1/me',{headers:{Authorization:'Bearer '+serviceToken.trim()}});if(!r.ok){setError(t('访问凭据无效，请重新配置'));return;}setToken(serviceToken.trim());setServiceToken('');complete();}catch{setError(t('登录服务暂不可用，请稍后重试'));}finally{setBusy('');}}}>{t('应用凭据')}</Button></details>
        {status?.development_access&&<Button className="login-development" type="link" onClick={()=>{setToken('');complete();}}>{t('进入本地体验')}</Button>}
      </div><footer>{t('企业级智能服务工作台')}</footer>
    </section>
  </main>;
}
