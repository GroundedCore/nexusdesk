import asyncio
from uuid import UUID, uuid4

import pytest
from test_model_gateway import setup
from test_platform_postgres import platform as _platform

from agent_platform.apps.seed_industries import BATCH, resolve_published_chat, seed
from agent_platform.modules.knowledge.service import DocumentInput
from agent_platform.platform.persistence.store import DomainError, execute, one

platform = _platform
pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


async def test_seed_concurrent_repeat_preserves_edits_and_searchable_content(platform):
    _client, runtime, settings = platform
    p, tenant = runtime.platform, settings.tenant_id
    reports = await asyncio.gather(seed(p, tenant), seed(p, tenant))
    assert sorted(r["created"]["agents"] for r in reports) == [0, 18]
    first = next(r for r in reports if r["created"]["agents"])
    assert first["created"] == {"agents": 18, "knowledge_bases": 9, "documents": 36}
    assert len(first["industries"]) == 9
    for industry in first["industries"]:
        for agent in industry["agents"]:
            row = await p.agents.get(tenant, UUID(agent["id"]))
            assert row["published_version"] is None
            assert row["draft"]["model_profile_id"] is None
            assert row["draft"]["knowledge_base_ids"] == [industry["knowledge_base_id"]]
    game = first["industries"][-1]
    kid, aid = UUID(game["knowledge_base_id"]), UUID(game["agents"][0]["id"])
    docs = await p.knowledge.documents(tenant, kid)
    assert len(docs) == 4
    assert await p.knowledge.search(tenant, "灯塔节", [kid])
    assert not await p.knowledge.search("another-tenant", "灯塔节", [kid])
    doc = docs[0]
    await p.knowledge.save_document(
        tenant,
        "editor",
        kid,
        DocumentInput(title="人工修改", content="保留手工内容"),
        did=doc["id"],
        expected_version=doc["current_version"],
    )
    async with p.engine.begin() as c:
        await execute(c, "UPDATE agents SET name='人工改名',archived=true WHERE id=:id", id=aid)
        before = await one(
            c, "SELECT count(*) AS n FROM audit_records WHERE tenant_id=:t", t=tenant
        )
    again = await seed(p, tenant)
    assert again["created"] == {"agents": 0, "knowledge_bases": 0, "documents": 0}
    assert again["preserved"] == first["created"]
    assert (await p.agents.get(tenant, aid))["name"] == "人工改名"
    assert (await p.agents.get(tenant, aid))["archived"]
    assert (
        next(d for d in await p.knowledge.documents(tenant, kid) if d["id"] == doc["id"])["content"]
        == "保留手工内容"
    )
    async with p.engine.connect() as c:
        after = await one(c, "SELECT count(*) AS n FROM audit_records WHERE tenant_id=:t", t=tenant)
        assert before == after
        marker = await one(
            c,
            "SELECT details FROM audit_records WHERE tenant_id=:t AND action='agent.created' LIMIT 1",
            t=tenant,
        )
        assert marker["details"]["batch"] == BATCH
        assert marker["details"]["industry"] and marker["details"]["scenario"]


async def test_invalid_binding_and_mid_import_failure_leave_no_partial_data(platform, monkeypatch):
    _client, runtime, settings = platform
    p, tenant = runtime.platform, settings.tenant_id
    with pytest.raises(DomainError):
        await seed(p, tenant, uuid4(), 1)
    original = p.knowledge.save_document
    count = 0

    async def fail_second(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("injected failure")
        return await original(*args, **kwargs)

    monkeypatch.setattr(p.knowledge, "save_document", fail_second)
    with pytest.raises(RuntimeError, match="injected failure"):
        await seed(p, tenant)
    assert await p.knowledge.bases(tenant) == []
    assert await p.agents.list(tenant) == []
    async with p.engine.connect() as c:
        assert (
            await one(c, "SELECT count(*) AS n FROM audit_records WHERE tenant_id=:t", t=tenant)
        )["n"] == 0


async def test_explicit_profile_binding_does_not_publish_or_call_model(platform):
    client, runtime, settings = platform
    _, _, profile = await setup(client)
    report = await seed(runtime.platform, settings.tenant_id, UUID(profile["id"]), 1)
    assert report["created"]["agents"] == 18
    for group in report["industries"]:
        for agent in group["agents"]:
            assert not agent["needs_model"]
            assert agent["published_version"] is None
    assert await runtime.platform.gateway.records(settings.tenant_id) == []


async def test_resolve_published_chat_targets_the_published_profile(platform):
    client, runtime, settings = platform
    _, _, profile = await setup(client)
    # Before publication there is nothing to bind, so quickstart leaves it empty.
    assert await resolve_published_chat(runtime.platform, "no-such-tenant") == (None, None)

    resolved = await resolve_published_chat(runtime.platform, settings.tenant_id)
    assert resolved == (UUID(profile["id"]), 1)
    # The bound profile lets the case agents report no missing model, still unpublished.
    report = await seed(runtime.platform, settings.tenant_id, *resolved)
    assert report["created"]["agents"] == 18
    assert all(
        not agent["needs_model"] for group in report["industries"] for agent in group["agents"]
    )
