"""Phase 5 agent memory config — PostgreSQL integration and API tests.

Case IDs refer to docs/requirement/agent-memory/phase-5-agent-memory-config/04-test-plan.md
(P5-IT-*, P5-API-*). Runs only with TEST_DATABASE_URL (CI Backend job); skipped
locally. Helpers are shared with the Phase 1/2 suites so the fixtures and data
construction stay consistent.
"""

import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from test_memory_customers_postgres import (
    conversation_detail,
    extract_tasks,
    ingest,
    items_of,
    make_channel,
    make_customer,
    make_worker,
    op_add,
)
from test_memory_events_postgres import items_by_key, run_event_tasks
from test_memory_postgres import RecordingGateway, audit_records
from test_platform_postgres import create_agent, execute_next
from test_platform_postgres import platform as _platform

from agent_platform.integrations.business_tools.platform_tools import PlatformTools

platform = _platform
pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


async def set_config(client, agent, body, revision, publish_revision):
    response = await client.put(
        f"/api/v1/agents/{agent['id']}", json={**body, "revision": revision}
    )
    assert response.status_code == 200, response.text
    response = await client.post(
        f"/api/v1/agents/{agent['id']}/publish", json={"revision": publish_revision}
    )
    assert response.status_code == 200, response.text


class _SearchStore:
    """In-memory memory_store double recording the effective search limit."""

    def __init__(self):
        self.limits = []

    async def search(self, tenant_id, customer_id, query, limit):
        self.limits.append(limit)
        return [
            {"id": str(uuid4()), "key": f"k{i}", "content": "c",
             "updated_at": "2026-01-01", "score": 0.9}
            for i in range(limit)
        ]


class _AllowAllPolicy:
    async def check(self, tenant, name, allowed):
        return None


def tools_for(settings, store, agent_config):
    """PlatformTools bound to a customer run carrying the given agent config."""
    row = {
        "id": str(uuid4()),
        "tenant_id": settings.tenant_id,
        "conversation_id": str(uuid4()),
        "customer_id": str(uuid4()),
        "config": {"agent": {"config": agent_config}},
    }
    platform_obj = SimpleNamespace(
        policy=_AllowAllPolicy(), memory_store=store, settings=settings
    )
    return PlatformTools(
        SimpleNamespace(schemas=[], specs={}), platform_obj, row, ["memory_search"], [], None
    )


def tool_names(tools):
    return {s["function"]["name"] for s in tools.schemas}


# ---------------------------------------------------------------------------
# P5-IT: runtime gates
# ---------------------------------------------------------------------------


async def test_p5_it_01_master_switch_off_disables_injection_and_extract(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    agent, body = await create_agent(client)
    channel = await make_channel(client, agent)
    # Seed one profile item while memory is fully on.
    first = await ingest(client, channel, "m1", "customer1", "我叫张三")
    await execute_next(services, settings)
    assert (await conversation_detail(client, first["conversation_id"]))["customer_id"]
    worker = make_worker(
        services,
        settings,
        RecordingGateway(
            '[{"op":"add","type":"profile","key":"name","content":"张三","confidence":0.9}]'
        ),
    )
    assert await worker.run_once() is True
    # Flip the master switch off and publish.
    body["config"]["memory_enabled"] = False
    await set_config(client, agent, body, 1, 2)
    # A new session neither injects the existing memory nor dispatches extract.
    await ingest(client, channel, "m2", "customer1", "我又来了")
    row = await services.repository.claim(services.worker.owner, 30, tenant)
    assert await services.worker._customer_memory(row) is None
    await services.worker._execute(row)
    assert [task["status"] for task in await extract_tasks(engine, tenant)] == ["done"]
    # With the master switch off the memory_search tool is not registered, and
    # a direct/hallucinated call is refused (AC-2).
    tools = tools_for(settings, _SearchStore(), {"memory_enabled": False})
    assert "memory_search" not in tool_names(tools)
    refused = await tools._execute("memory_search", {"query": "x"})
    assert refused == {"ok": False, "error": "memory_search_unavailable"}


async def test_p5_it_02_read_only_injects_but_never_writes(platform):
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
        RecordingGateway(
            '[{"op":"add","type":"profile","key":"name","content":"张三","confidence":0.9}]'
        ),
    )
    assert await worker.run_once() is True
    # Write off, read on.
    body["config"]["memory_write"] = False
    await set_config(client, agent, body, 1, 2)
    await ingest(client, channel, "m2", "customer1", "我又来了")
    row = await services.repository.claim(services.worker.owner, 30, tenant)
    injection = await services.worker._customer_memory(row)
    assert injection and "name: 张三" in injection
    await services.worker._execute(row)
    assert [task["status"] for task in await extract_tasks(engine, tenant)] == ["done"]
    assert {item["key"] for item in await items_of(engine, tenant, customer_id)} == {"name"}


