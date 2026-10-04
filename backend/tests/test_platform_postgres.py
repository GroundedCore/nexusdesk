import asyncio
import os
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import text

from agent_platform.apps.api.main import create_app
from agent_platform.settings import Settings

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


@pytest_asyncio.fixture
async def platform():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires a migrated PostgreSQL test database")
    settings = Settings(
        database_url=url,
        tenant_id="test-" + str(uuid4()),
        embedded_worker=False,
        model_backend="demo",
        tools_file=None,
        api_token=SecretStr("platform-admin"),
        operator_api_token=SecretStr("platform-operator"),
        viewer_api_token=SecretStr("platform-viewer"),
        # Keep tests hermetic: a local .env may narrow the outbound allowlist,
        # which would break tests that rely on the built-in defaults.
        model_gateway_allowed_hosts=Settings.model_fields["model_gateway_allowed_hosts"].default,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):  # noqa: SIM117
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer platform-admin"},
        ) as client:
            try:
                yield client, app.state.runtime, settings
            finally:
                engine = app.state.runtime.repository.engine
                async with engine.begin() as c:
                    await c.execute(
                        text(
                            "DELETE FROM knowledge_links WHERE document_id IN (SELECT id FROM knowledge_documents WHERE tenant_id=:t)"
                        ),
                        {"t": settings.tenant_id},
                    )
                    await c.execute(
                        text("DELETE FROM knowledge_tasks WHERE tenant_id=:t"),
                        {"t": settings.tenant_id},
                    )
                    for table in [
                        "open_request_logs",
                        "open_applications",
                        "enterprise_providers",
                        "enterprise_users",
                        "gateway_alert_events",
                        "gateway_alert_rules",
                        "gateway_access_keys",
                        "gateway_sensitive_words",
                        "gateway_model_settings",
                        "gateway_quotas",
                        "gateway_quota_counters",
                        "gateway_leases",
                        "knowledge_collection_cleanup",
                        "knowledge_vector_indexes",
                        "knowledge_jobs",
                        "staff_accounts",
                        "tool_calls",
                        "gateway_calls",
                        "gateway_media",
                        "gateway_profiles",
                        "gateway_models",
                        "gateway_connections",
                        "evaluation_reports",
                        "evaluation_cases",
                        "channels",
                        "tickets",
                        "ticket_types",
                        "tenant_ticket_settings",
                        "pending_actions",
                        "handoffs",
                    ]:
                        if table == "channels":
                            await c.execute(
                                text(
                                    "DELETE FROM channel_receipts WHERE channel_id IN (SELECT id FROM channels WHERE tenant_id=:t)"
                                ),
                                {"t": settings.tenant_id},
                            )
                        await c.execute(
                            text(f"DELETE FROM {table} WHERE tenant_id=:t"),
                            {"t": settings.tenant_id},
                        )
                    await c.execute(
                        text("DELETE FROM runtime_runs WHERE tenant_id=:t"),
                        {"t": settings.tenant_id},
                    )
                    await c.execute(
                        text("DELETE FROM runtime_conversations WHERE tenant_id=:t"),
                        {"t": settings.tenant_id},
                    )
                    await c.execute(
                        text(
                            "DELETE FROM agent_versions WHERE agent_id IN (SELECT id FROM agents WHERE tenant_id=:t)"
                        ),
                        {"t": settings.tenant_id},
                    )
                    for table in [
                        "agents",
                        "registered_tools",
                        "tool_collections",
                        "audit_records",
                    ]:
                        await c.execute(
                            text(f"DELETE FROM {table} WHERE tenant_id=:t"),
                            {"t": settings.tenant_id},
                        )
                    for table in ["knowledge_chunks", "knowledge_versions"]:
                        await c.execute(
                            text(
                                f"DELETE FROM {table} WHERE document_id IN (SELECT d.id FROM knowledge_documents d WHERE d.tenant_id=:t)"
                            ),
                            {"t": settings.tenant_id},
                        )
                    await c.execute(
                        text("DELETE FROM knowledge_documents WHERE tenant_id=:t"),
                        {"t": settings.tenant_id},
                    )
                    await c.execute(
                        text("DELETE FROM knowledge_folders WHERE tenant_id=:t"),
                        {"t": settings.tenant_id},
                    )
                    await c.execute(
                        text("DELETE FROM knowledge_bases WHERE tenant_id=:t"),
                        {"t": settings.tenant_id},
                    )


