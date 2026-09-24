"""Tenant identities and revocable, purpose-bound opaque browser credentials."""

import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent_platform.platform.persistence.store import (
    DomainError,
    audit,
    execute,
    one,
    required,
)
from agent_platform.platform.secrets.vault import CredentialVault

from .idp import IdentityProviders, origin, safe_url
from .service import digest


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    platform_origin: str
    client_id: str = Field(default="", max_length=255)
    issuer: str = Field(default="", max_length=2000)
    organization: str = Field(default="", max_length=255)
    agent_id: str = Field(default="", max_length=100)
    token_auth: Literal["client_secret_basic", "client_secret_post"] = "client_secret_basic"

    _origin = field_validator("platform_origin")(origin)

    @field_validator("issuer")
    @classmethod
    def issuer_url(cls, v):
        if v:
            safe_url(v)
        return v


class ProviderInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["oidc", "wecom", "dingtalk", "feishu"]
    config: ProviderConfig
    secret: str | None = Field(default=None, min_length=1, max_length=4000)
    enabled: bool = False
    workbench: bool = False
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def necessary(self):
        c = self.config
        if (
            self.kind == "oidc"
            and (not c.issuer or not c.client_id)
            or self.kind == "wecom"
            and (not c.organization or not c.agent_id)
            or self.kind == "dingtalk"
            and not c.client_id
            or self.kind == "feishu"
            and (not c.client_id or not c.organization)
        ):
            raise ValueError("provider_fields_required")
        return self


class EmbedInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    enabled: bool = False
    origins: list[str] = Field(default_factory=list, max_length=30)
    provider_ids: list[UUID] = Field(default_factory=list, max_length=20)
    title: str = Field(default="企业助手", min_length=1, max_length=80)
    color: str = Field(default="#6356d9", pattern=r"^#[0-9a-fA-F]{6}$")
    revision: int = Field(default=0, ge=0)

    @field_validator("origins")
    @classmethod
    def origins_list(cls, v):
        return sorted({origin(x) for x in v})


class UserInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    enabled: bool
    role: Literal["admin", "operator", "viewer"] | None = None
    revision: int = Field(ge=1)


