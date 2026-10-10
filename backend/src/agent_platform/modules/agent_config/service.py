import json
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent_platform.platform.persistence.store import (
    DomainError,
    audit,
    execute,
    many,
    one,
    required,
)

from .classification import INDUSTRIES, TAGS


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    system_prompt: str = Field(min_length=1, max_length=16000)
    tool_names: list[str] = Field(default_factory=list, max_length=30)
    knowledge_base_ids: list[UUID] = Field(default_factory=list, max_length=30)
    max_model_rounds: int = Field(default=6, ge=1, le=30)
    model_profile_id: UUID | None = None
    model_profile_version: int | None = Field(default=None, ge=1)
    reply_language: Literal["auto", "zh-CN", "zh-TW", "en", "hi"] = "auto"
    # Agent-level switch for the rolling conversation summary. Flows through the
    # existing draft/publish/snapshot chain, so no table change is needed.
    summary_enabled: bool = True

    @model_validator(mode="after")
    def model_binding(self):
        if (self.model_profile_id is None) != (self.model_profile_version is None):
            raise ValueError("model_profile_id and model_profile_version must be set together")
        return self


class AgentDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)
    config: AgentConfig
    industry: str = Field(default="", max_length=64)
    tags: list[Literal["consulting", "after_sales", "operations", "knowledge"]] = Field(
        default_factory=list, max_length=4
    )
    is_example: bool = False

    @field_validator("industry", mode="before")
    @classmethod
    def industry_code(cls, value):
        return INDUSTRIES.get(value, value) if isinstance(value, str) else value

    @field_validator("tags", mode="before")
    @classmethod
    def tag_codes(cls, value):
        return (
            [TAGS.get(tag, tag) if isinstance(tag, str) else tag for tag in value]
            if isinstance(value, list)
            else value
        )


