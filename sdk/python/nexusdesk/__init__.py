"""Dependency-free synchronous Python SDK. Keep application keys on the server."""
import hashlib
import hmac
import json
import time
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class APIError(Exception):
    def __init__(self, status, code, request_id=None, retry_after=None):
        self.status, self.code = status, code
        self.request_id, self.retry_after = request_id, retry_after
        super().__init__(f'{status}: {code} (request_id={request_id})')


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def verify_webhook(body, signature, timestamp, secret, *, now=None, tolerance=300):
    """Verify exact raw bytes before parsing JSON; separately deduplicate event_id."""
    try:
        if abs((time.time() if now is None else now)-int(timestamp)) > tolerance:
            return False
        expected='sha256='+hmac.new(secret.encode(),str(timestamp).encode()+b'.'+body,hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
    except (ValueError, TypeError):
        return False


class Client:
    def __init__(self, base_url, api_key, external_user_id=None, *, timeout=40, stream_timeout=600):
        parsed=urlsplit(base_url)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('invalid_base_url')
        self.base_url=base_url.rstrip('/')
        self._key=api_key
        self.user=external_user_id
        self.timeout,self.stream_timeout=timeout,stream_timeout
        self._opener=build_opener(_NoRedirect())

    def _open(self, path, method='GET', body=None, *, idempotency_key=None, last_event_id=None, stream=False):
        headers={'Authorization':'Bearer '+self._key,'Accept':'text/event-stream' if stream else 'application/json'}
        if self.user: headers['X-External-User-ID']=self.user
        if idempotency_key: headers['Idempotency-Key']=idempotency_key
        if last_event_id is not None: headers['Last-Event-ID']=str(last_event_id)
        data=None
        if body is not None:
            headers['Content-Type']='application/json'
            data=json.dumps(body,ensure_ascii=False).encode()
        try:
            return self._opener.open(Request(self.base_url+path,data=data,headers=headers,method=method),timeout=self.stream_timeout if stream else self.timeout)
        except HTTPError as exc:
            try: code=json.loads(exc.read(65536)).get('error',{}).get('code','http_error')
            except (ValueError,AttributeError): code='http_error'
            raise APIError(exc.code,code,exc.headers.get('X-Request-ID'),exc.headers.get('Retry-After')) from None

    def _json(self,path,method='GET',body=None,**options):
        with self._open(path,method,body,**options) as response:
            return json.load(response)

    def agents(self): return self._json('/agents')
    def chat_token(self, external_user_id, parent_origin, *, name=''):
        """Server-only: mint a 15-minute, user-bound embedded-chat credential."""
        return self._json('/chat/token','POST',{'external_user_id':external_user_id,'parent_origin':parent_origin,'name':name})
    def create_conversation(self,agent_id,external_session_id):
        return self._json('/conversations','POST',{'agent_id':agent_id,'external_session_id':external_session_id})
    def messages(self,conversation_id,after=0,limit=50):
        return self._json(f'/conversations/{quote(conversation_id,safe="")}/messages?after={int(after)}&limit={int(limit)}')
    def send_message(self,conversation_id,message,*,idempotency_key,wait_seconds=30):
        return self._json(f'/conversations/{quote(conversation_id,safe="")}/messages','POST',{'message':message,'wait_seconds':wait_seconds},idempotency_key=idempotency_key)
    def run(self,run_id):return self._json('/runs/'+quote(run_id,safe=''))
    def cancel(self,run_id):return self._json('/runs/'+quote(run_id,safe='')+'/cancel','POST')
    def events(self,run_id,*,last_event_id=None):
        return self._events('/runs/'+quote(run_id,safe='')+'/events',last_event_id=last_event_id)
    def stream_message(self,conversation_id,message,*,idempotency_key):
        return self._events('/conversations/'+quote(conversation_id,safe='')+'/messages','POST',{'message':message,'stream':True},idempotency_key=idempotency_key)
    def _events(self,path,method='GET',body=None,**options):
        with self._open(path,method,body,stream=True,**options) as response:
            event='message';event_id=None;data=[]
            for raw in response:
                line=raw.decode('utf-8').rstrip('\r\n')
                if not line:
                    if data:yield {'event':event,'id':event_id,'data':json.loads('\n'.join(data))}
                    event='message';data=[]
                elif line.startswith('event:'):event=line[6:].lstrip(' ')
                elif line.startswith('id:'):event_id=line[3:].lstrip(' ')
                elif line.startswith('data:'):data.append(line[5:].lstrip(' '))
    def knowledge_bases(self):return self._json('/knowledge-bases')
    def documents(self,base_id,offset=0):return self._json('/knowledge-bases/'+quote(base_id,safe='')+'/documents?offset='+str(int(offset)))
    def sync_document(self,base_id,external_id,title,content,expected_version=0):
        return self._json('/knowledge-bases/'+quote(base_id,safe='')+'/documents/'+quote(external_id,safe=''),'PUT',{'title':title,'content':content,'expected_version':expected_version})
    def delete_document(self,base_id,external_id,expected_version):
        return self._json('/knowledge-bases/'+quote(base_id,safe='')+'/documents/'+quote(external_id,safe='')+'?expected_version='+str(int(expected_version)),'DELETE')
    def publish_knowledge(self,base_id,*,idempotency_key):
        return self._json('/knowledge-bases/'+quote(base_id,safe='')+'/publish','POST',idempotency_key=idempotency_key)
    def knowledge_task(self,task_id):return self._json('/knowledge-tasks/'+quote(task_id,safe=''))
