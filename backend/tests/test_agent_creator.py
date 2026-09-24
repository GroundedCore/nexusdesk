from uuid import UUID

import pytest
from test_platform_postgres import platform as _platform

from agent_platform.modules.agent_config.service import AgentDraft

platform = _platform
pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


async def test_mine_uses_authenticated_creator_and_combines_filters(platform):
    client, runtime, settings = platform
    service = runtime.platform
    staff = await service.staff.create(settings.tenant_id, "admin", "同事", "operator")
    headers = {"Authorization": "Bearer " + staff["token"]}
    body = {
        "name": "自己的助手",
        "industry": "gaming",
        "tags": ["knowledge"],
        "config": {"system_prompt": "test"},
    }
    mine = (await client.post("/api/v1/agents", json=body)).json()
    assert mine["created_by"] == "admin"
    theirs = await service.agents.create(
        settings.tenant_id,
        "staff:" + str(staff["id"]),
        AgentDraft.model_validate({**body, "name": "同事的助手"}),
    )
    theirs["id"] = str(theirs["id"])
    assert theirs["created_by"] == "staff:" + str(staff["id"])
    await service.agents.create(
        settings.tenant_id,
        "seed-industries",
        AgentDraft.model_validate({**body, "is_example": True}),
    )
    endpoint = "/api/v1/agents/catalog"
    result = (
        await client.get(
            endpoint,
            params={
                "mine_only": True,
                "industry": "gaming",
                "tag": "knowledge",
                "q": "自己的",
                "status": "unpublished",
                "page_size": 1,
            },
        )
    ).json()
    assert result["total"] == 1 and result["items"][0]["id"] == mine["id"]
    assert (await client.get(endpoint)).json()["total"] == 3
    assert (await client.get(endpoint, params={"mine_only": True}, headers=headers)).json()[
        "items"
    ][0]["id"] == theirs["id"]
    # Neither editing by a colleague nor a supplied query parameter transfers ownership.
    changed = await client.put("/api/v1/agents/" + theirs["id"], json={**body, "revision": 1})
    assert changed.status_code == 200 and changed.json()["created_by"] == theirs["created_by"]
    result = (
        await client.get(endpoint, params={"mine_only": True, "created_by": theirs["created_by"]})
    ).json()
    assert result["total"] == 1 and result["items"][0]["id"] == mine["id"]
    forged = await client.post("/api/v1/agents", json={**body, "created_by": theirs["created_by"]})
    assert forged.status_code == 422
    archived = await client.patch(
        f"/api/v1/agents/{mine['id']}/archive", json={"archived": True, "revision": 1}
    )
    assert archived.status_code == 200, archived.text
    assert (await client.get(endpoint, params={"mine_only": True})).json()["total"] == 0
    assert (
        await client.get(endpoint, params={"mine_only": True, "include_archived": True})
    ).json()["total"] == 1
    assert (await service.agents.get(settings.tenant_id, UUID(mine["id"])))["created_by"] == "admin"