class Enterprise:
    def __init__(self, service):
        self.s, self.engine = service, service.engine
        self.tenant = service.p.settings.tenant_id
        self.vault = CredentialVault(service.p.settings.model_credential_key_file)
        self.idp = IdentityProviders()

    async def provider(self, c, pid, lock=False):
        return required(
            await one(
                c,
                "SELECT * FROM enterprise_providers WHERE tenant_id=:t AND id=:id"
                + (" FOR UPDATE" if lock else ""),
                t=self.tenant,
                id=pid,
            ),
            "identity_provider_not_found",
        )

    @staticmethod
    def public_provider(p):
        return {k: v for k, v in p.items() if k != "encrypted_secret"} | {
            "secret_configured": bool(p["encrypted_secret"])
        }

    async def save_provider(self, actor, body, pid=None):
        pid = pid or uuid4()
        async with self.engine.begin() as c:
            old = await one(
                c,
                "SELECT * FROM enterprise_providers WHERE id=:id AND tenant_id=:t FOR UPDATE",
                id=pid,
                t=self.tenant,
            )
            if body.revision != (old["revision"] if old else 0):
                raise DomainError("identity_revision_conflict", 409)
            config = body.config.model_dump()
            if old and (
                old["kind"] != body.kind
                or any(
                    old["config"].get(k) != config[k]
                    for k in ["issuer", "client_id", "organization", "agent_id"]
                )
            ):
                raise DomainError("create_new_provider_for_identity_change", 409)
            spec = {"base_url": str(pid), "protocol": "enterprise-sso"}
            secret = body.secret or (
                self.vault.decrypt(old["encrypted_secret"], self.tenant, pid, spec) if old else None
            )
            if not secret:
                raise DomainError("identity_secret_required", 422)
            encrypted = self.vault.encrypt(secret, self.tenant, pid, spec, create=True)
            row = await one(
                c,
                """INSERT INTO enterprise_providers(id,tenant_id,name,kind,config,encrypted_secret,enabled,workbench)
                VALUES(:id,:t,:name,:kind,CAST(:config AS jsonb),:secret,:enabled,:workbench)
                ON CONFLICT(id) DO UPDATE SET name=:name,config=CAST(:config AS jsonb),encrypted_secret=:secret,
                enabled=:enabled,workbench=:workbench,revision=enterprise_providers.revision+1 RETURNING *""",
                id=pid,
                t=self.tenant,
                name=body.name,
                kind=body.kind,
                config=json.dumps(config),
                secret=encrypted,
                enabled=body.enabled,
                workbench=body.workbench,
            )
            await audit(c, self.tenant, actor, "enterprise_provider.saved", pid, kind=body.kind)
            return self.public_provider(row)

    async def save_embed(self, actor, app_id, body):
        async with self.engine.begin() as c:
            await self.s.application(c, self.tenant, app_id, True)
            old = await one(c, "SELECT * FROM open_embed_configs WHERE app_id=:a", a=app_id)
            if body.revision != (old["revision"] if old else 0):
                raise DomainError("embed_revision_conflict", 409)
            for pid in body.provider_ids:
                await self.provider(c, pid)
            if body.enabled and not body.origins:
                raise DomainError("embed_origin_required", 422)
            row = await one(
                c,
                """INSERT INTO open_embed_configs(app_id,enabled,origins,provider_ids,title,color)
                VALUES(:a,:enabled,:origins,:providers,:title,:color) ON CONFLICT(app_id) DO UPDATE SET
                enabled=:enabled,origins=:origins,provider_ids=:providers,title=:title,color=:color,
                revision=open_embed_configs.revision+1 RETURNING *""",
                a=app_id,
                enabled=body.enabled,
                origins=body.origins,
                providers=body.provider_ids,
                title=body.title,
                color=body.color,
            )
            await audit(c, self.tenant, actor, "open_embed.saved", app_id)
            return row

    async def embed(self, c, app_id, parent_origin):
        app = await self.s.application(c, self.tenant, app_id)
        conf = await one(c, "SELECT * FROM open_embed_configs WHERE app_id=:a", a=app_id)
        if (
            not app["enabled"]
            or not conf
            or not conf["enabled"]
            or parent_origin not in conf["origins"]
        ):
            raise DomainError("embed_origin_not_allowed", 403)
        return conf

    async def resolve(self, c, source, subject, name):
        # Serialize first-login/upsert across API replicas without ever matching display names or email.
        await execute(
            c,
            "SELECT pg_advisory_xact_lock(hashtextextended(:key,0))",
            key=self.tenant + ":" + source + ":" + subject,
        )
        row = await one(
            c,
            """SELECT u.* FROM enterprise_identities i JOIN enterprise_users u ON u.id=i.user_id
            WHERE i.tenant_id=:t AND i.source=:s AND i.subject=:sub""",
            t=self.tenant,
            s=source,
            sub=subject,
        )
        if not row:
            uid = uuid4()
            row = await one(
                c,
                "INSERT INTO enterprise_users(id,tenant_id,name) VALUES(:id,:t,:n) RETURNING *",
                id=uid,
                t=self.tenant,
                n=name,
            )
            await execute(
                c,
                "INSERT INTO enterprise_identities(id,tenant_id,source,subject,user_id) VALUES(:id,:t,:s,:sub,:u)",
                id=uuid4(),
                t=self.tenant,
                s=source,
                sub=subject,
                u=uid,
            )
        return row

    async def issue(self, c, user, app_id=None, provider=None, key_id=None, embed_revision=None):
        if not user["enabled"]:
            raise DomainError("enterprise_user_disabled", 403)
        if not app_id and not user["role"]:
            raise DomainError("workbench_access_pending", 403)
        token = ("chat_" if app_id else "ssow_") + secrets.token_urlsafe(32)
        expiry = datetime.now(UTC) + timedelta(minutes=15 if app_id else 60)
        await execute(
            c,
            """INSERT INTO enterprise_sessions(id,tenant_id,user_id,app_id,provider_id,provider_revision,key_id,embed_revision,token_hash,expires_at)
            VALUES(:id,:t,:u,:a,:p,:rev,:key,:embed,:hash,:expiry)""",
            id=uuid4(),
            t=self.tenant,
            u=user["id"],
            a=app_id,
            p=provider["id"] if provider else None,
            rev=provider["revision"] if provider else None,
            key=key_id,
            embed=embed_revision,
            hash=digest(token),
            expiry=expiry,
        )
        return {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": 900 if app_id else 3600,
            "external_user_id": "enterprise:" + str(user["id"]),
            "user": {"id": user["id"], "name": user["name"], "role": user["role"]},
        }

    async def validate_session(self, c, session_id=None, token=None):
        row = await one(
            c,
            """SELECT s.*,u.enabled AS user_enabled,u.role,u.name FROM enterprise_sessions s
            JOIN enterprise_users u ON u.id=s.user_id WHERE s.tenant_id=:t AND
            (s.id=:id OR s.token_hash=:hash) AND NOT s.revoked AND s.expires_at>now()""",
            t=self.tenant,
            id=session_id,
            hash=digest(token) if token else None,
        )
        if not row or not row["user_enabled"]:
            raise DomainError("invalid_enterprise_session", 401)
        if row["provider_id"]:
            p = await self.provider(c, row["provider_id"])
            if (
                not p["enabled"]
                or p["revision"] != row["provider_revision"]
                or (not row["app_id"] and not p["workbench"])
            ):
                raise DomainError("identity_provider_session_revoked", 401)
        if row["app_id"]:
            embed = await one(
                c, "SELECT * FROM open_embed_configs WHERE app_id=:a", a=row["app_id"]
            )
            if (
                not embed
                or not embed["enabled"]
                or embed["revision"] != row["embed_revision"]
                or row["provider_id"]
                and row["provider_id"] not in embed["provider_ids"]
            ):
                raise DomainError("embed_session_revoked", 401)
        elif not row["role"]:
            raise DomainError("workbench_access_pending", 403)
        if row["key_id"] and not await one(
            c,
            "SELECT id FROM open_api_keys WHERE id=:id AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at>now())",
            id=row["key_id"],
        ):
            raise DomainError("application_key_revoked", 401)
        return row

    async def update_user(self, actor, uid, body):
        async with self.engine.begin() as c:
            from agent_platform.platform.identity.local_admin import protect_local_admin

            await protect_local_admin(c, self.tenant, uid)
            row = await one(
                c,
                """UPDATE enterprise_users SET name=:n,enabled=:enabled,role=:role,revision=revision+1
                WHERE tenant_id=:t AND id=:id AND revision=:rev RETURNING *""",
                t=self.tenant,
                id=uid,
                rev=body.revision,
                n=body.name,
                enabled=body.enabled,
                role=body.role,
            )
            if not row:
                raise DomainError("identity_revision_conflict", 409)
            await execute(c, "UPDATE enterprise_sessions SET revoked=true WHERE user_id=:u", u=uid)
            await audit(
                c,
                self.tenant,
                actor,
                "enterprise_user.updated",
                uid,
                role=body.role,
                enabled=body.enabled,
            )
            return row

    async def link_identity(self, actor, iid, uid):
        async with self.engine.begin() as c:
            identity = required(
                await one(
                    c,
                    "SELECT * FROM enterprise_identities WHERE id=:id AND tenant_id=:t",
                    id=iid,
                    t=self.tenant,
                )
            )
            from agent_platform.platform.identity.local_admin import protect_local_admin

            await protect_local_admin(c, self.tenant, uid, identity["user_id"])
            await execute(
                c,
                "SELECT pg_advisory_xact_lock(hashtextextended(:key,0))",
                key=self.tenant + ":" + identity["source"] + ":" + identity["subject"],
            )
            required(
                await one(
                    c,
                    "SELECT id FROM enterprise_users WHERE id=:id AND tenant_id=:t FOR UPDATE",
                    id=uid,
                    t=self.tenant,
                )
            )
            previous = required(
                await one(
                    c,
                    "SELECT * FROM enterprise_identities WHERE id=:id AND tenant_id=:t FOR UPDATE",
                    id=iid,
                    t=self.tenant,
                )
            )
            await execute(
                c, "UPDATE enterprise_identities SET user_id=:u WHERE id=:id", u=uid, id=iid
            )
            await execute(
                c,
                "UPDATE enterprise_sessions SET revoked=true WHERE user_id=:old OR user_id=:new",
                old=previous["user_id"],
                new=uid,
            )
            await audit(
                c,
                self.tenant,
                actor,
                "enterprise_identity.linked",
                iid,
                user_id=str(uid),
                previous_user_id=str(previous["user_id"]),
            )
        return {"linked": True}
