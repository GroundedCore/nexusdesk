import pytest
from test_platform_postgres import platform as _platform

platform = _platform
pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


async def test_classification_combined_filters_and_update_compatibility(platform):
    client, runtime, _settings = platform
    base = {"name": "案例助手", "config": {"system_prompt": "test"}}
    agents = []
    for industry, tags, example in [
        ("游戏", ["知识问答"], True),
        ("游戏", ["售后支持", "业务办理"], True),
        ("电商零售", ["售后支持"], True),
        ("制造业", ["知识问答"], False),
    ]:
        response = await client.post(
            "/api/v1/agents",
            json={**base, "industry": industry, "tags": tags, "is_example": example},
        )
        assert response.status_code == 201, response.text
        agents.append(response.json())
    endpoint = "/api/v1/agents/catalog"
    result = (
        await client.get(
            endpoint,
            params=[
                ("industry", "游戏"),
                ("industry", "电商零售"),
                ("tag", "售后支持"),
                ("examples_only", "true"),
                ("page_size", "1"),
            ],
        )
    ).json()
    assert result["total"] == 2 and len(result["items"]) == 1
    assert (
        await client.get(
            endpoint, params=[("industry", "游戏"), ("tag", "售后支持"), ("tag", "知识问答")]
        )
    ).json()["total"] == 2
    assert (await client.get(endpoint, params={"industry": "游戏", "tag": "咨询导购"})).json()[
        "total"
    ] == 0
    assert (await client.get(endpoint, params={"examples_only": True})).json()["total"] == 3
    assert (await runtime.platform.agents.catalog("other", industries=["游戏"]))["total"] == 0
    agent = agents[0]
    # Older callers that omit the new fields must not erase classifications.
    updated = await client.put(
        f"/api/v1/agents/{agent['id']}", json={**base, "name": "已改名", "revision": 1}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["industry"] == "gaming" and updated.json()["is_example"]
    cleared = await client.put(
        f"/api/v1/agents/{agent['id']}",
        json={**base, "revision": 2, "industry": "", "tags": [], "is_example": False},
    )
    assert cleared.status_code == 200 and cleared.json()["tags"] == []
    assert not cleared.json()["is_example"]
    denied = await client.put(
        f"/api/v1/agents/{agent['id']}",
        json={**base, "revision": 3},
        headers={"Authorization": "Bearer platform-viewer"},
    )
    assert denied.status_code == 403
    invalid = await client.post("/api/v1/agents", json={**base, "tags": ["未知用途"]})
    assert invalid.status_code == 422
