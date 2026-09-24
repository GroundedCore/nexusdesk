import re
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

from .chunking import ChunkingPolicy, chunk_document


def tokens(text):
    result = []
    for term in re.findall(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]+", text.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", term):
            result.extend(term[i : i + 2] for i in range(max(1, len(term) - 1)))
        else:
            result.append(term)
    return list(dict.fromkeys(result))


class DocumentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=200000)
    chunking: ChunkingPolicy = Field(default_factory=ChunkingPolicy)


class KnowledgeService:
    def __init__(self, engine):
        self.engine = engine

    async def bases(self, tenant):
        async with self.engine.connect() as c:
            return await many(
                c,
                """SELECT k.*,count(d.document_id) AS document_count FROM knowledge_bases k
                LEFT JOIN knowledge_links d ON d.kb_id=k.id WHERE k.tenant_id=:t
                GROUP BY k.id ORDER BY k.created_at DESC LIMIT 200""",
                t=tenant,
            )

    async def create_base(self, tenant, actor, name):
        async with self.engine.begin() as c:
            row = await one(
                c,
                "INSERT INTO knowledge_bases(id,tenant_id,name) VALUES(:id,:t,:n) RETURNING *",
                id=uuid4(),
                t=tenant,
                n=name,
            )
            await audit(c, tenant, actor, "knowledge_base.created", row["id"])
            return row

    async def documents(self, tenant, kid):
        async with self.engine.connect() as c:
            required(
                await one(
                    c,
                    "SELECT id FROM knowledge_bases WHERE id=:id AND tenant_id=:t",
                    id=kid,
                    t=tenant,
                )
            )
            return await many(
                c,
                """SELECT d.*,v.content FROM knowledge_documents d JOIN knowledge_versions v
                ON v.document_id=d.id AND v.version=d.current_version WHERE d.kb_id=:id
                ORDER BY d.created_at DESC LIMIT 100""",
                id=kid,
            )

    async def save_document(
        self, tenant, actor, kid, body, did=None, expected_version=None, connection=None
    ):
        async with transaction(self.engine, connection) as c:
            required(
                await one(
                    c,
                    "SELECT id FROM knowledge_bases WHERE id=:id AND tenant_id=:t",
                    id=kid,
                    t=tenant,
                )
            )
            if did:
                doc = required(
                    await one(
                        c,
                        "SELECT * FROM knowledge_documents WHERE id=:id AND kb_id=:kid FOR UPDATE",
                        id=did,
                        kid=kid,
                    )
                )
                if doc["current_version"] != expected_version:
                    raise DomainError("document_version_conflict", 409)
                version = doc["current_version"] + 1
                await execute(
                    c,
                    "UPDATE knowledge_documents SET title=:title,current_version=:v WHERE id=:id",
                    title=body.title,
                    v=version,
                    id=did,
                )
            else:
                did, version = uuid4(), 1
                await execute(
                    c,
                    "INSERT INTO knowledge_documents(id,kb_id,tenant_id,title) VALUES(:id,:kid,:tenant,:title)",
                    id=did,
                    kid=kid,
                    tenant=tenant,
                    title=body.title,
                )
            await execute(
                c,
                "INSERT INTO knowledge_versions(document_id,version,content,chunking) VALUES(:id,:v,:content,CAST(:chunking AS jsonb))",
                id=did,
                v=version,
                content=body.content,
                chunking=body.chunking.model_dump_json(),
            )
            for ordinal, item in enumerate(chunk_document(body.content, body.chunking)):
                chunk = item["content"]
                await execute(
                    c,
                    """INSERT INTO knowledge_chunks(id,document_id,version,ordinal,content,terms)
                    VALUES(:id,:did,:v,:o,:content,to_tsvector('simple',:terms))""",
                    id=uuid4(),
                    did=did,
                    v=version,
                    o=ordinal,
                    content=chunk,
                    terms=" ".join(tokens(chunk)),
                )
            await execute(
                c,
                "INSERT INTO knowledge_links(kb_id,document_id,draft_version,published_version) VALUES(:kid,:did,:v,:v) ON CONFLICT(kb_id,document_id) DO UPDATE SET draft_version=:v,published_version=:v",
                kid=kid,
                did=did,
                v=version,
            )
            await audit(c, tenant, actor, "knowledge.document_saved", did, version=version)
            return {"id": did, "version": version}

    async def toggle(self, tenant, actor, did, enabled):
        async with self.engine.begin() as c:
            row = required(
                await one(
                    c,
                    """UPDATE knowledge_documents d SET enabled=:enabled
                FROM knowledge_bases k WHERE d.kb_id=k.id AND k.tenant_id=:t AND d.id=:id RETURNING d.*""",
                    id=did,
                    t=tenant,
                    enabled=enabled,
                )
            )
            await audit(
                c,
                tenant,
                actor,
                "knowledge.document_enabled" if enabled else "knowledge.document_disabled",
                did,
            )
            return row

    async def search(self, tenant, query, kb_ids=None, limit=5):
        terms = tokens(query)[:24]
        if not terms or kb_ids == []:
            return []
        # Tokens are restricted to words / Han bigrams; never interpolate raw user tsquery syntax.
        tsquery = " | ".join(terms)
        async with self.engine.connect() as c:
            return await many(
                c,
                """SELECT ch.id AS chunk_id,d.id AS document_id,d.title,
                ch.version,ch.content AS excerpt,ch.source,k.id AS knowledge_base_id,
                ts_rank_cd(ch.terms,to_tsquery('simple',:q)) AS score
                FROM knowledge_chunks ch JOIN knowledge_documents d ON d.id=ch.document_id
                JOIN knowledge_links l ON l.document_id=d.id JOIN knowledge_bases k ON k.id=l.kb_id WHERE k.tenant_id=:t AND k.enabled AND d.enabled AND l.enabled AND ch.enabled
                AND ch.version=l.published_version AND ch.terms @@ to_tsquery('simple',:q)
                AND (:all_bases OR k.id=ANY(CAST(:ids AS uuid[])))
                ORDER BY score DESC,ch.id LIMIT :limit""",
                q=tsquery,
                t=tenant,
                all_bases=kb_ids is None,
                ids=kb_ids or [],
                limit=limit,
            )
