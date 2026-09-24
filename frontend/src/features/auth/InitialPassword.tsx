import {useState} from 'react';
import {Alert,Button,Card,Form,Input} from 'antd';
import {t} from '../../i18n';
import {post} from '../../shared/api/client';
import {showLogin} from './AuthBoundary';
import {loginError} from './LoginPage';

export function InitialPassword() {
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  return <main className="auth-loading"><div className="login-brand">nexus<span>desk</span></div>
    <Card title={t('首次登录，请修改初始密码')} style={{width:'100%',maxWidth:460}}>
      <p>{t('设置专属密码后即可进入工作台。')}</p>
      {error&&<Alert type="error" showIcon title={error} style={{marginBottom:16}}/>}
      <Form layout="vertical" requiredMark={false} disabled={busy} onFinish={async values=>{setBusy(true);setError('');try{
        await post('/auth/local/password',{old_password:values.old_password,new_password:values.new_password});showLogin();
      }catch(e){setError(loginError((e as Error).message.split(' · ')[1]));}finally{setBusy(false);}}}>
        <Form.Item name="old_password" label={t('原密码')} rules={[{required:true,message:t('请输入密码')}]}><Input.Password autoComplete="current-password" maxLength={128}/></Form.Item>
        <Form.Item name="new_password" label={t('新密码')} rules={[{required:true,message:t('请输入密码')},{min:12,max:128,message:t('密码需要 12–128 个字符')}]}><Input.Password autoComplete="new-password" maxLength={128}/></Form.Item>
        <Form.Item name="confirm" label={t('确认新密码')} dependencies={['new_password']} rules={[{required:true,message:t('请再次输入新密码')},({getFieldValue})=>({validator:(_,value)=>!value||value===getFieldValue('new_password')?Promise.resolve():Promise.reject(new Error(t('两次输入的密码不一致')))})]}><Input.Password autoComplete="new-password" maxLength={128}/></Form.Item>
        <Button block type="primary" htmlType="submit" loading={busy}>{t('修改密码并重新登录')}</Button>
        <Button block type="text" disabled={busy} style={{marginTop:10}} onClick={async()=>{setBusy(true);try{await post('/auth/local/logout');showLogin();}catch{setError(t('退出失败，请重试'));}finally{setBusy(false);}}}>{t('退出登录')}</Button>
      </Form>
    </Card>
  </main>;
}
