import asyncio
import time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from agent_platform.modules.evaluation.scoring import ProfileRef
from agent_platform.modules.model_gateway.contracts import RerankRequest
from agent_platform.platform.persistence.store import DomainError, many

from .chunking import ChunkingPolicy


class LibrarySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["keyword", "vector", "hybrid"] = "keyword"
    embedding: ProfileRef | None = None
    rerank: ProfileRef | None = None
    candidates: int = Field(default=20, ge=5, le=100)
    top_k: int = Field(default=5, ge=1, le=20)
    context_chars: int = Field(default=12000, ge=1000, le=80000)
    chunking: ChunkingPolicy = Field(default_factory=ChunkingPolicy)


class SearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    kb_ids: list[UUID] = Field(min_length=1, max_length=20)
    mode: Literal["keyword", "vector", "hybrid"] | None = None
    candidates: int | None = Field(default=None, ge=5, le=100)
    top_k: int | None = Field(default=None, ge=1, le=20)
    rerank: bool = True
    min_vector_score: float | None = Field(default=None, ge=-1, le=1, allow_inf_nan=False)
    min_rerank_score: float | None = Field(default=None, allow_inf_nan=False)


def fuse(rankings):
    found = {}
    for channel, rows in rankings:
        for rank, row in enumerate(rows, 1):
            key = str(row["chunk_id"])
            hit = found.setdefault(key, {**row, "score": 0.0, "channels": [], "scores": {}})
            if channel in hit["channels"]:
                continue
            hit["score"] += 1 / (60 + rank)
            hit["channels"].append(channel)
            hit["scores"][channel] = row.get("score")
    return sorted(found.values(), key=lambda row: (-row["score"], str(row["chunk_id"])))


class Retrieval:
    def __init__(self, engine, knowledge, vector, gateway):
        self.engine, self.knowledge, self.vector, self.gateway = engine, knowledge, vector, gateway

    async def search(self, tenant, body):
        start = time.monotonic()
        async with self.engine.connect() as c:
            bases = await many(
                c,
                "SELECT id,published_settings FROM knowledge_bases WHERE tenant_id=:t AND enabled AND id=ANY(CAST(:ids AS uuid[]))",
                t=tenant,
                ids=body.kb_ids,
            )
        if {b["id"] for b in bases} != set(body.kb_ids):
            raise DomainError("knowledge_base_not_found", 404)
        timings, all_hits = {}, []
        budget = min(
            (LibrarySettings.model_validate(b["published_settings"]).context_chars for b in bases),
            default=12000,
        )
        top_k = body.top_k or min(
            (LibrarySettings.model_validate(b["published_settings"]).top_k for b in bases),
            default=5,
        )
        for base in bases:
            settings = LibrarySettings.model_validate(base["published_settings"])
            mode, count = body.mode or settings.mode, body.candidates or settings.candidates
            if body.min_vector_score is not None and mode == "keyword":
                raise DomainError("vector_threshold_requires_vector_search", 422)
            if body.min_rerank_score is not None and (not settings.rerank or not body.rerank):
                raise DomainError("rerank_threshold_requires_rerank", 422)
            tick = time.monotonic()
            calls, channels = [], []
            if mode in {"keyword", "hybrid"}:
                channels.append("keyword")
                calls.append(self.knowledge.search(tenant, body.query, [base["id"]], count))
            if mode in {"vector", "hybrid"}:
                channels.append("vector")
                calls.append(self.vector.search(tenant, base["id"], body.query, count))
            rankings = await asyncio.gather(*calls)
            if body.min_vector_score is not None:
                index = channels.index("vector")
                rankings[index] = [
                    hit
                    for hit in rankings[index]
                    if hit.get("score") is not None and hit["score"] >= body.min_vector_score
                ]
            hits = fuse(zip(channels, rankings, strict=True))
            timings[str(base["id"])] = {"retrieval_ms": round((time.monotonic() - tick) * 1000, 1)}
            if settings.rerank and hits and body.rerank:
                tick = time.monotonic()
                candidates = hits[: min(100, count)]
                result = await self.gateway.invoke(
                    tenant,
                    settings.rerank.id,
                    settings.rerank.version,
                    RerankRequest(
                        query=body.query,
                        candidates=[
                            {"id": str(h["chunk_id"]), "text": h["excerpt"]} for h in candidates
                        ],
                        top_n=min(top_k, len(candidates)),
                    ),
                )
                by_id = {str(h["chunk_id"]): h for h in candidates}
                hits = [
                    {**by_id[r["id"]], "rerank_score": r["score"]}
                    for r in result["payload"]["results"]
                    if body.min_rerank_score is None or r["score"] >= body.min_rerank_score
                ]
                timings[str(base["id"])]["rerank_ms"] = round((time.monotonic() - tick) * 1000, 1)
            all_hits.append((str(base["id"]), hits))
        # Across libraries rank fusion avoids comparing scores from different embedding spaces.
        combined = {}
        for library_id, rows in all_hits:
            for rank, hit in enumerate(rows, 1):
                key = str(hit["chunk_id"])
                item = combined.setdefault(
                    key, {**hit, "library_fusion_score": 0.0, "knowledge_base_ids": []}
                )
                item["library_fusion_score"] += 1 / (60 + rank)
                item["knowledge_base_ids"].append(library_id)
        merged = sorted(
            combined.values(), key=lambda h: (-h["library_fusion_score"], str(h["chunk_id"]))
        )
        result, used, seen = [], 0, set()
        for hit in merged:
            identity = (str(hit["document_id"]), hit["version"], hit["excerpt"])
            if identity in seen or used + len(hit["excerpt"]) > budget:
                continue
            seen.add(identity)
            result.append(hit)
            used += len(hit["excerpt"])
            if len(result) >= top_k:
                break
        return {
            "items": result,
            "timings": timings,
            "total_ms": round((time.monotonic() - start) * 1000, 1),
            "context_chars": used,
        }

    async def for_agent(self, tenant, query, kb_ids):
        if not kb_ids:
            return []
        result = await self.search(tenant, SearchInput(query=query, kb_ids=kb_ids))
        return result["items"]
