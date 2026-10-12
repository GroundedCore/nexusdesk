"""Phase 2 customer profile/preference memory — PostgreSQL integration tests.

Case IDs refer to docs/requirement/agent-memory/phase-2-customer-profile/04-test-plan.md.
Runs only with TEST_DATABASE_URL (CI Backend job); skipped locally.
"""

import asyncio
import json
import logging
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from sqlalchemy import text
from test_memory_postgres import RecordingGateway, audit_records, force_claimable, memory_tasks
from test_open_platform import application, session
from test_platform_postgres import create_agent, execute_next
from test_platform_postgres import platform as _platform

from agent_platform.modules.channel.service import session_id_hash
from agent_platform.modules.memory.bootstrap import MemoryDispatcher
from agent_platform.modules.memory.extraction import EXTRACT_KIND, ExtractionService
from agent_platform.modules.memory.service import MemoryService
from agent_platform.modules.memory.summary import SummaryService
from agent_platform.modules.memory.worker import MemoryWorker
from agent_platform.platform.persistence.store import DomainError, execute

platform = _platform
pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_worker(services, settings, gateway=None, **kwargs):
    """Assemble a worker the way the deployment entries do, with a stub gateway."""
    engine = services.repository.engine
    gateway = gateway or services.platform.gateway
    dispatcher = MemoryDispatcher(
        SummaryService(engine, gateway, settings),
        ExtractionService(engine, gateway, settings, MemoryService(engine, settings)),
    )
    return MemoryWorker(engine, dispatcher, settings, **kwargs)


async def db_rows(engine, sql, **params):
    async with engine.connect() as c:
        rows = (await c.execute(text(sql), params)).mappings().all()
        return [dict(row) for row in rows]


async def extract_tasks(engine, tenant):
    return [task for task in await memory_tasks(engine, tenant) if task["kind"] == EXTRACT_KIND]


async def items_of(engine, tenant, customer_id):
    return await db_rows(
        engine,
        "SELECT * FROM memory_items WHERE tenant_id=:t AND customer_id=:c ORDER BY type,key",
        t=tenant,
        c=customer_id,
    )


async def identities_of(engine, tenant):
    return await db_rows(
        engine, "SELECT * FROM memory_identities WHERE tenant_id=:t ORDER BY created_at", t=tenant
    )


