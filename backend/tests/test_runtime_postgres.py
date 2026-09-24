import asyncio
import os
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import text

from agent_platform.modules.agent_runtime.engine import RuntimeEngine
from agent_platform.modules.agent_runtime.repository import RunRepository
from agent_platform.modules.agent_runtime.schemas import BusyError, CapacityError, RunRequest
from agent_platform.modules.agent_runtime.worker import RuntimeWorker
from agent_platform.modules.model_gateway.service import DemoModel
from agent_platform.modules.tool_gateway.service import ToolGateway
from agent_platform.platform.persistence.database import create_engine
from agent_platform.settings import Settings

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


@pytest_asyncio.fixture
async def database():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a migrated, dedicated PostgreSQL test database")
    tenant = "test-" + str(uuid4())
    settings = Settings(database_url=url, tenant_id=tenant, embedded_worker=False)
    engine = create_engine(settings)
    repo = RunRepository(engine)
    try:
        yield repo, settings
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM runtime_runs WHERE tenant_id=:tenant"), {"tenant": tenant}
            )
            await conn.execute(
                text("DELETE FROM runtime_conversations WHERE tenant_id=:tenant"),
                {"tenant": tenant},
            )
        await engine.dispose()


async def submit(repo, settings, conversation="one", **kwargs):
    return await repo.submit(
        settings.tenant_id,
        RunRequest(conversation_id=conversation, message="hello"),
        {"fingerprint": "test"},
        kwargs.get("capacity", 10000),
        kwargs.get("tenant_capacity", 100),
        120,
    )


async def test_concurrent_submit_and_exclusive_claim(database):
    repo, settings = database
    results = await asyncio.gather(
        *(submit(repo, settings) for _ in range(5)), return_exceptions=True
    )
    assert sum(isinstance(item, dict) for item in results) == 1
    assert sum(isinstance(item, BusyError) for item in results) == 4
    owners = [uuid4() for _ in range(5)]
    claims = await asyncio.gather(*(repo.claim(owner, 30, settings.tenant_id) for owner in owners))
    assert sum(item is not None for item in claims) == 1


async def test_capacity_and_tenant_isolation(database):
    repo, settings = database
    row = await submit(repo, settings, tenant_capacity=1)
    with pytest.raises(CapacityError):
        await submit(repo, settings, "two", tenant_capacity=1)
    assert await repo.get(row["id"], "other-tenant") is None
    assert await repo.events(row["id"], "other-tenant") == []
    assert await repo.cancel(row["id"], "other-tenant") is None


async def test_cancellation_wins_finish_race_and_event_replay(database):
    repo, settings = database
    row = await submit(repo, settings)
    owner = uuid4()
    await repo.claim(owner, 30, settings.tenant_id)
    await repo.cancel(row["id"], settings.tenant_id)
    await repo.finish(row["id"], owner, "completed", output="must not publish", history=[])
    final = await repo.get(row["id"], settings.tenant_id)
    assert final["status"] == "cancelled"
    assert final["output"] is None
    events = await repo.events(row["id"], settings.tenant_id)
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    replay = await repo.events(row["id"], settings.tenant_id, events[-2]["seq"])
    assert [event["type"] for event in replay] == ["run.cancelled"]
    await submit(repo, settings)  # active conversation slot was released


async def test_persisted_history_survives_repository_recreation(database):
    repo, settings = database
    row = await submit(repo, settings)
    owner = uuid4()
    await repo.claim(owner, 30, settings.tenant_id)
    history = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
    await repo.finish(row["id"], owner, "completed", output="hi", history=history)
    await submit(repo, settings)
    recreated = RunRepository(repo.engine)
    next_run = await recreated.claim(uuid4(), 30, settings.tenant_id)
    assert next_run["history"] == history


async def test_lost_worker_is_failed_and_cannot_publish_late_result(database):
    repo, settings = database
    row = await submit(repo, settings)
    owner = uuid4()
    await repo.claim(owner, 30, settings.tenant_id)
    async with repo.engine.begin() as conn:
        await conn.execute(
            text("UPDATE runtime_runs SET lease_until=now()-interval '1 second' WHERE id=:id"),
            {"id": row["id"]},
        )
    assert await repo.heartbeat(row["id"], owner, 30) == "lost"
    await repo.finish(row["id"], owner, "completed", output="expired", history=[])
    assert (await repo.get(row["id"], settings.tenant_id))["output"] is None
    await repo.reap(settings.tenant_id)
    await repo.finish(row["id"], owner, "completed", output="late", history=[])
    final = await repo.get(row["id"], settings.tenant_id)
    assert final["status"] == "failed"
    assert final["error_code"] == "worker_lost"


async def test_queue_expiration_and_cancel_without_worker(database):
    repo, settings = database
    row = await submit(repo, settings)
    final = await repo.cancel(row["id"], settings.tenant_id)
    assert final["status"] == "cancelled"
    next_run = await submit(repo, settings)
    async with repo.engine.begin() as conn:
        await conn.execute(
            text("UPDATE runtime_runs SET queue_expires_at=now()-interval '1 second' WHERE id=:id"),
            {"id": next_run["id"]},
        )
    await repo.reap(settings.tenant_id)
    assert (await repo.get(next_run["id"], settings.tenant_id))["error_code"] == "queue_timeout"