async def create_agent(client, tools=None, bases=None, publish=True):
    from test_model_gateway import setup

    _, _, profile = await setup(client, "chat")
    body = {
        "name": "售后客服",
        "description": "测试",
        "config": {
            "system_prompt": "依据知识回答。",
            "model_profile_id": profile["id"],
            "model_profile_version": 1,
            "tool_names": tools or [],
            "knowledge_base_ids": bases or [],
            "max_model_rounds": 6,
        },
    }
    response = await client.post("/api/v1/agents", json=body)
    assert response.status_code == 201, response.text
    agent = response.json()
    if publish:
        response = await client.post(f"/api/v1/agents/{agent['id']}/publish", json={"revision": 1})
        assert response.status_code == 200, response.text
    return agent, body


async def conversation(client, agent=None):
    response = await client.post(
        "/api/v1/conversations", json={"external_id": str(uuid4()), "agent_id": agent}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def execute_next(services, settings):
    row = await services.repository.claim(services.worker.owner, 30, settings.tenant_id)
    assert row is not None
    await services.worker._execute(row)
    return await services.repository.get(row["id"], settings.tenant_id)


async def test_agent_versions_conflicts_and_rollback(platform):
    client, _, _ = platform
    agent, body = await create_agent(client)
    aid = agent["id"]
    body["config"]["system_prompt"] = "新指令"
    assert (
        await client.put(f"/api/v1/agents/{aid}", json={**body, "revision": 1})
    ).status_code == 200
    assert (
        await client.put(f"/api/v1/agents/{aid}", json={**body, "revision": 1})
    ).status_code == 409
    assert (
        await client.post(f"/api/v1/agents/{aid}/publish", json={"revision": 1})
    ).status_code == 409
    assert (await client.post(f"/api/v1/agents/{aid}/publish", json={"revision": 2})).json()[
        "version"
    ] == 2
    assert (
        await client.post(f"/api/v1/agents/{aid}/rollback", json={"version": 1})
    ).status_code == 200
    assert (await client.post(f"/api/v1/agents/{aid}/publish", json={"revision": 2})).json()[
        "version"
    ] == 3
    versions = (await client.get(f"/api/v1/agents/{aid}/versions")).json()
    assert versions[-1]["config"]["system_prompt"] == "依据知识回答。"


async def test_knowledge_versions_chinese_search_and_scope(platform):
    client, services, settings = platform
    kb = (await client.post("/api/v1/knowledge-bases", json={"name": "售后政策"})).json()
    doc = (
        await client.post(
            f"/api/v1/knowledge-bases/{kb['id']}/documents",
            json={"title": "退货规则", "content": "商品支持七天退货，申请前保留包装。"},
        )
    ).json()
    hits = (
        await client.get("/api/v1/knowledge/search", params={"q": "退货规则", "kb": kb["id"]})
    ).json()
    assert hits and hits[0]["version"] == 1
    assert await services.platform.knowledge.search("another-tenant", "退货") == []
    assert await services.platform.knowledge.search(settings.tenant_id, "退货", []) == []
    response = await client.put(
        f"/api/v1/knowledge-bases/{kb['id']}/documents/{doc['id']}?version=1",
        json={"title": "保修规则", "content": "设备保修一年。"},
    )
    assert response.status_code == 200, response.text
    assert (await client.get("/api/v1/knowledge/search", params={"q": "七天退货"})).json() == []
    assert (await client.get("/api/v1/knowledge/search", params={"q": "保修"})).json()[0][
        "version"
    ] == 2
    await client.patch(f"/api/v1/documents/{doc['id']}", json={"enabled": False})
    assert (await client.get("/api/v1/knowledge/search", params={"q": "保修"})).json() == []


async def test_agent_cannot_publish_unauthorized_knowledge(platform):
    client, _, _ = platform
    agent, _ = await create_agent(client, bases=[str(uuid4())], publish=False)
    assert (
        await client.post(f"/api/v1/agents/{agent['id']}/publish", json={"revision": 1})
    ).status_code == 400


async def test_runtime_uses_published_snapshot_and_retrieves_knowledge(platform):
    client, services, settings = platform
    kb = (await client.post("/api/v1/knowledge-bases", json={"name": "政策"})).json()
    await client.post(
        f"/api/v1/knowledge-bases/{kb['id']}/documents",
        json={"title": "退货规则", "content": "七天内可申请退货。"},
    )
    agent, body = await create_agent(client, ["knowledge_search"], [kb["id"]])
    conv = await conversation(client, agent["id"])
    response = await client.post(
        f"/api/v1/conversations/{conv['id']}/messages", json={"content": "退货规则"}
    )
    assert response.status_code == 202, response.text
    rid = UUID(response.json()["run"]["id"])
    body["config"]["system_prompt"] = "修改后的草稿"
    await client.put(f"/api/v1/agents/{agent['id']}", json={**body, "revision": 1})
    row = await services.repository.get(rid, settings.tenant_id)
    assert row["config"]["agent"]["config"]["system_prompt"] == "依据知识回答。"
    final = await execute_next(services, settings)
    assert final["status"] == "completed", final
    assert "七天" in final["output"]
    events = await services.repository.events(rid, settings.tenant_id)
    assert any(e["type"] == "knowledge.retrieved" for e in events)
    detail = (await client.get(f"/api/v1/conversations/{conv['id']}")).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]


