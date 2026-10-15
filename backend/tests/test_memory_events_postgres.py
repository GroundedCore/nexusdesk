"""Phase 3 event memory + semantic search — PostgreSQL integration tests.

Case IDs refer to
docs/requirement/agent-memory/phase-3-event-memory-search/04-test-plan.md.
Runs only with TEST_DATABASE_URL; skipped locally without one.

Dual-mode matrix: run the suite twice, once against a plain postgres:17
database (text mode) and once against pgvector/pgvector:pg17 migrated with
the extension (vector mode). Tests that need the ``embedding`` column skip
themselves on plain PG; tests that assert the degraded path skip themselves
when the column is present.
"""

import logging
from uuid import uuid4

import pytest
from sqlalchemy import text
from test_platform_postgres import platform as _platform

from agent_platform.modules.memory.store import (
    EMBEDDING_DIMENSION,
    PgVectorStore,
    TsVectorStore,
    build_memory_store,
)

platform = _platform
pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def db_rows(engine, sql, **params):
    async with engine.connect() as c:
        rows = (await c.execute(text(sql), params)).mappings().all()
        return [dict(row) for row in rows]


async def has_embedding_column(engine) -> bool:
    rows = await db_rows(
        engine,
        """SELECT 1 AS x FROM information_schema.columns
        WHERE table_name='memory_items' AND column_name='embedding'""",
    )
    return bool(rows)


async def make_embed_profile(client, dimension=EMBEDDING_DIMENSION, operation="embed"):
    """Register + publish a model profile through the gateway API."""
    from test_model_gateway import setup

    _, _, profile = await setup(client, operation, model_extra={"embedding_dimension": dimension})
    return profile


def store_settings(settings, **overrides):
    base = {
        "memory_embedding_profile_id": None,
        "memory_embedding_profile_version": None,
        "memory_vector_enabled": "auto",
    }
    return settings.model_copy(update={**base, **overrides})


async def make_customer(engine, tenant):
    customer_id = uuid4()
    async with engine.begin() as c:
        await c.execute(
            text("INSERT INTO memory_customers(id,tenant_id) VALUES(:id,:t)"),
            {"id": customer_id, "t": tenant},
        )
    return customer_id


async def add_event_row(engine, tenant, customer_id, key, content, **overrides):
    """Direct-write one memory_items row (data construction, plan section 2)."""
    row = {
        "id": uuid4(),
        "key": key,
        "content": content,
        "type": "event",
        "status": "active",
        "source_type": "ticket",
        "confidence": 1.0,
        **overrides,
    }
    async with engine.begin() as c:
        await c.execute(
            text(
                """INSERT INTO memory_items(id,tenant_id,customer_id,type,key,content,
                confidence,status,source_type)
                VALUES(:id,:t,:c,:type,:key,:content,:confidence,:status,:source_type)"""
            ),
            {"id": row["id"], "t": tenant, "c": customer_id, **row},
        )
    return row["id"]


async def set_embedding(engine, item_id, vector):
    async with engine.begin() as c:
        await c.execute(
            text("UPDATE memory_items SET embedding=CAST(:v AS vector) WHERE id=:id"),
            {"v": "[" + ",".join(str(x) for x in vector) + "]", "id": item_id},
        )


class StubEmbedGateway:
    """Programmable embedding responses for PgVectorStore.search/embed_and_save."""

    def __init__(self, vector, error=None):
        self.vector = vector
        self.error = error
        self.calls = []

    async def invoke(self, tenant, profile_id, version, request, **kwargs):
        self.calls.append({"tenant": tenant, "inputs": request.inputs})
        if self.error is not None:
            raise self.error
        return {
            "payload": {
                "vectors": [{"id": item.id, "vector": self.vector} for item in request.inputs]
            }
        }


# ---------------------------------------------------------------------------
# P3-UT-01/02/03 store probing (build_memory_store)
# ---------------------------------------------------------------------------


async def test_p3_ut_01_probe_vector_when_column_and_profile_ready(platform):
    client, services, settings = platform
    engine = services.repository.engine
    if not await has_embedding_column(engine):
        pytest.skip("vector mode requires the pgvector-migrated database")
    profile = await make_embed_profile(client)
    probed = await build_memory_store(
        engine,
        services.platform.gateway,
        store_settings(
            settings,
            memory_embedding_profile_id=profile["id"],
            memory_embedding_profile_version=1,
        ),
    )
    assert isinstance(probed, PgVectorStore)
    assert probed.available() == "vector"


async def test_p3_ut_02_probe_text_without_vector_column(platform):
    client, services, settings = platform
    engine = services.repository.engine
    if await has_embedding_column(engine):
        pytest.skip("degraded-path assertion needs a database without the embedding column")
    profile = await make_embed_profile(client)
    probed = await build_memory_store(
        engine,
        services.platform.gateway,
        store_settings(
            settings,
            memory_embedding_profile_id=profile["id"],
            memory_embedding_profile_version=1,
        ),
    )
    assert isinstance(probed, TsVectorStore)
    assert probed.available() == "text"


async def test_p3_ut_02_probe_text_when_profile_missing(platform):
    _client, services, settings = platform
    engine = services.repository.engine
    if not await has_embedding_column(engine):
        pytest.skip("profile-probing variant needs the embedding column present")
    probed = await build_memory_store(engine, services.platform.gateway, store_settings(settings))
    assert isinstance(probed, TsVectorStore)
    assert probed.available() == "text"