async def make_channel(client, agent):
    response = await client.post(
        "/api/v1/channels", json={"name": "官网", "agent_id": agent["id"]}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def ingest(client, channel, message_id, session_id, text):
    response = await client.post(
        f"/api/v1/ingress/channels/{channel['id']}/messages",
        headers={"X-Channel-Token": channel["token"]},
        json={"message_id": message_id, "session_id": session_id, "text": text},
    )
    assert response.status_code == 202, response.text
    return response.json()


async def conversation_detail(client, cid):
    response = await client.get(f"/api/v1/conversations/{cid}")
    assert response.status_code == 200, response.text
    return response.json()


async def claim_and_run(services, settings):
    """Claim the next run and execute it through the wired worker."""
    row = await services.repository.claim(services.worker.owner, 30, settings.tenant_id)
    assert row is not None
    await services.worker._execute(row)
    return row


async def age_memory_tasks(engine, tenant, minutes=10):
    """Move all task timestamps beyond the debounce window (no waiting in tests)."""
    async with engine.begin() as c:
        await c.execute(
            text(
                "UPDATE memory_tasks SET created_at=now()-:minutes*interval '1 minute'"
                " WHERE tenant_id=:t"
            ),
            {"minutes": minutes, "t": tenant},
        )


async def make_customer(services, settings, external_key, ops=(), source="channel"):
    """Direct-write a customer, its identity and extracted items (data construction)."""
    engine, tenant = services.repository.engine, settings.tenant_id
    memory = services.platform.memory
    async with engine.begin() as c:
        customer_id = await memory.resolve_customer(c, tenant, source, external_key)
        if ops:
            await memory.apply_extracted(c, tenant, customer_id, ops, "conv-seed", 0.8, 0.5)
    return customer_id


def op_add(item_type, key, content, confidence=0.9):
    return {
        "op": "add",
        "type": item_type,
        "key": key,
        "content": content,
        "confidence": confidence,
        "reason": "",
    }


# ---------------------------------------------------------------------------
# P2-UT (database-bound service cases)
# ---------------------------------------------------------------------------


async def test_p2_ut_01_resolve_customer_creates_both_rows(platform):
    _client, services, settings = platform
    memory, engine = services.platform.memory, services.repository.engine
    async with engine.begin() as c:
        customer_id = await memory.resolve_customer(c, settings.tenant_id, "channel", "key-1")
    customers = await db_rows(
        engine, "SELECT * FROM memory_customers WHERE tenant_id=:t", t=settings.tenant_id
    )
    assert len(customers) == 1 and customers[0]["id"] == customer_id
    identities = await identities_of(engine, settings.tenant_id)
    assert len(identities) == 1
    assert identities[0]["customer_id"] == customer_id
    assert (identities[0]["source"], identities[0]["external_key"]) == ("channel", "key-1")


async def test_p2_ut_02_resolve_customer_hit_reuses_existing(platform):
    _client, services, settings = platform
    memory, engine = services.platform.memory, services.repository.engine
    async with engine.begin() as c:
        first = await memory.resolve_customer(c, settings.tenant_id, "channel", "key-1")
    async with engine.begin() as c:
        second = await memory.resolve_customer(c, settings.tenant_id, "channel", "key-1")
    assert second == first
    customers = await db_rows(
        engine, "SELECT * FROM memory_customers WHERE tenant_id=:t", t=settings.tenant_id
    )
    assert len(customers) == 1
    # A different key of the same source is a different customer (conservative).
    async with engine.begin() as c:
        other = await memory.resolve_customer(c, settings.tenant_id, "channel", "key-2")
    assert other != first


async def test_p2_ut_03_concurrent_resolution_loser_rereads_winner(platform):
    _client, services, settings = platform
    memory, engine = services.platform.memory, services.repository.engine
    winner_id, started = uuid4(), asyncio.Event()

    async def winner():
        async with engine.begin() as c:
            await execute(
                c,
                "INSERT INTO memory_customers(id,tenant_id) VALUES(:id,:t)",
                id=winner_id,
                t=settings.tenant_id,
            )
            await execute(
                c,
                """INSERT INTO memory_identities(id,tenant_id,customer_id,source,external_key)
                VALUES(:id,:t,:cid,'channel','race-key')""",
                id=uuid4(),
                t=settings.tenant_id,
                cid=winner_id,
            )
            started.set()
            # Hold the transaction open so the loser hits the speculative
            # insertion wait on the identity unique index.
            await asyncio.sleep(0.3)

    async def loser():
        await started.wait()
        async with engine.begin() as c:
            return await memory.resolve_customer(c, settings.tenant_id, "channel", "race-key")

    _, resolved = await asyncio.wait_for(asyncio.gather(winner(), loser()), 10)
    assert resolved == winner_id
    # The loser's orphan customer row was cleaned up.
    customers = await db_rows(
        engine, "SELECT * FROM memory_customers WHERE tenant_id=:t", t=settings.tenant_id
    )
    assert [row["id"] for row in customers] == [winner_id]


async def test_p2_ut_08_same_key_update_merges_with_prev_content(platform):
    _client, services, settings = platform
    memory, engine = services.platform.memory, services.repository.engine
    tenant = settings.tenant_id
    customer_id = await make_customer(services, settings, "merge-key")
    async with engine.begin() as c:
        counts = await memory.apply_extracted(
            c, tenant, customer_id, [op_add("profile", "name", "张三", 0.95)], "conv-1", 0.8, 0.5
        )
    assert counts["added"] == 1
    first = (await items_of(engine, tenant, customer_id))[0]
    assert first["revision"] == 1 and first["prev_content"] is None
    assert first["status"] == "active" and first["source_type"] == "extracted"
    assert first["source_ref"] == "conv-1"
    async with engine.begin() as c:
        counts = await memory.apply_extracted(
            c,
            tenant,
            customer_id,
            [{"op": "update", "type": "profile", "key": "name", "content": "张工",
              "confidence": 0.9, "reason": ""}],
            "conv-2",
            0.8,
            0.5,
        )
    assert counts["updated"] == 1
    rows = await items_of(engine, tenant, customer_id)
    assert len(rows) == 1  # same key merges; nothing stacks
    merged = rows[0]
    assert merged["content"] == "张工" and merged["prev_content"] == "张三"
    assert merged["revision"] == 2 and merged["source_ref"] == "conv-2"
    assert merged["last_referenced_at"] >= first["last_referenced_at"]
    assert merged["updated_at"] >= first["updated_at"]


async def test_p2_ut_09_add_on_existing_key_does_nothing(platform):
    _client, services, settings = platform
    memory, engine = services.platform.memory, services.repository.engine
    tenant = settings.tenant_id
    customer_id = await make_customer(services, settings, "dup-key")
    for _ in range(2):
        async with engine.begin() as c:
            counts = await memory.apply_extracted(
                c, tenant, customer_id, [op_add("profile", "name", "张三")], "conv-1", 0.8, 0.5
            )
    assert counts["noop"] == 1
    rows = await items_of(engine, tenant, customer_id)
    assert len(rows) == 1 and rows[0]["revision"] == 1


async def test_extracted_update_never_clobbers_human_edit(platform):
    """A human correction is protected: extraction updates skip human rows."""
    _client, services, settings = platform
    memory, engine = services.platform.memory, services.repository.engine
    tenant = settings.tenant_id
    customer_id = await make_customer(
        services, settings, "human-key", [op_add("profile", "name", "张三")]
    )
    item = (await items_of(engine, tenant, customer_id))[0]
    await memory.edit_item(tenant, "operator", item["id"], "张叁")
    async with engine.begin() as c:
        counts = await memory.apply_extracted(
            c,
            tenant,
            customer_id,
            [{"op": "update", "type": "profile", "key": "name", "content": "张四",
              "confidence": 0.95, "reason": ""}],
            "conv-9",
            0.8,
            0.5,
        )
    assert counts["updated"] == 0 and counts["noop"] == 1
    row = (await items_of(engine, tenant, customer_id))[0]
    assert row["content"] == "张叁" and row["source_type"] == "human"


# ---------------------------------------------------------------------------
# P2-IT: identity resolution and extraction pipeline
# ---------------------------------------------------------------------------


async def test_p2_it_01_returning_channel_customer_accumulates_memory(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    agent, _ = await create_agent(client)
    channel = await make_channel(client, agent)
    first = await ingest(
        client, channel, "m1", "customer1", "我叫张三，电话 13800001111，以后用英文回复我"
    )
    final = await execute_next(services, settings)
    assert final["status"] == "completed", final
    detail = await conversation_detail(client, first["conversation_id"])
    customer_id = detail["customer_id"]
    assert customer_id, "channel conversation must resolve a customer"
    # The identity key and the conversation external_id derive from one hash.
    digest = session_id_hash("customer1")
    assert detail["external_id"] == f"channel:{channel['id']}:{digest[:32]}"
    identities = await identities_of(engine, tenant)
    assert [(i["source"], i["external_key"]) for i in identities] == [("channel", digest)]
    # The successful run dispatched one extraction task and marked the trace.
    tasks = await extract_tasks(engine, tenant)
    assert len(tasks) == 1 and tasks[0]["status"] == "pending"
    assert tasks[0]["payload"]["conversation_id"] == first["conversation_id"]
    assert tasks[0]["payload"]["customer_id"] == customer_id
    events = await services.repository.events(final["id"], tenant)
    assert "memory_extract.queued" in [event["type"] for event in events]
    # Extraction writes the profile/preference items.
    gateway = RecordingGateway(
        '[{"op":"add","type":"profile","key":"name","content":"张三","confidence":0.95},'
        '{"op":"add","type":"profile","key":"phone","content":"13800001111","confidence":0.9},'
        '{"op":"add","type":"preference","key":"language","content":"英文回复","confidence":0.9}]'
    )
    worker = make_worker(services, settings, gateway)
    assert await worker.run_once() is True
    tasks = await extract_tasks(engine, tenant)
    assert tasks[0]["status"] == "done"
    items = await items_of(engine, tenant, customer_id)
    assert {item["key"] for item in items} == {"name", "phone", "language"}
    assert all(item["status"] == "active" for item in items)
    assert all(item["source_type"] == "extracted" for item in items)
    assert all(item["source_ref"] == first["conversation_id"] for item in items)
    # The extraction prompt carried the recent dialogue and the vocabulary.
    request = gateway.requests[0]
    assert "我叫张三" in request["messages"][1]["content"]
    assert "受控词表" in request["messages"][0]["content"]
    # Second contact on the same session resolves to the same customer, and
    # the next run injects the accumulated memory.
    second = await ingest(client, channel, "m2", "customer1", "我又来了")
    assert second["conversation_id"] == first["conversation_id"]
    row = await services.repository.claim(services.worker.owner, 30, tenant)
    assert str(row["customer_id"]) == customer_id
    injection = await services.worker._customer_memory(row)
    assert "【客户画像】" in injection and "name: 张三" in injection
    assert "【客户偏好】" in injection and "language: 英文回复" in injection
    await services.worker._execute(row)


async def test_p2_it_02_open_platform_identity_is_separate_from_channel(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    agent, _ = await create_agent(client)
    app, key = await application(client, agent["id"])
    cid = await session(client, key["key"], agent["id"], user="employee-9")
    detail = await conversation_detail(client, cid)
    open_customer = detail["customer_id"]
    assert open_customer
    identities = await identities_of(engine, tenant)
    assert [(i["source"], i["external_key"]) for i in identities] == [
        ("open_platform", f"{app['id']}:employee-9")
    ]
    # A channel session never collides with the open-platform customer.
    channel = await make_channel(client, agent)
    incoming = await ingest(client, channel, "m1", "employee-9", "你好")
    channel_detail = await conversation_detail(client, incoming["conversation_id"])
    assert channel_detail["customer_id"] != open_customer
    customers = await db_rows(
        engine, "SELECT id FROM memory_customers WHERE tenant_id=:t", t=tenant
    )
    assert len(customers) == 2


async def test_p2_it_03_resolution_failure_never_blocks_conversation(platform, caplog):
    client, services, settings = platform
    agent, _ = await create_agent(client)
    channel = await make_channel(client, agent)

    class BrokenMemory:
        async def resolve_customer(self, c, tenant, source, external_key):
            raise RuntimeError("simulated database hiccup")

    original = services.platform.conversations.memory
    services.platform.conversations.memory = BrokenMemory()
    try:
        with caplog.at_level(logging.WARNING):
            incoming = await ingest(client, channel, "m1", "customer1", "你好")
    finally:
        services.platform.conversations.memory = original
    detail = await conversation_detail(client, incoming["conversation_id"])
    assert detail["customer_id"] is None
    assert any("identity resolution failed" in record.message for record in caplog.records)
    # The conversation itself is fully usable.
    final = await execute_next(services, settings)
    assert final["status"] == "completed", final
    assert await extract_tasks(services.repository.engine, settings.tenant_id) == []


async def test_p2_it_04_playground_and_example_agents_stay_out_of_memory(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    agent, _ = await create_agent(client)
    response = await client.post(
        "/api/v1/conversations",
        json={"external_id": str(uuid4()), "agent_id": agent["id"], "source": "playground"},
    )
    assert response.status_code == 201, response.text
    conv = response.json()
    await client.post(
        f"/api/v1/conversations/{conv['id']}/messages",
        json={"content": "我叫李四，电话 13900002222"},
    )
    final = await execute_next(services, settings)
    assert final["status"] == "completed", final
    assert (await conversation_detail(client, conv["id"]))["customer_id"] is None
    # Example agent reached through a channel: no identity, no tasks either.
    example, body = await create_agent(client)
    body["is_example"] = True
    assert (
        await client.put(f"/api/v1/agents/{example['id']}", json={**body, "revision": 1})
    ).status_code == 200
    assert (
        await client.post(f"/api/v1/agents/{example['id']}/publish", json={"revision": 2})
    ).status_code == 200
    channel = await make_channel(client, example)
    incoming = await ingest(client, channel, "m1", "customer2", "我叫王五")
    final = await execute_next(services, settings)
    assert final["status"] == "completed", final
    assert (await conversation_detail(client, incoming["conversation_id"]))["customer_id"] is None
    assert await memory_tasks(engine, tenant) == []
    assert await identities_of(engine, tenant) == []
    assert await db_rows(engine, "SELECT id FROM memory_customers WHERE tenant_id=:t", t=tenant) == []


async def test_p2_it_05_extraction_debounces_per_conversation(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    agent, _ = await create_agent(client)
    channel = await make_channel(client, agent)
    await ingest(client, channel, "m1", "customer1", "第一轮")
    await execute_next(services, settings)
    await ingest(client, channel, "m2", "customer1", "第二轮")
    await execute_next(services, settings)
    assert len(await extract_tasks(engine, tenant)) == 1
    # Five minutes later (constructed, not waited) the next run dispatches again.
    await age_memory_tasks(engine, tenant)
    await ingest(client, channel, "m3", "customer1", "第三轮")
    await execute_next(services, settings)
    assert len(await extract_tasks(engine, tenant)) == 2


async def test_p2_it_06_correction_updates_instead_of_stacking(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    agent, _ = await create_agent(client)
    channel = await make_channel(client, agent)
    first = await ingest(client, channel, "m1", "customer1", "我叫张三")
    await execute_next(services, settings)
    customer_id = (await conversation_detail(client, first["conversation_id"]))["customer_id"]
    gateway = RecordingGateway(
        '[{"op":"add","type":"preference","key":"address_name","content":"张三","confidence":0.9}]',
        '[{"op":"update","type":"preference","key":"address_name","content":"张工","confidence":0.95}]',
    )
    worker = make_worker(services, settings, gateway)
    assert await worker.run_once() is True
    rows = await items_of(engine, tenant, customer_id)
    assert len(rows) == 1 and rows[0]["content"] == "张三" and rows[0]["revision"] == 1
    await age_memory_tasks(engine, tenant)
    await ingest(client, channel, "m2", "customer1", "其实叫我张工就行")
    await execute_next(services, settings)
    assert await worker.run_once() is True
    rows = await items_of(engine, tenant, customer_id)
    assert len(rows) == 1
    assert rows[0]["content"] == "张工" and rows[0]["prev_content"] == "张三"
    assert rows[0]["revision"] == 2


async def test_p2_it_07_medium_confidence_waits_for_human_confirmation(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    agent, _ = await create_agent(client)
    channel = await make_channel(client, agent)
    first = await ingest(client, channel, "m1", "customer1", "我叫张三")
    await execute_next(services, settings)
    customer_id = (await conversation_detail(client, first["conversation_id"]))["customer_id"]
    gateway = RecordingGateway(
        '[{"op":"add","type":"profile","key":"name","content":"张三","confidence":0.6}]'
    )
    worker = make_worker(services, settings, gateway)
    assert await worker.run_once() is True
    rows = await items_of(engine, tenant, customer_id)
    assert len(rows) == 1 and rows[0]["status"] == "pending"
    # Pending items are invisible to injection.
    await ingest(client, channel, "m2", "customer1", "在吗")
    row = await services.repository.claim(services.worker.owner, 30, tenant)
    assert await services.worker._customer_memory(row) is None
    await services.worker._execute(row)
    # Confirmation promotes it; the next run injects it.
    response = await client.post(f"/api/v1/memory/items/{rows[0]['id']}/confirm")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"
    await ingest(client, channel, "m3", "customer1", "还在吗")
    row = await services.repository.claim(services.worker.owner, 30, tenant)
    injection = await services.worker._customer_memory(row)
    assert "name: 张三" in injection
    await services.worker._execute(row)


async def test_p2_it_08_injection_sits_between_summary_and_history(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(
        services,
        settings,
        "inject-key",
        [op_add("profile", "name", "张三"), op_add("preference", "language", "英文回复")],
    )
    cid = uuid4()
    async with engine.begin() as c:
        await c.execute(
            text(
                """INSERT INTO runtime_conversations(id,tenant_id,external_id,history,summary,customer_id)
                VALUES(:id,:t,:external,CAST(:history AS jsonb),:summary,:customer)"""
            ),
            {
                "id": cid,
                "t": tenant,
                "external": "legacy-conv",
                "history": json.dumps(
                    [
                        {"role": "user", "content": "早期问题"},
                        {"role": "assistant", "content": "早期回答"},
                    ]
                ),
                "summary": "订单号 A123。",
                "customer": customer_id,
            },
        )

    class CaptureModel:
        def __init__(self):
            self.seen = []

        async def ainvoke(self, messages):
            self.seen.append(messages)
            return AIMessage(content="回答")

    capture = CaptureModel()
    services.worker.runtime.legacy.model = capture
    response = await client.post(
        f"/api/v1/conversations/{cid}/messages", json={"content": "新问题"}
    )
    assert response.status_code == 202, response.text
    await claim_and_run(services, settings)
    seen = capture.seen[0]
    assert isinstance(seen[0], SystemMessage)
    assert seen[1].content.startswith("【此前对话摘要】")
    assert seen[2].content == "【客户画像】\n- name: 张三\n\n【客户偏好】\n- language: 英文回复"
    assert isinstance(seen[3], HumanMessage) and seen[3].content == "早期问题"
    assert seen[-1].content == "新问题"


async def test_p2_it_09_agent_switches_split_read_and_write(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    agent, body = await create_agent(client)
    channel = await make_channel(client, agent)
    first = await ingest(client, channel, "m1", "customer1", "我叫张三")
    await execute_next(services, settings)
    customer_id = (await conversation_detail(client, first["conversation_id"]))["customer_id"]
    worker = make_worker(
        services,
        settings,
        RecordingGateway('[{"op":"add","type":"profile","key":"name","content":"张三","confidence":0.9}]'),
    )
    assert await worker.run_once() is True
    assert len(await items_of(engine, tenant, customer_id)) == 1
    # memory_write=false: runs complete but dispatch nothing.
    body["config"]["memory_write"] = False
    assert (
        await client.put(f"/api/v1/agents/{agent['id']}", json={**body, "revision": 1})
    ).status_code == 200
    assert (
        await client.post(f"/api/v1/agents/{agent['id']}/publish", json={"revision": 2})
    ).status_code == 200
    await age_memory_tasks(engine, tenant)
    await ingest(client, channel, "m2", "customer1", "还记得我吗")
    await execute_next(services, settings)
    assert [task["status"] for task in await extract_tasks(engine, tenant)] == ["done"]
    # memory_enabled=false: extraction still dispatches, injection stops.
    body["config"]["memory_write"] = True
    body["config"]["memory_enabled"] = False
    assert (
        await client.put(f"/api/v1/agents/{agent['id']}", json={**body, "revision": 2})
    ).status_code == 200
    assert (
        await client.post(f"/api/v1/agents/{agent['id']}/publish", json={"revision": 3})
    ).status_code == 200
    await ingest(client, channel, "m3", "customer1", "再看看")
    row = await services.repository.claim(services.worker.owner, 30, tenant)
    assert await services.worker._customer_memory(row) is None
    await services.worker._execute(row)
    assert [task["status"] for task in await extract_tasks(engine, tenant)] == ["done", "pending"]


async def test_p2_it_10_global_extract_switch_off_dispatches_nothing(platform):
    client, services, settings = platform
    settings.memory_extract_enabled = False
    agent, _ = await create_agent(client)
    channel = await make_channel(client, agent)
    incoming = await ingest(client, channel, "m1", "customer1", "我叫张三")
    final = await execute_next(services, settings)
    assert final["status"] == "completed", final
    # Identity resolution is independent of the write-side kill switch.
    assert (await conversation_detail(client, incoming["conversation_id"]))["customer_id"]
    assert await memory_tasks(services.repository.engine, settings.tenant_id) == []


async def test_p2_it_11_ticket_from_bound_conversation_carries_customer(platform):
    client, services, settings = platform
    tenant = settings.tenant_id
    agent, _ = await create_agent(client, ["propose_ticket"])
    channel = await make_channel(client, agent)
    incoming = await ingest(client, channel, "m1", "customer1", "请创建维修工单")
    final = await execute_next(services, settings)
    assert final["status"] == "completed", final
    detail = await conversation_detail(client, incoming["conversation_id"])
    customer_id = detail["customer_id"]
    assert customer_id
    action = detail["actions"][0]
    assert action["payload"]["customer_id"] == customer_id
    response = await client.post(f"/api/v1/actions/{action['id']}/decision", json={"approve": True})
    assert response.status_code == 200, response.text
    ticket = response.json()
    assert ticket["customer_id"] == customer_id
    listed = (await client.get("/api/v1/tickets")).json()
    assert listed[0]["customer_id"] == customer_id
    # str(UUID) form fits the legacy VARCHAR(100) column.
    rows = await db_rows(
        services.repository.engine,
        "SELECT customer_id FROM tickets WHERE tenant_id=:t",
        t=tenant,
    )
    assert rows[0]["customer_id"] == customer_id


async def test_p2_it_12_ticket_without_customer_stays_null(platform):
    client, services, settings = platform
    agent, _ = await create_agent(client, ["propose_ticket"])
    response = await client.post(
        "/api/v1/conversations", json={"external_id": str(uuid4()), "agent_id": agent["id"]}
    )
    conv = response.json()
    await client.post(
        f"/api/v1/conversations/{conv['id']}/messages", json={"content": "请创建维修工单"}
    )
    final = await execute_next(services, settings)
    assert final["status"] == "completed", final
    detail = await conversation_detail(client, conv["id"])
    assert detail["customer_id"] is None
    action = detail["actions"][0]
    assert action["payload"]["customer_id"] is None
    response = await client.post(f"/api/v1/actions/{action['id']}/decision", json={"approve": True})
    assert response.status_code == 200, response.text
    assert response.json()["customer_id"] is None


async def test_p2_it_13_legacy_conversation_without_customer_is_unaffected(platform):
    client, services, settings = platform
    agent, _ = await create_agent(client)
    response = await client.post(
        "/api/v1/conversations", json={"external_id": str(uuid4()), "agent_id": agent["id"]}
    )
    conv = response.json()
    await client.post(f"/api/v1/conversations/{conv['id']}/messages", json={"content": "你好"})
    final = await execute_next(services, settings)
    assert final["status"] == "completed", final
    detail = await conversation_detail(client, conv["id"])
    assert detail["customer_id"] is None
    assert await memory_tasks(services.repository.engine, settings.tenant_id) == []


async def test_extraction_failure_retries_then_fails_with_kind_audit(platform, caplog):
    """Extraction tasks share the worker retry policy; failure audit is kind-aware."""
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    agent, _ = await create_agent(client)
    channel = await make_channel(client, agent)
    await ingest(client, channel, "m1", "customer1", "我叫张三")
    await execute_next(services, settings)
    assert len(await extract_tasks(engine, tenant)) == 1
    gateway = RecordingGateway(error=DomainError("model_provider_unavailable", 502))
    worker = make_worker(services, settings, gateway)
    with caplog.at_level(logging.WARNING):
        assert await worker.run_once() is True
        await force_claimable(engine, tenant)
        assert await worker.run_once() is True
    task = (await extract_tasks(engine, tenant))[0]
    assert task["status"] == "failed" and task["error"] == "model_provider_unavailable"
    assert task["attempts"] == 2
    records = await audit_records(engine, tenant, "memory.extract.failed")
    assert len(records) == 1


# ---------------------------------------------------------------------------
# P2-API: console memory management endpoints
# ---------------------------------------------------------------------------

VIEWER = {"Authorization": "Bearer platform-viewer"}
OPERATOR = {"Authorization": "Bearer platform-operator"}
ANONYMOUS = {"Authorization": "Bearer no-such-token"}


async def test_p2_api_01_authorization_matrix(platform):
    client, services, settings = platform
    customer_id = await make_customer(
        services,
        settings,
        "auth-key",
        [
            op_add("profile", "name", "张三"),
            op_add("preference", "language", "英文", 0.6),
            op_add("preference", "style", "简洁", 0.6),
        ],
    )
    items = await items_of(services.repository.engine, settings.tenant_id, customer_id)
    active = next(item for item in items if item["status"] == "active")
    pending = next(item for item in items if item["key"] == "language")
    other_pending = next(item for item in items if item["key"] == "style")
    # Reads: any authenticated role; anonymous rejected.
    for path in (
        "/api/v1/memory/customers",
        f"/api/v1/memory/customers/{customer_id}",
        "/api/v1/memory/pending",
    ):
        assert (await client.get(path, headers=VIEWER)).status_code == 200
        assert (await client.get(path, headers=ANONYMOUS)).status_code == 401
    # Writes: viewer forbidden on every mutation verb.
    assert (
        await client.patch(f"/api/v1/memory/items/{active['id']}", headers=VIEWER, json={"content": "x"})
    ).status_code == 403
    assert (
        await client.delete(f"/api/v1/memory/items/{active['id']}", headers=VIEWER)
    ).status_code == 403
    for action in ("restore", "confirm", "reject"):
        assert (
            await client.post(f"/api/v1/memory/items/{active['id']}/{action}", headers=VIEWER)
        ).status_code == 403
    # Operator can write.
    assert (
        await client.patch(
            f"/api/v1/memory/items/{active['id']}", headers=OPERATOR, json={"content": "张工"}
        )
    ).status_code == 200
    assert (
        await client.post(f"/api/v1/memory/items/{pending['id']}/confirm", headers=OPERATOR)
    ).status_code == 200
    assert (
        await client.delete(f"/api/v1/memory/items/{active['id']}", headers=OPERATOR)
    ).status_code == 200
    assert (
        await client.post(f"/api/v1/memory/items/{active['id']}/restore", headers=OPERATOR)
    ).status_code == 200
    assert (
        await client.post(f"/api/v1/memory/items/{other_pending['id']}/reject", headers=OPERATOR)
    ).status_code == 200


async def test_p2_api_02_customer_list_search_and_pagination(platform):
    client, services, settings = platform
    alpha = await make_customer(services, settings, "alpha-1", [op_add("profile", "name", "张三")])
    await make_customer(services, settings, "beta-2")
    await make_customer(services, settings, "beta-3")
    listing = (await client.get("/api/v1/memory/customers")).json()
    assert listing["total"] == 3 and len(listing["items"]) == 3
    assert listing["page"] == 1 and listing["page_size"] == 20
    by_id = {str(item["id"]): item for item in listing["items"]}
    seeded = by_id[str(alpha)]
    assert seeded["external_keys"] == ["alpha-1"]
    assert seeded["item_count"] == 1 and seeded["last_active_at"] is not None
    empties = [item for key, item in by_id.items() if key != str(alpha)]
    assert all(item["item_count"] == 0 and item["last_active_at"] is None for item in empties)
    searched = (await client.get("/api/v1/memory/customers", params={"q": "beta"})).json()
    assert searched["total"] == 2
    assert {tuple(item["external_keys"]) for item in searched["items"]} == {("beta-2",), ("beta-3",)}
    page1 = (
        await client.get("/api/v1/memory/customers", params={"page": 1, "page_size": 2})
    ).json()
    page2 = (
        await client.get("/api/v1/memory/customers", params={"page": 2, "page_size": 2})
    ).json()
    assert len(page1["items"]) == 2 and len(page2["items"]) == 1
    assert page2["total"] == 3


async def test_p2_api_03_edit_turns_item_human_and_keeps_previous(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(
        services, settings, "edit-key", [op_add("profile", "name", "张三", 0.95)]
    )
    item = (await items_of(engine, tenant, customer_id))[0]
    response = await client.patch(
        f"/api/v1/memory/items/{item['id']}", json={"content": "张叁"}
    )
    assert response.status_code == 200, response.text
    edited = response.json()
    assert edited["content"] == "张叁" and edited["prev_content"] == "张三"
    assert edited["source_type"] == "human" and edited["confidence"] == 1.0
    assert edited["revision"] == 2
    # The next injection carries the edited content.
    active = await services.platform.memory.injection_items(tenant, customer_id, 20)
    assert [row["content"] for row in active] == ["张叁"]
    records = await audit_records(engine, tenant, "memory.item.edited")
    assert len(records) == 1
    details = records[0]["details"]
    assert details["customer_id"] == str(customer_id) and details["key"] == "name"
    assert "张" not in json.dumps(details, ensure_ascii=False)  # no content in the audit


async def test_p2_api_04_soft_delete_and_restore_control_injection(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(
        services, settings, "delete-key", [op_add("profile", "name", "张三")]
    )
    item = (await items_of(engine, tenant, customer_id))[0]
    response = await client.delete(f"/api/v1/memory/items/{item['id']}")
    assert response.status_code == 200 and response.json()["status"] == "deleted"
    assert await services.platform.memory.injection_items(tenant, customer_id, 20) == []
    # Repeating the delete is idempotent.
    assert (await client.delete(f"/api/v1/memory/items/{item['id']}")).status_code == 200
    response = await client.post(f"/api/v1/memory/items/{item['id']}/restore")
    assert response.status_code == 200 and response.json()["status"] == "active"
    active = await services.platform.memory.injection_items(tenant, customer_id, 20)
    assert [row["content"] for row in active] == ["张三"]
    # A non-deleted item cannot be restored.
    assert (await client.post(f"/api/v1/memory/items/{item['id']}/restore")).status_code == 409


async def test_p2_api_05_pending_queue_confirm_and_reject(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(
        services,
        settings,
        "pending-key",
        [
            op_add("profile", "name", "张三", 0.6),
            op_add("preference", "language", "英文", 0.7),
            op_add("profile", "company", "ACME", 0.95),
        ],
    )
    queue = (await client.get("/api/v1/memory/pending")).json()
    assert queue["total"] == 2
    assert {item["key"] for item in queue["items"]} == {"name", "language"}
    assert all(item["customer_id"] == str(customer_id) for item in queue["items"])
    assert all(item["customer_external_keys"] == ["pending-key"] for item in queue["items"])
    name = next(item for item in queue["items"] if item["key"] == "name")
    language = next(item for item in queue["items"] if item["key"] == "language")
    confirmed = await client.post(f"/api/v1/memory/items/{name['id']}/confirm")
    assert confirmed.status_code == 200 and confirmed.json()["status"] == "active"
    rejected = await client.post(f"/api/v1/memory/items/{language['id']}/reject")
    assert rejected.status_code == 200 and rejected.json()["status"] == "deleted"
    assert (await client.get("/api/v1/memory/pending")).json()["total"] == 0
    # Decided items cannot be decided again.
    assert (await client.post(f"/api/v1/memory/items/{name['id']}/confirm")).status_code == 409
    assert (await client.post(f"/api/v1/memory/items/{language['id']}/reject")).status_code == 409
    statuses = {item["key"]: item["status"] for item in await items_of(engine, tenant, customer_id)}
    assert statuses == {"name": "active", "language": "deleted", "company": "active"}


async def test_p2_api_06_cross_tenant_memory_is_unreachable(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(
        services, settings, "isolation-key", [op_add("profile", "name", "张三")]
    )
    item = (await items_of(engine, tenant, customer_id))[0]
    outsider_id = uuid4()
    async with engine.begin() as c:
        await c.execute(
            text("INSERT INTO memory_customers(id,tenant_id) VALUES(:id,'other-tenant')"),
            {"id": outsider_id},
        )
    # Another tenant's customer does not exist as far as the API is concerned.
    assert (await client.get(f"/api/v1/memory/customers/{outsider_id}")).status_code == 404
    listing = (await client.get("/api/v1/memory/customers")).json()
    assert listing["total"] == 1
    # Nor can this tenant's items be reached from another tenant's service calls.
    with pytest.raises(DomainError, match="memory_item_not_found"):
        await services.platform.memory.edit_item("other-tenant", "x", item["id"], "篡改")
    with pytest.raises(DomainError, match="memory_customer_not_found"):
        await services.platform.memory.customer_detail("other-tenant", customer_id)
    detail = (await client.get(f"/api/v1/memory/customers/{customer_id}")).json()
    assert [i["external_key"] for i in detail["identities"]] == ["isolation-key"]
    assert [i["key"] for i in detail["items"]] == ["name"]


# ---------------------------------------------------------------------------
# P2-CM: audit and gateway usage
# ---------------------------------------------------------------------------


async def test_p2_cm_03_mutations_leave_content_free_audit(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    agent, _ = await create_agent(client)
    channel = await make_channel(client, agent)
    first = await ingest(client, channel, "m1", "customer1", "我叫张三")
    await execute_next(services, settings)
    customer_id = (await conversation_detail(client, first["conversation_id"]))["customer_id"]
    worker = make_worker(
        services,
        settings,
        RecordingGateway('[{"op":"add","type":"profile","key":"name","content":"张三","confidence":0.9}]'),
    )
    assert await worker.run_once() is True
    item = (await items_of(engine, tenant, customer_id))[0]
    await client.patch(f"/api/v1/memory/items/{item['id']}", json={"content": "张叁"})
    await client.delete(f"/api/v1/memory/items/{item['id']}")
    await client.post(f"/api/v1/memory/items/{item['id']}/restore")
    actions = [
        "memory.extract.applied",
        "memory.item.edited",
        "memory.item.deleted",
        "memory.item.restored",
    ]
    for action in actions:
        records = await audit_records(engine, tenant, action)
        assert len(records) == 1, action
        blob = json.dumps(records[0]["details"], ensure_ascii=False)
        assert "张" not in blob  # audit payloads never carry memory content
    applied = (await audit_records(engine, tenant, "memory.extract.applied"))[0]
    assert applied["details"]["added"] == 1
    assert applied["details"]["conversation_id"] == first["conversation_id"]


async def test_p2_cm_04_extraction_usage_lands_in_gateway_calls(platform, caplog):
    """The real gateway path (demo profile) records usage attributable to memory."""
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    agent, _ = await create_agent(client)
    channel = await make_channel(client, agent)
    await ingest(client, channel, "m1", "customer1", "我叫张三")
    await execute_next(services, settings)
    # The demo model's prose is not valid JSON: the batch is dropped with a
    # warning, the task completes, and the call is still metered (宁缺毋滥).
    worker = make_worker(services, settings)
    with caplog.at_level(logging.WARNING):
        assert await worker.run_once() is True
    assert any("not valid JSON" in record.message for record in caplog.records)
    task = (await extract_tasks(engine, tenant))[0]
    assert task["status"] == "done"
    calls = await db_rows(
        engine,
        "SELECT * FROM gateway_calls WHERE tenant_id=:t AND actor='memory'",
        t=tenant,
    )
    assert len(calls) == 1 and calls[0]["status"] == "completed"
    rows = await db_rows(engine, "SELECT id FROM memory_items WHERE tenant_id=:t", t=tenant)
    assert rows == []  # nothing written
