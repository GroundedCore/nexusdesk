import json
from uuid import UUID

import pytest
from sqlalchemy import text
from test_model_gateway import setup
from test_platform_postgres import conversation
from test_platform_postgres import platform as _platform

from agent_platform.apps.api.platform_services import RuntimeFactory
from agent_platform.modules.agent_runtime.schemas import RuntimeFault

platform = _platform
pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


async def test_missing_model_draft_publish_legacy_run_and_rollback(platform):
    client, runtime, _settings = platform
    body = {"name": "unconfigured", "config": {"system_prompt": "test"}}
    response = await client.post("/api/v1/agents", json=body)
    assert response.status_code == 201
    agent = response.json()
    endpoint = f"/api/v1/agents/{agent['id']}"
    response = await client.post(endpoint + "/publish", json={"revision": 1})
    assert response.status_code == 409
    assert response.json()["detail"] == "agent_model_required"
    assert (await client.get(endpoint + "/versions")).json() == []

    # Simulate an older published version that relied on the deployment default.
    async with runtime.repository.engine.begin() as c:
        await c.execute(
            text("INSERT INTO agent_versions(agent_id,version,config,tool_snapshot) "
                 "VALUES(:id,1,CAST(:config AS jsonb),'[]'::jsonb)"),
            {"id": UUID(agent["id"]), "config": json.dumps(agent["draft"])},
        )
        await c.execute(
            text("UPDATE agents SET published_version=1 WHERE id=:id"),
            {"id": UUID(agent["id"])},
        )
    conv = await conversation(client, agent["id"])
    response = await client.post(
        f"/api/v1/conversations/{conv['id']}/messages", json={"content": "hello"}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "agent_model_required"

    _, _, profile = await setup(client)
    body["config"].update(model_profile_id=profile["id"], model_profile_version=1)
    assert (await client.put(endpoint, json={**body, "revision": 1})).status_code == 200
    assert (await client.post(endpoint + "/publish", json={"revision": 2})).status_code == 200
    response = await client.post(endpoint + "/rollback", json={"version": 1})
    assert response.status_code == 409
    assert (await client.get(endpoint)).json()["published_version"] == 2


async def test_captured_unconfigured_run_never_uses_default():
    factory = object.__new__(RuntimeFactory)
    factory.model = lambda _: pytest.fail("Default model must not be invoked")
    with pytest.raises(RuntimeFault, match="agent_model_required"):
        factory.bound_model({"config": {"model_profile_id": None}}, [])
