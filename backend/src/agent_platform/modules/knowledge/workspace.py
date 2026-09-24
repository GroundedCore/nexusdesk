import asyncio
import hashlib
import json
from pathlib import PurePath
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from agent_platform.platform.persistence.store import (
    DomainError,
    audit,
    execute,
    many,
    one,
    required,
    transaction,
)

from .chunking import ChunkingPolicy, chunk_pages
from .parsers import LOCAL_FORMATS, parse_file
from .service import tokens


class ChunkEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=4000)
    source: dict = Field(default_factory=dict)
    enabled: bool = True


class DraftInput(BaseModel):
    revision: int = Field(ge=1)
    policy: ChunkingPolicy = Field(default_factory=ChunkingPolicy)
    chunks: list[ChunkEdit] | None = Field(default=None, min_length=1, max_length=10000)
    offset: int | None = Field(default=None, ge=0)
    replace_count: int = Field(default=0, ge=0, le=100)
    regenerate: bool = False


class Workspace:
    def __init__(self, engine, settings, client, vector, gateway):
        self.engine, self.settings, self.client = engine, settings, client
        self.vector, self.gateway = vector, gateway

    async def document(self, c, tenant, did, lock=False):
        return required(
            await one(
                c,
                "SELECT * FROM knowledge_documents WHERE id=:id AND tenant_id=:t"
                + (" FOR UPDATE" if lock else ""),
                id=did,
                t=tenant,
            )
        )

    async def base(self, c, tenant, kid, lock=False):
        return required(
            await one(
                c,
                "SELECT * FROM knowledge_bases WHERE id=:id AND tenant_id=:t"
                + (" FOR UPDATE" if lock else ""),
                id=kid,
                t=tenant,
            )
        )

    async def folders(self, tenant):
        async with self.engine.connect() as c:
            return await many(
                c, "SELECT * FROM knowledge_folders WHERE tenant_id=:t ORDER BY name", t=tenant
            )

    async def create_folder(self, tenant, actor, name, parent):
        async with self.engine.begin() as c:
            if parent:
                required(
                    await one(
                        c,
                        "SELECT id FROM knowledge_folders WHERE id=:id AND tenant_id=:t",
                        id=parent,
                        t=tenant,
                    )
                )
            row = await one(
                c,
                "INSERT INTO knowledge_folders(id,tenant_id,name,parent_id) VALUES(:id,:t,:n,:p) RETURNING *",
                id=uuid4(),
                t=tenant,
                n=name,
                p=parent,
            )
            await audit(c, tenant, actor, "knowledge.folder_created", row["id"])
            return row

    async def list_documents(self, tenant, query="", folder=None, offset=0, kind=None, status=None):
        filters = "d.tenant_id=:t AND d.title ILIKE :q AND (CAST(:folder AS uuid) IS NULL OR d.folder_id=CAST(:folder AS uuid)) AND (CAST(:kind AS text) IS NULL OR lower(d.filename) LIKE :kind) AND (CAST(:status AS text) IS NULL OR d.processing=:status)"
        params = {
            "t": tenant,
            "q": "%" + query + "%",
            "folder": str(folder) if folder else None,
            "kind": "%." + kind.lower() if kind else None,
            "status": status,
        }
        async with self.engine.connect() as c:
            total = await one(
                c, "SELECT count(*) total FROM knowledge_documents d WHERE " + filters, **params
            )
            rows = await many(
                c,
                """SELECT d.id,d.title,d.filename,d.file_size,d.folder_id,d.processing,d.error_code,d.current_version,d.enabled,d.updated_at,d.created_by,
                (SELECT count(*) FROM knowledge_chunks ch WHERE ch.document_id=d.id AND ch.version=d.current_version) AS chunk_count,
                (SELECT count(*) FROM knowledge_links l WHERE l.document_id=d.id AND l.enabled) AS library_count,
                (SELECT count(*) FROM knowledge_links l WHERE l.document_id=d.id AND l.enabled AND l.published_version IS NOT NULL) AS published_library_count,
                (SELECT count(*) FROM knowledge_links l WHERE l.document_id=d.id AND l.enabled AND l.published_version IS DISTINCT FROM d.current_version) AS pending_library_count
                FROM knowledge_documents d WHERE """
                + filters
                + " ORDER BY d.updated_at DESC,d.id LIMIT 20 OFFSET :offset",
                **params,
                offset=offset,
            )
            return {"items": rows, "total": total["total"]}

    async def detail(self, tenant, did, offset=0, limit=20, revision=None):
        async with self.engine.connect() as c:
            doc = await self.document(c, tenant, did)
            doc.pop("original", None)
            if revision is not None and revision != doc["current_version"]:
                raise DomainError("document_version_conflict", 409)
            version = await one(
                c,
                "SELECT version,chunking FROM knowledge_versions WHERE document_id=:d AND version=:v",
                d=did,
                v=doc["current_version"],
            )
            chunks = await many(
                c,
                "SELECT id,ordinal,content,source,enabled FROM knowledge_chunks WHERE document_id=:d AND version=:v ORDER BY ordinal LIMIT :limit OFFSET :offset",
                d=did,
                v=doc["current_version"],
                limit=limit,
                offset=offset,
            )
            count = await one(
                c,
                "SELECT count(*) total FROM knowledge_chunks WHERE document_id=:d AND version=:v",
                d=did,
                v=doc["current_version"],
            )
            links = await many(
                c,
                "SELECT l.*,k.name FROM knowledge_links l JOIN knowledge_bases k ON k.id=l.kb_id WHERE l.document_id=:d",
                d=did,
            )
            return {
                **doc,
                "version": version,
                "chunks": chunks,
                "libraries": links,
                "total": count["total"],
                "offset": offset,
                "limit": limit,
            }

    async def source(self, tenant, did, offset=0, revision=None):
        async with self.engine.connect() as c:
            doc = await self.document(c, tenant, did)
            if revision is not None and revision != doc["current_version"]:
                raise DomainError("document_version_conflict", 409)
            row = required(
                await one(
                    c,
                    "SELECT substring(content FROM :start FOR 10000) text,char_length(content) total FROM knowledge_versions WHERE document_id=:d AND version=:v",
                    start=offset + 1,
                    d=did,
                    v=doc["current_version"],
                )
            )
            return {**row, "offset": offset, "limit": 10000, "revision": doc["current_version"]}

    async def enqueue(self, c, tenant, kind, resource, config):
        row = await one(
            c,
            """INSERT INTO knowledge_tasks(id,tenant_id,kind,resource_id,config)
            VALUES(:id,:t,:k,:r,CAST(:config AS jsonb)) ON CONFLICT(tenant_id,kind,resource_id)
            WHERE status IN ('queued','running') DO NOTHING RETURNING *""",
            id=uuid4(),
            t=tenant,
            k=kind,
            r=resource,
            config=json.dumps(config, default=str),
        )
        if not row:
            raise DomainError("knowledge_task_already_active", 409)
        return row

    async def upload(self, tenant, actor, filename, content, folder, mode, kid):
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
        if not filename or len(filename) > 200:
            raise DomainError("invalid_document_filename")
        suffix = PurePath(filename).suffix.lower()
        external = {
            ".png",
            ".jpg",
            ".jpeg",
            ".doc",
            ".ppt",
            ".xls",
            ".rtf",
            ".odt",
            ".ofd",
            ".caj",
            ".xps",
        }
        if suffix not in LOCAL_FORMATS | external:
            raise DomainError("unsupported_document_format", 415)
        if suffix not in LOCAL_FORMATS and not self.settings.knowledge_parser_url:
            raise DomainError("parser_not_configured", 503)
        if not content or len(content) > self.settings.knowledge_upload_bytes:
            raise DomainError("document_size_exceeded", 413)
        if mode == "automatic" and not kid:
            raise DomainError("automatic_requires_knowledge_base")
        async with self.engine.begin() as c:
            if folder:
                required(
                    await one(
                        c,
                        "SELECT id FROM knowledge_folders WHERE id=:id AND tenant_id=:t",
                        id=folder,
                        t=tenant,
                    )
                )
            policy = ChunkingPolicy()
            if kid:
                base = await self.base(c, tenant, kid)
                policy = ChunkingPolicy.model_validate(base["settings"].get("chunking", {}))
            digest = hashlib.sha256(content).hexdigest()
            await execute(
                c, "SELECT pg_advisory_xact_lock(hashtextextended(:key,0))", key=tenant + digest
            )
            existing = await one(
                c,
                "SELECT id FROM knowledge_documents WHERE tenant_id=:t AND content_hash=:hash AND processing<>'deleted'",
                t=tenant,
                hash=digest,
            )
            if existing:
                raise DomainError("document_already_uploaded:" + str(existing["id"]), 409)
            did = uuid4()
            await execute(
                c,
                """INSERT INTO knowledge_documents(id,tenant_id,title,folder_id,filename,file_size,original,processing,created_by,content_hash)
                VALUES(:id,:t,:name,:folder,:name,:size,:raw,'queued',:actor,:hash)""",
                id=did,
                t=tenant,
                name=filename,
                folder=folder,
                size=len(content),
                raw=content,
                hash=digest,
                actor=actor,
            )
            task = await self.enqueue(
                c, tenant, "parse", did, {"mode": mode, "kb_id": kid, "policy": policy.model_dump()}
            )
            await audit(c, tenant, actor, "knowledge.file_uploaded", did)
            return {"id": did, "task_id": task["id"]}

    async def write_version(self, c, doc, pages, policy, chunks):
        version = (
            await one(
                c,
                "SELECT coalesce(max(version),0)+1 AS v FROM knowledge_versions WHERE document_id=:d",
                d=doc["id"],
            )
        )["v"]
        if not chunks:
            raise DomainError("document_has_no_chunks")
        if len(chunks) > 10000:
            raise DomainError("document_too_many_chunks", 413)
        await execute(
            c,
            """INSERT INTO knowledge_versions(document_id,version,content,chunking,pages)
            VALUES(:d,:v,:text,CAST(:policy AS jsonb),CAST(:pages AS jsonb))""",
            d=doc["id"],
            v=version,
            text="\n\n".join(p["text"] for p in pages),
            policy=policy.model_dump_json(),
            pages=json.dumps(pages),
        )
        for ordinal, chunk in enumerate(chunks):
            await execute(
                c,
                """INSERT INTO knowledge_chunks(id,document_id,version,ordinal,content,terms,source,enabled)
                VALUES(:id,:d,:v,:o,:text,to_tsvector('simple',:terms),CAST(:source AS jsonb),:enabled)""",
                id=uuid4(),
                d=doc["id"],
                v=version,
                o=ordinal,
                text=chunk["content"],
                terms=" ".join(tokens(chunk["content"])),
                source=json.dumps(chunk.get("source", {})),
                enabled=chunk.get("enabled", True),
            )
        await execute(
            c,
            "UPDATE knowledge_documents SET current_version=:v,processing='ready',error_code=NULL,updated_at=now() WHERE id=:d",
            v=version,
            d=doc["id"],
        )
        return version

    async def replace_file(self, tenant, actor, did, filename, content):
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
        if (
            not filename
            or len(filename) > 200
            or PurePath(filename).suffix.lower() not in LOCAL_FORMATS
        ):
            raise DomainError("unsupported_document_format", 415)
        if not content or len(content) > self.settings.knowledge_upload_bytes:
            raise DomainError("document_size_exceeded", 413)
        digest = hashlib.sha256(content).hexdigest()
        async with self.engine.begin() as c:
            await execute(
                c, "SELECT pg_advisory_xact_lock(hashtextextended(:key,0))", key=tenant + digest
            )
            doc = await self.document(c, tenant, did, True)
            if doc["processing"] in {"queued", "running", "deleted"}:
                raise DomainError("document_not_replaceable", 409)
            if await one(
                c,
                "SELECT id FROM knowledge_documents WHERE tenant_id=:t AND content_hash=:hash AND processing<>'deleted'",
                t=tenant,
                hash=digest,
            ):
                raise DomainError("document_already_uploaded", 409)
            current = await one(
                c,
                "SELECT chunking FROM knowledge_versions WHERE document_id=:d AND version=:v",
                d=did,
                v=doc["current_version"],
            )
            policy = ChunkingPolicy.model_validate((current or {}).get("chunking") or {})
            task = await self.enqueue(
                c,
                tenant,
                "parse",
                did,
                {"mode": "manual", "kb_id": None, "policy": policy.model_dump()},
            )
            await execute(
                c,
                "UPDATE knowledge_documents SET filename=:name,title=:name,original=:content,file_size=:size,content_hash=:hash,processing='queued',error_code=NULL,updated_at=now() WHERE id=:d",
                name=filename,
                content=content,
                size=len(content),
                hash=digest,
                d=did,
            )
            await audit(c, tenant, actor, "knowledge.file_replaced", did)
            return {"id": did, "task_id": task["id"]}

    async def generate_chunks(self, tenant, did, pages, policy, connection=None, progress=None):
        if policy.strategy != "semantic":
            loop = asyncio.get_running_loop()
            last_report = 0.0

            def report(completed, total):
                nonlocal last_report
                import time

                now = time.monotonic()
                if progress and (completed == total or now - last_report >= 0.5):
                    asyncio.run_coroutine_threadsafe(
                        progress("chunking_pages", completed, total), loop
                    ).result()
                    last_report = now

            return await asyncio.to_thread(chunk_pages, pages, policy, report if progress else None)
        from .semantic import semantic_chunks

        # The source and fixed model version are part of the cache identity.
        key = hashlib.sha256(
            ("semantic-v1:" + policy.model_dump_json() + json.dumps(pages, sort_keys=True)).encode()
        ).hexdigest()
        async with transaction(self.engine, connection) as cache:
            await self.document(cache, tenant, did, True)
            await execute(
                cache,
                "SELECT pg_advisory_xact_lock(hashtextextended(:key,9123))",
                key=str(did) + key,
            )
            row = await one(
                cache,
                "SELECT chunks FROM knowledge_semantic_cache WHERE document_id=:d AND cache_key=:key",
                d=did,
                key=key,
            )
            if row:
                return row["chunks"]
            chunks = await semantic_chunks(pages, policy, self.gateway, tenant, progress=progress)
            if len(chunks) > 10000:
                raise DomainError("document_too_many_chunks", 413)
            await execute(
                cache,
                "INSERT INTO knowledge_semantic_cache(document_id,cache_key,chunks) VALUES(:d,:key,CAST(:chunks AS jsonb))",
                d=did,
                key=key,
                chunks=json.dumps(chunks),
            )
            return chunks

    async def start_preview(self, tenant, actor, did, policy, revision):
        async with self.engine.begin() as c:
            doc = await self.document(c, tenant, did)
            if doc["current_version"] != revision or doc["processing"] != "ready":
                raise DomainError("document_version_conflict", 409)
            task = await self.enqueue(
                c,
                tenant,
                "preview",
                did,
                {"revision": revision, "policy": policy.model_dump(mode="json")},
            )
            await audit(c, tenant, actor, "knowledge.preview_requested", did)
            return {"task_id": task["id"], "status": task["status"]}

    async def progress(self, task, stage, completed=None, total=None):
        async with self.engine.begin() as c:
            await self.live_task(c, task)
            await execute(
                c,
                "UPDATE knowledge_tasks SET progress=CAST(:p AS jsonb),updated_at=now() WHERE id=:id",
                id=task["id"],
                p=json.dumps({"stage": stage, "completed": completed, "total": total}),
            )

    async def preview_task(self, tenant, task):
        did = task["resource_id"]
        revision = task["config"]["revision"]
        policy = ChunkingPolicy.model_validate(task["config"]["policy"])
        await self.progress(task, "chunking")
        async with self.engine.connect() as c:
            doc = await self.document(c, tenant, did)
            if doc["current_version"] != revision or doc["processing"] != "ready":
                raise DomainError("document_version_conflict", 409)
            version = required(
                await one(
                    c,
                    "SELECT pages,content FROM knowledge_versions WHERE document_id=:d AND version=:v",
                    d=did,
                    v=revision,
                )
            )

        async def report(stage, completed=None, total=None):
            await self.progress(task, stage, completed, total)

        chunks = await self.generate_chunks(
            tenant,
            did,
            version["pages"] or [{"page_num": 1, "text": version["content"]}],
            policy,
            progress=report,
        )
        if len(chunks) > 10000:
            raise DomainError("document_too_many_chunks", 413)
        await report("saving_preview", len(chunks), len(chunks))
        async with self.engine.begin() as c:
            await self.live_task(c, task)
            doc = await self.document(c, tenant, did, True)
            if doc["current_version"] != revision or doc["processing"] != "ready":
                raise DomainError("document_version_conflict", 409)
            await execute(
                c,
                "UPDATE knowledge_tasks SET result=CAST(:r AS jsonb) WHERE id=:id",
                id=task["id"],
                r=json.dumps(chunks),
            )
            await self.finish(c, task)

    async def preview_result(self, tenant, tid, offset, limit):
        async with self.engine.connect() as c:
            task = required(
                await one(
                    c,
                    "SELECT resource_id,status,config FROM knowledge_tasks WHERE id=:id AND tenant_id=:t AND kind='preview'",
                    id=tid,
                    t=tenant,
                )
            )
            if task["status"] != "completed":
                raise DomainError("knowledge_preview_not_ready", 409)
            doc = await self.document(c, tenant, task["resource_id"])
            if doc["current_version"] != task["config"]["revision"] or doc["processing"] != "ready":
                raise DomainError("document_version_conflict", 409)
            total = await one(
                c,
                "SELECT jsonb_array_length(result) AS total FROM knowledge_tasks WHERE id=:id",
                id=tid,
            )
            rows = await many(
                c,
                "SELECT value FROM knowledge_tasks, jsonb_array_elements(result) WITH ORDINALITY AS item(value,n) WHERE id=:id AND n>:o AND n<=:end ORDER BY n",
                id=tid,
                o=offset,
                end=offset + limit,
            )
            return {
                "chunks": [r["value"] for r in rows],
                "total": total["total"],
                "offset": offset,
                "limit": limit,
                "policy": task["config"]["policy"],
                "revision": task["config"]["revision"],
            }

    async def preview(self, tenant, did, policy, offset=0, limit=20, revision=None):
        async with self.engine.connect() as c:
            doc = await self.document(c, tenant, did)
            if revision is not None and revision != doc["current_version"]:
                raise DomainError("document_version_conflict", 409)
            v = required(
                await one(
                    c,
                    "SELECT * FROM knowledge_versions WHERE document_id=:d AND version=:v",
                    d=did,
                    v=doc["current_version"],
                )
            )
        pages = v["pages"] or [{"page_num": 1, "text": v["content"]}]
        try:
            chunks = await self.generate_chunks(tenant, did, pages, policy)
        except ValueError as exc:
            raise DomainError(str(exc)) from exc
        if len(chunks) > 10000:
            raise DomainError("document_too_many_chunks", 413)
        return {
            "chunks": chunks[offset : offset + limit],
            "total": len(chunks),
            "offset": offset,
            "limit": limit,
            "revision": doc["current_version"],
            "policy": policy.model_dump(),
        }

    async def save_draft(self, tenant, actor, did, body):
        async with self.engine.begin() as c:
            doc = await self.document(c, tenant, did, True)
            if doc["current_version"] != body.revision:
                raise DomainError("document_version_conflict", 409)
            if doc["processing"] in {"queued", "running"}:
                raise DomainError("document_processing", 409)
            v = required(
                await one(
                    c,
                    "SELECT * FROM knowledge_versions WHERE document_id=:d AND version=:v",
                    d=did,
                    v=body.revision,
                )
            )
            pages = v["pages"] or [{"page_num": 1, "text": v["content"]}]
            try:
                chunks = (
                    [ch.model_dump() for ch in body.chunks]
                    if body.chunks is not None
                    else await self.generate_chunks(tenant, did, pages, body.policy, c)
                )
            except ValueError as exc:
                raise DomainError(str(exc)) from exc
            if body.offset is not None:
                if body.chunks is None or not body.replace_count:
                    raise DomainError("invalid_chunk_page", 422)
                if body.regenerate:
                    existing = await self.generate_chunks(tenant, did, pages, body.policy, c)
                else:
                    if body.policy != ChunkingPolicy.model_validate(v["chunking"]):
                        raise DomainError("chunk_policy_requires_preview", 409)
                    existing = await many(
                        c,
                        "SELECT content,source,enabled FROM knowledge_chunks WHERE document_id=:d AND version=:v ORDER BY ordinal",
                        d=did,
                        v=body.revision,
                    )
                if body.offset + body.replace_count > len(existing):
                    raise DomainError("invalid_chunk_page", 422)
                chunks = (
                    existing[: body.offset] + chunks + existing[body.offset + body.replace_count :]
                )
            version = await self.write_version(c, doc, pages, body.policy, chunks)
            await audit(c, tenant, actor, "knowledge.draft_saved", did, version=version)
            return {"id": did, "version": version}

    async def link(self, tenant, actor, kid, did):
        async with self.engine.begin() as c:
            await self.base(c, tenant, kid, True)
            doc = await self.document(c, tenant, did)
            if doc["processing"] != "ready":
                raise DomainError("document_not_ready", 409)
            await execute(
                c,
                """INSERT INTO knowledge_links(kb_id,document_id,draft_version)
                VALUES(:k,:d,:v) ON CONFLICT(kb_id,document_id) DO UPDATE SET draft_version=:v""",
                k=kid,
                d=did,
                v=doc["current_version"],
            )
            await execute(c, "UPDATE knowledge_bases SET revision=revision+1 WHERE id=:k", k=kid)
            await audit(c, tenant, actor, "knowledge.document_linked", did, kb_id=str(kid))
            return {"version": doc["current_version"]}

    async def library(self, tenant, kid):
        async with self.engine.connect() as c:
            base = await self.base(c, tenant, kid)
            documents = await many(
                c,
                """SELECT l.*,d.title,d.current_version,d.processing,d.enabled document_enabled,
                (SELECT count(*) FROM knowledge_chunks ch WHERE ch.document_id=d.id AND ch.version=l.draft_version AND ch.enabled) chunk_count
                FROM knowledge_links l JOIN knowledge_documents d ON d.id=l.document_id WHERE l.kb_id=:k ORDER BY d.title""",
                k=kid,
            )
            tasks = await many(
                c,
                "SELECT id,status,error_code,created_at,updated_at,progress FROM knowledge_tasks WHERE tenant_id=:t AND kind='index' AND resource_id=:k ORDER BY created_at DESC LIMIT 10",
                t=tenant,
                k=kid,
            )
            readiness = await one(
                c,
                """SELECT
                EXISTS(SELECT 1 FROM knowledge_vector_indexes WHERE kb_id=:k AND tenant_id=:t) vector_ready,
                (SELECT count(*) FROM knowledge_links l JOIN knowledge_documents d ON d.id=l.document_id
                 WHERE l.kb_id=:k AND l.enabled AND d.enabled AND l.published_version IS NOT NULL) published_document_count""",
                k=kid,
                t=tenant,
            )
            active = next((task for task in tasks if task["status"] in {"queued", "running"}), None)
            for doc in documents:
                doc["embedded_chunk_count"] = 0
                doc["embedding_count_source"] = "none"
                if (
                    not doc["enabled"]
                    or not doc["document_enabled"]
                    or base["settings"].get("mode", "keyword") == "keyword"
                ):
                    continue
                if active:
                    progress = active["progress"] or {}
                    item = progress.get("documents", {}).get(str(doc["document_id"]), {})
                    doc["embedding_count_source"] = "building"
                    if (
                        progress.get("revision") == base["revision"]
                        and item.get("version") == doc["draft_version"]
                    ):
                        doc["embedded_chunk_count"] = min(item["completed"], doc["chunk_count"])
                    elif active["status"] == "running" and not progress:
                        doc["embedded_chunk_count"] = None
                elif (
                    readiness["vector_ready"]
                    and doc["published_version"] == doc["draft_version"]
                    and base["published_settings"].get("embedding")
                    == base["settings"].get("embedding")
                    and base["published_settings"].get("mode", "keyword") != "keyword"
                ):
                    doc["embedded_chunk_count"] = doc["chunk_count"]
                    doc["embedding_count_source"] = "published"
            return {**base, "documents": documents, "tasks": tasks, **readiness}

    async def publish(self, tenant, actor, kid, connection=None):
        async with transaction(self.engine, connection) as c:
            base = await self.base(c, tenant, kid, True)
            links = await many(
                c,
                "SELECT document_id,draft_version FROM knowledge_links WHERE kb_id=:k AND enabled ORDER BY document_id",
                k=kid,
            )
            if not links:
                raise DomainError("knowledge_base_empty")
            task = await self.enqueue(
                c,
                tenant,
                "index",
                kid,
                {
                    "revision": base["revision"],
                    "settings": base["settings"],
                    "manifest": {str(l["document_id"]): l["draft_version"] for l in links},
                },
            )
            await audit(c, tenant, actor, "knowledge.publish_requested", kid)
            return {"task_id": task["id"], "status": task["status"]}

    async def mutate_document(self, tenant, actor, did, folder=None, enabled=None, delete=False):
        async with self.engine.begin() as c:
            await self.document(c, tenant, did, True)
            if folder:
                required(
                    await one(
                        c,
                        "SELECT id FROM knowledge_folders WHERE id=:id AND tenant_id=:t",
                        id=folder,
                        t=tenant,
                    )
                )
            if delete:
                # Logical deletion immediately hides every published association and preserves history.
                await execute(
                    c,
                    "UPDATE knowledge_documents SET enabled=false,processing='deleted',original=NULL,updated_at=now() WHERE id=:d",
                    d=did,
                )
                await execute(
                    c, "UPDATE knowledge_links SET enabled=false WHERE document_id=:d", d=did
                )
                await execute(
                    c,
                    "UPDATE knowledge_tasks SET cancel_requested=true,updated_at=now(),status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END WHERE resource_id=:d AND kind IN ('parse','preview') AND status IN ('queued','running')",
                    d=did,
                )
            elif enabled is not None:
                await execute(
                    c,
                    "UPDATE knowledge_documents SET enabled=:enabled,updated_at=now() WHERE id=:d AND processing<>'deleted'",
                    d=did,
                    enabled=enabled,
                )
            else:
                await execute(
                    c,
                    "UPDATE knowledge_documents SET folder_id=:f,updated_at=now() WHERE id=:d",
                    f=folder,
                    d=did,
                )
            await audit(c, tenant, actor, "knowledge.document_changed", did, deleted=delete)
            return {"ok": True}

    async def tasks(self, tenant, document_id=None):
        async with self.engine.connect() as c:
            return await many(
                c,
                "SELECT id,kind,resource_id,status,attempts,error_code,cancel_requested,progress,updated_at FROM knowledge_tasks WHERE tenant_id=:t AND (CAST(:d AS uuid) IS NULL OR resource_id=CAST(:d AS uuid)) ORDER BY created_at DESC LIMIT 100",
                t=tenant,
                d=document_id,
            )

    async def task_action(self, tenant, actor, tid, action):
        async with self.engine.begin() as c:
            task = required(
                await one(
                    c,
                    "SELECT * FROM knowledge_tasks WHERE id=:id AND tenant_id=:t FOR UPDATE",
                    id=tid,
                    t=tenant,
                )
            )
            if action == "retry":
                if task["status"] not in {"failed", "cancelled"}:
                    raise DomainError("job_not_retryable", 409)
                if task["kind"] == "parse":
                    doc = await self.document(c, tenant, task["resource_id"])
                    if doc["processing"] == "deleted":
                        raise DomainError("document_deleted", 409)
                    await execute(
                        c,
                        "UPDATE knowledge_documents SET processing='queued',error_code=NULL WHERE id=:d",
                        d=doc["id"],
                    )
                return await self.enqueue(
                    c, tenant, task["kind"], task["resource_id"], task["config"]
                )
            await execute(
                c,
                "UPDATE knowledge_tasks SET cancel_requested=true,updated_at=now(),status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END WHERE id=:id AND status IN ('queued','running')",
                id=tid,
            )
            if task["kind"] == "parse" and task["status"] == "queued":
                await execute(
                    c,
                    "UPDATE knowledge_documents SET processing='cancelled' WHERE id=:d AND processing<>'deleted'",
                    d=task["resource_id"],
                )
            await audit(c, tenant, actor, "knowledge.task_cancelled", tid)
            return {"ok": True}

    async def run_next(self, tenant):
        owner = uuid4()
        async with self.engine.begin() as c:
            pending = await many(
                c,
                "SELECT id FROM knowledge_bases k WHERE tenant_id=:t AND index_requested AND NOT EXISTS(SELECT 1 FROM knowledge_tasks j WHERE j.resource_id=k.id AND j.kind='index' AND j.status IN ('queued','running')) FOR UPDATE SKIP LOCKED",
                t=tenant,
            )
            for base in pending:
                await self.enqueue(c, tenant, "index", base["id"], {})
                await execute(
                    c, "UPDATE knowledge_bases SET index_requested=false WHERE id=:k", k=base["id"]
                )
            recovered = await many(
                c,
                """UPDATE knowledge_tasks SET status=CASE WHEN cancel_requested THEN 'cancelled' WHEN attempts>=3 THEN 'failed' ELSE 'queued' END,
                owner=NULL,error_code='worker_lease_expired' WHERE tenant_id=:t AND status='running' AND lease_until<now() RETURNING resource_id,kind,status""",
                t=tenant,
            )
            for recovered_task in recovered:
                if recovered_task["kind"] == "parse":
                    await execute(
                        c,
                        "UPDATE knowledge_documents SET processing=:status,error_code='worker_lease_expired' WHERE id=:id AND processing<>'deleted'",
                        status=recovered_task["status"],
                        id=recovered_task["resource_id"],
                    )
            task = await one(
                c,
                """UPDATE knowledge_tasks SET status='running',owner=:o,attempts=attempts+1,lease_until=now()+interval '120 seconds',updated_at=now()
                WHERE id=(SELECT id FROM knowledge_tasks WHERE tenant_id=:t AND status='queued' ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *""",
                o=owner,
                t=tenant,
            )
        if not task:
            await self.vector.cleanup(tenant)
            return False

        async def heartbeat():
            while True:
                await asyncio.sleep(20)
                async with self.engine.begin() as c:
                    live = await one(
                        c,
                        "UPDATE knowledge_tasks SET lease_until=now()+interval '120 seconds' WHERE id=:id AND owner=:o AND status='running' AND lease_until>now() AND NOT cancel_requested RETURNING id",
                        id=task["id"],
                        o=owner,
                    )
                if not live:
                    return

        heart = asyncio.create_task(heartbeat())
        try:
            async with asyncio.timeout(600):
                if task["kind"] == "parse":
                    await self.parse_task(tenant, task)
                elif task["kind"] == "preview":
                    await self.preview_task(tenant, task)
                else:
                    await self.vector.publish_workspace(tenant, task, self)
        except Exception as exc:  # noqa: BLE001 -- task errors are persisted without provider payloads
            async with self.engine.begin() as c:
                changed = await one(
                    c,
                    "UPDATE knowledge_tasks SET status=CASE WHEN cancel_requested THEN 'cancelled' ELSE 'failed' END,error_code=:error,updated_at=now() WHERE id=:id AND owner=:o AND status='running' RETURNING status",
                    id=task["id"],
                    o=owner,
                    error=getattr(exc, "code", "knowledge_processing_failed"),
                )
                if changed and task["kind"] == "parse":
                    await execute(
                        c,
                        "UPDATE knowledge_documents SET processing=:status,error_code=:e WHERE id=:d AND processing<>'deleted'",
                        status=changed["status"],
                        e=getattr(exc, "code", "knowledge_processing_failed"),
                        d=task["resource_id"],
                    )
        finally:
            heart.cancel()
            await asyncio.gather(heart, return_exceptions=True)
        return True

    async def live_task(self, c, task):
        live = required(
            await one(
                c,
                "SELECT * FROM knowledge_tasks WHERE id=:id AND owner=:o AND status='running' AND lease_until>now() FOR UPDATE",
                id=task["id"],
                o=task["owner"],
            ),
            "knowledge_task_ownership_lost",
        )
        if live["cancel_requested"]:
            raise DomainError("knowledge_task_cancelled", 409)

    async def finish(self, c, task):
        await execute(
            c,
            "UPDATE knowledge_tasks SET status='completed',lease_until=NULL,updated_at=now(),error_code=NULL WHERE id=:id",
            id=task["id"],
        )

    async def parse_task(self, tenant, task):
        async with self.engine.begin() as c:
            await self.live_task(c, task)
            doc = await self.document(c, tenant, task["resource_id"], True)
            if doc["processing"] == "deleted":
                raise DomainError("document_deleted", 409)
            await execute(
                c,
                "UPDATE knowledge_documents SET processing='running',error_code=NULL WHERE id=:id",
                id=doc["id"],
            )
        if self.settings.knowledge_s3_bucket:
            from agent_platform.platform.storage.objects import S3Archive

            await S3Archive(self.settings).put(
                "knowledge/"
                + tenant
                + "/"
                + str(doc["id"])
                + "/"
                + (doc["content_hash"] or "original"),
                bytes(doc["original"]),
            )
        await self.progress(task, "parsing")
        try:
            pages = await asyncio.to_thread(
                parse_file,
                doc["filename"],
                bytes(doc["original"]),
                max_chars=self.settings.knowledge_extracted_chars,
            )
        except DomainError as exc:
            if (
                exc.code not in {"external_parser_required", "ocr_required"}
                or not self.settings.knowledge_parser_url
            ):
                raise
            from .ingestion import ParsedPage

            async with self.client.stream(
                "POST",
                self.settings.knowledge_parser_url.rstrip("/") + "/parse",
                files={"file": (doc["filename"], bytes(doc["original"]))},
                timeout=90,
            ) as response:
                response.raise_for_status()
                raw = bytearray()
                async for block in response.aiter_bytes():
                    raw.extend(block)
                    if len(raw) > self.settings.knowledge_upload_bytes:
                        raise DomainError("parser_output_too_large")
            pages = [ParsedPage.model_validate(p).model_dump() for p in json.loads(raw)["pages"]]
            if sorted(p["page_num"] for p in pages) != list(range(1, len(pages) + 1)):
                raise DomainError("invalid_parser_page_numbers")
            pages.sort(key=lambda p: p["page_num"])
        if (
            len(pages) > 10000
            or sum(len(p["text"]) for p in pages) > self.settings.knowledge_extracted_chars
        ):
            raise DomainError("extracted_text_too_large", 413)
        policy = ChunkingPolicy.model_validate(task["config"]["policy"])

        async def report(stage, completed=None, total=None):
            await self.progress(task, stage, completed, total)

        await report("chunking")
        chunks = await self.generate_chunks(tenant, doc["id"], pages, policy, progress=report)
        await report("saving_preview", len(chunks), len(chunks))
        async with self.engine.begin() as c:
            await self.live_task(c, task)
            doc = await self.document(c, tenant, doc["id"], True)
            version = await self.write_version(c, doc, pages, policy, chunks)
            kid = task["config"].get("kb_id")
            if kid:
                await self.base(c, tenant, kid, True)
                await execute(
                    c,
                    "INSERT INTO knowledge_links(kb_id,document_id,draft_version) VALUES(:k,:d,:v) ON CONFLICT(kb_id,document_id) DO UPDATE SET draft_version=:v",
                    k=kid,
                    d=doc["id"],
                    v=version,
                )
                await execute(
                    c, "UPDATE knowledge_bases SET revision=revision+1 WHERE id=:k", k=kid
                )
            if kid and task["config"]["mode"] == "automatic":
                await execute(
                    c, "UPDATE knowledge_bases SET index_requested=true WHERE id=:k", k=kid
                )
            await self.finish(c, task)