async def test_p5_it_03_review_routes_high_confidence_to_pending(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    agent, body = await create_agent(client)
    body["config"]["memory_write_mode"] = "review"
    await set_config(client, agent, body, 1, 2)
    channel = await make_channel(client, agent)
    first = await ingest(client, channel, "m1", "customer1", "我叫张三")
    await execute_next(services, settings)
    customer_id = (await conversation_detail(client, first["conversation_id"]))["customer_id"]
    worker = make_worker(
        services,
        settings,
        RecordingGateway(
            '[{"op":"add","type":"profile","key":"name","content":"张三","confidence":0.99}]'
        ),
    )
    assert await worker.run_once() is True
    items = await items_of(engine, tenant, customer_id)
    assert len(items) == 1 and items[0]["status"] == "pending"
    # AC-4: before confirmation the pending item is invisible to injection...
    await ingest(client, channel, "m2", "customer1", "在吗")
    row = await services.repository.claim(services.worker.owner, 30, tenant)
    assert await services.worker._customer_memory(row) is None
    await services.worker._execute(row)
    # ...and confirming it makes the next session inject it.
    response = await client.post(f"/api/v1/memory/items/{items[0]['id']}/confirm")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"
    await ingest(client, channel, "m3", "customer1", "还在吗")
    row = await services.repository.claim(services.worker.owner, 30, tenant)
    injection = await services.worker._customer_memory(row)
    assert "name: 张三" in injection
    await services.worker._execute(row)


async def test_p5_it_04_review_snapshot_survives_later_config_change(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    agent, body = await create_agent(client)
    body["config"]["memory_write_mode"] = "review"
    await set_config(client, agent, body, 1, 2)
    channel = await make_channel(client, agent)
    first = await ingest(client, channel, "m1", "customer1", "我叫张三")
    # The queued run snapshots review; flip the agent back to auto before it runs.
    body["config"]["memory_write_mode"] = "auto"
    await set_config(client, agent, body, 2, 3)
    await execute_next(services, settings)
    customer_id = (await conversation_detail(client, first["conversation_id"]))["customer_id"]
    worker = make_worker(
        services,
        settings,
        RecordingGateway(
            '[{"op":"add","type":"profile","key":"name","content":"张三","confidence":0.99}]'
        ),
    )
    assert await worker.run_once() is True
    items = await items_of(engine, tenant, customer_id)
    assert len(items) == 1 and items[0]["status"] == "pending"


async def test_p5_it_05_inject_cap_bounds_prompt_items(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    agent, body = await create_agent(client)
    body["config"]["memory_inject_max_items"] = 2
    await set_config(client, agent, body, 1, 2)
    channel = await make_channel(client, agent)
    first = await ingest(client, channel, "m1", "customer1", "我叫张三")
    await execute_next(services, settings)
    customer_id = (await conversation_detail(client, first["conversation_id"]))["customer_id"]
    worker = make_worker(
        services,
        settings,
        RecordingGateway(
            '[{"op":"add","type":"profile","key":"name","content":"张三","confidence":0.9},'
            '{"op":"add","type":"profile","key":"phone","content":"138","confidence":0.9},'
            '{"op":"add","type":"profile","key":"company","content":"某公司","confidence":0.9}]'
        ),
    )
    assert await worker.run_once() is True
    assert len(await items_of(engine, tenant, customer_id)) == 3
    await ingest(client, channel, "m2", "customer1", "我又来了")
    row = await services.repository.claim(services.worker.owner, 30, tenant)
    injection = await services.worker._customer_memory(row)
    assert len([line for line in injection.splitlines() if line.startswith("- ")]) == 2


async def test_types_filter_drops_disallowed_extracted_types(platform):
    _client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    memory = services.platform.memory
    async with engine.begin() as c:
        customer_id = await memory.resolve_customer(c, tenant, "channel", "types-cust")
        counts = await memory.apply_extracted(
            c,
            tenant,
            customer_id,
            [
                op_add("profile", "name", "张三", 0.9),
                op_add("preference", "language", "英文", 0.9),
            ],
            "conv-seed",
            0.8,
            0.5,
            types=["profile"],
        )
    assert counts["added"] == 1 and counts["dropped"] == 1
    assert {item["key"] for item in await items_of(engine, tenant, customer_id)} == {"name"}


async def test_p5_it_06_search_limit_clamps_and_switch_unregisters(platform):
    """C-4: the effective search limit tightens the model-supplied one; with
    search off the tool is not registered and a direct call is refused."""
    _client, _services, settings = platform
    store = _SearchStore()
    # search_limit=2 clamps the model-supplied limit=10 down to 2.
    tools = tools_for(settings, store, {"memory_search_limit": 2})
    assert "memory_search" in tool_names(tools)
    result = await tools._execute("memory_search", {"query": "退货", "limit": 10})
    assert result["ok"] is True and len(result["data"]) == 2
    assert store.limits[-1] == 2
    # search_enabled=false: not registered, and a direct call is refused.
    off = tools_for(settings, store, {"memory_search_enabled": False})
    assert "memory_search" not in tool_names(off)
    refused = await off._execute("memory_search", {"query": "x"})
    assert refused == {"ok": False, "error": "memory_search_unavailable"}


async def test_p5_it_07_event_written_but_not_injected_without_event_type(platform):
    """D3: the event write path is independent of the agent types (global event
    switch on → still written), while injection follows "event" in types."""
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    agent, body = await create_agent(client, ["propose_ticket"])
    body["config"]["memory_types"] = ["profile"]  # no "event"
    await set_config(client, agent, body, 1, 2)
    channel = await make_channel(client, agent)
    incoming = await ingest(
        client, channel, "m-" + uuid4().hex[:8], "customer1", "请创建维修工单"
    )
    assert (await execute_next(services, settings))["status"] == "completed"
    detail = await conversation_detail(client, incoming["conversation_id"])
    customer_id = detail["customer_id"]
    action = detail["actions"][0]
    response = await client.post(
        f"/api/v1/actions/{action['id']}/decision", json={"approve": True}
    )
    assert response.status_code == 200, response.text
    # types excludes event, yet the event rows are written (write path is not
    # gated by the agent types).
    assert await run_event_tasks(engine, services, settings) == ["done"]
    assert any(row["type"] == "event" for row in (await items_by_key(engine, tenant, customer_id)).values())

    def row_with_types(types):
        return {
            "id": str(uuid4()),
            "tenant_id": tenant,
            "customer_id": customer_id,
            "config": {"agent": {"config": {"memory_types": types}}},
            "input": "在吗",
        }

    # Not injected while "event" is absent...
    body_text = await services.worker._customer_memory(row_with_types(["profile"]))
    assert "【待跟进事项】" not in (body_text or "")
    # ...and injected once "event" is allowed.
    assert "【待跟进事项】" in await services.worker._customer_memory(
        row_with_types(["profile", "event"])
    )


async def test_p5_it_08_inflight_run_keeps_submit_time_snapshot(platform):
    """AC-10: a run snapshots the agent config at submit time; editing the
    agent afterwards leaves the in-flight run untouched, new runs see the new
    config."""
    client, services, settings = platform
    tenant = settings.tenant_id
    agent, body = await create_agent(client)
    conv = (
        await client.post(
            "/api/v1/conversations",
            json={"external_id": str(uuid4()), "agent_id": agent["id"]},
        )
    ).json()
    response = await client.post(
        f"/api/v1/conversations/{conv['id']}/messages", json={"content": "第一条"}
    )
    assert response.status_code == 202, response.text
    run_id = UUID(response.json()["run"]["id"])
    # Edit the agent while the run is still queued.
    body["config"]["memory_read_enabled"] = False
    await set_config(client, agent, body, 1, 2)
    # The queued run still carries its submit-time snapshot.
    snapshot = (await services.repository.get(run_id, tenant))["config"]["agent"]["config"]
    assert snapshot["memory_read_enabled"] is True
    # Execute it (the snapshot governs the run), then a new run sees the new config.
    await execute_next(services, settings)
    response = await client.post(
        f"/api/v1/conversations/{conv['id']}/messages", json={"content": "第二条"}
    )
    assert response.status_code == 202, response.text
    new_run_id = UUID(response.json()["run"]["id"])
    new_snapshot = (await services.repository.get(new_run_id, tenant))["config"]["agent"]["config"]
    assert new_snapshot["memory_read_enabled"] is False


async def test_p5_it_09_default_injection_is_byte_identical_to_phase2_fixture(platform):
    """AC-9: under the all-default (Phase 5) config the rendered injection is
    byte-identical to the Phase 2 fixture."""
    _client, services, settings = platform
    tenant = settings.tenant_id
    customer_id = await make_customer(
        services,
        settings,
        "legacy-inject-key",
        [op_add("profile", "name", "张三"), op_add("preference", "language", "英文回复")],
    )
    row = {
        "id": str(uuid4()),
        "tenant_id": tenant,
        "customer_id": customer_id,
        "config": {"agent": {"config": {}}},
        "input": "你好",
    }
    assert await services.worker._customer_memory(row) == (
        "【客户画像】\n- name: 张三\n\n【客户偏好】\n- language: 英文回复"
    )


async def test_p5_it_10_blacklist_handoff_sinks_to_agent_switch(platform):
    """03 §黑名单: the platform switch stays off while the agent opts in — a
    blacklisted customer's message is handed straight to a human (OR semantics
    applied to the conversation's agent snapshot)."""
    client, services, settings = platform
    tenant = settings.tenant_id
    agent, body = await create_agent(client)
    body["config"]["memory_blacklist_auto_handoff"] = True
    await set_config(client, agent, body, 1, 2)
    channel = await make_channel(client, agent)
    first = await ingest(client, channel, "m1", "customer1", "你好")
    await execute_next(services, settings)
    customer_id = (await conversation_detail(client, first["conversation_id"]))["customer_id"]
    assert customer_id
    await services.platform.memory.upsert_flag(
        tenant, "operator", customer_id, "blacklist", "黑名单客户"
    )
    # Platform-level switch is off; only the agent opted in.
    assert settings.memory_blacklist_auto_handoff is False
    response = await client.post(
        f"/api/v1/conversations/{first['conversation_id']}/messages", json={"content": "在吗"}
    )
    assert response.status_code == 202, response.text
    body_json = response.json()
    assert body_json["run"] is None and body_json["handoff"] is not None


# ---------------------------------------------------------------------------
# P5-API
# ---------------------------------------------------------------------------


async def test_p5_api_01_config_save_read_roundtrip_and_rbac(platform):
    client, _, _ = platform
    agent, body = await create_agent(client)
    body["config"].update(
        {
            "memory_read_enabled": False,
            "memory_types": ["profile", "preference"],
            "memory_write_mode": "review",
            "memory_inject_max_items": 15,
            "memory_search_limit": 7,
            "memory_blacklist_auto_handoff": True,
        }
    )
    assert (
        await client.put(f"/api/v1/agents/{agent['id']}", json={**body, "revision": 1})
    ).status_code == 200
    draft = (await client.get(f"/api/v1/agents/{agent['id']}")).json()["draft"]
    assert draft["memory_read_enabled"] is False
    assert draft["memory_types"] == ["profile", "preference"]
    assert draft["memory_write_mode"] == "review"
    assert draft["memory_inject_max_items"] == 15
    assert draft["memory_search_limit"] == 7
    assert draft["memory_blacklist_auto_handoff"] is True
    viewer = {"Authorization": "Bearer platform-viewer"}
    assert (await client.get(f"/api/v1/agents/{agent['id']}", headers=viewer)).status_code == 200
    assert (
        await client.put(
            f"/api/v1/agents/{agent['id']}", json={**body, "revision": 2}, headers=viewer
        )
    ).status_code == 403
    assert (
        await client.get("/api/v1/agents/memory-config-caps", headers={"Authorization": "Bearer bad"})
    ).status_code == 401


async def test_p5_api_02_invalid_config_returns_422(platform):
    client, _, _ = platform
    # Only lower-bound/enum/type violations are rejected; the error detail
    # must name the offending field so the form can point at it.
    cases = (
        ({"memory_types": []}, "memory_types"),
        ({"memory_types": ["flag"]}, "memory_types"),
        ({"memory_write_mode": "manual"}, "memory_write_mode"),
        ({"memory_inject_max_items": 0}, "memory_inject_max_items"),
        ({"memory_search_limit": 0}, "memory_search_limit"),
    )
    for bad, field in cases:
        response = await client.post(
            "/api/v1/agents", json={"name": "x", "config": {"system_prompt": "hi", **bad}}
        )
        assert response.status_code == 422, response.text
        assert field in json.dumps(response.json()["detail"], ensure_ascii=False)
    # AC-7: an above-ceiling value is accepted (saved verbatim) and truncated
    # to the platform ceiling at runtime, so 999 must not be a 422.
    response = await client.post(
        "/api/v1/agents",
        json={"name": "x", "config": {"system_prompt": "hi", "memory_inject_max_items": 999}},
    )
    assert response.status_code == 201, response.text
    assert response.json()["draft"]["memory_inject_max_items"] == 999


async def test_p5_api_03_caps_endpoint_matches_settings(platform):
    client, _, settings = platform
    settings.memory_inject_max_items = 30
    response = await client.get("/api/v1/agents/memory-config-caps")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "summary_enabled": settings.summary_enabled,
        "memory_inject_enabled": settings.memory_inject_enabled,
        "memory_extract_enabled": settings.memory_extract_enabled,
        "memory_inject_max_items": 30,
        "memory_search_limit": settings.memory_search_limit,
        "memory_blacklist_auto_handoff": settings.memory_blacklist_auto_handoff,
    }


async def test_p5_api_04_config_change_is_audited_and_diffable(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    agent, body = await create_agent(client)
    body["config"]["memory_write_mode"] = "review"
    await set_config(client, agent, body, 1, 2)
    records = await audit_records(engine, tenant, "agent.updated")
    assert records and records[0]["actor"]
    # AC-11: the audit record itself carries the before/after values.
    details = records[0]["details"]
    assert details["changes"]["memory_write_mode"] == {"from": "auto", "to": "review"}
    diff = (await client.get(f"/api/v1/agents/{agent['id']}/diff?version=1")).json()
    assert {"field": "memory_write_mode", "published": "auto", "draft": "review"} in diff[
        "changes"
    ]


async def test_p5_api_05_platform_capabilities_never_leak(platform):
    client, _, _ = platform
    agent, _ = await create_agent(client)
    draft = (await client.get(f"/api/v1/agents/{agent['id']}")).json()["draft"]
    for key in (
        "memory_pii_masking_enabled",
        "memory_decay_enabled",
        "memory_max_items_per_customer",
        "memory_rule_scan_interval_minutes",
        "memory_embedding_profile_id",
    ):
        assert key not in draft
    # flags are platform-governed and never an accepted memory_types value.
    assert "flag" not in (await client.get("/api/v1/agents/memory-config-caps")).json()