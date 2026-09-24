from uuid import uuid4

import pytest
from sqlalchemy import text
from test_platform_postgres import create_agent
from test_platform_postgres import platform as _platform

from agent_platform.platform.persistence.store import DomainError

platform = _platform
pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


async def test_catalog_real_pagination_filters_and_legacy(platform):
    client, runtime, settings = platform
    agent, _ = await create_agent(client)
    async with runtime.repository.engine.begin() as c:
        await c.execute(
            text("""INSERT INTO agents(id,tenant_id,name,description,draft)
            SELECT gen_random_uuid(),:t,'分页-' || n,'','{}'::jsonb FROM generate_series(1,205) AS n"""),
            {"t": settings.tenant_id},
        )
    first = (
        await client.get("/api/v1/agents/catalog", params={"q": "分页-", "page_size": 12})
    ).json()
    last = (
        await client.get(
            "/api/v1/agents/catalog", params={"q": "分页-", "page_size": 12, "page": 18}
        )
    ).json()
    assert first["total"] == 205 and len(first["items"]) == 12
    assert last["total"] == 205 and len(last["items"]) == 1
    assert not {x["id"] for x in first["items"]} & {x["id"] for x in last["items"]}
    published = (await client.get("/api/v1/agents/catalog?status=published")).json()
    assert [x["id"] for x in published["items"]] == [agent["id"]]
    assert (await client.get("/api/v1/agents/catalog?status=unpublished")).json()["total"] == 205
    assert isinstance((await client.get("/api/v1/agents")).json(), list)
    assert (await client.get(f"/api/v1/agents/{agent['id']}")).json()["published_version"] == 1
    assert (await client.get("/api/v1/agents/catalog?page=0")).status_code == 422
    assert (await client.get("/api/v1/agents/catalog?page_size=101")).status_code == 422
    await client.patch(
        f"/api/v1/agents/{agent['id']}/archive", json={"archived": True, "revision": 1}
    )
    assert (await client.get("/api/v1/agents/catalog?status=published")).json()["total"] == 0
    assert (
        await client.get("/api/v1/agents/catalog?status=published&include_archived=true")
    ).json()["total"] == 1
    assert (await runtime.platform.agents.catalog("other-tenant"))["total"] == 0


async def test_agent_records_sources_and_full_history(platform):
    client, runtime, _settings = platform
    agent, _ = await create_agent(client)
    other, _ = await create_agent(client)
    rows = []
    for aid, source in [
        (agent["id"], "business"),
        (agent["id"], "playground"),
        (other["id"], "playground"),
    ]:
        response = await client.post(
            "/api/v1/conversations",
            json={"external_id": str(uuid4()), "agent_id": aid, "source": source},
        )
        assert response.status_code == 201, response.text
        rows.append(response.json())
    endpoint = f"/api/v1/agents/{agent['id']}/conversations"
    result = (await client.get(endpoint + "?page_size=1")).json()
    assert result["total"] == 2 and len(result["items"]) == 1
    result = (await client.get(endpoint + "?source=playground")).json()
    assert [x["id"] for x in result["items"]] == [rows[1]["id"]]
    assert (await client.get(endpoint + "?source=business")).json()["items"][0]["id"] == rows[0][
        "id"
    ]
    assert (await client.get(endpoint, params={"q": rows[1]["external_id"]})).json()["total"] == 1
    cid = rows[1]["id"]
    async with runtime.repository.engine.begin() as c:
        await c.execute(
            text("""INSERT INTO conversation_messages(id,conversation_id,role,content)
            SELECT gen_random_uuid(),:cid,'user','message-' || n FROM generate_series(1,225) AS n"""),
            {"cid": cid},
        )
    history = []
    before = None
    while True:
        response = await client.get(
            f"/api/v1/conversations/{cid}/history", params={"before": before} if before else {}
        )
        assert response.status_code == 200, response.text
        data = response.json()
        history = data["items"] + history
        if not data["has_more"]:
            break
        before = data["items"][0]["seq"]
    assert len(history) == 225 and len({x["id"] for x in history}) == 225
    assert [x["seq"] for x in history] == sorted(x["seq"] for x in history)
    with pytest.raises(DomainError) as exc:
        await runtime.platform.conversations.history("other-tenant", cid)
    assert exc.value.status == 404
    with pytest.raises(DomainError) as exc:
        await runtime.platform.conversations.agent_records("other-tenant", agent["id"])
    assert exc.value.status == 404
    viewer = {"Authorization": "Bearer platform-viewer"}
    assert (await client.get(endpoint, headers=viewer)).status_code == 200
    assert (
        await client.post(
            "/api/v1/conversations",
            headers=viewer,
            json={"external_id": str(uuid4()), "agent_id": agent["id"], "source": "playground"},
        )
    ).status_code == 403


async def test_playground_requires_available_published_agent(platform):
    client, _, _ = platform
    agent, _ = await create_agent(client, publish=False)
    body = {"external_id": str(uuid4()), "agent_id": agent["id"], "source": "playground"}
    assert (await client.post("/api/v1/conversations", json=body)).status_code == 409
    assert (
        await client.post(
            "/api/v1/conversations", json={"external_id": str(uuid4()), "source": "playground"}
        )
    ).status_code == 422
    assert (
        await client.post("/api/v1/conversations", json={**body, "source": "unknown"})
    ).status_code == 422
    await client.post(f"/api/v1/agents/{agent['id']}/publish", json={"revision": 1})
    assert (await client.post("/api/v1/conversations", json=body)).status_code == 201
    await client.patch(
        f"/api/v1/agents/{agent['id']}/archive", json={"archived": True, "revision": 1}
    )
    assert (
        await client.post("/api/v1/conversations", json={**body, "external_id": str(uuid4())})
    ).status_code == 409
