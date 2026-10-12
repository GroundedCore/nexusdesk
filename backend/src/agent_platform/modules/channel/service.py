import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from collections.abc import Iterable
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from agent_platform.integrations.channels import provider_catalog
from agent_platform.integrations.channels.base import (
    CHANNEL_WORK_CHANNEL,
    DeliveryResult,
    IncomingEnvelope,
)
from agent_platform.integrations.channels.registry import adapter_for, bind_token_store
from agent_platform.integrations.channels.tokens import TokenStore
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

RETRY_DELAYS = [2, 10, 30, 120, 600]


class IncomingMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=8000)


class ChannelService:
    """Channel management, provider webhooks, inbound idempotency, and delivery."""

    def __init__(self, engine, conversations, customer, agents, settings):
        self.engine, self.conversations, self.customer, self.agents = (
            engine,
            conversations,
            customer,
            agents,
        )
        self.settings = settings
        self.vault = CredentialVault(settings.model_credential_key_file)
        self.delivery_stopping = asyncio.Event()
        self.inbound_stopping = asyncio.Event()
        # Wake an idle loop the moment work is queued; the poll interval is only
        # a fallback for missed notifications and lease-expiry reclaim.
        self.delivery_wakeup = asyncio.Event()
        self.inbound_wakeup = asyncio.Event()
        self.poll_interval = settings.channel_worker_poll_seconds
        self.concurrency = max(1, settings.channel_worker_concurrency)
        self.listener = None
        self._wakeup_attached = False
        self._pool_tasks: set[asyncio.Task] = set()
        # Share provider access tokens across replicas instead of minting one
        # per process. Falls back to the in-process cache when unbound.
        self.token_store = TokenStore(engine)
        bind_token_store(self.token_store)

    def bind_listener(self, listener) -> None:
        """Attach the shared LISTEN connection used for instant wakeups."""

        self.listener = listener

    def providers(self):
        return provider_catalog()

    async def list(self, tenant):
        async with self.engine.connect() as c:
            return await many(
                c,
                """SELECT id,name,agent_id,enabled,provider,config,last_error_code,
                last_activity_at,created_at,
                encrypted_credentials IS NOT NULL AS credentials_configured
                FROM channels WHERE tenant_id=:t ORDER BY created_at DESC LIMIT 100""",
                t=tenant,
            )

    async def create(
        self,
        tenant,
        actor,
        name,
        agent_id,
        signing_secret_ref=None,
        *,
        provider="generic_webhook",
        config=None,
        credentials=None,
    ):
        token = secrets.token_urlsafe(32)
        config = config or {}
        credentials = credentials or {}
        if provider == "generic_webhook":
            if credentials or config:
                raise DomainError("channel_generic_config_not_supported", 422)
        else:
            adapter_for(provider).validate(config, credentials)
        async with self.engine.begin() as c:
            await self.agents.snapshot(tenant, agent_id, c)
            channel_id = uuid4()
            encrypted = (
                self._encrypt_credentials(tenant, channel_id, provider, credentials)
                if credentials
                else None
            )
            row = await one(
                c,
                """INSERT INTO channels(
                    id,tenant_id,name,agent_id,token_hash,signing_secret_ref,provider,config,
                    encrypted_credentials
                )
                VALUES(
                    :id,:t,:name,:aid,:hash,:ref,:provider,CAST(:config AS jsonb),:credentials
                )
                RETURNING id,name,agent_id,enabled,provider,config,signing_secret_ref,
                    encrypted_credentials IS NOT NULL AS credentials_configured""",
                id=channel_id,
                t=tenant,
                name=name,
                aid=agent_id,
                hash=hashlib.sha256(token.encode()).hexdigest(),
                ref=signing_secret_ref,
                provider=provider,
                config=json.dumps(config),
                credentials=encrypted,
            )
            await audit(c, tenant, actor, "channel.created", row["id"], provider=provider)
            return {**row, "token": token if provider == "generic_webhook" else None}

    async def update(
        self,
        tenant,
        actor,
        cid,
        *,
        config: dict[str, Any] | None = None,
        credentials: dict[str, Any] | None = None,
    ):
        async with self.engine.begin() as c:
            row = required(
                await one(
                    c,
                    "SELECT * FROM channels WHERE id=:id AND tenant_id=:t FOR UPDATE",
                    id=cid,
                    t=tenant,
                ),
                "channel_not_found",
            )
            if row["provider"] == "generic_webhook":
                raise DomainError("channel_generic_config_not_supported", 422)
            merged_config = {**(row["config"] or {}), **(config or {})}
            old_credentials = (
                self._decrypt_credentials(row, tenant)
                if row["encrypted_credentials"]
                else {}
            )
            merged_credentials = {**old_credentials, **(credentials or {})}
            adapter_for(row["provider"]).validate(merged_config, merged_credentials)
            encrypted = (
                self._encrypt_credentials(
                    tenant, row["id"], row["provider"], merged_credentials
                )
                if merged_credentials
                else None
            )
            updated = await one(
                c,
                """UPDATE channels SET config=CAST(:config AS jsonb),
                encrypted_credentials=:credentials
                WHERE id=:id AND tenant_id=:t
                RETURNING id,name,agent_id,enabled,provider,config,
                    encrypted_credentials IS NOT NULL AS credentials_configured""",
                id=cid,
                t=tenant,
                config=json.dumps(merged_config),
                credentials=encrypted,
            )
            await audit(c, tenant, actor, "channel.configured", cid, provider=row["provider"])
            return updated

    async def toggle(self, tenant, actor, cid, enabled):
        async with self.engine.begin() as c:
            row = required(
                await one(
                    c,
                    """UPDATE channels SET enabled=:enabled WHERE id=:id AND tenant_id=:t
                    RETURNING id,name,enabled,provider""",
                    id=cid,
                    t=tenant,
                    enabled=enabled,
                )
            )
            await audit(
                c, tenant, actor, "channel.enabled" if enabled else "channel.disabled", cid
            )
            return row

    async def rotate(self, tenant, actor, cid):
        token = secrets.token_urlsafe(32)
        async with self.engine.begin() as c:
            row = required(
                await one(
                    c,
                    "SELECT provider FROM channels WHERE id=:id AND tenant_id=:t FOR UPDATE",
                    id=cid,
                    t=tenant,
                )
            )
            if row["provider"] != "generic_webhook":
                raise DomainError("channel_token_not_supported", 422)
            row = await one(
                c,
                """UPDATE channels SET token_hash=:hash WHERE id=:id AND tenant_id=:t
                RETURNING id,name,enabled,provider""",
                id=cid,
                t=tenant,
                hash=hashlib.sha256(token.encode()).hexdigest(),
            )
            await audit(c, tenant, actor, "channel.token_rotated", cid)
            return {**row, "token": token}

    async def verify_signature(self, cid, raw, timestamp, signature):
        async with self.engine.connect() as c:
            row = await one(
                c,
                "SELECT signing_secret_ref,provider FROM channels WHERE id=:id AND enabled",
                id=cid,
            )
        if not row or not row["signing_secret_ref"]:
            return
        secret = os.environ.get(row["signing_secret_ref"])
        try:
            fresh = abs(time.time() - int(timestamp or "")) <= 300
        except ValueError:
            fresh = False
        if (
            not secret
            or not fresh
            or not signature
            or not hmac.compare_digest(
                signature.encode(),
                hmac.new(secret.encode(), timestamp.encode() + b"." + raw, hashlib.sha256)
                .hexdigest()
                .encode(),
            )
        ):
            raise DomainError("invalid_channel_signature", 401)

    async def ingest(self, cid, token, body):
        async with self.engine.connect() as c:
            channel = await one(
                c,
                """SELECT * FROM channels
                WHERE id=:id AND enabled AND provider='generic_webhook'""",
                id=cid,
            )
        if not channel or not hmac.compare_digest(
            channel["token_hash"], hashlib.sha256(token.encode()).hexdigest()
        ):
            raise DomainError("invalid_channel_token", 401)
        envelope = IncomingEnvelope(
            external_message_id=body.message_id,
            external_conversation_id=body.session_id,
            external_user_id=body.session_id,
            external_thread_id=None,
            text=body.text,
            target={},
        )
        return await self._accept_envelope(channel, envelope)

    async def webhook(
        self,
        cid: UUID,
        provider: str,
        body: bytes,
        query: dict[str, str],
        headers: dict[str, str],
    ):
        adapter = adapter_for(provider)
        async with self.engine.connect() as c:
            channel = await one(
                c,
                """SELECT * FROM channels
                WHERE id=:id AND enabled AND provider=:provider""",
                id=cid,
                provider=provider,
            )
        if not channel:
            raise DomainError("channel_not_found", 404)
        credentials = self._decrypt_credentials(channel, channel["tenant_id"])
        result = await adapter.handle_webhook(
            body=body,
            query=query,
            headers=headers,
            config=channel["config"] or {},
            credentials=credentials,
            cursor=channel.get("provider_cursor"),
        )
        envelopes = list(result.envelopes)
        if result.envelope is not None:
            envelopes.append(result.envelope)
        if envelopes or result.cursor is not None:
            await self._enqueue_inbound_batch(channel, envelopes, result.cursor)
        return result

    async def _enqueue_inbound_batch(
        self,
        channel,
        envelopes: Iterable[IncomingEnvelope],
        cursor: str | None = None,
    ) -> None:
        envelopes = list(envelopes)
        async with self.engine.begin() as c:
            for envelope in envelopes:
                digest = hashlib.sha256(
                    json.dumps(
                        {
                            "id": envelope.external_message_id,
                            "conversation": envelope.external_conversation_id,
                            "text": envelope.text,
                        },
                        sort_keys=True,
                    ).encode()
                ).hexdigest()
                inserted = await one(
                    c,
                    """INSERT INTO channel_inbound_events(
                        id,channel_id,tenant_id,provider,external_message_id,
                        external_conversation_id,external_user_id,external_thread_id,
                        content,target,payload_hash
                    ) VALUES(
                        :id,:channel,:tenant,:provider,:mid,:external,:user,:thread,
                        :content,CAST(:target AS jsonb),:hash
                    )
                    ON CONFLICT(channel_id,external_message_id) DO NOTHING
                    RETURNING id""",
                    id=uuid4(),
                    channel=channel["id"],
                    tenant=channel["tenant_id"],
                    provider=channel["provider"],
                    mid=envelope.external_message_id,
                    external=envelope.external_conversation_id,
                    user=envelope.external_user_id,
                    thread=envelope.external_thread_id,
                    content=envelope.text,
                    target=json.dumps(envelope.target),
                    hash=digest,
                )
                if not inserted:
                    prior = await one(
                        c,
                        """SELECT payload_hash FROM channel_inbound_events
                        WHERE channel_id=:id AND external_message_id=:mid""",
                        id=channel["id"],
                        mid=envelope.external_message_id,
                    )
                    if prior and prior["payload_hash"] != digest:
                        raise DomainError("message_id_conflict", 409)
            if cursor is not None:
                await execute(
                    c,
                    "UPDATE channels SET provider_cursor=:cursor WHERE id=:id",
                    id=channel["id"],
                    cursor=cursor,
                )
            if envelopes:
                await execute(
                    c,
                    "UPDATE channels SET last_activity_at=now(),last_error_code=NULL WHERE id=:id",
                    id=channel["id"],
                )
                # NOTIFY is transactional: the wakeup lands when the enqueue commits.
                await execute(
                    c, "SELECT pg_notify(:ch, :kind)", ch=CHANNEL_WORK_CHANNEL, kind="inbound"
                )

    async def _enqueue_inbound(self, channel, envelope: IncomingEnvelope) -> None:
        await self._enqueue_inbound_batch(channel, [envelope])

    async def replies(self, cid, token, session_id, after):
        async with self.engine.connect() as c:
            channel = await one(
                c,
                """SELECT * FROM channels
                WHERE id=:id AND enabled AND provider='generic_webhook'""",
                id=cid,
            )
            if not channel or not hmac.compare_digest(
                channel["token_hash"], hashlib.sha256(token.encode()).hexdigest()
            ):
                raise DomainError("invalid_channel_token", 401)
            external = self._external_id(cid, session_id)
            return await many(
                c,
                """SELECT m.seq,m.role,m.content,m.created_at FROM conversation_messages m
                JOIN runtime_conversations v ON v.id=m.conversation_id
                WHERE v.tenant_id=:t AND v.external_id=:external
                AND m.seq>:after AND m.role IN ('assistant','human','system')
                ORDER BY m.seq LIMIT 100""",
                t=channel["tenant_id"],
                external=external,
                after=after,
            )

    async def deliveries(self, tenant, cid, page=1):
        async with self.engine.connect() as c:
            required(
                await one(
                    c,
                    "SELECT id FROM channels WHERE id=:id AND tenant_id=:t",
                    id=cid,
                    t=tenant,
                ),
                "channel_not_found",
            )
            total = await one(
                c,
                "SELECT count(*) AS total FROM channel_deliveries WHERE channel_id=:id",
                id=cid,
            )
            items = await many(
                c,
                """SELECT id,message_id,role,seq,status,attempts,http_status,error_code,
                next_attempt_at,created_at,updated_at
                FROM channel_deliveries WHERE channel_id=:id
                ORDER BY created_at DESC,seq ASC LIMIT 20 OFFSET :offset""",
                id=cid,
                offset=(page - 1) * 20,
            )
            return {"items": items, "total": total["total"]}

    async def retry_delivery(self, tenant, actor, cid, did):
        async with self.engine.begin() as c:
            required(
                await one(
                    c,
                    "SELECT id FROM channels WHERE id=:id AND tenant_id=:t",
                    id=cid,
                    t=tenant,
                ),
                "channel_not_found",
            )
            row = required(
                await one(
                    c,
                    """UPDATE channel_deliveries SET status='pending',attempts=0,
                    next_attempt_at=now(),owner=NULL,lease_until=NULL,error_code=NULL,updated_at=now()
                    WHERE id=:id AND channel_id=:channel AND status='failed'
                    RETURNING id""",
                    id=did,
                    channel=cid,
                ),
                "delivery_not_retryable",
            )
            await audit(c, tenant, actor, "channel.delivery_retried", did)
            await execute(
                c, "SELECT pg_notify(:ch, :kind)", ch=CHANNEL_WORK_CHANNEL, kind="delivery"
            )
            return row

    async def _accept_envelope(self, channel, envelope: IncomingEnvelope):
        digest = hashlib.sha256(
            json.dumps(
                {
                    "id": envelope.external_message_id,
                    "conversation": envelope.external_conversation_id,
                    "text": envelope.text,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        async with self.engine.begin() as c:
            await execute(
                c,
                "SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))",
                key="channel-inbound:" + str(channel["id"]),
            )
            current = await one(
                c,
                "SELECT * FROM channels WHERE id=:id AND enabled",
                id=channel["id"],
            )
            if not current:
                raise DomainError("channel_not_found", 404)
            prior = await one(
                c,
                "SELECT * FROM channel_receipts WHERE channel_id=:id AND message_id=:mid",
                id=channel["id"],
                mid=envelope.external_message_id,
            )
            if prior:
                if prior["payload_hash"] != digest:
                    raise DomainError("message_id_conflict", 409)
                return {
                    "conversation_id": prior["conversation_id"],
                    "run_id": prior["run_id"],
                    "duplicate": True,
                }
            mapping = await one(
                c,
                """SELECT * FROM channel_conversations
                WHERE channel_id=:id AND external_conversation_id=:external""",
                id=channel["id"],
                external=envelope.external_conversation_id,
            )
            conv = None
            if mapping:
                conv = await one(
                    c,
                    "SELECT * FROM runtime_conversations WHERE id=:id",
                    id=mapping["conversation_id"],
                )
            if not conv:
                external = self._external_id(channel["id"], envelope.external_conversation_id)
                conv = await one(
                    c,
                    """SELECT * FROM runtime_conversations
                    WHERE tenant_id=:t AND external_id=:external""",
                    t=current["tenant_id"],
                    external=external,
                )
                if not conv:
                    conv = await self.conversations.create(
                        current["tenant_id"],
                        "channel",
                        external,
                        current["agent_id"],
                        c,
                    )
                await execute(
                    c,
                    """INSERT INTO channel_conversations(
                        channel_id,conversation_id,external_conversation_id,external_user_id,
                        external_thread_id,target
                    ) VALUES(
                        :channel,:conversation,:external,:user,:thread,CAST(:target AS jsonb)
                    )
                    ON CONFLICT(channel_id,external_conversation_id) DO UPDATE SET
                        conversation_id=EXCLUDED.conversation_id,
                        target=EXCLUDED.target,
                        external_user_id=EXCLUDED.external_user_id,
                        external_thread_id=EXCLUDED.external_thread_id,
                        updated_at=now()""",
                    channel=channel["id"],
                    conversation=conv["id"],
                    external=envelope.external_conversation_id,
                    user=envelope.external_user_id,
                    thread=envelope.external_thread_id,
                    target=json.dumps(envelope.target),
                )
            else:
                await execute(
                    c,
                    """UPDATE channel_conversations
                    SET target=CAST(:target AS jsonb),updated_at=now()
                    WHERE channel_id=:channel AND external_conversation_id=:external""",
                    channel=channel["id"],
                    external=envelope.external_conversation_id,
                    target=json.dumps(envelope.target),
                )
            result = await self.customer.send(
                current["tenant_id"], "channel", conv["id"], envelope.text, c
            )
            run_id = result["run"]["id"] if result["run"] else None
            await execute(
                c,
                """INSERT INTO channel_receipts(
                    channel_id,message_id,payload_hash,conversation_id,run_id
                ) VALUES(:id,:mid,:hash,:cid,:rid)""",
                id=channel["id"],
                mid=envelope.external_message_id,
                hash=digest,
                cid=conv["id"],
                rid=run_id,
            )
            await execute(
                c,
                "UPDATE channels SET last_activity_at=now(),last_error_code=NULL WHERE id=:id",
                id=channel["id"],
            )
            return {"conversation_id": conv["id"], "run_id": run_id, "duplicate": False}

    def _external_id(self, channel_id, external_conversation_id: str) -> str:
        digest = hashlib.sha256(external_conversation_id.encode()).hexdigest()[:32]
        return "channel:" + str(channel_id) + ":" + digest

    def _vault_spec(self, provider: str) -> dict[str, str]:
        return {"base_url": "channel:" + provider, "protocol": "channel"}

    def _encrypt_credentials(
        self, tenant: str, channel_id, provider: str, credentials: dict[str, Any]
    ) -> str:
        return self.vault.encrypt(
            json.dumps(credentials, ensure_ascii=False, sort_keys=True),
            tenant,
            channel_id,
            self._vault_spec(provider),
            create=True,
        )

    def _decrypt_credentials(self, channel, tenant: str) -> dict[str, Any]:
        raw = channel.get("encrypted_credentials")
        if not raw:
            return {}
        channel_id = channel.get("channel_id") or channel["id"]
        value = self.vault.decrypt(
            raw,
            tenant,
            channel_id,
            self._vault_spec(channel["provider"]),
        )
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            raise DomainError("channel_credentials_invalid", 500) from None
        return parsed if isinstance(parsed, dict) else {}

    async def run_next_inbound(self) -> bool:
        owner = uuid4()
        async with self.engine.begin() as c:
            row = await one(
                c,
                """SELECT e.*,ch.enabled
                FROM channel_inbound_events e
                JOIN channels ch ON ch.id=e.channel_id
                WHERE ((e.status='pending' AND e.next_attempt_at<=now())
                    OR (e.status='running' AND e.lease_until<now()))
                ORDER BY e.next_attempt_at FOR UPDATE OF e SKIP LOCKED LIMIT 1"""
            )
            if not row:
                return False
            if not row["enabled"]:
                await execute(
                    c,
                    """UPDATE channel_inbound_events SET status='failed',
                    error_code='channel_unavailable',updated_at=now() WHERE id=:id""",
                    id=row["id"],
                )
                return True
            if row["attempts"] >= row["max_attempts"]:
                await execute(
                    c,
                    """UPDATE channel_inbound_events SET status='failed',
                    error_code='inbound_attempts_exhausted',updated_at=now() WHERE id=:id""",
                    id=row["id"],
                )
                return True
            await execute(
                c,
                """UPDATE channel_inbound_events SET status='running',owner=:owner,
                lease_until=now()+interval '60 seconds',attempts=attempts+1,updated_at=now()
                WHERE id=:id""",
                id=row["id"],
                owner=owner,
            )
        envelope = IncomingEnvelope(
            external_message_id=row["external_message_id"],
            external_conversation_id=row["external_conversation_id"],
            external_user_id=row["external_user_id"],
            external_thread_id=row["external_thread_id"],
            text=row["content"],
            target=row["target"] or {},
        )
        error_code = None
        result = None
        try:
            result = await self._accept_envelope({"id": row["channel_id"]}, envelope)
        except DomainError as exc:
            error_code = exc.code
        except Exception as exc:  # noqa: BLE001
            logger.warning("channel_inbound_unavailable %s: %s", type(exc).__name__, exc)
            error_code = "channel_inbound_" + type(exc).__name__.lower()
        attempts = row["attempts"] + 1
        status = (
            "processed"
            if error_code is None
            else ("failed" if attempts >= row["max_attempts"] else "pending")
        )
        delay = RETRY_DELAYS[min(attempts - 1, len(RETRY_DELAYS) - 1)]
        async with self.engine.begin() as c:
            await execute(
                c,
                """UPDATE channel_inbound_events SET status=:status,error_code=:error,
                conversation_id=COALESCE(:conversation_id,conversation_id),
                run_id=COALESCE(:run_id,run_id),
                next_attempt_at=now()+CAST(:delay AS int)*interval '1 second',
                processed_at=CASE WHEN :processed THEN now() ELSE processed_at END,
                owner=NULL,lease_until=NULL,updated_at=now()
                WHERE id=:id AND owner=:owner AND status='running'""",
                status=status,
                processed=status == "processed",
                error=error_code,
                conversation_id=result["conversation_id"] if result else None,
                run_id=result["run_id"] if result else None,
                delay=delay,
                id=row["id"],
                owner=owner,
            )
            if status == "failed":
                await execute(
                    c,
                    "UPDATE channels SET last_error_code=:error WHERE id=:id",
                    id=row["channel_id"],
                    error=error_code,
                )
        return True

    async def run_next_delivery(self):
        owner = uuid4()
        async with self.engine.begin() as c:
            row = await one(
                c,
                """SELECT d.*,c.provider,c.config,c.encrypted_credentials,c.enabled,
                    c.tenant_id,cc.target,cc.external_conversation_id
                FROM channel_deliveries d
                JOIN channels c ON c.id=d.channel_id
                LEFT JOIN channel_conversations cc
                    ON cc.channel_id=d.channel_id AND cc.conversation_id=d.conversation_id
                WHERE ((d.status='pending' AND d.next_attempt_at<=now())
                    OR (d.status='running' AND d.lease_until<now()))
                AND NOT EXISTS (
                    SELECT 1 FROM channel_deliveries e
                    WHERE e.channel_id=d.channel_id AND e.message_id=d.message_id
                        AND e.seq<d.seq AND e.status<>'delivered'
                )
                ORDER BY d.next_attempt_at,d.channel_id,d.message_id,d.seq
                FOR UPDATE OF d SKIP LOCKED LIMIT 1"""
            )
            if not row:
                return False
            if not row["enabled"] or row["provider"] == "generic_webhook":
                await execute(
                    c,
                    """UPDATE channel_deliveries SET status='cancelled',
                    error_code='channel_unavailable',updated_at=now() WHERE id=:id""",
                    id=row["id"],
                )
                return True
            if row["attempts"] >= row["max_attempts"]:
                await execute(
                    c,
                    """UPDATE channel_deliveries SET status='failed',
                    error_code='delivery_attempts_exhausted',updated_at=now() WHERE id=:id""",
                    id=row["id"],
                )
                return True
            await execute(
                c,
                """UPDATE channel_deliveries SET status='running',owner=:owner,
                lease_until=now()+interval '60 seconds',attempts=attempts+1,updated_at=now()
                WHERE id=:id""",
                id=row["id"],
                owner=owner,
            )
        started = time.monotonic()
        result = None
        try:
            adapter = adapter_for(row["provider"])
            credentials = self._decrypt_credentials(row, row["tenant_id"])
            result = await adapter.deliver(
                content=row["content"],
                target=row["target"] or {},
                config=row["config"] or {},
                credentials=credentials,
            )
        except Exception as exc:  # noqa: BLE001 - adapter failures must become retryable state
            logger.warning("channel_delivery_unavailable type=%s", type(exc).__name__)
            result = DeliveryResult(
                ok=False,
                error_code="channel_delivery_" + type(exc).__name__.lower(),
            )
        attempts = row["attempts"] + 1
        status = (
            "delivered"
            if result and result.ok
            else ("failed" if attempts >= row["max_attempts"] else "pending")
        )
        error_code = (
            None
            if status == "delivered"
            else (result.error_code if result and result.error_code else "channel_delivery_error")
        )
        if result and result.retry_after is not None:
            delay = max(1, min(int(result.retry_after), 3600))
        else:
            delay = RETRY_DELAYS[min(attempts - 1, len(RETRY_DELAYS) - 1)]
        async with self.engine.begin() as c:
            await execute(
                c,
                """UPDATE channel_deliveries SET status=:status,http_status=:http,
                external_message_id=COALESCE(:external_message_id,external_message_id),
                error_code=:error,
                next_attempt_at=now()+CAST(:delay AS int)*interval '1 second',
                owner=NULL,lease_until=NULL,updated_at=now()
                WHERE id=:id AND owner=:owner AND status='running'""",
                status=status,
                http=result.http_status if result else None,
                external_message_id=result.external_message_id if result else None,
                error=error_code,
                delay=delay,
                id=row["id"],
                owner=owner,
            )
            await execute(
                c,
                """INSERT INTO channel_delivery_attempts(
                    delivery_id,attempt,status,http_status,error_code,duration_ms
                ) VALUES(:id,:attempt,:status,:http,:error,:duration)""",
                id=row["id"],
                attempt=attempts,
                status=status,
                http=result.http_status if result else None,
                error=error_code,
                duration=int((time.monotonic() - started) * 1000),
            )
            if status == "failed":
                await execute(
                    c,
                    "UPDATE channels SET last_error_code=:error WHERE id=:id",
                    id=row["channel_id"],
                    error=error_code,
                )
        return True

    async def _attach_wakeup(self) -> None:
        if self.listener is None or self._wakeup_attached:
            return
        try:
            await self.listener.on(CHANNEL_WORK_CHANNEL, self._on_work)
            self._wakeup_attached = True
        except Exception as exc:  # noqa: BLE001
            # Wakeups are best-effort; the poll fallback keeps the loops correct.
            logger.warning("channel_wakeup_unavailable %s", type(exc).__name__)

    def _on_work(self, _payload=None) -> None:
        # Any queued channel work wakes both loops; each drains its own queue.
        self.delivery_wakeup.set()
        self.inbound_wakeup.set()

    async def _wait_for_work(self, wakeup: asyncio.Event) -> None:
        try:
            await asyncio.wait_for(wakeup.wait(), timeout=self.poll_interval)
        except TimeoutError:
            pass

    async def _delivery_loop(self) -> None:
        while not self.delivery_stopping.is_set():
            # Clear before draining so a notification that lands during the
            # drain is not lost: the next wait returns immediately.
            self.delivery_wakeup.clear()
            try:
                if await self.run_next_delivery():
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("channel_delivery_unavailable %s", type(exc).__name__)
            await self._wait_for_work(self.delivery_wakeup)

    async def _inbound_loop(self) -> None:
        while not self.inbound_stopping.is_set():
            self.inbound_wakeup.clear()
            try:
                if await self.run_next_inbound():
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("channel_inbound_unavailable %s: %s", type(exc).__name__, exc)
            await self._wait_for_work(self.inbound_wakeup)

    async def _serve_pool(self, loop) -> None:
        await self._attach_wakeup()
        tasks = [asyncio.create_task(loop()) for _ in range(self.concurrency)]
        self._pool_tasks.update(tasks)
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._pool_tasks.difference_update(tasks)

    async def stop(self) -> None:
        """Stop both worker pools and wait until no channel work is in flight."""

        self.delivery_stopping.set()
        self.inbound_stopping.set()
        self.delivery_wakeup.set()
        self.inbound_wakeup.set()
        tasks = list(self._pool_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def serve_deliveries(self):
        await self._serve_pool(self._delivery_loop)

    async def serve_inbound(self):
        await self._serve_pool(self._inbound_loop)
