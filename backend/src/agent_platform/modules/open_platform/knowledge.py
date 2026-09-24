"""Application-owned text/Markdown synchronization, staged before library publication."""

import hashlib
import json
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from agent_platform.modules.knowledge.chunking import ChunkingPolicy, chunk_pages
from agent_platform.platform.persistence.store import (
    DomainError,
    audit,
    execute,
    many,
    one,
    required,
)


class SyncInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=200000)
    expected_version: int = Field(default=0, ge=0)


class KnowledgeSync:
    def __init__(self, service):
        self.s = service
        self.p = service.p

    async def authorize(self, c, identity, kid):
        app = await self.s.check(c, identity, lock=True)
        if kid not in app["knowledge_base_ids"]:
            raise DomainError("knowledge_base_not_authorized", 403)
        return required(
            await one(
                c,
                "SELECT * FROM knowledge_bases WHERE id=:id AND tenant_id=:t AND enabled FOR UPDATE",
                id=kid,
                t=identity.tenant,
            ),
            "knowledge_base_not_found",
        )

    async def save(self, identity, kid, external, body):
        payload_hash = hashlib.sha256(
            json.dumps([body.title, body.content], ensure_ascii=False).encode()
        ).hexdigest()
        async with self.s.engine.begin() as c:
            await self.authorize(c, identity, kid)
            mapping = await one(
                c,
                "SELECT * FROM open_synced_documents WHERE app_id=:a AND kb_id=:k AND external_id=:e",
                a=identity.app_id,
                k=kid,
                e=external,
            )
            if mapping:
                doc = await self.p.knowledge_workspace.document(
                    c, identity.tenant, mapping["document_id"], True
                )
                if (
                    not mapping["deleted"]
                    and mapping["payload_hash"] == payload_hash
                    and doc["current_version"] == mapping["synced_version"]
                ):
                    return {
                        "document_id": doc["id"],
                        "version": doc["current_version"],
                        "unchanged": True,
                    }
                if body.expected_version != doc["current_version"]:
                    raise DomainError("document_version_conflict", 409)
                if doc["processing"] in {"queued", "running"}:
                    raise DomainError("document_busy", 409)
            else:
                if body.expected_version != 0:
                    raise DomainError("document_version_conflict", 409)
                doc = await one(
                    c,
                    """INSERT INTO knowledge_documents(id,kb_id,tenant_id,title,filename,processing,created_by)
                    VALUES(:id,:k,:t,:title,:title,'ready',:actor) RETURNING *""",
                    id=uuid4(),
                    k=kid,
                    t=identity.tenant,
                    title=body.title,
                    actor="app:" + str(identity.app_id),
                )
            policy = ChunkingPolicy()
            pages = [{"page_num": 1, "text": body.content}]
            version = await self.p.knowledge_workspace.write_version(
                c, doc, pages, policy, chunk_pages(pages, policy)
            )
            await execute(
                c,
                "UPDATE knowledge_documents SET title=:title,enabled=true,processing='ready' WHERE id=:id",
                title=body.title,
                id=doc["id"],
            )
            await execute(
                c,
                """INSERT INTO knowledge_links(kb_id,document_id,draft_version,enabled) VALUES(:k,:d,:v,true)
                ON CONFLICT(kb_id,document_id) DO UPDATE SET draft_version=:v,enabled=true,
                published_version=CASE WHEN :restore THEN NULL ELSE knowledge_links.published_version END""",
                k=kid,
                d=doc["id"],
                v=version,
                restore=bool(mapping and mapping["deleted"]),
            )
            await execute(
                c,
                """INSERT INTO open_synced_documents(app_id,kb_id,external_id,document_id,payload_hash,synced_version)
                VALUES(:a,:k,:e,:d,:hash,:v) ON CONFLICT(app_id,kb_id,external_id) DO UPDATE SET payload_hash=:hash,synced_version=:v,deleted=false,updated_at=now()""",
                a=identity.app_id,
                k=kid,
                e=external,
                d=doc["id"],
                hash=payload_hash,
                v=version,
            )
            await execute(c, "UPDATE knowledge_bases SET revision=revision+1 WHERE id=:k", k=kid)
            await audit(
                c,
                identity.tenant,
                "app:" + str(identity.app_id),
                "open_knowledge.synced",
                doc["id"],
                version=version,
            )
            return {"document_id": doc["id"], "version": version, "unchanged": False}

    async def documents(self, identity, kid, offset):
        async with self.s.engine.begin() as c:
            await self.authorize(c, identity, kid)
            return await many(
                c,
                """SELECT s.external_id,s.document_id,s.deleted,d.title,d.current_version AS version,
                l.published_version,s.updated_at FROM open_synced_documents s JOIN knowledge_documents d ON d.id=s.document_id
                LEFT JOIN knowledge_links l ON l.kb_id=s.kb_id AND l.document_id=s.document_id
                WHERE s.app_id=:a AND s.kb_id=:k ORDER BY s.external_id LIMIT 100 OFFSET :off""",
                a=identity.app_id,
                k=kid,
                off=offset,
            )

    async def delete(self, identity, kid, external, version):
        async with self.s.engine.begin() as c:
            await self.authorize(c, identity, kid)
            mapping = required(
                await one(
                    c,
                    "SELECT * FROM open_synced_documents WHERE app_id=:a AND kb_id=:k AND external_id=:e",
                    a=identity.app_id,
                    k=kid,
                    e=external,
                ),
                "synced_document_not_found",
            )
            doc = await self.p.knowledge_workspace.document(
                c, identity.tenant, mapping["document_id"], True
            )
            if doc["current_version"] != version:
                raise DomainError("document_version_conflict", 409)
            if not mapping["deleted"]:
                # Disable only this application's association; never remove other libraries' links.
                await execute(
                    c,
                    "UPDATE knowledge_links SET enabled=false WHERE kb_id=:k AND document_id=:d",
                    k=kid,
                    d=doc["id"],
                )
                await execute(
                    c,
                    "UPDATE open_synced_documents SET deleted=true,updated_at=now() WHERE app_id=:a AND kb_id=:k AND external_id=:e",
                    a=identity.app_id,
                    k=kid,
                    e=external,
                )
                await execute(
                    c, "UPDATE knowledge_bases SET revision=revision+1 WHERE id=:k", k=kid
                )
                await audit(
                    c,
                    identity.tenant,
                    "app:" + str(identity.app_id),
                    "open_knowledge.removed",
                    doc["id"],
                )
            return {"deleted": True}

    async def publish(self, identity, kid, key):
        async with self.s.engine.begin() as c:
            await self.authorize(c, identity, kid)
            old = await one(
                c,
                "SELECT * FROM open_sync_tasks WHERE app_id=:a AND idempotency_key=:key",
                a=identity.app_id,
                key=key,
            )
            if old:
                if old["kb_id"] != kid:
                    raise DomainError("idempotency_conflict", 409)
                return await one(
                    c,
                    "SELECT id AS task_id,status,error_code FROM knowledge_tasks WHERE id=:id",
                    id=old["task_id"],
                )
            result = await self.p.knowledge_workspace.publish(
                identity.tenant, "app:" + str(identity.app_id), kid, connection=c
            )
            await execute(
                c,
                "INSERT INTO open_sync_tasks(task_id,app_id,kb_id,idempotency_key) VALUES(:id,:a,:k,:key)",
                id=result["task_id"],
                a=identity.app_id,
                k=kid,
                key=key,
            )
            return result

    async def task(self, identity, tid):
        async with self.s.engine.begin() as c:
            row = required(
                await one(
                    c,
                    "SELECT kb_id FROM open_sync_tasks WHERE task_id=:id AND app_id=:a",
                    id=tid,
                    a=identity.app_id,
                ),
                "task_not_found",
            )
            await self.authorize(c, identity, row["kb_id"])
            return required(
                await one(
                    c,
                    "SELECT id,status,error_code,progress,created_at,updated_at FROM knowledge_tasks WHERE id=:id AND tenant_id=:t",
                    id=tid,
                    t=identity.tenant,
                ),
                "task_not_found",
            )