async def test_ticket_proposal_confirm_is_idempotent(platform):
    client, services, settings = platform
    agent, _ = await create_agent(client, ["propose_ticket"])
    conv = await conversation(client, agent["id"])
    await client.post(
        f"/api/v1/conversations/{conv['id']}/messages", json={"content": "请创建维修工单"}
    )
    final = await execute_next(services, settings)
    assert final["status"] == "completed", final
    assert (await client.get("/api/v1/tickets")).json() == []
    detail = (await client.get(f"/api/v1/conversations/{conv['id']}")).json()
    aid = detail["actions"][0]["id"]
    replies = await asyncio.gather(
        *(client.post(f"/api/v1/actions/{aid}/decision", json={"approve": True}) for _ in range(4))
    )
    assert all(r.status_code == 200 for r in replies), [r.text for r in replies]
    assert len({r.json()["id"] for r in replies}) == 1
    tickets = (await client.get("/api/v1/tickets")).json()
    assert len(tickets) == 1
    ticket = tickets[0]
    assert (
        await client.patch(
            f"/api/v1/tickets/{ticket['id']}",
            json={"status": "resolved", "note": "", "revision": 1},
        )
    ).status_code == 409
    assert (
        await client.patch(
            f"/api/v1/tickets/{ticket['id']}",
            json={"status": "in_progress", "note": "已安排", "revision": 1},
        )
    ).status_code == 200


async def test_handoff_cancels_queue_and_routes_human_messages(platform):
    client, _, _ = platform
    conv = await conversation(client)
    queued = await client.post(
        f"/api/v1/conversations/{conv['id']}/messages", json={"content": "你好"}
    )
    rid = queued.json()["run"]["id"]
    handoff = (
        await client.post(
            f"/api/v1/conversations/{conv['id']}/handoffs", json={"reason": "需要人工"}
        )
    ).json()
    assert (await client.get(f"/api/v1/runs/{rid}")).json()["status"] == "cancelled"
    repeat = (
        await client.post(f"/api/v1/conversations/{conv['id']}/handoffs", json={"reason": "重复"})
    ).json()
    assert repeat["id"] == handoff["id"]
    assert (
        await client.post(
            "/api/v1/runs", json={"conversation_id": conv["external_id"], "message": "绕过"}
        )
    ).status_code == 409
    await client.post(
        f"/api/v1/handoffs/{handoff['id']}/transition",
        json={"operation": "claim", "assignee": "客服甲"},
    )
    customer = (
        await client.post(
            f"/api/v1/conversations/{conv['id']}/messages", json={"content": "补充信息"}
        )
    ).json()
    assert customer["run"] is None
    reply = await client.post(
        f"/api/v1/conversations/{conv['id']}/human-replies", json={"content": "人工已收到"}
    )
    assert reply.status_code == 200
    await client.post(f"/api/v1/handoffs/{handoff['id']}/transition", json={"operation": "resume"})
    next_run = await client.post(
        f"/api/v1/conversations/{conv['id']}/messages", json={"content": "继续自动处理"}
    )
    assert next_run.json()["run"] is not None


