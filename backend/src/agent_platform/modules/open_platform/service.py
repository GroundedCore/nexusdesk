"""Application credentials, authorization and isolated enterprise conversations."""

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from agent_platform.modules.agent_runtime.schemas import RunRequest
from agent_platform.platform.persistence.store import (
    DomainError,
    audit,
    execute,
    many,
    one,
    required,
)


class ApplicationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)
    enabled: bool = True
    agent_ids: list[UUID] = Field(default_factory=list, max_length=100)
    agent_versions: dict[UUID, int] = Field(default_factory=dict, max_length=100)
    knowledge_base_ids: list[UUID] = Field(default_factory=list, max_length=100)
    rpm: int = Field(default=120, ge=1, le=10000)
    max_concurrency: int = Field(default=5, ge=1, le=100)


class ApplicationUpdate(ApplicationInput):
    revision: int = Field(ge=1)


@dataclass(frozen=True)
class AppIdentity:
    app_id: UUID
    key_id: UUID
    tenant: str
    external_user_id: str | None = None


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class OpenPlatform:
    def __init__(self, platform):
        self.p = platform
        self.engine = platform.engine
        from .knowledge import KnowledgeSync
        from .webhooks import Webhooks

        self.knowledge = KnowledgeSync(self)
        self.webhooks = Webhooks(self)
        from .enterprise import Enterprise

        self.enterprise = Enterprise(self)

    async def application(self, c, tenant, app_id, lock=False):
        return required(
            await one(
                c,
                "SELECT * FROM open_applications WHERE tenant_id=:t AND id=:id"
                + (" FOR UPDATE" if lock else ""),
                t=tenant,
                id=app_id,
            ),
            "application_not_found",
        )

    async def save(self, tenant, actor, body, app_id=None):
        async with self.engine.begin() as c:
            if app_id:
                old = await self.application(c, tenant, app_id, True)
                if old["revision"] != body.revision:
                    raise DomainError("application_revision_conflict", 409)
            for aid in set(body.agent_ids):
                row = await one(
                    c,
                    "SELECT id FROM agents WHERE id=:id AND tenant_id=:t AND NOT archived AND published_version IS NOT NULL",
                    id=aid,
                    t=tenant,
                )
                if not row:
                    raise DomainError("agent_not_published_or_available", 422)
            for aid, version in body.agent_versions.items():
                if (
                    aid not in body.agent_ids
                    or version < 1
                    or not await one(
                        c,
                        "SELECT version FROM agent_versions WHERE agent_id=:a AND version=:v",
                        a=aid,
                        v=version,
                    )
                ):
                    raise DomainError("invalid_agent_version_pin", 422)
            for kid in set(body.knowledge_base_ids):
                if not await one(
                    c,
                    "SELECT id FROM knowledge_bases WHERE id=:id AND tenant_id=:t AND enabled",
                    id=kid,
                    t=tenant,
                ):
                    raise DomainError("knowledge_base_not_available", 422)
            values = body.model_dump(exclude={"revision"})
            values["agent_versions"] = json.dumps(
                {str(k): v for k, v in body.agent_versions.items()}
            )
            values["knowledge_base_ids"] = list(set(body.knowledge_base_ids))
            values["agent_ids"] = list(set(body.agent_ids))
            values.update(id=app_id or uuid4(), t=tenant)
            if app_id:
                row = await one(
                    c,
                    """UPDATE open_applications SET name=:name,description=:description,
                    enabled=:enabled,agent_ids=:agent_ids,rpm=:rpm,max_concurrency=:max_concurrency,
                    agent_versions=CAST(:agent_versions AS jsonb),knowledge_base_ids=:knowledge_base_ids,
                    revision=revision+1,updated_at=now() WHERE id=:id AND tenant_id=:t RETURNING *""",
                    **values,
                )
            else:
                row = await one(
                    c,
                    """INSERT INTO open_applications(id,tenant_id,name,description,enabled,agent_ids,rpm,max_concurrency,agent_versions,knowledge_base_ids)
                    VALUES(:id,:t,:name,:description,:enabled,:agent_ids,:rpm,:max_concurrency,CAST(:agent_versions AS jsonb),:knowledge_base_ids) RETURNING *""",
                    **values,
                )
            await audit(
                c,
                tenant,
                actor,
                "open_application.updated" if app_id else "open_application.created",
                row["id"],
            )
            return row

    async def keys(self, tenant, app_id):
        async with self.engine.connect() as c:
            await self.application(c, tenant, app_id)
            return await many(
                c,
                """SELECT id,prefix,created_at,expires_at,revoked_at,
                (revoked_at IS NULL AND (expires_at IS NULL OR expires_at>now())) AS active
                FROM open_api_keys WHERE app_id=:id ORDER BY created_at DESC""",
                id=app_id,
            )

    async def issue_key(
        self, tenant, actor, app_id, expires_at=None, replace_id=None, grace_minutes=0
    ):
        if expires_at and expires_at <= datetime.now(UTC):
            raise DomainError("key_expiry_must_be_future", 422)
        secret = "opk_" + secrets.token_urlsafe(36)
        async with self.engine.begin() as c:
            await self.application(c, tenant, app_id, True)
            if replace_id:
                required(
                    await one(
                        c,
                        """UPDATE open_api_keys SET expires_at=LEAST(COALESCE(expires_at,'infinity'::timestamptz),
                    now()+:grace*interval '1 minute') WHERE app_id=:a AND id=:id AND revoked_at IS NULL
                    AND (expires_at IS NULL OR expires_at>now()) RETURNING id""",
                        a=app_id,
                        id=replace_id,
                        grace=grace_minutes,
                    ),
                    "active_key_not_found",
                )
            count = await one(
                c,
                """SELECT count(*) AS n FROM open_api_keys WHERE app_id=:a AND revoked_at IS NULL
                AND (expires_at IS NULL OR expires_at>now())""",
                a=app_id,
            )
            if count["n"] >= 10:
                raise DomainError("active_key_limit", 409)
            row = await one(
                c,
                """INSERT INTO open_api_keys(id,app_id,token_hash,prefix,expires_at)
                VALUES(:id,:a,:hash,:prefix,:expiry) RETURNING id,prefix,created_at,expires_at""",
                id=uuid4(),
                a=app_id,
                hash=digest(secret),
                prefix=secret[:12] + "…" + secret[-4:],
                expiry=expires_at,
            )
            await audit(
                c,
                tenant,
                actor,
                "open_key.rotated" if replace_id else "open_key.created",
                row["id"],
                app_id=str(app_id),
            )
            return {**row, "key": secret}

    async def revoke(self, tenant, actor, app_id, key_id):
        async with self.engine.begin() as c:
            await self.application(c, tenant, app_id, True)
            required(
                await one(
                    c,
                    "UPDATE open_api_keys SET revoked_at=COALESCE(revoked_at,now()) WHERE app_id=:a AND id=:id RETURNING id",
                    a=app_id,
                    id=key_id,
                )
            )
            await audit(c, tenant, actor, "open_key.revoked", key_id, app_id=str(app_id))
        return {"revoked": True}

    async def authenticate(self, authorization, request):
        if authorization and authorization.startswith("Bearer chat_") and len(authorization) < 200:
            async with self.engine.connect() as c:
                session = await self.enterprise.validate_session(c, token=authorization[7:])
            if not session["app_id"] or not re.fullmatch(
                r"/openapi/v1/(agents|conversations(?:/[0-9a-f-]+/messages)?|runs/[0-9a-f-]+(?:/(events|cancel))?)",
                request.url.path,
            ):
                raise DomainError("chat_scope_required", 403)
            identity = AppIdentity(
                session["app_id"],
                session["id"],
                session["tenant_id"],
                "enterprise:" + str(session["user_id"]),
            )
            return await self.admit(identity, request)
        if (
            not authorization
            or not authorization.startswith("Bearer opk_")
            or len(authorization) > 200
        ):
            raise DomainError("invalid_application_key", 401)
        async with self.engine.connect() as c:
            row = await one(
                c,
                """SELECT k.id,k.app_id,a.tenant_id FROM open_api_keys k JOIN open_applications a ON a.id=k.app_id
                WHERE k.token_hash=:hash AND a.tenant_id=:t""",
                hash=digest(authorization[7:]),
                t=self.p.settings.tenant_id,
            )
        if not row:
            raise DomainError("invalid_application_key", 401)
        identity = AppIdentity(row["app_id"], row["id"], row["tenant_id"])
        return await self.admit(identity, request)

    async def admit(self, identity, request):
        request.state.open_identity = identity
        async with self.engine.begin() as c:
            app = await self.check(c, identity, lock=True)
            window = await one(
                c,
                """INSERT INTO open_rate_windows(app_id,window_start,requests)
                VALUES(:id,date_trunc('minute',now()),1) ON CONFLICT(app_id) DO UPDATE SET
                window_start=EXCLUDED.window_start,requests=CASE WHEN open_rate_windows.window_start=EXCLUDED.window_start
                THEN open_rate_windows.requests+1 ELSE 1 END RETURNING requests""",
                id=identity.app_id,
            )
            exceeded = window["requests"] > app["rpm"]
        if exceeded:
            raise DomainError("application_rate_limit", 429)
        return identity

    async def check(self, c, identity, lock=False):
        app = await self.application(c, identity.tenant, identity.app_id, lock)
        if identity.external_user_id:
            session = await self.enterprise.validate_session(c, session_id=identity.key_id)
            if (
                session["app_id"] != identity.app_id
                or "enterprise:" + str(session["user_id"]) != identity.external_user_id
            ):
                raise DomainError("invalid_enterprise_session", 401)
            if not app["enabled"]:
                raise DomainError("application_disabled", 403)
            return app
        key = await one(
            c,
            """SELECT id FROM open_api_keys WHERE id=:id AND app_id=:a AND revoked_at IS NULL
            AND (expires_at IS NULL OR expires_at>now())""",
            id=identity.key_id,
            a=identity.app_id,
        )
        if not key:
            raise DomainError("invalid_application_key", 401)
        if not app["enabled"]:
            raise DomainError("application_disabled", 403)
        return app

    async def grant(self, c, app, agent_id):
        if agent_id not in app["agent_ids"]:
            raise DomainError("agent_not_authorized", 403)
        if not await one(
            c,
            "SELECT id FROM agents WHERE id=:id AND tenant_id=:t AND NOT archived AND published_version IS NOT NULL",
            id=agent_id,
            t=app["tenant_id"],
        ):
            raise DomainError("agent_not_available", 409)

    async def session(self, c, identity, user, cid, app=None):
        app = app or await self.check(c, identity)
        row = required(
            await one(
                c,
                """SELECT s.*,v.external_id FROM open_sessions s
            JOIN runtime_conversations v ON v.id=s.conversation_id
            WHERE s.conversation_id=:id AND s.app_id=:a AND s.external_user_id=:u AND v.tenant_id=:t""",
                id=cid,
                a=identity.app_id,
                u=user,
                t=identity.tenant,
            ),
            "conversation_not_found",
        )
        await self.grant(c, app, row["agent_id"])
        return row

    async def create_session(self, identity, user, agent_id, external_session):
        async with self.engine.begin() as c:
            app = await self.check(c, identity, lock=True)
            await self.grant(c, app, agent_id)
            old = await one(
                c,
                "SELECT conversation_id,agent_id FROM open_sessions WHERE app_id=:a AND external_user_id=:u AND external_session_id=:s",
                a=identity.app_id,
                u=user,
                s=external_session,
            )
            if old:
                if old["agent_id"] != agent_id:
                    raise DomainError("session_agent_conflict", 409)
                return old
            key = (
                "open:"
                + str(identity.app_id)
                + ":"
                + digest(json.dumps([user, external_session]))[:48]
            )
            conv = await self.p.conversations.create(
                identity.tenant, "app:" + str(identity.app_id), key, agent_id, connection=c
            )
            await execute(
                c,
                """INSERT INTO open_sessions(conversation_id,app_id,external_user_id,external_session_id,agent_id)
                VALUES(:id,:a,:u,:s,:agent)""",
                id=conv["id"],
                a=identity.app_id,
                u=user,
                s=external_session,
                agent=agent_id,
            )
            return {"conversation_id": conv["id"], "agent_id": agent_id}

    async def submit(self, identity, user, cid, message, idempotency, request_id):
        payload_hash = digest(json.dumps([str(cid), message]))
        async with self.engine.begin() as c:
            # Same lock order as all Runtime admission paths.
            await execute(c, "SELECT pg_advisory_xact_lock(71432019)")
            app = await self.check(c, identity, lock=True)
            session = await self.session(c, identity, user, cid, app)
            old = await one(
                c,
                "SELECT * FROM open_runs WHERE app_id=:a AND external_user_id=:u AND idempotency_key=:k",
                a=identity.app_id,
                u=user,
                k=idempotency,
            )
            if old:
                if old["payload_hash"] != payload_hash:
                    raise DomainError("idempotency_conflict", 409)
                return required(
                    await one(
                        c,
                        "SELECT * FROM runtime_runs WHERE id=:id AND tenant_id=:t",
                        id=old["run_id"],
                        t=identity.tenant,
                    )
                )
            count = await one(
                c,
                """SELECT count(*) AS n FROM open_runs o JOIN runtime_runs r ON r.id=o.run_id
                WHERE o.app_id=:a AND r.status IN ('queued','running')""",
                a=identity.app_id,
            )
            if count["n"] >= app["max_concurrency"]:
                raise DomainError("application_concurrency_limit", 429)
            snapshot = dict(self.p.snapshot)
            snapshot["agent"] = await self.p.agents.snapshot(
                identity.tenant,
                session["agent_id"],
                c,
                version=app["agent_versions"].get(str(session["agent_id"])),
            )
            settings = self.p.settings
            row = await self.p.repository.submit(
                identity.tenant,
                RunRequest(conversation_id=session["external_id"], message=message),
                snapshot,
                settings.queue_capacity,
                settings.tenant_capacity,
                settings.queue_timeout_seconds,
                connection=c,
            )
            await execute(
                c,
                """INSERT INTO open_runs(run_id,app_id,external_user_id,conversation_id,idempotency_key,payload_hash,request_id)
                VALUES(:id,:a,:u,:cid,:k,:hash,:request)""",
                id=row["id"],
                a=identity.app_id,
                u=user,
                cid=cid,
                k=idempotency,
                hash=payload_hash,
                request=request_id,
            )
            return row

    async def run(self, identity, user, run_id):
        async with self.engine.connect() as c:
            app = await self.check(c, identity)
            row = required(
                await one(
                    c,
                    "SELECT conversation_id FROM open_runs WHERE run_id=:id AND app_id=:a AND external_user_id=:u",
                    id=run_id,
                    a=identity.app_id,
                    u=user,
                ),
                "run_not_found",
            )
            await self.session(c, identity, user, row["conversation_id"], app)
        return required(await self.p.repository.get(run_id, identity.tenant), "run_not_found")
