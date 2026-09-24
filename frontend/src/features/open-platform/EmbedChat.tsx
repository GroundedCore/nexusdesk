import {useEffect,useRef,useState} from 'react';
import {Alert,Button,Input,Select,Space,Spin} from 'antd';
import {SendOutlined,RobotOutlined,PlusOutlined} from '@ant-design/icons';
import {t} from '../../i18n';
import {ssoLogin,type LoginProvider} from './SsoLogin';
import './embed-chat.css';

interface Config {title:string;color:string;providers:LoginProvider[]}
interface ChatMessage {role:string;content:string}
interface Run {id:string;status:string;response?:string;error_code?:string}
export function EmbedChat() {
  const appId=location.pathname.split('/')[2],parentOrigin=new URLSearchParams(location.search).get('parent_origin')||'';
  const [config,setConfig]=useState<Config|null>(null),[error,setError]=useState(''),[credential,setCredential]=useState(''),[ready,setReady]=useState(false);
  const [agents,setAgents]=useState<{id:string;name:string}[]>([]),[agentId,setAgentId]=useState(''),[messages,setMessages]=useState<ChatMessage[]>([]),[input,setInput]=useState(''),[busy,setBusy]=useState(false),[loginBusy,setLoginBusy]=useState(false);
  const conversation=useRef(''),generation=useRef(0),bottom=useRef<HTMLDivElement>(null),credentialRef=useRef('');
  const pending=useRef<{text:string;key:string}|null>(null);
  function reset(){generation.current++;conversation.current='';pending.current=null;setMessages([]);setBusy(false);setError('');}
  useEffect(()=>{
    if(window.parent===window||!/^https?:\/\//.test(parentOrigin)){setError(t('请通过企业页面中的嵌入组件打开聊天'));return;}
    let active=true;
    const receive=(event:MessageEvent)=>{
      if(event.source!==window.parent||event.origin!==parentOrigin)return;
      if(event.data?.type==='nexusdesk.init'){setReady(true);if(event.data.agentId)setAgentId(event.data.agentId);}
      if(event.data?.type==='nexusdesk.token'&&typeof event.data.token==='string'&&event.data.token.startsWith('chat_')){reset();setCredential(event.data.token);}
      if(event.data?.type==='nexusdesk.token-error')setError(t('获取登录凭据失败，请重试'));
    };
    window.addEventListener('message',receive);
    fetch(`/api/v1/sso/embed/${appId}?parent_origin=${encodeURIComponent(parentOrigin)}`).then(async r=>{const data=await r.json();if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:'embed_origin_not_allowed');if(active){setConfig(data);window.parent.postMessage({type:'nexusdesk.ready'},parentOrigin);}}).catch(e=>{if(active)setError(e.message);});
    return ()=>{active=false;generation.current++;window.removeEventListener('message',receive);};
  },[appId,parentOrigin]);
  useEffect(()=>{credentialRef.current=credential;},[credential]);
  async function api<T>(path:string,body?:unknown,headers:Record<string,string>={}):Promise<T>{
    const r=await fetch('/openapi/v1'+path,{method:body===undefined?'GET':'POST',headers:{Authorization:'Bearer '+credentialRef.current,...(body===undefined?{}:{'Content-Type':'application/json'}),...headers},body:body===undefined?undefined:JSON.stringify(body)});
    const data=await r.json();
    if(!r.ok){if(r.status===401){setCredential('');reset();}throw new Error(data.error?.code||data.detail||'request_failed');}
    return data;
  }
  useEffect(()=>{if(!credential)return;credentialRef.current=credential;let active=true;api<{id:string;name:string}[]>('/agents').then(items=>{if(active){setAgents(items);setAgentId(old=>items.some(a=>a.id===old)?old:items[0]?.id||'');}}).catch(e=>{if(active)setError(e.message);});return()=>{active=false;};},[credential]);
  useEffect(()=>{bottom.current?.scrollIntoView({behavior:'smooth'});},[messages,busy]);
  async function send(){
    if(busy||!input.trim()||!agentId)return;
    const text=input.trim(),stamp=generation.current;
    const attempt=pending.current?.text===text?pending.current:{text,key:crypto.randomUUID()};pending.current=attempt;
    setBusy(true);setError('');
    try{
      if(!conversation.current){const session=await api<{conversation_id:string}>('/conversations',{agent_id:agentId,external_session_id:attempt.key});if(stamp!==generation.current)return;conversation.current=session.conversation_id;}
      let run=await api<Run>(`/conversations/${conversation.current}/messages`,{message:text,wait_seconds:0},{'Idempotency-Key':attempt.key});
      if(stamp!==generation.current)return;
      setMessages(old=>[...old,{role:'user',content:text}]);setInput('');pending.current=null;
      const deadline=Date.now()+300000;
      while(['queued','running'].includes(run.status)){
        if(stamp!==generation.current)return;
        if(Date.now()>deadline)throw new Error(t('运行仍在处理中，请稍后重新打开会话'));
        await new Promise(r=>setTimeout(r,1500));
        if(stamp!==generation.current)return;
        run=await api<Run>('/runs/'+run.id);
      }
      if(stamp!==generation.current)return;
      const history=await api<{items:ChatMessage[]}>(`/conversations/${conversation.current}/messages?limit=200`);
      if(stamp!==generation.current)return;
      setMessages(history.items.filter(m=>m.role==='user'||m.role==='assistant'));
      if(run.status!=='completed')setError(run.error_code||run.status);
    }catch(e){if(stamp===generation.current)setError((e as Error).message);}finally{if(stamp===generation.current)setBusy(false);}
  }
  return <div className="embed-chat" style={{'--chat-color':config?.color||'#6562ff'} as React.CSSProperties}>
    <header><div className="embed-avatar"><RobotOutlined/></div><div><h1>{config?.title||t('企业助手')}</h1><small>{t('企业智能服务')}</small></div>{credential&&<Button type="text" icon={<PlusOutlined/>} disabled={busy} onClick={reset} aria-label={t('新建会话')}/>}</header>
    {error&&<Alert showIcon type="error" title={error} closable onClose={()=>setError('')}/>}
    {!config&&!error?<Spin/>:!ready?<div className="embed-empty">{t('正在连接企业页面')}</div>:!credential?<div className="embed-login"><RobotOutlined style={{fontSize:42,color:config?.color}}/><h2>{t('登录后开始对话')}</h2><p>{t('使用企业账号登录，继续你的专属会话。')}</p><Space direction="vertical" style={{width:'100%'}}>{config?.providers.map(p=><Button block key={p.id} disabled={loginBusy} onClick={async()=>{setLoginBusy(true);setError('');try{const session=await ssoLogin(p,appId,parentOrigin);reset();setCredential(session.access_token);}catch(e){setError((e as Error).message);}finally{setLoginBusy(false);}}}>{p.name}</Button>)}<Button block type="link" onClick={()=>window.parent.postMessage({type:'nexusdesk.refresh-token'},parentOrigin)}>{t('获取企业登录凭据')}</Button></Space></div>:<>
      <div className="embed-controls"><Select aria-label={t('选择 Agent')} value={agentId||undefined} disabled={busy||!!conversation.current} onChange={setAgentId} options={agents.map(a=>({value:a.id,label:a.name}))} style={{flex:1}}/><Button type="text" onClick={async()=>{await fetch('/api/v1/sso/logout',{method:'POST',headers:{Authorization:'Bearer '+credential}});reset();setCredential('');}}>{t('退出')}</Button></div>
      <div className="embed-messages" aria-live="polite">{!messages.length&&<div className="embed-empty"><RobotOutlined/><p>{t('有什么可以帮你？')}</p></div>}{messages.map((m,i)=><div key={i} className={'embed-message '+m.role}>{m.content}</div>)}{busy&&<div className="embed-thinking"><Spin size="small"/> {t('正在思考…')}</div>}<div ref={bottom}/></div>
      <div className="embed-composer"><Input.TextArea aria-label={t('输入消息')} placeholder={t('输入消息')} value={input} maxLength={8000} autoSize={{minRows:1,maxRows:4}} disabled={busy} onChange={e=>setInput(e.target.value)} onPressEnter={e=>{if(!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();void send();}}}/><Button type="primary" icon={<SendOutlined/>} aria-label={t('发送')} loading={busy} disabled={!agentId||!input.trim()} onClick={()=>void send()}/></div>
      <footer>{t('AI 回答仅供参考，请核实重要信息。')}</footer></>}
  </div>;
}
