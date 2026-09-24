import pytest
from test_platform_postgres import create_agent
from test_platform_postgres import platform as _platform_fixture

from agent_platform.modules.policy.contracts import ResourceFacts
from agent_platform.modules.policy.service import ExecutionPolicy
from agent_platform.platform.identity.context import ExecutionContext

platform = _platform_fixture


def test_policy_denies_unknown_actions_and_cross_tenant():
    policy = ExecutionPolicy(None)
    context = ExecutionContext("a", "operator", frozenset({"operator"}))
    assert not policy.authorize(context, "resource.read", ResourceFacts("b")).allowed
    assert not policy.authorize(context, "unknown", ResourceFacts("a")).allowed
    assert not policy.authorize(context, "configuration.write", ResourceFacts("a")).allowed
    decision = policy.authorize(context, "business.confirm", ResourceFacts("a"))
    assert decision.allowed and decision.obligations == ("verify_business_confirmation",)


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_tool_release_is_pinned_to_agent(platform):
    client, services, settings = platform
    spec = {
        "name": "order_lookup",
        "description": "v1",
        "url": "http://localhost/orders",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }
    tool = (await client.post("/api/v1/tools", json=spec)).json()
    agent, _ = await create_agent(client, [spec["name"]], publish=False)
    assert (
        await client.post(f"/api/v1/agents/{agent['id']}/publish", json={"revision": 1})
    ).status_code == 200
    assert (
        await client.put(
            f"/api/v1/tools/{tool['id']}", json={**spec, "description": "v2", "revision": 1}
        )
    ).status_code == 200
    assert (
        await client.post(f"/api/v1/tools/{tool['id']}/publish", json={"revision": 2})
    ).status_code == 200
    async with services.platform.engine.connect() as c:
        snapshot = await services.platform.agents.snapshot(settings.tenant_id, agent["id"], c)
    assert snapshot["tools"][0]["version"] == 1
    assert snapshot["tools"][0]["spec"]["description"] == "v1"
    assert (
        await client.post(f"/api/v1/agents/{agent['id']}/publish", json={"revision": 1})
    ).status_code == 200
    async with services.platform.engine.connect() as c:
        snapshot = await services.platform.agents.snapshot(settings.tenant_id, agent["id"], c)
    assert snapshot["tools"][0]["version"] == 2
    assert (await client.get("/api/v1/tool-calls")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_agent_archive_conflict_and_diff(platform):
    client, _services, _settings = platform
    body = {"name": "archive", "config": {"system_prompt": "first"}}
    agent = (await client.post("/api/v1/agents", json=body)).json()
    await client.post(f"/api/v1/agents/{agent['id']}/publish", json={"revision": 1})
    body["config"]["system_prompt"] = "second"
    await client.put(f"/api/v1/agents/{agent['id']}", json={**body, "revision": 1})
    diff = await client.get(f"/api/v1/agents/{agent['id']}/diff?version=1")
    assert diff.json()["changes"] == [
        {"field": "system_prompt", "published": "first", "draft": "second"}
    ]
    assert (
        await client.patch(
            f"/api/v1/agents/{agent['id']}/archive", json={"archived": True, "revision": 1}
        )
    ).status_code == 409
    assert (
        await client.patch(
            f"/api/v1/agents/{agent['id']}/archive", json={"archived": True, "revision": 2}
        )
    ).status_code == 200
    assert (await client.get("/api/v1/agents")).json() == []
    assert len((await client.get("/api/v1/agents?include_archived=true")).json()) == 1
    assert (
        await client.post(f"/api/v1/agents/{agent['id']}/publish", json={"revision": 3})
    ).status_code == 409