async def test_p3_ut_02_probe_text_when_profile_not_embed(platform):
    client, services, settings = platform
    engine = services.repository.engine
    if not await has_embedding_column(engine):
        pytest.skip("profile-probing variant needs the embedding column present")
    profile = await make_embed_profile(client, operation="chat")
    probed = await build_memory_store(
        engine,
        services.platform.gateway,
        store_settings(
            settings,
            memory_embedding_profile_id=profile["id"],
            memory_embedding_profile_version=1,
        ),
    )
    assert isinstance(probed, TsVectorStore)
    assert probed.available() == "text"


async def test_p3_dp_05_probe_text_with_warning_on_dimension_mismatch(platform, caplog):
    """P3-DP-05 per R14: a wrong-dimension profile degrades probing to text
    mode with a clear warning (there is no binding-time validation entry)."""
    client, services, settings = platform
    engine = services.repository.engine
    if not await has_embedding_column(engine):
        pytest.skip("dimension probing needs the embedding column present")
    profile = await make_embed_profile(client, dimension=8)
    with caplog.at_level(logging.WARNING, logger="agent_platform.modules.memory.store"):
        probed = await build_memory_store(
            engine,
            services.platform.gateway,
            store_settings(
                settings,
                memory_embedding_profile_id=profile["id"],
                memory_embedding_profile_version=1,
            ),
        )
    assert isinstance(probed, TsVectorStore)
    assert probed.available() == "text"
    assert any(
        "8" in record.getMessage() and str(EMBEDDING_DIMENSION) in record.getMessage()
        for record in caplog.records
    )


async def test_p3_ut_03_vector_enabled_false_forces_text(platform):
    client, services, settings = platform
    engine = services.repository.engine
    if not await has_embedding_column(engine):
        pytest.skip("override assertion needs the embedding column present")
    profile = await make_embed_profile(client)
    probed = await build_memory_store(
        engine,
        services.platform.gateway,
        store_settings(
            settings,
            memory_embedding_profile_id=profile["id"],
            memory_embedding_profile_version=1,
            memory_vector_enabled="false",
        ),
    )
    assert isinstance(probed, TsVectorStore)
    assert probed.available() == "text"


async def test_p3_ut_03_vector_enabled_true_forces_vector_without_profile(platform):
    _client, services, settings = platform
    engine = services.repository.engine
    if not await has_embedding_column(engine):
        pytest.skip("override assertion needs the embedding column present")
    probed = await build_memory_store(
        engine,
        services.platform.gateway,
        store_settings(settings, memory_vector_enabled="true"),
    )
    assert isinstance(probed, PgVectorStore)
    assert probed.available() == "vector"


