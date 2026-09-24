import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createHmac} from 'node:crypto';
import {Client,APIError,verifyWebhook} from './index.js';

test('SSE UTF-8 boundaries, CRLF, comments, multiline and resume cursor',async()=>{
 let request;
 const data=new TextEncoder().encode(': heartbeat\r\n\r\nid: 7\r\nevent: run.completed\r\ndata: {"output":\r\ndata: "你好"}\r\n\r\n');
 const fetch=async(url,init)=>{request={url,init};return new Response(new ReadableStream({start(c){for(const byte of data)c.enqueue(Uint8Array.of(byte));c.close();}}));};
 const client=new Client({baseUrl:'http://localhost/openapi/v1',apiKey:'test-key',externalUserId:'employee',fetch});
 const events=[];for await(const e of client.events('run-1',{lastEventId:6}))events.push(e);
 assert.deepEqual(events,[{event:'run.completed',id:'7',data:{output:'你好'}}]);
 assert.equal(request.init.headers['Last-Event-ID'],'6');assert.equal(request.init.redirect,'error');assert.equal(request.init.headers['X-External-User-ID'],'employee');
});
test('errors and idempotency key pass-through without hidden retries',async()=>{
 let calls=0;
 const client=new Client({baseUrl:'http://localhost/openapi/v1',apiKey:'secret',fetch:async(url,init)=>{calls++;assert.equal(init.headers['Idempotency-Key'],'retry-1');return new Response(JSON.stringify({error:{code:'application_rate_limit'}}),{status:429,headers:{'X-Request-ID':'req-1','Retry-After':'60'}});}});
 await assert.rejects(client.sendMessage('cid','hello',{idempotencyKey:'retry-1'}),e=>e instanceof APIError&&e.status===429&&e.requestId==='req-1'&&e.retryAfter==='60'&&!e.message.includes('secret'));assert.equal(calls,1);
});
test('webhook signature, body tampering and replay window',()=>{
 const body=Buffer.from('{"event_id":"e1"}');const timestamp='1000';const signature='sha256='+createHmac('sha256','secret').update(timestamp+'.').update(body).digest('hex');
 assert.equal(verifyWebhook(body,signature,timestamp,'secret',{now:1000}),true);
 assert.equal(verifyWebhook(Buffer.from('changed'),signature,timestamp,'secret',{now:1000}),false);
 assert.equal(verifyWebhook(body,signature,timestamp,'secret',{now:1400}),false);
});
