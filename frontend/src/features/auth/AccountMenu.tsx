import {useState, type ReactNode} from 'react';
import {Alert, App, Dropdown, Form, Input, Modal} from 'antd';
import {t} from '../../i18n';
import {post,token} from '../../shared/api/client';
import {showLogin} from './AuthBoundary';
import {loginError} from './LoginPage';

export function AccountMenu({children,access}:{children:ReactNode;access:()=>void}) {
  const {modal}=App.useApp();const [open,setOpen]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');const [form]=Form.useForm();
  const local=token().startsWith('local_');
  async function logout(){const credential=token();try{
    if(credential.startsWith('local_'))await post('/auth/local/logout');
    else if(credential.startsWith('ssow_'))await post('/sso/logout');
    showLogin();
  }catch(e){if((e as Error).message.startsWith('401')){showLogin();return;}modal.error({title:t('退出失败，请重试'),content:t('登录服务暂不可用，请稍后重试')});}}
  return <><Dropdown trigger={['click']} menu={{items:[{key:'access',label:t('访问与策略')},...(local?[{key:'password',label:t('修改密码')}]:[]),{type:'divider'},{key:'logout',label:token()?t('退出登录'):t('前往登录')}],onClick:({key})=>{
    if(key==='access')access();if(key==='password'){setError('');setOpen(true);}if(key==='logout'){if(!token()){showLogin();return;}modal.confirm({title:t('退出登录'),content:t('退出后需要重新登录，未保存的修改将丢失。'),okText:t('退出登录'),cancelText:t('取消'),onOk:logout});}
  }}}>{children}</Dropdown>
  <Modal open={open} title={t('修改密码')} onCancel={()=>{if(!busy){setOpen(false);form.resetFields();}}} onOk={()=>form.submit()} confirmLoading={busy} destroyOnHidden>
    <p>{t('修改密码后，所有本地登录会话将失效。')}</p>{error&&<Alert type="error" showIcon title={error} style={{marginBottom:16}}/>}
    <Form form={form} layout="vertical" preserve={false} onFinish={async values=>{setBusy(true);setError('');try{await post('/auth/local/password',{old_password:values.old_password,new_password:values.new_password});setOpen(false);form.resetFields();showLogin();}catch(e){const code=(e as Error).message.split(' · ')[1];setError(loginError(code));}finally{setBusy(false);}}}>
      <Form.Item name="old_password" label={t('原密码')} rules={[{required:true,message:t('请输入密码')}]}><Input.Password autoComplete="current-password" maxLength={128}/></Form.Item>
      <Form.Item name="new_password" label={t('新密码')} rules={[{required:true,message:t('请输入密码')},{min:12,max:128,message:t('密码需要 12–128 个字符')}]}><Input.Password autoComplete="new-password" maxLength={128}/></Form.Item>
      <Form.Item name="confirm" label={t('确认新密码')} dependencies={['new_password']} rules={[{required:true,message:t('请再次输入新密码')},({getFieldValue})=>({validator:(_,value)=>!value||value===getFieldValue('new_password')?Promise.resolve():Promise.reject(new Error(t('两次输入的密码不一致')))})]}><Input.Password autoComplete="new-password" maxLength={128}/></Form.Item>
    </Form>
  </Modal></>;
}
