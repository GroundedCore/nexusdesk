import hashlib
import hmac
import os
import secrets
import time
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from agent_platform.platform.persistence.store import (
    DomainError,
    audit,
    execute,
    many,
    one,
    required,
)


class IncomingMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=8000)


class ChannelService:
    def __init__(self, engine, conversations, customer, agents):
        self.engine, self.conversations, self.customer, self.agents = (
            engine,
            conversations,
            customer,
            agents,
        )

    async def list(self, tenant):
        async with self.engine.connect() as c:
            return await many(
                c,
                "SELECT id,name,agent_id,enabled,created_at FROM channels WHERE tenant_id=:t ORDER BY created_at DESC LIMIT 100",
                t=tenant,
            )

    async def create(self, tenant, actor, name, agent_id, signing_secret_ref=None):
        token = secrets.token_urlsafe(32)
        async with self.engine.begin() as c:
            await self.agents.snapshot(tenant, agent_id, c)
            row = await one(
                c,
                """INSERT INTO channels(id,tenant_id,name,agent_id,token_hash,signing_secret_ref)
                VALUES(:id,:t,:name,:aid,:hash,:ref) RETURNING id,name,agent_id,enabled,signing_secret_ref""",
                id=uuid4(),
                t=tenant,
                name=name,
                aid=agent_id,
                hash=hashlib.sha256(token.encode()).hexdigest(),
                ref=signing_secret_ref,
            )
            await audit(c, tenant, actor, "channel.created", row["id"])
            return {**row, "token": token}

    async def toggle(self, tenant, actor, cid, enabled):
        async with self.engine.begin() as c:
            row = required(
                await one(
                    c,
                    "UPDATE channels SET enabled=:enabled WHERE id=:id AND tenant_id=:t RETURNING id,name,enabled",
                    id=cid,
                    t=tenant,
                    enabled=enabled,
                )
            )
            await audit(c, tenant, actor, "channel.enabled" if enabled else "channel.disabled", cid)
            return row

    async def rotate(self, tenant, actor, cid):
        token = secrets.token_urlsafe(32)
        async with self.engine.begin() as c:
            row = required(
                await one(
                    c,
                    "UPDATE channels SET token_hash=:hash WHERE id=:id AND tenant_id=:t RETURNING id,name,enabled",
                    id=cid,
                    t=tenant,
                    hash=hashlib.sha256(token.encode()).hexdigest(),
                )
            )
            await audit(c, tenant, actor, "channel.token_rotated", cid)
            return {**row, "token": token}

    async def verify_signature(self, cid, raw, timestamp, signature):
        async with self.engine.connect() as c:
            row = await one(
                c, "SELECT signing_secret_ref FROM channels WHERE id=:id AND enabled", id=cid
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
        digest = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
        async with self.engine.begin() as c:
            # Same lock order as normal admission; retries commit receipt and run atomically.
            await execute(c, "SELECT pg_advisory_xact_lock(71432019)")
            channel = await one(c, "SELECT * FROM channels WHERE id=:id AND enabled", id=cid)
            if not channel or not hmac.compare_digest(
                channel["token_hash"], hashlib.sha256(token.encode()).hexdigest()
            ):
                raise DomainError("invalid_channel_token", 401)
            prior = await one(
                c,
                "SELECT * FROM channel_receipts WHERE channel_id=:id AND message_id=:mid",
                id=cid,
                mid=body.message_id,
            )
            if prior:
                if prior["payload_hash"] != digest:
                    raise DomainError("message_id_conflict", 409)
                return {
                    "conversation_id": prior["conversation_id"],
                    "run_id": prior["run_id"],
                    "duplicate": True,
                }
            external = (
                "channel:"
                + str(cid)
                + ":"
                + hashlib.sha256(body.session_id.encode()).hexdigest()[:32]
            )
            conv = await one(
                c,
                "SELECT * FROM runtime_conversations WHERE tenant_id=:t AND external_id=:external",
                t=channel["tenant_id"],
                external=external,
            )
            if not conv:
                conv = await self.conversations.create(
                    channel["tenant_id"], "channel", external, channel["agent_id"], c
                )
            result = await self.customer.send(
                channel["tenant_id"], "channel", conv["id"], body.text, c
            )
            run_id = result["run"]["id"] if result["run"] else None
            await execute(
                c,
                """INSERT INTO channel_receipts(channel_id,message_id,payload_hash,conversation_id,run_id)
                VALUES(:id,:mid,:hash,:cid,:rid)""",
                id=cid,
                mid=body.message_id,
                hash=digest,
                cid=conv["id"],
                rid=run_id,
            )
            return {"conversation_id": conv["id"], "run_id": run_id, "duplicate": False}

    async def replies(self, cid, token, session_id, after):
        async with self.engine.connect() as c:
            channel = await one(c, "SELECT * FROM channels WHERE id=:id AND enabled", id=cid)
            if not channel or not hmac.compare_digest(
                channel["token_hash"], hashlib.sha256(token.encode()).hexdigest()
            ):
                raise DomainError("invalid_channel_token", 401)
            external = (
                "channel:" + str(cid) + ":" + hashlib.sha256(session_id.encode()).hexdigest()[:32]
            )
            return await many(
                c,
                """SELECT m.seq,m.role,m.content,m.created_at FROM conversation_messages m
                JOIN runtime_conversations v ON v.id=m.conversation_id WHERE v.tenant_id=:t AND v.external_id=:external
                AND m.seq>:after AND m.role IN ('assistant','human','system') ORDER BY m.seq LIMIT 100""",
                t=channel["tenant_id"],
                external=external,
                after=after,
            )
