from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from agent_platform.modules.knowledge.chunking import ChunkingPolicy
from agent_platform.modules.knowledge.semantic import semantic_chunks


def test_default_unchanged_and_semantic_requires_model():
    assert ChunkingPolicy().strategy == "hybrid"
    with pytest.raises(ValidationError, match="semantic_embedding_profile_required"):
        ChunkingPolicy(strategy="semantic")


@pytest.mark.asyncio
async def test_semantic_groups_similar_units_and_respects_boundaries():
    async def invoke(tenant, profile, version, request):
        return {
            "payload": {
                "vectors": [
                    {"id": x.id, "vector": [1, 0] if "A" in x.text else [0, 1]}
                    for x in request.inputs
                ]
            }
        }

    gateway = SimpleNamespace(
        catalog=SimpleNamespace(
            resolve=AsyncMock(
                return_value={
                    "spec": {"operation": "embed"},
                    "routes": [{"model": {"max_batch": 4}}],
                }
            )
        ),
        invoke=AsyncMock(side_effect=invoke),
    )
    policy = ChunkingPolicy(strategy="semantic", semantic_profile_id=uuid4(), size=1000)
    pages = [
        {"page_num": 1, "text": "A" * 280 + "\n\n" + "A" * 280 + "\n\n" + "B" * 280},
        {"page_num": 2, "text": "B" * 280},
    ]
    chunks = await semantic_chunks(pages, policy, gateway, "tenant")
    assert len(chunks) == 3
    assert chunks[0]["content"].count("A") == 560
    assert all(len(x["content"]) <= 1000 for x in chunks)
    assert chunks[-1]["source"]["page_num"] == 2


@pytest.mark.asyncio
async def test_semantic_tables_keep_headers_without_embedding_calls():
    gateway = SimpleNamespace(
        catalog=SimpleNamespace(
            resolve=AsyncMock(
                return_value={
                    "spec": {"operation": "embed"},
                    "routes": [{"model": {"max_batch": 4}}],
                }
            )
        ),
        invoke=AsyncMock(),
    )
    policy = ChunkingPolicy(strategy="semantic", semantic_profile_id=uuid4())
    chunks = await semantic_chunks(
        [
            {
                "page_num": 1,
                "text": "header\nvalue",
                "elements": [{"type": "table", "header": ["header"], "rows": [["value"]]}],
            }
        ],
        policy,
        gateway,
        "tenant",
    )
    assert "header" in chunks[0]["content"] and "value" in chunks[0]["content"]
    gateway.invoke.assert_not_called()


@pytest.mark.asyncio
async def test_semantic_cancel_stops_next_model_batch():
    async def invoke(tenant, profile, version, request):
        return {"payload": {"vectors": [{"id": x.id, "vector": [1, 0]} for x in request.inputs]}}

    gateway = SimpleNamespace(
        catalog=SimpleNamespace(
            resolve=AsyncMock(
                return_value={
                    "spec": {"operation": "embed"},
                    "routes": [{"model": {"max_batch": 1}}],
                }
            )
        ),
        invoke=AsyncMock(side_effect=invoke),
    )

    async def progress(stage, completed, total):
        if completed:
            raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        await semantic_chunks(
            [{"page_num": 1, "text": "A" * 1200}],
            ChunkingPolicy(strategy="semantic", semantic_profile_id=uuid4()),
            gateway,
            "tenant",
            progress=progress,
        )
    assert gateway.invoke.call_count == 1
