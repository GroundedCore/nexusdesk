import {createHmac,timingSafeEqual} from 'node:crypto';
export class APIError extends Error {
 constructor(status,code,requestId,retryAfter){super(`${status}: ${code} (request_id=${requestId})`);Object.assign(this,{status,code,requestId,retryAfter});}
}
export function verifyWebhook(body,signature,timestamp,secret,{now=Date.now()/1000,tolerance=300}={}){
 if(!/^\d+$/.test(String(timestamp))||Math.abs(now-Number(timestamp))>tolerance)return false;
 const expected=Buffer.from('sha256='+createHmac('sha256',secret).update(String(timestamp)+'.').update(body).digest('hex'));
 const actual=Buffer.from(signature||'');return expected.length===actual.length&&timingSafeEqual(expected,actual);
}
export class Client {
 #key;
 constructor({baseUrl,apiKey,externalUserId,timeout=40000,streamTimeout=600000,fetch:transport=globalThis.fetch}){
  const u=new URL(baseUrl);if(!['http:','https:'].includes(u.protocol)||u.username||u.password||u.search||u.hash)throw Error('invalid_base_url');
  this.baseUrl=baseUrl.replace(/\/$/,'');this.#key=apiKey;this.user=externalUserId;this.timeout=timeout;this.streamTimeout=streamTimeout;this.fetch=transport;
 }
 async request(path,{method='GET',body,idempotencyKey,lastEventId,stream=false,signal}={}){
  const headers={Authorization:'Bearer '+this.#key,Accept:stream?'text/event-stream':'application/json'};
  if(this.user)headers['X-External-User-ID']=this.user;
  if(body!==undefined)headers['Content-Type']='application/json';
  if(idempotencyKey)headers['Idempotency-Key']=idempotencyKey;
  if(lastEventId!=null)headers['Last-Event-ID']=String(lastEventId);
  const deadline=AbortSignal.timeout(stream?this.streamTimeout:this.timeout);
  const r=await this.fetch(this.baseUrl+path,{method,headers,body:body===undefined?undefined:JSON.stringify(body),redirect:'error',signal:signal?AbortSignal.any([signal,deadline]):deadline});
  if(!r.ok){let code='http_error';try{code=(await r.json()).error?.code||code;}catch{}throw new APIError(r.status,code,r.headers.get('X-Request-ID'),r.headers.get('Retry-After'));}return r;
 }
 async json(path,options){return (await this.request(path,options)).json();}
 agents(){return this.json('/agents');}
 createConversation(agentId,externalSessionId){return this.json('/conversations',{method:'POST',body:{agent_id:agentId,external_session_id:externalSessionId}});}
 messages(id,after=0,limit=50){return this.json(`/conversations/${encodeURIComponent(id)}/messages?after=${Number(after)}&limit=${Number(limit)}`);}
 sendMessage(id,message,{idempotencyKey,waitSeconds=30,signal}={}){if(!idempotencyKey)throw Error('idempotencyKey_required');return this.json(`/conversations/${encodeURIComponent(id)}/messages`,{method:'POST',body:{message,wait_seconds:waitSeconds},idempotencyKey,signal});}
 run(id){return this.json('/runs/'+encodeURIComponent(id));}
 cancel(id){return this.json('/runs/'+encodeURIComponent(id)+'/cancel',{method:'POST'});}
 events(id,options={}){return this.stream('/runs/'+encodeURIComponent(id)+'/events',options);}
 streamMessage(id,message,{idempotencyKey,signal}={}){if(!idempotencyKey)throw Error('idempotencyKey_required');return this.stream(`/conversations/${encodeURIComponent(id)}/messages`,{method:'POST',body:{message,stream:true},idempotencyKey,signal});}
 async *stream(path,options={}){
  const r=await this.request(path,{...options,stream:true});const reader=r.body.getReader();const decoder=new TextDecoder();let buffer='';let lastId=null;
  try{while(true){const {done,value}=await reader.read();buffer+=decoder.decode(value,{stream:!done});let match;
   while((match=/\r?\n\r?\n/.exec(buffer))){const block=buffer.slice(0,match.index);buffer=buffer.slice(match.index+match[0].length);let event='message';const data=[];
    for(const line of block.split(/\r?\n/)){if(line.startsWith('event:'))event=line.slice(6).trimStart();if(line.startsWith('id:'))lastId=line.slice(3).trimStart();if(line.startsWith('data:'))data.push(line.slice(5).replace(/^ /,''));}
    if(data.length)yield {event,id:lastId,data:JSON.parse(data.join('\n'))};
   }if(done)break;
  }}finally{await reader.cancel();reader.releaseLock();}
 }
 chatToken(externalUserId,parentOrigin,{name=''}={}){return this.json('/chat/token',{method:'POST',body:{external_user_id:externalUserId,parent_origin:parentOrigin,name}});}
 knowledgeBases(){return this.json('/knowledge-bases');}
 documents(baseId,offset=0){return this.json(`/knowledge-bases/${encodeURIComponent(baseId)}/documents?offset=${Number(offset)}`);}
 syncDocument(baseId,externalId,{title,content,expectedVersion=0}){return this.json(`/knowledge-bases/${encodeURIComponent(baseId)}/documents/${encodeURIComponent(externalId)}`,{method:'PUT',body:{title,content,expected_version:expectedVersion}});}
 deleteDocument(baseId,externalId,expectedVersion){return this.json(`/knowledge-bases/${encodeURIComponent(baseId)}/documents/${encodeURIComponent(externalId)}?expected_version=${Number(expectedVersion)}`,{method:'DELETE'});}
 publishKnowledge(baseId,{idempotencyKey}={}){if(!idempotencyKey)throw Error('idempotencyKey_required');return this.json(`/knowledge-bases/${encodeURIComponent(baseId)}/publish`,{method:'POST',idempotencyKey});}
 knowledgeTask(id){return this.json('/knowledge-tasks/'+encodeURIComponent(id));}
}
