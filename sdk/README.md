# NexusDesk enterprise SDKs 0.2.0

Server-side clients for `/openapi/v1`. These packages are distributed as source archives from the Open Platform console; they have **not** been published to PyPI or npm.

- Python 3.10+: unzip, then `pip install ./python`. No runtime dependencies.
- Node.js 20+: unzip, then `npm install ./javascript` (ES modules).
- Set base URL to `https://your-host/openapi/v1`, with an application key and, for conversations, your authenticated user's external ID. Do not embed keys in public clients.
- Normal messages return a run object: queued/running means HTTP 202; poll `run` or use `events`. SSE is runtime progress plus the final complete answer, not token deltas.
- SDKs never retry writes automatically. Retry a message or publication with the **same** idempotency key and payload. Retain the last SSE event `id` to resume via `events(..., last_event_id=...)` (Python) / `events(..., {lastEventId})` (JS).
- Breaking an event iterator closes the connection, not the remote run; call `cancel` explicitly. Python iterators should be explicitly closed when stopping early (`contextlib.closing`).
- APIError contains HTTP status, code, request ID and Retry-After. It does not include request headers or secrets.

## Python

```python
from nexusdesk import Client, verify_webhook
from uuid import uuid4
from contextlib import closing

client = Client("https://your-host/openapi/v1", "<APP_KEY>", "employee-001")
agent = client.agents()[0]
conversation = client.create_conversation(agent["id"], "session-001")
message_key = str(uuid4())  # persist this value for network retries
with closing(client.stream_message(conversation["conversation_id"], "Hello", idempotency_key=message_key)) as events:
    for event in events:
        print(event)

base_id = client.knowledge_bases()[0]["id"]
doc = client.sync_document(base_id, "handbook-001", "Handbook", "# Handbook\nWelcome", expected_version=0)
# Updates: expected_version=doc['version']; identical text retries are deduplicated.
publish_key = str(uuid4())
task = client.publish_knowledge(base_id, idempotency_key=publish_key)
print(client.knowledge_task(task["task_id"]))
```

## Node.js

```javascript
import {Client, verifyWebhook} from '@nexusdesk/enterprise';
import {randomUUID} from 'node:crypto';
const client = new Client({baseUrl:'https://your-host/openapi/v1', apiKey:'<APP_KEY>', externalUserId:'employee-001'});
const [agent] = await client.agents();
const c = await client.createConversation(agent.id, 'session-001');
const messageKey = randomUUID(); // retain for network retries
for await (const event of client.streamMessage(c.conversation_id, 'Hello', {idempotencyKey:messageKey})) console.log(event);
const [base] = await client.knowledgeBases();
const doc = await client.syncDocument(base.id, 'handbook-001', {title:'Handbook',content:'# Handbook\nWelcome'});
const task = await client.publishKnowledge(base.id, {idempotencyKey:randomUUID()});
console.log(await client.knowledgeTask(task.task_id));
```

## Webhook receiver

The signature is `sha256=<hex HMAC-SHA256(secret, timestamp + '.' + raw_body)>`. Verify the **exact raw request bytes**, not JSON re-serialized by your framework. Read headers `X-Webhook-Signature`, `X-Webhook-Timestamp`, `X-Webhook-ID`. Helpers reject timestamps outside ±300 seconds by default; keep receiver clocks synchronized.

```python
valid = verify_webhook(raw_body, signature_header, timestamp_header, signing_secret)
# Only after valid=True: parse JSON, atomically deduplicate event_id, enqueue processing, return 2xx.
```

```javascript
const valid = verifyWebhook(rawBodyBuffer, signatureHeader, timestampHeader, signingSecret);
// Only after valid=true: parse JSON, atomically deduplicate event_id, enqueue processing, return 2xx.
```

Deliveries are at least once: a receiver must deduplicate event_id even with a valid signature. Payloads contain type/app_id and resource IDs, not prompts or complete answers. Types: `run.completed`, `run.failed`, `run.cancelled`, `knowledge.completed`, `knowledge.failed`, `knowledge.cancelled`. Query the corresponding authorized API for details. A running run may finish just after cancellation, so always check terminal state.

Disabling an application stops future deliveries but cannot recall a request already in flight. Saving webhook configuration cancels pending old-generation events; the new configuration receives terminal events occurring after that save. Failed current-generation deliveries can be manually retried; retries keep event_id.

## Embedded chat credentials

Keep your application key on the enterprise server. Resolve the employee ID from your authenticated session, then call `client.chat_token(employee_id, "https://oa.company.com", name=display_name)` in Python or `client.chatToken(employeeId, "https://oa.company.com", {name: displayName})` in JavaScript. Return only the short-lived `chat_` credential to your browser widget. Enable embedding and the exact parent origin in the app settings first. User chat credentials cannot call management or knowledge synchronization APIs.

The browser widget is served separately at `/embed.js`, with `NexusDeskChat.mount({appId, baseUrl, getToken})`. `getToken` calls your authenticated server endpoint; omit it to use configured SSO providers. See the platform's enterprise identity guide for callback setup and deployment.
