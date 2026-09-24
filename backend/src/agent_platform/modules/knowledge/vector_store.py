"""Milvus REST v2 adapter. PG remains the authority for document visibility."""

import json
import logging
from uuid import uuid4

import httpx

from agent_platform.modules.model_gateway.contracts import EmbedRequest
from agent_platform.platform.persistence.store import (
    DomainError,
    audit,
    execute,
    many,
    one,
    required,
)


class VectorIndex:
    def __init__(self, engine, gateway, settings, client):
        self.engine, self.gateway, self.settings, self.client = engine, gateway, settings, client

    async def request(self, path, body):
        if not self.settings.milvus_url:
            raise DomainError("milvus_not_configured", 503)
        headers = (
            {"Authorization": "Bearer " + self.settings.milvus_token.get_secret_value()}
            if self.settings.milvus_token
            else {}
        )
        try:
            response = await self.client.post(
                self.settings.milvus_url.rstrip("/") + "/v2/vectordb/" + path,
                json=body,
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise DomainError("milvus_unavailable", 502) from exc
        if data.get("code") != 0:
            raise DomainError("milvus_operation_failed", 502)
        return data.get("data")

    async def build(
        self,
        tenant,
        actor,
        kid,
        profile,
        workspace_rows=None,
        publish=True,
        collection_name=None,
        progress=None,
    ):
        snapshot = await self.gateway.catalog.resolve(tenant, profile.id, profile.version)
        if snapshot["spec"]["operation"] != "embed":
            raise DomainError("embedding_profile_required")
        if not self.settings.milvus_url:
            raise DomainError("milvus_not_configured", 503)
        async with self.engine.connect() as c:
            required(
                await one(
                    c,
                    "SELECT id FROM knowledge_bases WHERE tenant_id=:t AND id=:id AND enabled",
                    t=tenant,
                    id=kid,
                )
            )
            rows = await many(
                c,
                "SELECT ch.id,ch.content,ch.version,d.id document_id FROM knowledge_chunks ch JOIN knowledge_documents d ON d.id=ch.document_id WHERE d.kb_id=:kid AND d.enabled AND ch.version=d.current_version ORDER BY ch.id LIMIT 10001",
                kid=kid,
            )
        if workspace_rows is not None:
            rows = workspace_rows
        if not rows or len(rows) > 10000:
            raise DomainError("index_requires_1_to_10000_chunks")
        collection = collection_name or "knowledge_" + uuid4().hex
        dimension = snapshot["routes"][0]["model"]["embedding_dimension"]
        await self.request(
            "collections/create",
            {
                "collectionName": collection,
                "schema": {
                    "autoID": False,
                    "enableDynamicField": True,
                    "fields": [
                        {
                            "fieldName": "id",
                            "dataType": "VarChar",
                            "isPrimary": True,
                            "elementTypeParams": {"max_length": "64"},
                        },
                        {
                            "fieldName": "vector",
                            "dataType": "FloatVector",
                            "elementTypeParams": {"dim": str(dimension)},
                        },
                    ],
                },
                "indexParams": [
                    {
                        "fieldName": "vector",
                        "indexName": "vector_index",
                        "metricType": "COSINE",
                        "params": {"index_type": "AUTOINDEX"},
                    }
                ],
            },
        )
        for start in range(0, len(rows), 16):
            if progress:
                await progress([])
            batch = rows[start : start + 16]
            result = await self.gateway.invoke(
                tenant,
                profile.id,
                profile.version,
                EmbedRequest(inputs=[{"id": str(r["id"]), "text": r["content"]} for r in batch]),
            )
            vectors = result["payload"]["vectors"]
            if len(vectors) != len(batch) or {v["id"] for v in vectors} != {
                str(r["id"]) for r in batch
            }:
                raise DomainError("embedding_result_mismatch", 502)
            await self.request(
                "entities/upsert",
                {
                    "collectionName": collection,
                    "data": [
                        {
                            "id": v["id"],
                            "vector": v["vector"],
                            "tenant_id": tenant,
                            "kb_id": str(kid),
                        }
                        for v in vectors
                    ],
                },
            )
            if progress:
                await progress(batch)
        await self.request("collections/load", {"collectionName": collection})
        if not publish:
            return {"collection": collection, "chunks": len(rows), "dimension": dimension}
        async with self.engine.begin() as c:
            required(
                await one(
                    c,
                    "SELECT id FROM knowledge_bases WHERE tenant_id=:t AND id=:id AND enabled FOR UPDATE",
                    t=tenant,
                    id=kid,
                )
            )
            await execute(
                c,
                "INSERT INTO knowledge_vector_indexes(tenant_id,kb_id,collection_name,profile_id,profile_version,dimension) VALUES(:t,:kid,:name,:p,:v,:dim) ON CONFLICT(kb_id) DO UPDATE SET collection_name=EXCLUDED.collection_name,profile_id=EXCLUDED.profile_id,profile_version=EXCLUDED.profile_version,dimension=EXCLUDED.dimension,created_at=now()",
                t=tenant,
                kid=kid,
                name=collection,
                p=profile.id,
                v=profile.version,
                dim=dimension,
            )
            await audit(c, tenant, actor, "knowledge.vector_index_published", kid)
        return {
            "collection": collection,
            "chunks": len(rows),
            "profile_id": profile.id,
            "profile_version": profile.version,
        }

    async def search(self, tenant, kid, query, limit=5):
        async with self.engine.connect() as c:
            index = await one(
                c,
                "SELECT i.* FROM knowledge_vector_indexes i JOIN knowledge_bases k ON k.id=i.kb_id WHERE i.tenant_id=:t AND i.kb_id=:kid AND k.enabled",
                t=tenant,
                kid=kid,
            )
            if index is None:
                raise DomainError("knowledge_index_not_ready", 409)
        response = await self.gateway.invoke(
            tenant,
            index["profile_id"],
            index["profile_version"],
            EmbedRequest(inputs=[{"id": "query", "text": query}]),
        )
        hits = await self.request(
            "entities/search",
            {
                "collectionName": index["collection_name"],
                "data": [response["payload"]["vectors"][0]["vector"]],
                "annsField": "vector",
                "filter": "tenant_id == "
                + json.dumps(tenant)
                + " and kb_id == "
                + json.dumps(str(kid)),
                "limit": min(100, limit * 5),
                "outputFields": ["id"],
                "consistencyLevel": "Strong",
            },
        )
        if not isinstance(hits, list):
            raise DomainError("invalid_milvus_response", 502)
        ids = [h["id"] for h in hits]
        async with self.engine.connect() as c:
            rows = await many(
                c,
                "SELECT ch.id chunk_id,d.id document_id,d.title,ch.version,ch.content excerpt,ch.source,k.id knowledge_base_id FROM knowledge_chunks ch JOIN knowledge_documents d ON d.id=ch.document_id JOIN knowledge_links l ON l.document_id=d.id JOIN knowledge_bases k ON k.id=l.kb_id WHERE k.tenant_id=:t AND k.id=:kid AND k.enabled AND d.enabled AND l.enabled AND ch.enabled AND ch.version=l.published_version AND ch.id=ANY(CAST(:ids AS uuid[]))",
                t=tenant,
                kid=kid,
                ids=ids,
            )
        found = {str(r["chunk_id"]): r for r in rows}
        return [
            {**found[h["id"]], "score": h.get("distance"), "retrieval": "vector"}
            for h in hits
            if h["id"] in found
        ][:limit]

    async def publish_workspace(self, tenant, task, workspace):
        from agent_platform.modules.evaluation.scoring import ProfileRef

        kid = task["resource_id"]
        async with self.engine.begin() as c:
            await workspace.live_task(c, task)
            base = await workspace.base(c, tenant, kid, True)
            links = await many(
                c,
                "SELECT document_id,draft_version FROM knowledge_links WHERE kb_id=:k AND enabled",
                k=kid,
            )
            manifest = {str(l["document_id"]): l["draft_version"] for l in links}
            revision, settings = base["revision"], base["settings"]
            rows = await many(
                c,
                "SELECT ch.id,ch.content,ch.version,d.id document_id FROM knowledge_links l JOIN knowledge_documents d ON d.id=l.document_id JOIN knowledge_chunks ch ON ch.document_id=d.id AND ch.version=l.draft_version WHERE l.kb_id=:k AND l.enabled AND d.enabled AND ch.enabled ORDER BY ch.id LIMIT 10001",
                k=kid,
            )
        collection = None
        try:
            if settings.get("mode", "keyword") != "keyword":
                if not settings.get("embedding"):
                    raise DomainError("embedding_profile_required")
                profile = ProfileRef.model_validate(settings["embedding"])
                collection = "knowledge_" + uuid4().hex
                async with self.engine.begin() as c:
                    await execute(
                        c,
                        "INSERT INTO knowledge_collection_cleanup(collection_name,tenant_id,delete_after) VALUES(:n,:t,now()+interval '1 hour')",
                        n=collection,
                        t=tenant,
                    )
                counts = {
                    str(row["document_id"]): {"version": row["version"], "completed": 0, "total": 0}
                    for row in rows
                }
                for row in rows:
                    counts[str(row["document_id"])]["total"] += 1

                async def report(batch):
                    for row in batch:
                        counts[str(row["document_id"])]["completed"] += 1
                    async with self.engine.begin() as c:
                        await workspace.live_task(c, task)
                        await execute(
                            c,
                            "UPDATE knowledge_tasks SET progress=CAST(:p AS jsonb),updated_at=now() WHERE id=:id",
                            id=task["id"],
                            p=json.dumps(
                                {
                                    "stage": "embedding",
                                    "revision": revision,
                                    "documents": counts,
                                    "completed": sum(d["completed"] for d in counts.values()),
                                    "total": len(rows),
                                }
                            ),
                        )

                await report([])
                result = await self.build(
                    tenant,
                    "knowledge-worker",
                    kid,
                    profile,
                    rows,
                    False,
                    collection,
                    progress=report,
                )
                collection = result["collection"]
            async with self.engine.begin() as c:
                await workspace.live_task(c, task)
                current = await workspace.base(c, tenant, kid, True)
                if current["revision"] != revision:
                    raise DomainError("knowledge_publication_conflict", 409)
                old = await one(
                    c, "SELECT collection_name FROM knowledge_vector_indexes WHERE kb_id=:k", k=kid
                )
                if old:
                    await execute(
                        c,
                        "INSERT INTO knowledge_collection_cleanup(collection_name,tenant_id,delete_after) VALUES(:n,:t,now()+interval '5 minutes') ON CONFLICT(collection_name) DO NOTHING",
                        n=old["collection_name"],
                        t=tenant,
                    )
                if collection:
                    await execute(
                        c,
                        "DELETE FROM knowledge_collection_cleanup WHERE collection_name=:n",
                        n=collection,
                    )
                for did, version in manifest.items():
                    await execute(
                        c,
                        "UPDATE knowledge_links SET published_version=:v WHERE kb_id=:k AND document_id=:d",
                        v=version,
                        k=kid,
                        d=did,
                    )
                if collection:
                    await execute(
                        c,
                        "INSERT INTO knowledge_vector_indexes(tenant_id,kb_id,collection_name,profile_id,profile_version,dimension) VALUES(:t,:k,:n,:p,:v,:dim) ON CONFLICT(kb_id) DO UPDATE SET collection_name=:n,profile_id=:p,profile_version=:v,dimension=:dim,created_at=now()",
                        t=tenant,
                        k=kid,
                        n=collection,
                        p=profile.id,
                        v=profile.version,
                        dim=result["dimension"],
                    )
                else:
                    await execute(c, "DELETE FROM knowledge_vector_indexes WHERE kb_id=:k", k=kid)
                await execute(
                    c,
                    "UPDATE knowledge_bases SET published_settings=CAST(:s AS jsonb) WHERE id=:k",
                    s=json.dumps(settings),
                    k=kid,
                )
                await audit(
                    c,
                    tenant,
                    "knowledge-worker",
                    "knowledge.publication_completed",
                    kid,
                    revision=revision,
                )
                await workspace.finish(c, task)
        except BaseException:
            if collection:
                try:
                    await self.request("collections/drop", {"collectionName": collection})
                except DomainError:
                    logging.getLogger(__name__).warning(
                        "knowledge collection cleanup pending: %s", collection
                    )
                else:
                    async with self.engine.begin() as c:
                        await execute(
                            c,
                            "DELETE FROM knowledge_collection_cleanup WHERE collection_name=:n",
                            n=collection,
                        )
            raise

    async def cleanup(self, tenant):
        async with self.engine.connect() as c:
            rows = await many(
                c,
                "SELECT collection_name FROM knowledge_collection_cleanup g WHERE tenant_id=:t AND delete_after<now() AND NOT EXISTS(SELECT 1 FROM knowledge_vector_indexes i WHERE i.collection_name=g.collection_name) LIMIT 5",
                t=tenant,
            )
        for row in rows:
            try:
                await self.request("collections/drop", {"collectionName": row["collection_name"]})
            except DomainError:
                async with self.engine.begin() as c:
                    await execute(
                        c,
                        "UPDATE knowledge_collection_cleanup SET attempts=attempts+1,delete_after=now()+interval '10 minutes' WHERE collection_name=:n",
                        n=row["collection_name"],
                    )
            else:
                async with self.engine.begin() as c:
                    await execute(
                        c,
                        "DELETE FROM knowledge_collection_cleanup WHERE collection_name=:n",
                        n=row["collection_name"],
                    )