class AgentService:
    def __init__(self, engine, tools, model_catalog=None):
        self.engine, self.tools, self.model_catalog = engine, tools, model_catalog

    async def validate_model(self, tenant, config, c):
        if not config.model_profile_id:
            raise DomainError("agent_model_required", 409)
        if config.model_profile_id:
            snapshot = await self.model_catalog.resolve(
                tenant, config.model_profile_id, config.model_profile_version, c
            )
            if snapshot["spec"]["operation"] != "chat" or (
                config.tool_names
                and any(not r["model"]["tool_calling"] for r in snapshot["routes"])
            ):
                raise DomainError("agent_model_capability_mismatch")

    async def list(self, tenant, q="", include_archived=False):
        async with self.engine.connect() as c:
            return await many(
                c,
                "SELECT * FROM agents WHERE tenant_id=:t AND (:archived OR NOT archived) AND (:q='' OR name ILIKE :pattern) ORDER BY created_at DESC LIMIT 200",
                t=tenant,
                archived=include_archived,
                q=q,
                pattern="%" + q + "%",
            )

    async def get(self, tenant, aid):
        async with self.engine.connect() as c:
            return required(
                await one(
                    c, "SELECT * FROM agents WHERE tenant_id=:t AND id=:id", t=tenant, id=aid
                ),
                "agent_not_found",
            )

    async def catalog(
        self,
        tenant,
        q="",
        include_archived=False,
        status="all",
        page=1,
        page_size=12,
        industries=None,
        tags=None,
        examples_only=False,
        created_by=None,
    ):
        where = """tenant_id=:t AND (:archived OR NOT archived)
            AND (:q='' OR name ILIKE :pattern)
            AND (:status='all' OR (:status='published' AND published_version IS NOT NULL)
                 OR (:status='unpublished' AND published_version IS NULL))
            AND (:all_industries OR industry=ANY(CAST(:industries AS text[])))
            AND (:all_tags OR tags && CAST(:tags AS text[]))
            AND (NOT :examples_only OR is_example)
            AND (:all_creators OR created_by=:creator)"""
        params = {
            "t": tenant,
            "archived": include_archived,
            "q": q,
            "pattern": "%" + q + "%",
            "status": status,
            "industries": [INDUSTRIES.get(v, v) for v in (industries or [])],
            "all_industries": not industries,
            "tags": [TAGS.get(v, v) for v in (tags or [])],
            "all_tags": not tags,
            "examples_only": examples_only,
            "all_creators": created_by is None,
            "creator": created_by or "",
        }
        async with self.engine.connect() as c:
            total = await one(c, "SELECT count(*) AS total FROM agents WHERE " + where, **params)
            items = await many(
                c,
                "SELECT * FROM agents WHERE "
                + where
                + " ORDER BY created_at DESC,id DESC LIMIT :lim OFFSET :off",
                **params,
                lim=page_size,
                off=(page - 1) * page_size,
            )
            return {"items": items, "total": total["total"], "page": page, "page_size": page_size}

    async def create(self, tenant, actor, body):
        async with self.engine.begin() as c:
            row = await one(
                c,
                """INSERT INTO agents(id,tenant_id,name,description,draft,industry,tags,is_example,created_by)
                VALUES(:id,:t,:name,:description,CAST(:draft AS jsonb),:industry,:tags,:is_example,:creator) RETURNING *""",
                id=uuid4(),
                t=tenant,
                name=body.name,
                description=body.description,
                draft=body.config.model_dump_json(),
                industry=body.industry,
                tags=list(dict.fromkeys(body.tags)),
                is_example=body.is_example,
                creator=actor,
            )
            await audit(c, tenant, actor, "agent.created", row["id"])
            return row

    async def update(self, tenant, actor, aid, body, revision):
        async with self.engine.begin() as c:
            row = await one(
                c,
                """UPDATE agents SET name=:name,description=:description,draft=CAST(:draft AS jsonb),
                industry=CASE WHEN :set_industry THEN :industry ELSE industry END,
                tags=CASE WHEN :set_tags THEN CAST(:tags AS text[]) ELSE tags END,
                is_example=CASE WHEN :set_example THEN :is_example ELSE is_example END,
                draft_revision=draft_revision+1,updated_at=now() WHERE id=:id AND tenant_id=:t
                AND draft_revision=:revision RETURNING *""",
                id=aid,
                t=tenant,
                name=body.name,
                description=body.description,
                draft=body.config.model_dump_json(),
                industry=body.industry,
                tags=list(dict.fromkeys(body.tags)),
                is_example=body.is_example,
                set_industry="industry" in body.model_fields_set,
                set_tags="tags" in body.model_fields_set,
                set_example="is_example" in body.model_fields_set,
                revision=revision,
            )
            if not row:
                raise DomainError("agent_missing_or_revision_conflict", 409)
            await audit(c, tenant, actor, "agent.updated", aid, revision=row["draft_revision"])
            return row

    async def versions(self, tenant, aid):
        async with self.engine.connect() as c:
            required(
                await one(
                    c, "SELECT id FROM agents WHERE id=:id AND tenant_id=:t", id=aid, t=tenant
                )
            )
            return await many(
                c, "SELECT * FROM agent_versions WHERE agent_id=:id ORDER BY version DESC", id=aid
            )

    async def publish(self, tenant, actor, aid, revision):
        async with self.engine.begin() as c:
            agent = required(
                await one(
                    c,
                    "SELECT * FROM agents WHERE id=:id AND tenant_id=:t FOR UPDATE",
                    id=aid,
                    t=tenant,
                )
            )
            if agent["draft_revision"] != revision:
                raise DomainError("draft_revision_conflict", 409)
            config = AgentConfig.model_validate(agent["draft"])
            await self.validate_model(tenant, config, c)
            if agent["archived"]:
                raise DomainError("agent_archived", 409)
            tool_snapshot = await self.tools.resolve(tenant, config.tool_names, c)
            for kid in config.knowledge_base_ids:
                if not await one(
                    c,
                    "SELECT id FROM knowledge_bases WHERE id=:id AND tenant_id=:t AND enabled",
                    id=kid,
                    t=tenant,
                ):
                    raise DomainError("knowledge_base_unavailable")
            version = (
                await one(
                    c,
                    "SELECT COALESCE(max(version),0)+1 AS n FROM agent_versions WHERE agent_id=:id",
                    id=aid,
                )
            )["n"]
            await execute(
                c,
                "INSERT INTO agent_versions(agent_id,version,config,tool_snapshot) VALUES(:id,:v,CAST(:config AS jsonb),CAST(:tools AS jsonb))",
                id=aid,
                v=version,
                config=config.model_dump_json(),
                tools=json.dumps(tool_snapshot),
            )
            await execute(
                c,
                "UPDATE agents SET published_version=:v,updated_at=now() WHERE id=:id",
                id=aid,
                v=version,
            )
            await audit(c, tenant, actor, "agent.published", aid, version=version)
            return {"agent_id": aid, "version": version}

    async def rollback(self, tenant, actor, aid, version):
        async with self.engine.begin() as c:
            required(
                await one(
                    c,
                    "SELECT id FROM agents WHERE id=:id AND tenant_id=:t FOR UPDATE",
                    id=aid,
                    t=tenant,
                )
            )
            target = required(
                await one(
                    c,
                    "SELECT version,config FROM agent_versions WHERE agent_id=:id AND version=:v",
                    id=aid,
                    v=version,
                )
            )
            await self.validate_model(tenant, AgentConfig.model_validate(target["config"]), c)
            await execute(
                c,
                "UPDATE agents SET published_version=:v,updated_at=now() WHERE id=:id",
                id=aid,
                v=version,
            )
            await audit(c, tenant, actor, "agent.rolled_back", aid, version=version)
            return {"agent_id": aid, "version": version}

    async def snapshot(self, tenant, aid, c, version=None):
        row = required(
            await one(
                c,
                """SELECT a.id,v.version AS published_version,a.archived,v.config,v.tool_snapshot FROM agents a
            LEFT JOIN agent_versions v ON v.agent_id=a.id AND v.version=COALESCE(CAST(:version AS integer),a.published_version)
            WHERE a.id=:id AND a.tenant_id=:t""",
                id=aid,
                t=tenant,
                version=version,
            ),
            "agent_not_found",
        )
        if not row["config"]:
            raise DomainError("agent_not_published", 409)
        if row["archived"]:
            raise DomainError("agent_archived", 409)
        current = await self.tools.resolve(tenant, row["config"]["tool_names"], c)
        specs = row["tool_snapshot"] if row["tool_snapshot"] is not None else current
        await self.validate_model(tenant, AgentConfig.model_validate(row["config"]), c)
        return {
            "id": str(aid),
            "version": row["published_version"],
            "config": row["config"],
            "tools": specs,
        }

    async def archive(self, tenant, actor, aid, archived, revision):
        async with self.engine.begin() as c:
            row = await one(
                c,
                "UPDATE agents SET archived=:a,draft_revision=draft_revision+1,updated_at=now() WHERE id=:id AND tenant_id=:t AND draft_revision=:r RETURNING *",
                id=aid,
                t=tenant,
                a=archived,
                r=revision,
            )
            if not row:
                raise DomainError("agent_revision_conflict", 409)
            await audit(c, tenant, actor, "agent.archived" if archived else "agent.restored", aid)
            return row

    async def delete(self, tenant, actor, aid, revision):
        async with self.engine.begin() as c:
            # Same admission/conversation lock order as customer message submission.
            await execute(c, "SELECT pg_advisory_xact_lock(71432019)")
            agent = required(
                await one(
                    c,
                    "SELECT * FROM agents WHERE id=:id AND tenant_id=:t FOR UPDATE",
                    id=aid,
                    t=tenant,
                ),
                "agent_not_found",
            )
            if agent["draft_revision"] != revision:
                raise DomainError("agent_revision_conflict", 409)
            if not agent["archived"]:
                raise DomainError("agent_must_be_archived", 409)
            if await one(c, "SELECT id FROM channels WHERE agent_id=:id LIMIT 1", id=aid):
                raise DomainError("agent_referenced_by_channels", 409)
            if await one(
                c, "SELECT id FROM evaluation_cases WHERE agent_id=:id LIMIT 1", id=aid
            ) or await one(
                c, "SELECT id FROM evaluation_reports WHERE agent_id=:id LIMIT 1", id=aid
            ):
                raise DomainError("agent_referenced_by_evaluations", 409)
            conversations = await many(
                c,
                "SELECT id,mode FROM runtime_conversations WHERE agent_id=:id ORDER BY id FOR UPDATE",
                id=aid,
            )
            if await one(
                c,
                """SELECT r.id FROM runtime_runs r
                JOIN runtime_conversations v ON v.id=r.conversation_id
                WHERE v.agent_id=:id AND r.status IN ('queued','running') LIMIT 1""",
                id=aid,
            ):
                raise DomainError("agent_has_active_runs", 409)
            if any(row["mode"] in {"waiting", "human"} for row in conversations) or await one(
                c,
                """SELECT h.id FROM handoffs h JOIN runtime_conversations v ON v.id=h.conversation_id
                WHERE v.agent_id=:id AND h.status IN ('waiting','active') LIMIT 1""",
                id=aid,
            ):
                raise DomainError("agent_has_open_handoffs", 409)
            if await one(
                c,
                """SELECT a.id FROM pending_actions a
                JOIN runtime_conversations v ON v.id=a.conversation_id
                WHERE v.agent_id=:id AND a.status='pending' AND a.expires_at>now() LIMIT 1""",
                id=aid,
            ):
                raise DomainError("agent_has_pending_actions", 409)
            snapshot = {
                "id": str(aid),
                "name": agent["name"],
                "published_version": agent["published_version"],
            }
            await execute(
                c,
                """UPDATE runtime_conversations SET agent_id=NULL, mode='closed',
                deleted_agent=CAST(:snapshot AS jsonb) WHERE agent_id=:id""",
                snapshot=json.dumps(snapshot),
                id=aid,
            )
            await execute(c, "DELETE FROM agent_versions WHERE agent_id=:id", id=aid)
            await execute(c, "DELETE FROM agents WHERE id=:id AND tenant_id=:t", id=aid, t=tenant)
            await audit(
                c,
                tenant,
                actor,
                "agent.deleted",
                aid,
                name=agent["name"],
                revision=revision,
                preserved_conversations=len(conversations),
            )
            return {"deleted": True, "id": aid, "preserved_conversations": len(conversations)}

    async def diff(self, tenant, aid, version):
        async with self.engine.connect() as c:
            row = required(
                await one(
                    c,
                    "SELECT a.draft,v.config FROM agents a JOIN agent_versions v ON v.agent_id=a.id AND v.version=:v WHERE a.id=:id AND a.tenant_id=:t",
                    id=aid,
                    t=tenant,
                    v=version,
                )
            )
            return {
                "version": version,
                "changes": [
                    {"field": k, "published": row["config"].get(k), "draft": row["draft"].get(k)}
                    for k in sorted(set(row["config"]) | set(row["draft"]))
                    if row["config"].get(k) != row["draft"].get(k)
                ],
            }
