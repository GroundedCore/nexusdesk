from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from agent_platform.modules.knowledge import retrieval
from agent_platform.platform.persistence.store import DomainError


@pytest.mark.asyncio
async def test_thresholds_filter_raw_scores_and_preserve_keyword_channel(monkeypatch):
    kid = uuid4()
    profile = {"id": str(uuid4()), "version": 1}

    @asynccontextmanager
    async def connect():
        yield None

    monkeypatch.setattr(
        retrieval,
        "many",
        AsyncMock(
            return_value=[{"id": kid, "published_settings": {"mode": "hybrid", "rerank": profile}}]
        ),
    )
    rows = [
        {
            "chunk_id": str(uuid4()),
            "document_id": str(uuid4()),
            "version": 1,
            "excerpt": str(i),
            "score": score,
        }
        for i, score in enumerate([-0.1, 0.0, 0.8])
    ]
    gateway = SimpleNamespace(
        invoke=AsyncMock(
            return_value={
                "payload": {
                    "results": [
                        {"id": rows[2]["chunk_id"], "score": 2.0},
                        {"id": rows[0]["chunk_id"], "score": 0.2},
                    ]
                }
            }
        )
    )
    service = retrieval.Retrieval(
        SimpleNamespace(connect=connect),
        SimpleNamespace(search=AsyncMock(return_value=[rows[0]])),
        SimpleNamespace(search=AsyncMock(return_value=rows)),
        gateway,
    )
    base = {"query": "test", "kb_ids": [kid], "rerank": False}
    result = await service.search(
        "tenant", retrieval.SearchInput(**base, mode="vector", min_vector_score=0)
    )
    assert {h["chunk_id"] for h in result["items"]} == {r["chunk_id"] for r in rows[1:]}
    result = await service.search("tenant", retrieval.SearchInput(**base, min_vector_score=0.8))
    assert {h["chunk_id"] for h in result["items"]} == {rows[0]["chunk_id"], rows[2]["chunk_id"]}
    assert next(h for h in result["items"] if h["chunk_id"] == rows[0]["chunk_id"])["channels"] == [
        "keyword"
    ]
    result = await service.search(
        "tenant", retrieval.SearchInput(query="test", kb_ids=[kid], min_rerank_score=2)
    )
    assert [h["rerank_score"] for h in result["items"]] == [2.0]
    result = await service.search(
        "tenant", retrieval.SearchInput(query="test", kb_ids=[kid], min_rerank_score=3)
    )
    assert result["items"] == []
    with pytest.raises(DomainError, match="rerank_threshold_requires_rerank"):
        await service.search("tenant", retrieval.SearchInput(**base, min_rerank_score=0))
    with pytest.raises(DomainError, match="vector_threshold_requires_vector_search"):
        await service.search(
            "tenant", retrieval.SearchInput(**base, mode="keyword", min_vector_score=0)
        )


@pytest.mark.parametrize(
    "field,value",
    [("min_vector_score", 1.1), ("min_vector_score", -1.1), ("min_rerank_score", float("nan"))],
)
def test_invalid_thresholds_rejected(field, value):
    with pytest.raises(ValidationError):
        retrieval.SearchInput(query="test", kb_ids=[uuid4()], **{field: value})
