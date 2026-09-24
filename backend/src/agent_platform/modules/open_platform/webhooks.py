"""Durable, signed, at-least-once webhook delivery. Response bodies are never retained."""

import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent_platform.platform.persistence.store import (
    DomainError,
    audit,
    execute,
    many,
    one,
    required,
)
from agent_platform.platform.secrets.vault import CredentialVault

logger = logging.getLogger(__name__)


class WebhookInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    url: str = Field(min_length=1, max_length=2000)
    enabled: bool = False
    revision: int = Field(default=0, ge=0)
    rotate_secret: bool = False

    @field_validator("url")
    @classmethod
    def valid_url(cls, value):
        try:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.fragment
                or any(ord(c) < 33 for c in value)
            ):
                raise ValueError("invalid_webhook_url")
            _ = parsed.port
        except ValueError:
            raise ValueError("invalid_webhook_url") from None
        return value


class Webhooks:
    def __init__(self, service):
        self.s = service
        self.engine = service.engine
        self.vault = CredentialVault(service.p.settings.model_credential_key_file)
        self.stopping = asyncio.Event()

    async def config(self, tenant, app_id):
        async with self.engine.connect() as c:
            await self.s.application(c, tenant, app_id)
            return await one(
                c,
                "SELECT app_id,url,enabled,revision,created_at,updated_at FROM open_webhooks WHERE app_id=:a",
                a=app_id,
            )

    async def save(self, tenant, actor, app_id, body):
        async with self.engine.begin() as c:
            await self.s.application(c, tenant, app_id, True)
            old = await one(c, "SELECT * FROM open_webhooks WHERE app_id=:a FOR UPDATE", a=app_id)
            if body.revision != (old["revision"] if old else 0):
                raise DomainError("webhook_revision_conflict", 409)
            new_secret = (
                "whsec_" + secrets.token_urlsafe(32) if not old or body.rotate_secret else None
            )
            secret = new_secret or self.vault.decrypt(
                old["encrypted_secret"],
                tenant,
                app_id,
                {"base_url": old["url"], "protocol": "webhook"},
            )
            encrypted = self.vault.encrypt(
                secret, tenant, app_id, {"base_url": body.url, "protocol": "webhook"}, create=True
            )
            row = await one(
                c,
                """INSERT INTO open_webhooks(app_id,url,encrypted_secret,enabled)
                VALUES(:a,:url,:secret,:enabled) ON CONFLICT(app_id) DO UPDATE SET url=:url,encrypted_secret=:secret,
                enabled=:enabled,revision=open_webhooks.revision+1,updated_at=now()
                RETURNING app_id,url,enabled,revision,created_at,updated_at""",
                a=app_id,
                url=body.url,
                secret=encrypted,
                enabled=body.enabled,
            )
            await execute(
                c,
                "UPDATE open_deliveries SET status='cancelled',error_code='webhook_configuration_changed',updated_at=now() WHERE app_id=:a AND status IN ('pending','running')",
                a=app_id,
            )
            await audit(c, tenant, actor, "open_webhook.configured", app_id, enabled=body.enabled)
            return {**row, **({"secret": new_secret} if new_secret else {})}

    async def collect(self):
        # INSERT + unique event key survives crashes and concurrent API replicas.
        async with self.engine.begin() as c:
            rows = await many(
                c,
                """SELECT o.app_id,r.id AS resource_id,'run.'||r.status AS event_type,
                'run:'||r.id::text AS event_key,w.revision,o.request_id,r.conversation_id,r.error_code
                FROM open_runs o JOIN runtime_runs r ON r.id=o.run_id
                JOIN open_applications a ON a.id=o.app_id JOIN open_webhooks w ON w.app_id=a.id
                WHERE a.tenant_id=:t AND a.enabled AND w.enabled AND r.status IN ('completed','failed','cancelled')
                AND r.finished_at>=w.updated_at AND NOT EXISTS(SELECT 1 FROM open_deliveries d WHERE d.app_id=a.id AND d.event_key='run:'||r.id::text)
                ORDER BY r.finished_at LIMIT 100""",
                t=self.s.p.settings.tenant_id,
            )
            rows += await many(
                c,
                """SELECT s.app_id,t.id AS resource_id,'knowledge.'||t.status AS event_type,
                'knowledge:'||t.id::text AS event_key,w.revision,t.error_code
                FROM open_sync_tasks s JOIN knowledge_tasks t ON t.id=s.task_id
                JOIN open_applications a ON a.id=s.app_id JOIN open_webhooks w ON w.app_id=a.id
                WHERE a.tenant_id=:tenant AND a.enabled AND w.enabled AND t.status IN ('completed','failed','cancelled')
                AND t.updated_at>=w.updated_at AND NOT EXISTS(SELECT 1 FROM open_deliveries d WHERE d.app_id=a.id AND d.event_key='knowledge:'||t.id::text)
                ORDER BY t.updated_at LIMIT 100""",
                tenant=self.s.p.settings.tenant_id,
            )
            for row in rows:
                eid = uuid4()
                payload = {
                    "event_id": str(eid),
                    "type": row["event_type"],
                    "app_id": str(row["app_id"]),
                    "data": {
                        k: str(v) if v is not None else None
                        for k, v in row.items()
                        if k in {"resource_id", "request_id", "conversation_id", "error_code"}
                    },
                }
                await execute(
                    c,
                    """INSERT INTO open_deliveries(id,app_id,event_key,event_type,resource_id,webhook_revision,payload)
                    VALUES(:id,:a,:key,:type,:resource,:revision,CAST(:payload AS jsonb)) ON CONFLICT(app_id,event_key) DO NOTHING""",
                    id=eid,
                    a=row["app_id"],
                    key=row["event_key"],
                    type=row["event_type"],
                    resource=row["resource_id"],
                    revision=row["revision"],
                    payload=json.dumps(payload),
                )

    async def send(self, url, body, headers):
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=10) as client:  # noqa: SIM117
            async with client.stream("POST", url, content=body, headers=headers) as response:
                return response.status_code

    async def run_next(self):
        await self.collect()
        owner = uuid4()
        async with self.engine.begin() as c:
            row = await one(
                c,
                """SELECT d.* FROM open_deliveries d JOIN open_applications a ON a.id=d.app_id
                WHERE a.tenant_id=:t AND ((d.status='pending' AND d.next_attempt_at<=now()) OR (d.status='running' AND d.lease_until<now()))
                ORDER BY d.next_attempt_at FOR UPDATE OF d SKIP LOCKED LIMIT 1""",
                t=self.s.p.settings.tenant_id,
            )
            if not row:
                return False
            config = await one(
                c,
                "SELECT w.*,a.enabled AS app_enabled,a.tenant_id FROM open_webhooks w JOIN open_applications a ON a.id=w.app_id WHERE w.app_id=:a",
                a=row["app_id"],
            )
            if (
                not config
                or not config["app_enabled"]
                or not config["enabled"]
                or config["revision"] != row["webhook_revision"]
            ):
                await execute(
                    c,
                    "UPDATE open_deliveries SET status='cancelled',error_code='webhook_unavailable',updated_at=now() WHERE id=:id",
                    id=row["id"],
                )
                return True
            if row["attempts"] >= 5:
                await execute(
                    c,
                    "UPDATE open_deliveries SET status='failed',error_code='delivery_attempts_exhausted',updated_at=now() WHERE id=:id",
                    id=row["id"],
                )
                return True
            await execute(
                c,
                "UPDATE open_deliveries SET status='running',owner=:owner,lease_until=now()+interval '60 seconds',attempts=attempts+1,updated_at=now() WHERE id=:id",
                owner=owner,
                id=row["id"],
            )
        code = None
        status = None
        try:
            secret = self.vault.decrypt(
                config["encrypted_secret"],
                config["tenant_id"],
                row["app_id"],
                {"base_url": config["url"], "protocol": "webhook"},
            )
            body = json.dumps(row["payload"], ensure_ascii=False, separators=(",", ":")).encode()
            timestamp = str(int(time.time()))
            signed = timestamp.encode() + b"." + body
            signature = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
            status = await self.send(
                config["url"],
                body,
                {
                    "Content-Type": "application/json",
                    "X-Webhook-ID": str(row["id"]),
                    "X-Webhook-Timestamp": timestamp,
                    "X-Webhook-Signature": "sha256=" + signature,
                },
            )
            if not 200 <= status < 300:
                code = "webhook_http_error"
        except (httpx.HTTPError, DomainError, ValueError):
            code = "webhook_delivery_error"
        attempts = row["attempts"] + 1
        state = "succeeded" if code is None else "failed" if attempts >= 5 else "pending"
        async with self.engine.begin() as c:
            await execute(
                c,
                """UPDATE open_deliveries SET status=:status,http_status=:http,error_code=:error,
                next_attempt_at=now()+:delay*interval '1 second',lease_until=NULL,updated_at=now()
                WHERE id=:id AND owner=:owner AND status='running'""",
                status=state,
                http=status,
                error=code,
                delay=[2, 10, 30, 120, 600][attempts - 1],
                id=row["id"],
                owner=owner,
            )
        return True

    async def deliveries(self, tenant, app_id, page):
        async with self.engine.connect() as c:
            await self.s.application(c, tenant, app_id)
            rows = await many(
                c,
                "SELECT id,event_type,resource_id,status,attempts,http_status,error_code,created_at,updated_at,next_attempt_at FROM open_deliveries WHERE app_id=:a ORDER BY created_at DESC LIMIT 20 OFFSET :off",
                a=app_id,
                off=(page - 1) * 20,
            )
            count = await one(
                c, "SELECT count(*) AS n FROM open_deliveries WHERE app_id=:a", a=app_id
            )
            return {"items": rows, "total": count["n"]}

    async def retry(self, tenant, actor, app_id, eid):
        async with self.engine.begin() as c:
            app = await self.s.application(c, tenant, app_id, True)
            config = required(
                await one(c, "SELECT * FROM open_webhooks WHERE app_id=:a AND enabled", a=app_id),
                "webhook_not_enabled",
            )
            if not app["enabled"]:
                raise DomainError("application_disabled", 409)
            row = required(
                await one(
                    c,
                    """UPDATE open_deliveries SET status='pending',attempts=0,next_attempt_at=now(),error_code=NULL,
                updated_at=now() WHERE id=:id AND app_id=:a AND webhook_revision=:r AND status='failed' RETURNING id""",
                    id=eid,
                    a=app_id,
                    r=config["revision"],
                ),
                "delivery_not_retryable",
            )
            await audit(c, tenant, actor, "open_webhook.retried", eid)
            return row

    async def serve(self):
        while not self.stopping.is_set():
            try:
                if await self.run_next():
                    continue
            except Exception:  # noqa: BLE001 - retry durable queue without logging secrets
                logger.warning("webhook_dispatch_unavailable")
            try:
                await asyncio.wait_for(self.stopping.wait(), timeout=2)
            except TimeoutError:
                pass
