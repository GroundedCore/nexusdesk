from uuid import UUID

import pytest
from test_model_gateway import setup
from test_platform_postgres import platform as _platform

platform = _platform
pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


@pytest.mark.parametrize("new_language", ["en", "hi"])
async def test_response_language_only_changes_published_snapshot_after_publish(platform, new_language):
    client, runtime, settings = platform
    _, _, profile = await setup(client)
    config = {
        "system_prompt": "用户原始提示词",
        "model_profile_id": profile["id"],
        "model_profile_version": 1,
        "reply_language": "zh-TW",
    }
    created = await client.post("/api/v1/agents", json={"name": "语言助手", "config": config})
    assert created.status_code == 201, created.text
    aid = created.json()["id"]
    published = await client.post(f"/api/v1/agents/{aid}/publish", json={"revision": 1})
    assert published.status_code == 200, published.text

    async def snapshot_now():
        async with runtime.platform.engine.connect() as connection:
            return await runtime.platform.agents.snapshot(settings.tenant_id, UUID(aid), connection)

    snapshot = await snapshot_now()
    assert snapshot["config"]["reply_language"] == "zh-TW"
    updated = await client.put(
        f"/api/v1/agents/{aid}",
        json={"name": "语言助手", "revision": 1, "config": {**config, "reply_language": new_language}},
    )
    assert updated.status_code == 200, updated.text
    old = await snapshot_now()
    assert old["config"]["reply_language"] == "zh-TW"
    published = await client.post(f"/api/v1/agents/{aid}/publish", json={"revision": 2})
    assert published.status_code == 200, published.text
    new = await snapshot_now()
    assert new["config"]["reply_language"] == new_language
    assert new["config"]["system_prompt"] == config["system_prompt"]
