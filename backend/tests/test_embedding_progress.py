import pytest
from test_platform_postgres import platform as _platform_fixture

from agent_platform.platform.persistence.store import DomainError

platform = _platform_fixture
ROOT = "/api/v1/knowledge-workspace"


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_document_embedding_counts_follow_successful_batches(platform):
    from test_model_gateway import setup

    client, services, settings = platform
    _, _, profile = await setup(client, "embed")
    kid = (await client.post("/api/v1/knowledge-bases", json={"name": "progress"})).json()["id"]
    response = await client.put(
        ROOT + f"/libraries/{kid}/settings",
        json={
            "revision": 1,
            "settings": {"mode": "vector", "embedding": {"id": profile["id"], "version": 1}},
        },
    )
    assert response.status_code == 200
    upload = await client.post(
        ROOT + f"/uploads?filename=progress.md&kb={kid}", content=("知识内容。" * 5000).encode()
    )
    did = upload.json()["id"]
    w = services.platform.knowledge_workspace
    await w.run_next(settings.tenant_id)

    async def document():
        return (await client.get(ROOT + f"/libraries/{kid}")).json()["documents"][0]

    assert (await document())["embedded_chunk_count"] == 0
    original = services.platform.vector_index.request
    original_milvus_url = services.platform.vector_index.settings.milvus_url
    batches = 0
    fail = False

    async def milvus(path, body):
        nonlocal batches
        if path == "entities/upsert":
            current = await document()
            assert current["embedded_chunk_count"] == min(batches * 16, current["chunk_count"])
            assert current["embedding_count_source"] == "building"
            if fail and batches == 1:
                raise DomainError("test_upsert_failed", 502)
            batches += 1
        return {}

    services.platform.vector_index.request = milvus
    # VectorIndex.build guards on milvus_url before issuing any request, so the
    # fully mocked adapter still needs a dummy URL to reach the upsert path.
    services.platform.vector_index.settings.milvus_url = "http://milvus.invalid"
    try:
        await client.post(ROOT + f"/libraries/{kid}/publish")
        assert (await document())["embedded_chunk_count"] == 0
        await w.run_next(settings.tenant_id)
        doc = await document()
        assert doc["embedded_chunk_count"] == doc["chunk_count"] > 16
        assert doc["embedding_count_source"] == "published"
        # A new draft must not inherit vectors from the old published version.
        saved = await client.put(
            ROOT + f"/documents/{did}/draft", json={"revision": 1, "policy": {"size": 700}}
        )
        assert saved.status_code == 200
        await client.post(ROOT + f"/libraries/{kid}/documents", json={"document_id": did})
        assert (await document())["embedded_chunk_count"] == 0
        batches, fail = 0, True
        await client.post(ROOT + f"/libraries/{kid}/publish")
        await w.run_next(settings.tenant_id)
        doc = await document()
        assert doc["embedded_chunk_count"] == 0
        assert doc["published_version"] == 1
    finally:
        services.platform.vector_index.request = original
        services.platform.vector_index.settings.milvus_url = original_milvus_url