async def test_handoff_prevents_inflight_bot_reply(platform):
    client, services, settings = platform
    started = asyncio.Event()

    class SlowModel:
        async def ainvoke(self, messages):
            started.set()
            await asyncio.sleep(30)

    services.worker.runtime.legacy.model = SlowModel()
    conv = await conversation(client)
    await client.post(f"/api/v1/conversations/{conv['id']}/messages", json={"content": "你好"})
    row = await services.repository.claim(services.worker.owner, 30, settings.tenant_id)
    task = asyncio.create_task(services.worker._execute(row))
    try:
        await asyncio.wait_for(started.wait(), 3)
        await client.post(f"/api/v1/conversations/{conv['id']}/handoffs", json={"reason": "接管"})
        await asyncio.wait_for(task, 5)
        detail = (await client.get(f"/api/v1/conversations/{conv['id']}")).json()
        assert not any(m["role"] == "assistant" for m in detail["messages"])
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_channel_dedup_auth_and_replies(platform):
    client, services, settings = platform
    agent, _ = await create_agent(client)
    channel = (
        await client.post("/api/v1/channels", json={"name": "官网", "agent_id": agent["id"]})
    ).json()
    cid = channel["id"]
    headers = {"X-Channel-Token": channel["token"]}
    body = {"message_id": "m1", "session_id": "customer1", "text": "你好"}
    assert (
        await client.post(
            f"/api/v1/ingress/channels/{cid}/messages",
            headers={"X-Channel-Token": "wrong"},
            json=body,
        )
    ).status_code == 401
    replies = await asyncio.gather(
        *(
            client.post(f"/api/v1/ingress/channels/{cid}/messages", headers=headers, json=body)
            for _ in range(3)
        )
    )
    assert all(r.status_code == 202 for r in replies), [r.text for r in replies]
    assert len({r.json()["run_id"] for r in replies}) == 1
    assert sum(r.json()["duplicate"] for r in replies) == 2
    assert (
        await client.post(
            f"/api/v1/ingress/channels/{cid}/messages",
            headers=headers,
            json={**body, "text": "不同内容"},
        )
    ).status_code == 409
    await execute_next(services, settings)
    response = await client.get(
        f"/api/v1/ingress/channels/{cid}/replies",
        headers=headers,
        params={"session_id": "customer1"},
    )
    assert response.status_code == 200 and response.json()[0]["role"] == "assistant"
    assert (
        await client.get(
            f"/api/v1/ingress/channels/{cid}/replies",
            headers=headers,
            params={"session_id": "customer2"},
        )
    ).json() == []
    assert "token_hash" not in (await client.get("/api/v1/channels")).text


async def test_registry_host_permissions_and_live_disable(platform):
    client, services, settings = platform
    body = {
        "name": "order_lookup",
        "description": "查询订单",
        "url": "http://127.0.0.1:8010/orders",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }
    assert (
        await client.post("/api/v1/tools", json={**body, "url": "https://unapproved.test/orders"})
    ).status_code == 400
    assert (await client.post("/api/v1/tools", json={**body, "method": "PUT"})).status_code == 422
    assert (
        await client.post(
            "/api/v1/tools",
            json={**body, "headers_from_env": {"Authorization": "AGENT_MODEL_API_KEY"}},
        )
    ).status_code == 400
    assert (
        await client.post("/api/v1/tools", json={**body, "name": "order_post", "method": "POST"})
    ).status_code == 201
    row = (await client.post("/api/v1/tools", json=body)).json()
    assert (await client.post("/api/v1/tools", json=body)).status_code == 409
    agent, _ = await create_agent(client, ["order_lookup"])
    await client.patch(f"/api/v1/tools/{row['id']}", json={"enabled": False})
    assert not await services.platform.tools.enabled(settings.tenant_id, "order_lookup")
    assert (
        await client.post(f"/api/v1/agents/{agent['id']}/publish", json={"revision": 1})
    ).status_code == 400


