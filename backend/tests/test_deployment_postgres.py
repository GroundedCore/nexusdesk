import pytest
from sqlalchemy import text
from test_platform_postgres import execute_next
from test_platform_postgres import platform as _platform_fixture

from agent_platform.apps import seed

platform = _platform_fixture
pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


async def test_quickstart_seed_is_repeatable_and_scenarios_work(platform, monkeypatch):
    client, services, settings = platform
    monkeypatch.setattr(seed, "Settings", lambda: settings)
    await seed.main()
    await seed.main()
    async with services.platform.engine.connect() as connection:
        for table in ("agents", "knowledge_bases", "gateway_profiles", "runtime_conversations"):
            count = await connection.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                {"tenant": settings.tenant_id},
            )
            assert count == 1, table
        cid = await connection.scalar(
            text("SELECT id FROM runtime_conversations WHERE tenant_id=:tenant"),
            {"tenant": settings.tenant_id},
        )
    response = await client.post(f"/api/v1/conversations/{cid}/messages", json={"content": "发货需要多久？"})
    assert response.status_code == 202, response.text
    result = await execute_next(services, settings)
    assert result["status"] == "completed", result
    assert "48" in result["output"]
    response = await client.post(
        f"/api/v1/conversations/{cid}/messages", json={"content": "请创建售后工单"}
    )
    assert response.status_code == 202, response.text
    result = await execute_next(services, settings)
    assert result["status"] == "completed", result
    assert (await client.get("/api/v1/tickets")).json() == []
    detail = (await client.get(f"/api/v1/conversations/{cid}")).json()
    action_id = detail["actions"][0]["id"]
    response = await client.post(f"/api/v1/actions/{action_id}/decision", json={"approve": True})
    assert response.status_code == 200, response.text
    assert len((await client.get("/api/v1/tickets")).json()) == 1