async def test_worker_runs_real_graph_and_persists_events(database):
    repo, settings = database
    row = await submit(repo, settings)
    async with httpx.AsyncClient() as client:
        worker = RuntimeWorker(
            repo, RuntimeEngine(DemoModel(), ToolGateway(client, []), settings), settings, "test"
        )
        task = asyncio.create_task(worker.serve())
        try:
            async with asyncio.timeout(5):
                while (final := await repo.get(row["id"], settings.tenant_id))["status"] not in {
                    "completed",
                    "failed",
                }:
                    await asyncio.sleep(0.05)
            assert final["status"] == "completed"
            assert "演示模式" in final["output"]
            events = await repo.events(row["id"], settings.tenant_id)
            assert events[-1]["type"] == "run.completed"
        finally:
            worker.stop()
            await task


async def test_http_api_authorization_conflicts_cancel_and_sse(database):
    from agent_platform.apps.api.main import create_app

    _repo, settings = database
    settings = settings.model_copy(update={"api_token": SecretStr("test-token")})
    app = create_app(settings)
    async with app.router.lifespan_context(app):  # noqa: SIM117
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/api/v1/ready")).status_code == 401
            client.headers["Authorization"] = "Bearer test-token"
            assert (await client.get("/api/v1/ready")).status_code == 200
            payload = {"conversation_id": "http-test", "message": "hello"}
            response = await client.post("/api/v1/runs", json=payload)
            assert response.status_code == 202
            run_id = response.json()["id"]
            assert (await client.post("/api/v1/runs", json=payload)).status_code == 409
            cancelled = await client.post(f"/api/v1/runs/{run_id}/cancel")
            assert cancelled.json()["status"] == "cancelled"
            stream = await client.get(f"/api/v1/runs/{run_id}/events")
            assert stream.status_code == 200
            assert "event: run.queued" in stream.text and "event: run.cancelled" in stream.text
            replay = await client.get(
                f"/api/v1/runs/{run_id}/events", headers={"Last-Event-ID": "1"}
            )
            assert "run.queued" not in replay.text and "run.cancelled" in replay.text
            invalid = await client.get(
                f"/api/v1/runs/{run_id}/events", headers={"Last-Event-ID": "-1"}
            )
            assert invalid.status_code == 400
            assert (await client.get(f"/api/v1/runs/{uuid4()}")).status_code == 404


async def test_end_to_end_demo_uses_real_http_and_persistent_events(database, tmp_path):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from agent_platform.apps.api.main import create_app

    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.path)
            body = json.dumps({"demo": True, "status": "shipped"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _repo, settings = database
    tool_file = tmp_path / "tools.json"
    tool_file.write_text(
        json.dumps(
            [
                {
                    "name": "demo_order_lookup",
                    "description": "Demo query",
                    "url": f"http://127.0.0.1:{server.server_port}/orders",
                    "parameters": {
                        "type": "object",
                        "properties": {"order_id": {"type": "string"}},
                        "required": ["order_id"],
                        "additionalProperties": False,
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    app = create_app(settings.model_copy(update={"tools_file": tool_file, "embedded_worker": True}))
    try:
        async with app.router.lifespan_context(app):  # noqa: SIM117
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/runs", json={"conversation_id": "live-http", "message": "查询演示"}
                )
                assert response.status_code == 202
                run_id = response.json()["id"]
                async with asyncio.timeout(10):
                    while True:
                        result = (await client.get(f"/api/v1/runs/{run_id}")).json()
                        if result["status"] in {"completed", "failed"}:
                            break
                        await asyncio.sleep(0.05)
                assert result["status"] == "completed", result
                assert "shipped" in result["output"]
                assert received == ["/orders?order_id=DEMO-001"]
                events = (await client.get(f"/api/v1/runs/{run_id}/events")).text
                assert "tool.started" in events and "tool.completed" in events
                assert "run.completed" in events
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=2)


async def test_cancellation_interrupts_inflight_model(database):
    repo, settings = database
    row = await submit(repo, settings)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class SlowModel:
        async def ainvoke(self, messages):
            started.set()
            try:
                await asyncio.sleep(100)
            finally:
                cancelled.set()

    async with httpx.AsyncClient() as client:
        worker = RuntimeWorker(
            repo, RuntimeEngine(SlowModel(), ToolGateway(client, []), settings), settings, "test"
        )
        task = asyncio.create_task(worker.serve())
        try:
            await asyncio.wait_for(started.wait(), 5)
            await repo.cancel(row["id"], settings.tenant_id)
            await asyncio.wait_for(cancelled.wait(), 5)
            async with asyncio.timeout(5):
                while (await repo.get(row["id"], settings.tenant_id))["status"] != "cancelled":
                    await asyncio.sleep(0.05)
        finally:
            worker.stop()
            await task
