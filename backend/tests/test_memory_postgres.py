"""Phase 1 conversation summary — PostgreSQL integration tests.

Case IDs refer to docs/requirement/agent-memory/phase-1-conversation-summary/04-test-plan.md.
Runs only with TEST_DATABASE_URL (CI Backend job); skipped locally.
"""

import asyncio
import json
import logging
from uuid import uuid4

import pytest
from sqlalchemy import text
from test_platform_postgres import create_agent, execute_next
from test_platform_postgres import platform as _platform

from agent_platform.modules.memory.summary import SUMMARY_KIND, SummaryService, enqueue_summary_task
from agent_platform.modules.memory.worker import MemoryWorker
from agent_platform.platform.persistence.store import DomainError

platform = _platform
pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


async def send_and_run(client, services, settings, cid, text):
    response = await client.post(f"/api/v1/conversations/{cid}/messages", json={"content": text})
    assert response.status_code == 202, response.text
    final = await execute_next(services, settings)
    assert final["status"] == "completed", final
    return final


async def make_conversation(client, agent):
    response = await client.post(
        "/api/v1/conversations", json={"external_id": str(uuid4()), "agent_id": agent["id"]}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def memory_tasks(engine, tenant):
    async with engine.connect() as c:
        rows = (
            (
                await c.execute(
                    text("SELECT * FROM memory_tasks WHERE tenant_id=:t ORDER BY created_at"),
                    {"t": tenant},
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]


async def conversation_summary(engine, cid):
    async with engine.connect() as c:
        return await c.scalar(
            text("SELECT summary FROM runtime_conversations WHERE id=:id"), {"id": cid}
        )


async def audit_records(engine, tenant, action):
    async with engine.connect() as c:
        rows = (
            (
                await c.execute(
                    text("SELECT * FROM audit_records WHERE tenant_id=:t AND action=:a"),
                    {"t": tenant, "a": action},
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]


class RecordingGateway:
    """Programmable summary model: canned replies, recorded prompts, optional failures."""

    def __init__(self, *replies, error=None):
        self.replies = list(replies) or ["记录摘要"]
        self.error = error
        self.requests = []

    async def invoke(self, tenant, profile_id, version, payload, **kwargs):
        request = payload.model_dump(mode="json")
        self.requests.append(request)
        if self.error:
            raise self.error
        content = self.replies.pop(0) if self.replies else "记录摘要"
        return {
            "payload": {"content": content},
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }


def stub_memory(services, settings, gateway):
    service = SummaryService(services.repository.engine, gateway, settings)
    services.memory_worker.service = service
    return service


async def force_claimable(engine, tenant):
    async with engine.begin() as c:
        await c.execute(
            text("UPDATE memory_tasks SET run_after=now()-interval '1 second' WHERE tenant_id=:t"),
            {"t": tenant},
        )


async def test_p1_it_01_truncation_dispatches_task_and_summary_is_written(platform):
    client, services, settings = platform
    settings.history_turns = 2
    agent, _ = await create_agent(client)
    conv = await make_conversation(client, agent)
    # Turns 1-2 fit the window; turn 3 drops the first turn.
    for i in range(2):
        await send_and_run(client, services, settings, conv["id"], f"问题{i}")
    assert await memory_tasks(services.repository.engine, settings.tenant_id) == []
    final = await send_and_run(client, services, settings, conv["id"], "问题2")
    tasks = await memory_tasks(services.repository.engine, settings.tenant_id)
    assert len(tasks) == 1 and tasks[0]["kind"] == SUMMARY_KIND and tasks[0]["status"] == "pending"
    payload = tasks[0]["payload"]
    assert payload["conversation_id"] == conv["id"]
    assert [m["content"] for m in payload["dropped_messages"] if m["role"] == "user"] == ["问题0"]
    # FR-5: the run trace marks that a summary task was queued.
    events = await services.repository.events(final["id"], settings.tenant_id)
    assert "summary.queued" in [event["type"] for event in events]
    # The real gateway path (demo profile) writes the summary and usage.
    assert await services.memory_worker.run_once() is True
    summary = await conversation_summary(services.repository.engine, conv["id"])
    assert summary and "演示模式" in summary
    tasks = await memory_tasks(services.repository.engine, settings.tenant_id)
    assert tasks[0]["status"] == "done"
    async with services.repository.engine.connect() as c:
        calls = (
            (
                await c.execute(
                    text("SELECT * FROM gateway_calls WHERE tenant_id=:t AND actor='memory'"),
                    {"t": settings.tenant_id},
                )
            )
            .mappings()
            .all()
        )
    assert len(calls) == 1 and calls[0]["status"] == "completed"
    # P1-CM-03: audit carries lengths and usage but never message content.
    records = await audit_records(
        services.repository.engine, settings.tenant_id, "memory.summary.generated"
    )
    assert len(records) == 1
    details = records[0]["details"]
    assert details["conversation_id"] == conv["id"] and details["input_messages"] == 2
    assert details["old_length"] == 0 and details["new_length"] == len(summary)
    assert "问题0" not in json.dumps(details, ensure_ascii=False)
    # The next claimed run carries the summary for injection (P1-CM-04 scoping).
    response = await client.post(
        f"/api/v1/conversations/{conv['id']}/messages", json={"content": "问题3"}
    )
    assert response.status_code == 202
    row = await services.repository.claim(services.worker.owner, 30, settings.tenant_id)
    assert row["summary"] == summary
    other = await services.repository.claim(uuid4(), 30, "other-tenant")
    assert other is None


async def test_p1_it_02_summary_rolls_forward_with_previous_summary(platform):
    client, services, settings = platform
    settings.history_turns = 1
    agent, _ = await create_agent(client)
    conv = await make_conversation(client, agent)
    gateway = stub_memory(services, settings, RecordingGateway("摘要一", "摘要二"))
    await send_and_run(client, services, settings, conv["id"], "第1轮 订单号 A123")
    await send_and_run(client, services, settings, conv["id"], "第2轮")
    assert await services.memory_worker.run_once() is True
    assert await conversation_summary(services.repository.engine, conv["id"]) == "摘要一"
    await send_and_run(client, services, settings, conv["id"], "第3轮")
    assert await services.memory_worker.run_once() is True
    assert await conversation_summary(services.repository.engine, conv["id"]) == "摘要二"
    # Second call merged the first summary with the newly dropped messages.
    second = gateway.requests[1]
    assert "摘要一" in second["messages"][1]["content"]
    assert "第2轮" in second["messages"][1]["content"]


async def test_p1_it_04_pending_summary_never_blocks_a_run(platform):
    client, services, settings = platform
    settings.history_turns = 1
    agent, _ = await create_agent(client)
    conv = await make_conversation(client, agent)
    stub_memory(services, settings, RecordingGateway("旧摘要"))
    await send_and_run(client, services, settings, conv["id"], "第1轮")
    await send_and_run(client, services, settings, conv["id"], "第2轮")
    assert await services.memory_worker.run_once() is True
    assert await conversation_summary(services.repository.engine, conv["id"]) == "旧摘要"
    # Next truncation queues a task but the run after it still sees the old summary.
    final = await send_and_run(client, services, settings, conv["id"], "第3轮")
    assert final["status"] == "completed"
    assert [t["status"] for t in await memory_tasks(services.repository.engine, settings.tenant_id)] == [
        "done",
        "pending",
    ]
    response = await client.post(
        f"/api/v1/conversations/{conv['id']}/messages", json={"content": "第4轮"}
    )
    assert response.status_code == 202
    row = await services.repository.claim(services.worker.owner, 30, settings.tenant_id)
    assert row["summary"] == "旧摘要"


async def test_p1_it_05_model_failure_retries_then_fails_keeping_old_summary(platform, caplog):
    client, services, settings = platform
    settings.history_turns = 1
    agent, _ = await create_agent(client)
    conv = await make_conversation(client, agent)
    gateway = RecordingGateway("保留摘要")
    stub_memory(services, settings, gateway)
    await send_and_run(client, services, settings, conv["id"], "第1轮")
    await send_and_run(client, services, settings, conv["id"], "第2轮")
    assert await services.memory_worker.run_once() is True
    assert await conversation_summary(services.repository.engine, conv["id"]) == "保留摘要"
    # Now the summary model breaks; the conversation itself keeps working (AC-4).
    gateway.error = DomainError("model_provider_unavailable", 502)
    await send_and_run(client, services, settings, conv["id"], "第3轮")
    with caplog.at_level(logging.WARNING):
        # P1-UT-10: first failure reschedules with backoff and attempts=1.
        assert await services.memory_worker.run_once() is True
        task = (await memory_tasks(services.repository.engine, settings.tenant_id))[-1]
        assert task["status"] == "pending" and task["attempts"] == 1
        assert task["run_after"].timestamp() > task["updated_at"].timestamp()
        # P1-UT-11: reaching max_attempts marks the task failed, no more retries.
        await force_claimable(services.repository.engine, settings.tenant_id)
        assert await services.memory_worker.run_once() is True
    task = (await memory_tasks(services.repository.engine, settings.tenant_id))[-1]
    assert task["status"] == "failed" and task["attempts"] == 2
    assert task["error"] == "model_provider_unavailable"
    assert await conversation_summary(services.repository.engine, conv["id"]) == "保留摘要"
    records = await audit_records(
        services.repository.engine, settings.tenant_id, "memory.summary.failed"
    )
    assert len(records) == 1 and records[0]["details"]["error"] == "model_provider_unavailable"
    assert any("failed permanently" in record.message for record in caplog.records)


async def test_p1_it_05b_no_chat_profile_fails_without_retry(platform):
    client, services, settings = platform
    agent, _ = await create_agent(client)
    conv = await make_conversation(client, agent)
    stub_memory(services, settings, RecordingGateway())
    # A conversation whose agent binding vanished cannot pick a profile (P1-UT-09).
    async with services.repository.engine.begin() as c:
        await c.execute(
            text("UPDATE runtime_conversations SET agent_id=NULL WHERE id=:id"), {"id": conv["id"]}
        )
        await enqueue_summary_task(
            c,
            settings.tenant_id,
            conv["id"],
            [{"role": "user", "content": "x"}],
            None,
            40,
        )
    assert await services.memory_worker.run_once() is True
    task = (await memory_tasks(services.repository.engine, settings.tenant_id))[-1]
    assert task["status"] == "failed" and task["error"] == "no_chat_profile"
    assert task["attempts"] == 1


async def test_p1_it_06_no_task_or_usage_before_first_truncation(platform):
    client, services, settings = platform
    settings.history_turns = 2
    agent, _ = await create_agent(client)
    conv = await make_conversation(client, agent)
    await send_and_run(client, services, settings, conv["id"], "唯一一轮")
    assert await memory_tasks(services.repository.engine, settings.tenant_id) == []
    assert await services.memory_worker.run_once() is False
    async with services.repository.engine.connect() as c:
        count = await c.scalar(
            text("SELECT count(*) FROM gateway_calls WHERE tenant_id=:t AND actor='memory'"),
            {"t": settings.tenant_id},
        )
    assert count == 0


async def test_p1_it_07_handoff_includes_summary_and_recent_sections(platform):
    client, services, settings = platform
    agent, _ = await create_agent(client)
    conv = await make_conversation(client, agent)
    await send_and_run(client, services, settings, conv["id"], "帮我查订单")
    async with services.repository.engine.begin() as c:
        await c.execute(
            text("UPDATE runtime_conversations SET summary=:s WHERE id=:id"),
            {"id": conv["id"], "s": "客户对花生过敏，订单号 A123。"},
        )
    handoff = (
        await client.post(
            f"/api/v1/conversations/{conv['id']}/handoffs", json={"reason": "需要人工"}
        )
    ).json()
    assert handoff["summary"].startswith("【对话摘要】客户对花生过敏，订单号 A123。")
    assert "\n\n【最近消息】\n" in handoff["summary"]
    assert "user: 帮我查订单" in handoff["summary"]


async def test_p1_it_08_handoff_without_summary_matches_legacy_format(platform):
    client, services, settings = platform
    agent, _ = await create_agent(client)
    conv = await make_conversation(client, agent)
    await send_and_run(client, services, settings, conv["id"], "帮我查订单")
    handoff = (
        await client.post(
            f"/api/v1/conversations/{conv['id']}/handoffs", json={"reason": "需要人工"}
        )
    ).json()
    assert "【对话摘要】" not in handoff["summary"] and "【最近消息】" not in handoff["summary"]
    assert "user: 帮我查订单" in handoff["summary"]


async def test_p1_it_09_global_switch_off_dispatches_nothing(platform):
    client, services, settings = platform
    settings.history_turns = 1
    settings.summary_enabled = False
    agent, _ = await create_agent(client)
    conv = await make_conversation(client, agent)
    for i in range(3):
        final = await send_and_run(client, services, settings, conv["id"], f"第{i}轮")
        assert final["status"] == "completed"
    assert await memory_tasks(services.repository.engine, settings.tenant_id) == []
    assert await conversation_summary(services.repository.engine, conv["id"]) is None


async def test_p1_it_10_agent_switch_follows_draft_publish_rollback(platform):
    client, services, settings = platform
    settings.history_turns = 1
    agent, body = await create_agent(client)
    other, _ = await create_agent(client)
    # Disable via draft and publish v2.
    body["config"]["summary_enabled"] = False
    response = await client.put(f"/api/v1/agents/{agent['id']}", json={**body, "revision": 1})
    assert response.status_code == 200, response.text
    assert (
        await client.post(f"/api/v1/agents/{agent['id']}/publish", json={"revision": 2})
    ).status_code == 200
    disabled = await make_conversation(client, agent)
    enabled = await make_conversation(client, other)
    for i in range(2):
        await send_and_run(client, services, settings, disabled["id"], f"关{i}")
        await send_and_run(client, services, settings, enabled["id"], f"开{i}")
    tasks = await memory_tasks(services.repository.engine, settings.tenant_id)
    assert len(tasks) == 1 and tasks[0]["payload"]["conversation_id"] == enabled["id"]
    # Rollback to v1 (summary enabled) restores the feature for new conversations.
    assert (
        await client.post(f"/api/v1/agents/{agent['id']}/rollback", json={"version": 1})
    ).status_code == 200
    restored = await make_conversation(client, agent)
    for i in range(2):
        await send_and_run(client, services, settings, restored["id"], f"回{i}")
    tasks = await memory_tasks(services.repository.engine, settings.tenant_id)
    assert {t["payload"]["conversation_id"] for t in tasks} == {enabled["id"], restored["id"]}


async def test_p1_it_11_closed_or_deleted_conversation_is_skipped(platform):
    client, services, settings = platform
    agent, _ = await create_agent(client)
    conv = await make_conversation(client, agent)
    stub_memory(services, settings, RecordingGateway())
    async with services.repository.engine.begin() as c:
        await c.execute(
            text("UPDATE runtime_conversations SET mode='closed' WHERE id=:id"), {"id": conv["id"]}
        )
        await enqueue_summary_task(
            c, settings.tenant_id, conv["id"], [{"role": "user", "content": "x"}], None, 40
        )
        await enqueue_summary_task(
            c, settings.tenant_id, uuid4(), [{"role": "user", "content": "x"}], None, 40
        )
    assert await services.memory_worker.run_once() is True
    assert await services.memory_worker.run_once() is True
    tasks = await memory_tasks(services.repository.engine, settings.tenant_id)
    assert [task["status"] for task in tasks] == ["done", "done"]
    assert await conversation_summary(services.repository.engine, conv["id"]) is None


async def test_p1_it_12_evaluation_never_dispatches_summary_tasks(platform):
    client, services, settings = platform
    agent, _ = await create_agent(client)
    response = await client.post(
        f"/api/v1/agents/{agent['id']}/evaluation-cases",
        json={"name": "c1", "input": "你好", "expected_contains": ["演示模式"]},
    )
    assert response.status_code == 201, response.text
    response = await client.post(f"/api/v1/agents/{agent['id']}/evaluate")
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["results"][0]["passed"] is True
    assert await memory_tasks(services.repository.engine, settings.tenant_id) == []


async def test_p1_it_13_concurrent_claim_runs_a_task_exactly_once(platform):
    client, services, settings = platform
    agent, _ = await create_agent(client)
    conv = await make_conversation(client, agent)
    async with services.repository.engine.begin() as c:
        await enqueue_summary_task(
            c, settings.tenant_id, conv["id"], [{"role": "user", "content": "x"}], None, 40
        )
    engine = services.repository.engine
    worker_a = MemoryWorker(engine, services.memory_worker.service, settings)
    worker_b = MemoryWorker(engine, services.memory_worker.service, settings)
    claimed = await asyncio.gather(worker_a.claim(), worker_b.claim())
    assert sum(row is not None for row in claimed) == 1
    assert sum(row is None for row in claimed) == 1


async def test_p1_it_14_graceful_shutdown_releases_running_tasks(platform):
    client, services, settings = platform
    agent, _ = await create_agent(client)
    conv = await make_conversation(client, agent)
    stub_memory(services, settings, RecordingGateway())
    async with services.repository.engine.begin() as c:
        await enqueue_summary_task(
            c, settings.tenant_id, conv["id"], [{"role": "user", "content": "x"}], None, 40
        )
    worker = services.memory_worker
    claimed = await worker.claim()
    assert claimed["status"] == "running" and claimed["owner"] == worker.owner
    worker.stop()
    await worker.serve()  # the stopping flag short-circuits the loop into finally
    task = (await memory_tasks(services.repository.engine, settings.tenant_id))[0]
    assert task["status"] == "pending" and task["owner"] is None


async def test_p1_ut_05_pending_task_merges_new_dropped_messages(platform):
    client, services, settings = platform
    agent, _ = await create_agent(client)
    conv = await make_conversation(client, agent)
    engine = services.repository.engine
    async with engine.begin() as c:
        await enqueue_summary_task(
            c, settings.tenant_id, conv["id"], [{"role": "user", "content": "一"}], None, 40
        )
    async with engine.begin() as c:
        await enqueue_summary_task(
            c, settings.tenant_id, conv["id"], [{"role": "user", "content": "二"}], None, 40
        )
    tasks = await memory_tasks(engine, settings.tenant_id)
    assert len(tasks) == 1
    assert [m["content"] for m in tasks[0]["payload"]["dropped_messages"]] == ["一", "二"]


async def test_p1_ut_06_running_task_is_not_disturbed(platform):
    client, services, settings = platform
    agent, _ = await create_agent(client)
    conv = await make_conversation(client, agent)
    engine = services.repository.engine
    async with engine.begin() as c:
        await enqueue_summary_task(
            c, settings.tenant_id, conv["id"], [{"role": "user", "content": "一"}], None, 40
        )
        await c.execute(text("UPDATE memory_tasks SET status='running'"))
        await enqueue_summary_task(
            c, settings.tenant_id, conv["id"], [{"role": "user", "content": "二"}], None, 40
        )
    tasks = await memory_tasks(engine, settings.tenant_id)
    assert [task["status"] for task in tasks] == ["running", "pending"]
    assert [m["content"] for m in tasks[0]["payload"]["dropped_messages"]] == ["一"]
    assert [m["content"] for m in tasks[1]["payload"]["dropped_messages"]] == ["二"]


async def test_p1_ut_07_enqueue_trims_to_max_input_messages(platform):
    client, services, settings = platform
    agent, _ = await create_agent(client)
    conv = await make_conversation(client, agent)
    engine = services.repository.engine
    dropped = [{"role": "user", "content": f"m{i}"} for i in range(50)]
    async with engine.begin() as c:
        await enqueue_summary_task(c, settings.tenant_id, conv["id"], dropped, None, 40)
    async with engine.begin() as c:
        await enqueue_summary_task(
            c, settings.tenant_id, conv["id"], [{"role": "user", "content": "new"}], None, 40
        )
    task = (await memory_tasks(engine, settings.tenant_id))[0]
    contents = [m["content"] for m in task["payload"]["dropped_messages"]]
    assert len(contents) == 40 and contents[-1] == "new" and contents[0] == "m11"


async def test_p1_cm_04_cross_tenant_summary_is_unreachable(platform):
    client, services, settings = platform
    agent, _ = await create_agent(client)
    conv = await make_conversation(client, agent)
    engine = services.repository.engine
    async with engine.begin() as c:
        await enqueue_summary_task(
            c, settings.tenant_id, conv["id"], [{"role": "user", "content": "x"}], None, 40
        )
        # A task pointing at another tenant's conversation must not touch it.
        await enqueue_summary_task(
            c, "other-tenant", conv["id"], [{"role": "user", "content": "x"}], None, 40
        )
    outsider = MemoryWorker(
        engine,
        services.memory_worker.service,
        settings.model_copy(update={"tenant_id": "other-tenant"}),
    )
    assert await outsider.run_once() is True  # executes its own tenant's task, skips writes
    assert await conversation_summary(engine, conv["id"]) is None
    # This tenant's own task is untouched by the outsider.
    task = (await memory_tasks(engine, settings.tenant_id))[0]
    assert task["status"] == "pending"
