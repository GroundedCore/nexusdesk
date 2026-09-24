import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text
from test_platform_postgres import create_agent
from test_platform_postgres import platform as _platform

from agent_platform.platform.persistence.store import DomainError

platform = _platform
pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


async def archive(client, aid):
    result = await client.patch(
        f"/api/v1/agents/{aid}/archive", json={"archived": True, "revision": 1}
    )
    assert result.status_code == 200
    return result.json()


async def test_delete_preserves_history_and_removes_configuration(platform):
    client, runtime, settings = platform
    agent, _ = await create_agent(client)
    response = await client.post(
        "/api/v1/conversations", json={"external_id": str(uuid4()), "agent_id": agent["id"]}
    )
    conv = response.json()
    sent = await client.post(
        f"/api/v1/conversations/{conv['id']}/messages", json={"content": "保留这条消息"}
    )
    run_id = sent.json()["run"]["id"]
    await archive(client, agent["id"])
    endpoint = f"/api/v1/agents/{agent['id']}?revision=2"
    assert (await client.delete(endpoint)).json()["detail"] == "agent_has_active_runs"
    async with runtime.repository.engine.begin() as c:
        await c.execute(
            text("UPDATE runtime_runs SET status='completed',output='历史回答' WHERE id=:id"),
            {"id": run_id},
        )
    deleted = await client.delete(endpoint)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["preserved_conversations"] == 1
    assert (await client.get(f"/api/v1/agents/{agent['id']}")).status_code == 404
    assert (await client.get("/api/v1/agents/catalog?include_archived=true")).json()["total"] == 0
    assert (await client.delete(endpoint)).status_code == 404
    detail = (await client.get(f"/api/v1/conversations/{conv['id']}")).json()
    assert detail["agent_id"] is None and detail["mode"] == "closed"
    assert detail["deleted_agent"]["id"] == agent["id"]
    assert detail["deleted_agent"]["name"] == agent["name"]
    assert detail["messages"][0]["content"] == "保留这条消息"
    assert detail["runs"][0]["id"] == run_id and detail["runs"][0]["output"] == "历史回答"
    assert (await client.get(f"/api/v1/conversations/{conv['id']}/history")).json()["items"]
    assert (
        await client.post(
            f"/api/v1/conversations/{conv['id']}/messages", json={"content": "不能重新发送"}
        )
    ).status_code == 409
    reopened = await client.post(
        f"/api/v1/conversations/{conv['id']}/transition",
        json={"operation": "reopen", "revision": detail["revision"]},
    )
    assert reopened.json()["detail"] == "conversation_agent_deleted"
    async with runtime.repository.engine.connect() as c:
        assert (
            await c.execute(
                text("SELECT count(*) FROM agent_versions WHERE agent_id=:id"), {"id": agent["id"]}
            )
        ).scalar() == 0
        event = (
            await c.execute(
                text(
                    "SELECT details FROM audit_records WHERE tenant_id=:t AND action='agent.deleted'"
                ),
                {"t": settings.tenant_id},
            )
        ).scalar()
        assert event["name"] == agent["name"] and event["preserved_conversations"] == 1
        config = (
            await c.execute(text("SELECT config FROM runtime_runs WHERE id=:id"), {"id": run_id})
        ).scalar()
        assert config["agent"]["id"] == agent["id"]


async def test_delete_permissions_archive_revision_and_tenant(platform):
    client, runtime, _ = platform
    agent, _ = await create_agent(client)
    endpoint = f"/api/v1/agents/{agent['id']}"
    assert (await client.delete(endpoint + "?revision=1")).json()[
        "detail"
    ] == "agent_must_be_archived"
    await archive(client, agent["id"])
    for role in ("operator", "viewer"):
        assert (
            await client.delete(
                endpoint + "?revision=2", headers={"Authorization": f"Bearer platform-{role}"}
            )
        ).status_code == 403
    assert (await client.delete(endpoint)).status_code == 422
    assert (await client.delete(endpoint + "?revision=1")).json()[
        "detail"
    ] == "agent_revision_conflict"
    with pytest.raises(DomainError) as exc:
        await runtime.platform.agents.delete("other-tenant", "admin", agent["id"], 2)
    assert exc.value.status == 404
    assert (await client.get(endpoint)).status_code == 200


@pytest.mark.parametrize(
    "kind", ["channel", "evaluation_case", "evaluation_report", "handoff", "pending_action"]
)
async def test_delete_blocks_references_without_partial_changes(platform, kind):
    client, runtime, settings = platform
    agent, _ = await create_agent(client)
    response = await client.post(
        "/api/v1/conversations", json={"external_id": str(uuid4()), "agent_id": agent["id"]}
    )
    conv = response.json()
    expected = {
        "channel": "agent_referenced_by_channels",
        "evaluation_case": "agent_referenced_by_evaluations",
        "evaluation_report": "agent_referenced_by_evaluations",
        "handoff": "agent_has_open_handoffs",
        "pending_action": "agent_has_pending_actions",
    }[kind]
    if kind == "channel":
        row = (
            await client.post(
                "/api/v1/channels", json={"name": "仍被引用的渠道", "agent_id": agent["id"]}
            )
        ).json()
        await client.patch(f"/api/v1/channels/{row['id']}", json={"enabled": False})
    elif kind == "handoff":
        assert (
            await client.post(
                f"/api/v1/conversations/{conv['id']}/handoffs", json={"reason": "需要客服"}
            )
        ).status_code == 200
    else:
        async with runtime.repository.engine.begin() as c:
            params = {"id": uuid4(), "t": settings.tenant_id, "aid": agent["id"], "cid": conv["id"]}
            if kind == "evaluation_case":
                await c.execute(
                    text(
                        "INSERT INTO evaluation_cases(id,tenant_id,agent_id,name,spec) VALUES(:id,:t,:aid,'评测','{}')"
                    ),
                    params,
                )
            elif kind == "evaluation_report":
                await c.execute(
                    text(
                        "INSERT INTO evaluation_reports(id,tenant_id,agent_id,agent_version,results) VALUES(:id,:t,:aid,1,'[]')"
                    ),
                    params,
                )
            else:
                await c.execute(
                    text(
                        "INSERT INTO pending_actions(id,tenant_id,conversation_id,kind,payload,dedup_key,expires_at) VALUES(:id,:t,:cid,'ticket','{}','test',now()+interval '1 hour')"
                    ),
                    params,
                )
    await archive(client, agent["id"])
    result = await client.delete(f"/api/v1/agents/{agent['id']}?revision=2")
    assert result.status_code == 409 and result.json()["detail"] == expected, result.text
    detail = (await client.get(f"/api/v1/conversations/{conv['id']}")).json()
    assert detail["agent_id"] == agent["id"] and detail["deleted_agent"] is None
    assert (await client.get(f"/api/v1/agents/{agent['id']}/versions")).json()


async def test_delete_restore_race_never_deletes_restored_agent(platform):
    client, _, _ = platform
    agent, _ = await create_agent(client)
    await archive(client, agent["id"])
    deleted, restored = await asyncio.gather(
        client.delete(f"/api/v1/agents/{agent['id']}?revision=2"),
        client.patch(
            f"/api/v1/agents/{agent['id']}/archive", json={"archived": False, "revision": 2}
        ),
    )
    assert sorted([deleted.status_code, restored.status_code]) == [200, 409]
    if restored.status_code == 200:
        assert (await client.get(f"/api/v1/agents/{agent['id']}")).json()["archived"] is False
    else:
        assert (await client.get(f"/api/v1/agents/{agent['id']}")).status_code == 404