async def test_p3_ut_03_vector_enabled_true_without_column_still_text(platform, caplog):
    _client, services, settings = platform
    engine = services.repository.engine
    if await has_embedding_column(engine):
        pytest.skip("needs a database without the embedding column")
    with caplog.at_level(logging.WARNING, logger="agent_platform.modules.memory.store"):
        probed = await build_memory_store(
            engine,
            services.platform.gateway,
            store_settings(settings, memory_vector_enabled="true"),
        )
    assert isinstance(probed, TsVectorStore)
    assert any("memory_vector_enabled=true" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# P3-UT-11 / P3-IT-11 retrieval filtering: tenant + customer + active + event.
# ---------------------------------------------------------------------------


async def _seed_isolation_rows(engine, tenant, customer_id):
    """One matching row plus decoys along every filter dimension."""
    target = await add_event_row(
        engine, tenant, customer_id, "ticket:TK-100:resolved", "工单 TK-100 已解决：更换了主板"
    )
    other_customer = await make_customer(engine, tenant)
    decoys = [
        await add_event_row(
            engine,
            tenant,
            other_customer,
            "ticket:TK-200:resolved",
            "工单 TK-200 已解决：更换了主板",
        ),
        await add_event_row(
            engine,
            "other-" + tenant,
            customer_id,
            "ticket:TK-300:resolved",
            "工单 TK-300 已解决：更换了主板",
        ),
        await add_event_row(
            engine,
            tenant,
            customer_id,
            "ticket:TK-400:resolved",
            "工单 TK-400 已解决：更换了主板",
            status="expired",
        ),
        await add_event_row(
            engine,
            tenant,
            customer_id,
            "profile:name",
            "客户喜欢主板话题",
            type="profile",
        ),
    ]
    return target, decoys


async def test_p3_ut_11_text_store_filters_tenant_customer_status_type(platform):
    _client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    target, _decoys = await _seed_isolation_rows(engine, tenant, customer_id)
    store = TsVectorStore(engine, store_settings(settings))
    hits = await store.search(tenant, customer_id, "主板", 10)
    assert [hit["key"] for hit in hits] == ["ticket:TK-100:resolved"]
    # And the target row really is the seeded one (tenant-scoped: earlier
    # runs of this suite can leave same-key rows under other tenants).
    rows = await db_rows(
        engine,
        "SELECT id FROM memory_items WHERE tenant_id=:t AND key='ticket:TK-100:resolved'",
        t=tenant,
    )
    assert rows[0]["id"] == target


async def test_p3_ut_11_vector_store_applies_same_filters(platform):
    _client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    if not await has_embedding_column(engine):
        pytest.skip("vector store filtering needs the embedding column")
    customer_id = await make_customer(engine, tenant)
    target, decoys = await _seed_isolation_rows(engine, tenant, customer_id)
    # Embeddings for every seeded row: identical vectors, so any leak would
    # surface as an extra hit rather than being masked by distance.
    for item_id in [target, *decoys]:
        await set_embedding(engine, item_id, [1.0] + [0.0] * (EMBEDDING_DIMENSION - 1))
    gateway = StubEmbedGateway([1.0] + [0.0] * (EMBEDDING_DIMENSION - 1))
    store = PgVectorStore(engine, gateway, store_settings(settings))
    hits = await store.search(tenant, customer_id, "anything", 10)
    assert [hit["key"] for hit in hits] == ["ticket:TK-100:resolved"]


async def test_p3_ut_09_search_limit_clamped_to_configured_max(platform):
    _client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    for i in range(12):
        await add_event_row(
            engine, tenant, customer_id, f"ticket:TK-{i:03d}:created", f"工单 TK-{i:03d} 主板维修"
        )
    store = TsVectorStore(engine, store_settings(settings, memory_search_limit_max=10))
    hits = await store.search(tenant, customer_id, "主板", 50)
    assert len(hits) == 10


# ---------------------------------------------------------------------------
# Ticket lifecycle helpers (T4/T5: hooks dispatch, EventWriter executes)
# ---------------------------------------------------------------------------


async def make_bound_ticket(client, services, settings, text="请创建维修工单"):
    """Approve a proposed ticket from a customer-bound channel conversation."""
    from test_memory_customers_postgres import (
        conversation_detail,
        ingest,
        make_channel,
    )
    from test_platform_postgres import create_agent, execute_next

    agent, _ = await create_agent(client, ["propose_ticket"])
    channel = await make_channel(client, agent)
    incoming = await ingest(client, channel, "m-" + uuid4().hex[:8], "customer1", text)
    final = await execute_next(services, settings)
    assert final["status"] == "completed", final
    detail = await conversation_detail(client, incoming["conversation_id"])
    action = detail["actions"][0]
    response = await client.post(f"/api/v1/actions/{action['id']}/decision", json={"approve": True})
    assert response.status_code == 200, response.text
    return response.json(), detail["customer_id"]


async def event_tasks(engine, tenant):
    return await db_rows(
        engine,
        "SELECT * FROM memory_tasks WHERE tenant_id=:t AND kind='event_write'"
        " ORDER BY created_at,id",
        t=tenant,
    )


async def run_event_tasks(engine, services, settings, store=None):
    """Execute every queued event_write task through the real EventWriter."""
    from agent_platform.modules.memory.events import EventWriter

    store = store or TsVectorStore(engine, store_settings(settings))
    writer = EventWriter(engine, store, store_settings(settings))
    outcomes = []
    for task in await event_tasks(engine, settings.tenant_id):
        if task["status"] != "pending":
            continue
        outcomes.append(await writer.execute(task))
        async with engine.begin() as c:
            await c.execute(
                text("UPDATE memory_tasks SET status='done' WHERE id=:id"), {"id": task["id"]}
            )
    return outcomes


async def items_by_key(engine, tenant, customer_id):
    rows = await db_rows(
        engine,
        "SELECT * FROM memory_items WHERE tenant_id=:t AND customer_id=:c ORDER BY key",
        t=tenant,
        c=customer_id,
    )
    return {row["key"]: row for row in rows}


# ---------------------------------------------------------------------------
# P3-IT-01 ticket lifecycle -> event rows (FR-1, AC-6, P3-UT-07/08)
# ---------------------------------------------------------------------------


async def test_p3_it_01_ticket_lifecycle_writes_event_memory(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    ticket, customer_id = await make_bound_ticket(client, services, settings)
    ticket_no = ticket["ticket_no"]

    # create: the hook queued exactly one event_write task, synchronously.
    tasks = await event_tasks(engine, tenant)
    assert len(tasks) == 1 and tasks[0]["payload"]["event"] == "created"
    assert tasks[0]["status"] == "pending"  # async: nothing written yet
    assert await run_event_tasks(engine, services, settings) == ["done"]

    items = await items_by_key(engine, tenant, customer_id)
    created = items[f"ticket:{ticket_no}:created"]
    open_row = items[f"open_ticket:{ticket_no}"]
    for row in (created, open_row):
        assert row["status"] == "active"
        assert row["source_type"] == "ticket"
        assert row["confidence"] == 1.0
        assert row["source_ref"] == ticket["id"]
        assert row["type"] == "event"
    assert created["content"].startswith(f"工单 {ticket_no} 已创建：")
    # P3-UT-08: event rows expire at now + TTL; two rows of one event share it.
    ttl_seconds = (created["expires_at"] - created["updated_at"]).total_seconds()
    assert abs(ttl_seconds - settings.memory_event_ttl_days * 86400) < 120

    # resolve: one more task; open_ticket row expires (AC-6), not deleted.
    response = await client.patch(
        f"/api/v1/tickets/{ticket['id']}",
        json={"status": "in_progress", "note": "已安排师傅", "revision": 1},
    )
    assert response.status_code == 200, response.text
    response = await client.patch(
        f"/api/v1/tickets/{ticket['id']}",
        json={"status": "resolved", "note": "更换主板后恢复", "revision": 2},
    )
    assert response.status_code == 200, response.text
    assert (await event_tasks(engine, tenant))[-1]["payload"]["event"] == "resolved"
    assert await run_event_tasks(engine, services, settings) == ["done"]

    items = await items_by_key(engine, tenant, customer_id)
    resolved = items[f"ticket:{ticket_no}:resolved"]
    assert resolved["status"] == "active"
    assert resolved["content"] == f"工单 {ticket_no} 已解决：更换主板后恢复"
    assert items[f"open_ticket:{ticket_no}"]["status"] == "expired"  # kept, not deleted

    # close: third event row.
    response = await client.patch(
        f"/api/v1/tickets/{ticket['id']}",
        json={"status": "closed", "note": "客户确认无误", "revision": 3},
    )
    assert response.status_code == 200, response.text
    assert await run_event_tasks(engine, services, settings) == ["done"]

    items = await items_by_key(engine, tenant, customer_id)
    closed = items[f"ticket:{ticket_no}:closed"]
    assert closed["status"] == "active"
    assert closed["content"] == f"工单 {ticket_no} 已关闭：客户确认无误"
    active_events = [
        key for key, row in items.items() if row["type"] == "event" and row["status"] == "active"
    ]
    assert sorted(active_events) == [
        f"ticket:{ticket_no}:closed",
        f"ticket:{ticket_no}:created",
        f"ticket:{ticket_no}:resolved",
    ]


async def test_p3_it_02_event_write_is_asynchronous(platform):
    """The synchronous ticket path only INSERTs memory_tasks (pending); the
    memory rows appear only after the task executes (non-functional)."""
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    _ticket, customer_id = await make_bound_ticket(client, services, settings)
    tasks = await event_tasks(engine, tenant)
    assert len(tasks) == 1 and tasks[0]["status"] == "pending"
    # Before any worker pass: no memory rows for this customer at all.
    assert await items_by_key(engine, tenant, customer_id) == {}
    assert await run_event_tasks(engine, services, settings) == ["done"]
    assert len(await items_by_key(engine, tenant, customer_id)) == 2


async def test_p3_it_03_customerless_ticket_produces_no_event(platform):
    from test_platform_postgres import conversation, create_agent, execute_next

    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    agent, _ = await create_agent(client, ["propose_ticket"])
    conv = await conversation(client, agent["id"])
    await client.post(
        f"/api/v1/conversations/{conv['id']}/messages", json={"content": "请创建维修工单"}
    )
    final = await execute_next(services, settings)
    assert final["status"] == "completed", final
    detail = (await client.get(f"/api/v1/conversations/{conv['id']}")).json()
    assert detail["customer_id"] is None
    action = detail["actions"][0]
    response = await client.post(f"/api/v1/actions/{action['id']}/decision", json={"approve": True})
    assert response.status_code == 200, response.text  # ticket flow unaffected
    assert response.json()["customer_id"] is None
    assert await event_tasks(engine, tenant) == []  # no dispatch, nothing to run


async def test_p3_it_04_commitment_remark_entry(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    ticket, customer_id = await make_bound_ticket(client, services, settings)

    # Unflagged remark: no commitment task.
    response = await client.patch(
        f"/api/v1/tickets/{ticket['id']}",
        json={"status": "in_progress", "note": "普通备注", "revision": 1},
    )
    assert response.status_code == 200, response.text
    assert [t["payload"]["event"] for t in await event_tasks(engine, tenant)] == ["created"]

    # Flagged remark: commitment task carrying the process-log id.
    response = await client.patch(
        f"/api/v1/tickets/{ticket['id']}",
        json={
            "status": "in_progress",
            "note": "48 小时内完成退款",
            "revision": 2,
            "is_commitment": True,
        },
    )
    assert response.status_code == 200, response.text
    commitment_task = (await event_tasks(engine, tenant))[-1]
    assert commitment_task["payload"]["event"] == "commitment"
    assert commitment_task["payload"]["log_id"] is not None

    # The process-log row carries the flag.
    logs = await db_rows(
        engine,
        "SELECT * FROM ticket_process_log WHERE tenant_id=:t AND ticket_id=:id"
        " AND action_type='remark' ORDER BY id",
        t=tenant,
        id=ticket["id"],
    )
    assert [row["is_commitment"] for row in logs] == [False, True]

    assert await run_event_tasks(engine, services, settings) == ["done", "done"]
    items = await items_by_key(engine, tenant, customer_id)
    commitment = items[f"commitment:{commitment_task['payload']['log_id']}"]
    assert commitment["content"] == "48 小时内完成退款"
    assert commitment["status"] == "active"
    assert commitment["expires_at"] is None  # P3-UT-08: commitments never TTL-expire
    assert commitment["source_ref"] == f"{ticket['id']}:{commitment_task['payload']['log_id']}"


# ---------------------------------------------------------------------------
# P3-IT-05 pending-event injection (FR-3, AC-3, AC-6)
# ---------------------------------------------------------------------------


class CaptureModel:
    def __init__(self, reply="回答"):
        self.seen = []
        self.reply = reply

    async def ainvoke(self, messages):
        self.seen.append(messages)
        from langchain_core.messages import AIMessage

        return AIMessage(content=self.reply)


async def make_bound_conversation(client, services, settings, customer_id):
    """A conversation row pre-bound to a customer (data construction)."""
    import json as _json

    cid = uuid4()
    engine, tenant = services.repository.engine, settings.tenant_id
    async with engine.begin() as c:
        await c.execute(
            text(
                """INSERT INTO runtime_conversations(id,tenant_id,external_id,history,customer_id)
                VALUES(:id,:t,:external,CAST(:history AS jsonb),:customer)"""
            ),
            {
                "id": cid,
                "t": tenant,
                "external": "conv-" + uuid4().hex[:8],
                "history": _json.dumps([]),
                "customer": customer_id,
            },
        )
    return cid


async def run_capture(client, services, settings, cid, model):
    from test_memory_customers_postgres import claim_and_run

    services.worker.runtime.legacy.model = model
    response = await client.post(
        f"/api/v1/conversations/{cid}/messages", json={"content": "进度怎样了"}
    )
    assert response.status_code == 202, response.text
    await claim_and_run(services, settings)


def injected_text(model):
    return "\n".join(message.content for message in model.seen[0] if message.type == "system")


async def test_p3_it_05_pending_events_injected_then_cleared_on_resolve(platform):
    client, services, settings = platform
    engine = services.repository.engine
    ticket, customer_id = await make_bound_ticket(client, services, settings)
    ticket_no = ticket["ticket_no"]
    assert await run_event_tasks(engine, services, settings) == ["done"]
    cid = await make_bound_conversation(client, services, settings, customer_id)

    # AC-3: an open ticket shows up in the injected system context.
    capture = CaptureModel()
    await run_capture(client, services, settings, cid, capture)
    context = injected_text(capture)
    assert "【待跟进事项】" in context
    assert f"工单 {ticket_no} 处理中" in context

    # AC-6: once resolved and the event task ran, the section disappears.
    response = await client.patch(
        f"/api/v1/tickets/{ticket['id']}",
        json={"status": "in_progress", "note": "处理中", "revision": 1},
    )
    assert response.status_code == 200, response.text
    response = await client.patch(
        f"/api/v1/tickets/{ticket['id']}",
        json={"status": "resolved", "note": "已修复", "revision": 2},
    )
    assert response.status_code == 200, response.text
    assert await run_event_tasks(engine, services, settings) == ["done"]
    capture = CaptureModel()
    await run_capture(client, services, settings, cid, capture)
    assert "【待跟进事项】" not in injected_text(capture)


async def test_p3_it_05_injection_capped_at_five_newest_first(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    # Seven candidate rows: 6 open tickets + 1 commitment, staggered ages.
    for i in range(6):
        await add_event_row(
            engine, tenant, customer_id, f"open_ticket:TK-{i:03d}", f"工单 TK-{i:03d} 事项"
        )
    await add_event_row(engine, tenant, customer_id, "commitment:1", "承诺退款", source_ref="t:1")
    async with engine.begin() as c:
        await c.execute(
            text(
                """UPDATE memory_items SET updated_at=now()-(:i*interval '1 day')
                WHERE tenant_id=:t AND key=:key"""
            ),
            [{"i": i, "t": tenant, "key": f"open_ticket:TK-{i:03d}"} for i in range(6)]
            + [{"i": 9, "t": tenant, "key": "commitment:1"}],
        )
        # commitment:1 is oldest; TK-000 newest.
    cid = await make_bound_conversation(client, services, settings, customer_id)
    capture = CaptureModel()
    await run_capture(client, services, settings, cid, capture)
    context = injected_text(capture)
    assert "【待跟进事项】" in context
    section = context.split("【待跟进事项】", 1)[1]
    lines = [line for line in section.splitlines() if line.startswith("- ")]
    assert len(lines) == 5  # memory_event_inject_max default
    # updated_at DESC: TK-000 (today) first; the 6-day-old TK-005 and the
    # 9-day-old commitment fall off the cap.
    assert lines[0].startswith("- 工单 TK-000 处理中")
    assert "TK-005" not in section and "承诺退款" not in section
    # Commitment rows render with their own prefix when inside the cap.
    async with engine.begin() as c:
        await c.execute(
            text(
                "UPDATE memory_items SET updated_at=now() WHERE tenant_id=:t AND key='commitment:1'"
            ),
            {"t": tenant},
        )
    capture = CaptureModel()
    await run_capture(client, services, settings, cid, capture)
    assert "- 承诺待兑现：承诺退款" in injected_text(capture)


# ---------------------------------------------------------------------------
# P3-IT-06 / P3-IT-07 memory_search through a run, both retrieval modes
# ---------------------------------------------------------------------------


async def make_memory_agent(client, tools=("memory_search",)):
    from test_platform_postgres import create_agent

    agent, _ = await create_agent(client, list(tools))
    return agent


def tool_call_then_answer(query, answer="根据记录回答"):
    from langchain_core.messages import AIMessage

    class Model:
        def __init__(self):
            self.responses = [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"id": "call_1", "name": "memory_search", "args": {"query": query}}
                    ],
                ),
                AIMessage(content=answer),
            ]
            self.seen = []

        async def ainvoke(self, messages):
            self.seen.append(messages)
            return self.responses.pop(0)

    return Model()


async def seed_resolution_event(
    engine, tenant, customer_id, days_old=0, key="ticket:TK-100:resolved"
):
    item_id = await add_event_row(
        engine, tenant, customer_id, key, "工单 TK-100 已解决：更换了主板"
    )
    if days_old:
        async with engine.begin() as c:
            await c.execute(
                text(
                    "UPDATE memory_items SET last_referenced_at=now()-:d*interval '1 day'"
                    " WHERE id=:id"
                ),
                {"d": days_old, "id": item_id},
            )
    return item_id


async def test_p3_it_06_memory_search_tool_end_to_end(platform):
    """Mock (demo) model calls memory_search mid-run; the tool executes against
    the startup-probed store and the hit feeds the final answer (AC-1/AC-4).

    The demo model emits the tool call when the message contains "上次" and
    uses the whole message as the query, so the seeded event content carries
    that exact phrase (text mode needs lexical overlap, plan section 2).
    """
    from test_platform_postgres import conversation, execute_next

    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    agent = await make_memory_agent(client)
    customer_id = await make_customer(engine, tenant)
    message = "上次的工单处理进展"
    item_id = await add_event_row(
        engine,
        tenant,
        customer_id,
        "ticket:TK-100:resolved",
        "工单 TK-100 已解决：上次的工单处理进展为已更换主板",
    )
    conv = await conversation(client, agent["id"])
    async with engine.begin() as c:
        await c.execute(
            text("UPDATE runtime_conversations SET customer_id=:c WHERE id=:id"),
            {"c": customer_id, "id": conv["id"]},
        )

    if await has_embedding_column(engine):
        # Vector leg: route the tool through a PgVectorStore with a stubbed
        # embed gateway (deterministic cosine) over the real pgvector SQL.
        await set_embedding(engine, item_id, [1.0] + [0.0] * (EMBEDDING_DIMENSION - 1))
        services.platform.memory_store = PgVectorStore(
            engine,
            StubEmbedGateway([1.0] + [0.0] * (EMBEDDING_DIMENSION - 1)),
            store_settings(settings),
        )
    else:
        services.platform.memory_store = TsVectorStore(engine, store_settings(settings))

    response = await client.post(
        f"/api/v1/conversations/{conv['id']}/messages", json={"content": message}
    )
    assert response.status_code == 202, response.text
    final = await execute_next(services, settings)
    assert final["status"] == "completed", final

    # The tool executed and the event content flowed back into the answer.
    calls = await db_rows(
        engine,
        "SELECT * FROM tool_calls WHERE tenant_id=:t AND tool_name='memory_search'",
        t=tenant,
    )
    assert len(calls) == 1 and calls[0]["status"] == "succeeded"
    detail = (await client.get(f"/api/v1/conversations/{conv['id']}")).json()
    answers = [m["content"] for m in detail["messages"] if m["role"] == "assistant"]
    assert any("更换主板" in answer for answer in answers), answers


async def test_p3_it_07_dual_mode_retrieval_equivalence(platform):
    """The same query finds the target event in text mode and (when the
    column exists) vector mode (AC-4)."""
    _client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    target = await seed_resolution_event(engine, tenant, customer_id)
    await add_event_row(
        engine, tenant, customer_id, "ticket:TK-101:resolved", "工单 TK-101 已解决：重启了路由器"
    )
    text_hits = await TsVectorStore(engine, store_settings(settings)).search(
        tenant, customer_id, "主板", 5
    )
    assert [hit["key"] for hit in text_hits] == ["ticket:TK-100:resolved"]
    if not await has_embedding_column(engine):
        pytest.skip("vector leg needs the pgvector-migrated database")
    # Vector mode with controlled embeddings: target close to the query,
    # decoy orthogonal.
    await set_embedding(engine, target, [1.0] + [0.0] * (EMBEDDING_DIMENSION - 1))
    decoy = await db_rows(
        engine,
        "SELECT id FROM memory_items WHERE tenant_id=:t AND key='ticket:TK-101:resolved'",
        t=tenant,
    )
    await set_embedding(engine, decoy[0]["id"], [0.0, 1.0] + [0.0] * (EMBEDDING_DIMENSION - 2))
    vector_hits = await PgVectorStore(
        engine,
        StubEmbedGateway([0.99, 0.01] + [0.0] * (EMBEDDING_DIMENSION - 2)),
        store_settings(settings),
    ).search(tenant, customer_id, "主板", 5)
    assert vector_hits and vector_hits[0]["key"] == "ticket:TK-100:resolved"


async def test_p3_it_08_recency_orders_equal_relevance_hits(platform):
    """AC-4b: equal-relevance events rank by recency (3d before 150d); the
    half-life setting shifts scores per the shared formula.

    Text-mode note: segmentable (English) content gives a real ts_rank, so the
    decay math is observable; Chinese ILIKE-only hits all rank 0 (the R9
    documented limitation), where decay cannot reorder ties.
    """
    _client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    recent = await add_event_row(
        engine, tenant, customer_id, "ticket:TK-301:resolved", "ticket resolved: replaced mainboard"
    )
    old = await add_event_row(
        engine, tenant, customer_id, "ticket:TK-302:resolved", "ticket resolved: replaced mainboard"
    )
    async with engine.begin() as c:
        await c.execute(
            text("UPDATE memory_items SET last_referenced_at=now()-interval '3 days' WHERE id=:id"),
            {"id": recent},
        )
        await c.execute(
            text(
                "UPDATE memory_items SET last_referenced_at=now()-interval '150 days' WHERE id=:id"
            ),
            {"id": old},
        )
    hits = await TsVectorStore(engine, store_settings(settings)).search(
        tenant, customer_id, "mainboard", 5
    )
    assert [hit["key"] for hit in hits] == [
        "ticket:TK-301:resolved",
        "ticket:TK-302:resolved",
    ]
    # Real ts_rank on both rows: the decay ratio below is meaningful.
    assert hits[0]["score"] > 0 and hits[1]["score"] > 0
    # Both rows stay visible — decay only reorders (P3-UT-05 at the SQL level).
    assert len(hits) == 2
    # Scores follow relevance * exp(-ln2 * age / half_life) with half_life=30.
    assert hits[0]["score"] / hits[1]["score"] == pytest.approx(2 ** ((150 - 3) / 30), rel=1e-2)
    # A longer half-life compresses the ratio per the same formula.
    hits = await TsVectorStore(
        engine, store_settings(settings, memory_search_half_life_days=365)
    ).search(tenant, customer_id, "mainboard", 5)
    assert hits[0]["score"] / hits[1]["score"] == pytest.approx(2 ** ((150 - 3) / 365), rel=1e-2)


# ---------------------------------------------------------------------------
# P3-IT-09 TTL maintenance (AC-5); P3-IT-10 embedding failure + retry
# ---------------------------------------------------------------------------


def make_maintenance_worker(services, settings, store=None):
    from agent_platform.modules.memory.worker import MemoryWorker

    engine = services.repository.engine
    return MemoryWorker(
        engine,
        service=None,
        settings=store_settings(settings),
        store=store,
    )


async def test_p3_it_09_ttl_pass_soft_expires_and_hides(platform):
    _client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    stale = await add_event_row(
        engine, tenant, customer_id, "ticket:TK-501:resolved", "工单 TK-501 已解决：超龄事件"
    )
    open_row = await add_event_row(engine, tenant, customer_id, "open_ticket:TK-502", "在途但超龄")
    await add_event_row(engine, tenant, customer_id, "commitment:9", "承诺不过期", source_ref="t:9")
    profile_row = await add_event_row(
        engine, tenant, customer_id, "profile:name", "画像不受事件 TTL 影响", type="profile"
    )
    async with engine.begin() as c:
        await c.execute(
            text(
                "UPDATE memory_items SET expires_at=now()-interval '1 day'"
                " WHERE tenant_id=:t AND id IN (:a,:b)"
            ).bindparams(),
            {"t": tenant, "a": stale, "b": open_row},
        )
        # The commitment row outlives any TTL (expires_at IS NULL); the
        # profile row is force-aged to prove the sweep is event-scoped.
        await c.execute(
            text("UPDATE memory_items SET expires_at=now()-interval '1 day' WHERE id=:id"),
            {"id": profile_row},
        )

    worker = make_maintenance_worker(services, settings)
    await worker.maintenance()

    items = await items_by_key(engine, tenant, customer_id)
    assert items["ticket:TK-501:resolved"]["status"] == "expired"  # soft: row kept
    assert items["open_ticket:TK-502"]["status"] == "expired"
    assert items["commitment:9"]["status"] == "active"  # commitment exempt
    assert items["profile:name"]["status"] == "active"  # sweep is event-scoped

    # Expired rows are invisible to both retrieval and injection.
    hits = await TsVectorStore(engine, store_settings(settings)).search(
        tenant, customer_id, "超龄", 5
    )
    assert hits == []
    from agent_platform.modules.memory.service import MemoryService

    pending = await MemoryService(engine, settings).pending_event_items(tenant, customer_id, 5)
    assert [row["key"] for row in pending] == ["commitment:9"]


async def test_p3_it_10_embedding_failure_marks_then_retry_clears(platform):
    """Embedding outage: items still write, embedding_failed=true, keyword
    search keeps working; the maintenance retry clears the flag on recovery
    and keeps it on persistent failure."""
    _client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    if not await has_embedding_column(engine):
        pytest.skip("embedding retry needs the pgvector-migrated database")
    customer_id = await make_customer(engine, tenant)
    item_id = await seed_resolution_event(engine, tenant, customer_id)

    failing = PgVectorStore(
        engine, StubEmbedGateway(None, error=RuntimeError("model down")), store_settings(settings)
    )
    await failing.embed_and_save(item_id, "工单 TK-100 已解决：更换了主板")
    row = (
        await db_rows(
            engine, "SELECT embedding_failed,embedding FROM memory_items WHERE id=:id", id=item_id
        )
    )[0]
    assert row["embedding_failed"] is True and row["embedding"] is None
    # Keyword retrieval is unaffected by the missing embedding.
    hits = await TsVectorStore(engine, store_settings(settings)).search(
        tenant, customer_id, "主板", 5
    )
    assert [hit["key"] for hit in hits] == ["ticket:TK-100:resolved"]

    # Maintenance retry with the model still down: flag kept for the next pass.
    worker = make_maintenance_worker(services, settings, store=failing)
    await worker.maintenance()
    assert (
        await db_rows(engine, "SELECT embedding_failed FROM memory_items WHERE id=:id", id=item_id)
    )[0]["embedding_failed"] is True

    # Recovery: the retry pass writes the vector and clears the flag.
    working = PgVectorStore(
        engine,
        StubEmbedGateway([1.0] + [0.0] * (EMBEDDING_DIMENSION - 1)),
        store_settings(settings),
    )
    worker = make_maintenance_worker(services, settings, store=working)
    await worker.maintenance()
    row = (
        await db_rows(
            engine, "SELECT embedding_failed,embedding FROM memory_items WHERE id=:id", id=item_id
        )
    )[0]
    assert row["embedding_failed"] is False and row["embedding"] is not None


async def test_p3_it_10_event_writer_survives_embedding_outage(platform):
    """Through the real pipeline: a failing embedding provider never fails
    the event_write task itself — rows land keyword-searchable (non-functional
    degradation contract)."""
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    if not await has_embedding_column(engine):
        pytest.skip("needs the pgvector-migrated database")
    ticket, customer_id = await make_bound_ticket(client, services, settings)
    failing = PgVectorStore(
        engine, StubEmbedGateway(None, error=RuntimeError("model down")), store_settings(settings)
    )
    assert await run_event_tasks(engine, services, settings, store=failing) == ["done"]
    items = await items_by_key(engine, tenant, customer_id)
    created = items[f"ticket:{ticket['ticket_no']}:created"]
    assert created["status"] == "active" and created["embedding_failed"] is True


async def test_p3_cm_02_embedding_usage_lands_in_gateway_calls(platform):
    """Embedding goes through the model gateway (actor='memory') and meters
    into gateway_calls like every other model call."""
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    if not await has_embedding_column(engine):
        pytest.skip("needs the pgvector-migrated database")
    profile = await make_embed_profile(client)  # demo adapter, 1536 dimensions
    store = PgVectorStore(
        engine,
        services.platform.gateway,
        store_settings(
            settings,
            memory_embedding_profile_id=profile["id"],
            memory_embedding_profile_version=1,
        ),
    )
    customer_id = await make_customer(engine, tenant)
    item_id = await seed_resolution_event(engine, tenant, customer_id)
    await store.embed_and_save(item_id, "工单 TK-100 已解决：更换了主板")
    row = (
        await db_rows(
            engine, "SELECT embedding_failed,embedding FROM memory_items WHERE id=:id", id=item_id
        )
    )[0]
    assert row["embedding_failed"] is False and row["embedding"] is not None
    calls = await db_rows(
        engine,
        "SELECT * FROM gateway_calls WHERE tenant_id=:t AND actor='memory' AND operation='embed'",
        t=tenant,
    )
    assert calls, "embedding must meter into gateway_calls with actor='memory'"


async def test_p3_cm_03_event_task_failure_backoff_and_audit(platform):
    """memory_tasks semantics for event_write: a crashing executor backs off
    to pending, then fails permanently at the attempts cap with the
    kind-specific audit action memory.event.failed."""
    from test_memory_postgres import audit_records, force_claimable

    from agent_platform.modules.memory.worker import MemoryWorker

    class CrashingDispatcher:
        async def execute(self, task):
            if task["kind"] == "event_write":
                raise RuntimeError("boom")
            return "done"  # other queued kinds (extraction) are not on trial

    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    _ticket, _customer_id = await make_bound_ticket(client, services, settings)
    worker = MemoryWorker(engine, CrashingDispatcher(), settings)
    # Drive the queue until the event task exhausts its attempts cap (other
    # kinds drain alongside); each cycle re-arms the backoff window.
    for _ in range(10):
        await force_claimable(engine, tenant)
        if not await worker.run_once():
            break
        status = (await event_tasks(engine, tenant))[0]["status"]
        if status == "failed":
            break
    tasks = await event_tasks(engine, tenant)
    assert tasks[0]["status"] == "failed"
    assert tasks[0]["attempts"] == tasks[0]["max_attempts"]
    records = await audit_records(engine, tenant, "memory.event.failed")
    assert len(records) == 1
    assert records[0]["details"]["task_id"] == str(tasks[0]["id"])


# ---------------------------------------------------------------------------
# P3-IT-12 retrieval performance benchmark (vector mode, local)
# ---------------------------------------------------------------------------


async def test_p3_it_12_vector_search_p99_under_300ms(platform):
    import time as _time

    _client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    if not await has_embedding_column(engine):
        pytest.skip("vector benchmark needs the pgvector-migrated database")
    customer_id = await make_customer(engine, tenant)
    async with engine.begin() as c:
        await c.execute(
            text(
                """INSERT INTO memory_items(tenant_id,customer_id,type,key,content,source_type)
                SELECT :t,:c,'event','ticket-PERF-'||i,
                '工单 PERF-'||i||' 已解决：维修记录','ticket'
                FROM generate_series(1,1000) AS i"""
            ),
            {"t": tenant, "c": customer_id},
        )
        await c.execute(
            text(
                "UPDATE memory_items SET embedding=CAST(:v AS vector)"
                " WHERE tenant_id=:t AND customer_id=:c"
            ),
            {
                "v": "[" + ",".join(["0.001"] * EMBEDDING_DIMENSION) + "]",
                "t": tenant,
                "c": customer_id,
            },
        )
    store = PgVectorStore(
        engine,
        StubEmbedGateway([0.001] * EMBEDDING_DIMENSION),
        store_settings(settings),
    )
    samples = []
    for _ in range(20):
        started = _time.perf_counter()
        hits = await store.search(tenant, customer_id, "维修", 10)
        samples.append((_time.perf_counter() - started) * 1000)
    assert hits
    p99 = sorted(samples)[int(len(samples) * 0.99) - 1]
    assert p99 <= 300, f"vector search p99 {p99:.1f}ms exceeds the 300ms budget"


# ---------------------------------------------------------------------------
# Regression for 97b74f4: content overwrite clears the stale embedding.
# ---------------------------------------------------------------------------


async def test_add_event_item_overwrite_clears_stale_embedding(platform):
    from agent_platform.modules.memory.service import MemoryService

    _client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    if not await has_embedding_column(engine):
        pytest.skip("needs the pgvector-migrated database")
    customer_id = await make_customer(engine, tenant)
    item_id = await seed_resolution_event(engine, tenant, customer_id)
    await set_embedding(engine, item_id, [1.0] + [0.0] * (EMBEDDING_DIMENSION - 1))
    memory = MemoryService(engine, settings)
    async with engine.begin() as c:
        overwritten = await memory.add_event_item(
            c,
            tenant,
            customer_id,
            "ticket:TK-100:resolved",
            "工单 TK-100 已解决：更换了主板与风扇",
            "ref",
            clear_embedding=True,
        )
    assert overwritten == item_id
    row = (
        await db_rows(
            engine,
            "SELECT content,prev_content,embedding,embedding_failed FROM memory_items WHERE id=:id",
            id=item_id,
        )
    )[0]
    assert row["content"] == "工单 TK-100 已解决：更换了主板与风扇"
    assert row["prev_content"] == "工单 TK-100 已解决：更换了主板"
    assert row["embedding"] is None and row["embedding_failed"] is False