async def test_roles_are_enforced_for_runtime_and_admin_operations(platform):
    client, _, _ = platform
    viewer = {"Authorization": "Bearer platform-viewer"}
    operator = {"Authorization": "Bearer platform-operator"}
    assert (await client.get("/api/v1/me", headers=viewer)).json()["role"] == "viewer"
    assert (await client.get("/api/v1/agents", headers=viewer)).status_code == 200
    assert (
        await client.post(
            "/api/v1/runs", headers=viewer, json={"conversation_id": "bad", "message": "hi"}
        )
    ).status_code == 403
    assert (
        await client.post("/api/v1/knowledge-bases", headers=operator, json={"name": "bad"})
    ).status_code == 403
    assert (await client.get("/api/v1/audit", headers=viewer)).status_code == 403
    assert (
        await client.post(
            "/api/v1/conversations", headers=operator, json={"external_id": "operator-session"}
        )
    ).status_code == 201


async def test_evaluation_never_executes_live_business_tools(platform):
    client, _, _ = platform
    agent, _ = await create_agent(client, ["propose_ticket"])
    aid = agent["id"]
    response = await client.post(
        f"/api/v1/agents/{aid}/evaluation-cases",
        json={
            "name": "拟定工单",
            "input": "创建维修工单",
            "expected_contains": ["需确认"],
            "expected_tools": ["propose_ticket"],
            "mock_tools": {"propose_ticket": {"ok": True, "data": "需确认"}},
        },
    )
    assert response.status_code == 201, response.text
    report = await client.post(f"/api/v1/agents/{aid}/evaluate")
    assert report.status_code == 200, report.text
    assert report.json()["results"][0]["passed"] is True
    assert (await client.get("/api/v1/tickets")).json() == []
    assert (await client.get("/api/v1/conversations")).json() == []


async def test_metrics_and_audit_reflect_real_operations(platform):
    client, services, settings = platform
    conv = await conversation(client)
    await client.post(f"/api/v1/conversations/{conv['id']}/messages", json={"content": "你好"})
    await execute_next(services, settings)
    metrics = (await client.get("/api/v1/observability/summary")).json()
    assert metrics["total"] == 1 and metrics["completed"] == 1
    assert metrics["p95_seconds"] is not None
    audit = await client.get("/api/v1/audit")
    assert any(r["action"] == "run.submitted" for r in audit.json())
    assert "platform-admin" not in audit.text


async def test_recent_messages_and_bounded_human_context(platform):
    client, services, settings = platform
    conv = await conversation(client)
    cid = conv["id"]
    async with services.repository.engine.begin() as c:
        await c.execute(
            text("""INSERT INTO conversation_messages(id,conversation_id,role,content)
            SELECT gen_random_uuid(),CAST(:cid AS uuid),'user','message-' || n
            FROM generate_series(1,205) n ORDER BY n"""),
            {"cid": cid},
        )
    messages = (await client.get(f"/api/v1/conversations/{cid}")).json()["messages"]
    assert len(messages) == 200
    assert messages[0]["content"] == "message-6"
    assert messages[-1]["content"] == "message-205"
    incremental = (
        await client.get(f"/api/v1/conversations/{cid}", params={"after": messages[-2]["seq"]})
    ).json()["messages"]
    assert [m["content"] for m in incremental] == ["message-205"]
    handoff = (
        await client.post(f"/api/v1/conversations/{cid}/handoffs", json={"reason": "测试上下文"})
    ).json()
    await client.post(
        f"/api/v1/handoffs/{handoff['id']}/transition",
        json={"operation": "claim", "assignee": "客服"},
    )
    for i in range(settings.history_turns + 2):
        assert (
            await client.post(f"/api/v1/conversations/{cid}/messages", json={"content": f"客户{i}"})
        ).status_code == 202
        assert (
            await client.post(
                f"/api/v1/conversations/{cid}/human-replies", json={"content": f"客服{i}"}
            )
        ).status_code == 200
    detail = (await client.get(f"/api/v1/conversations/{cid}")).json()
    assert len(detail["history"]) == settings.history_turns * 2
    assert detail["history"][-1]["content"] == f"客服{settings.history_turns + 1}"


async def test_evaluation_case_limit_is_atomic(platform):
    client, _, _ = platform
    agent, _ = await create_agent(client)
    path = f"/api/v1/agents/{agent['id']}/evaluation-cases"
    body = {"name": "边界用例", "input": "你好", "expected_contains": ["你好"]}
    responses = await asyncio.gather(*(client.post(path, json=body) for _ in range(24)))
    assert sum(r.status_code == 201 for r in responses) == 20
    assert sum(r.status_code == 400 for r in responses) == 4
    assert len((await client.get(path)).json()) == 20
