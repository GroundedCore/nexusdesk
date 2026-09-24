# ruff: noqa: F811
import asyncio
import hashlib
import hmac
import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from test_open_platform import PUBLIC, ROOT, application, headers, session
from test_platform_postgres import create_agent, execute_next, platform  # noqa: F401

from agent_platform.platform.secrets.vault import CredentialVault

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


def body(app, **changes):
    return {
        **{
            k: app[k]
            for k in [
                "name",
                "description",
                "enabled",
                "agent_ids",
                "rpm",
                "max_concurrency",
                "revision",
                "agent_versions",
                "knowledge_base_ids",
            ]
        },
        **changes,
    }


async def test_pinned_agent_version_and_grant_validation(platform):
    client, services, _ = platform
    agent, draft = await create_agent(client)
    aid = agent["id"]
    draft["config"]["system_prompt"] = "Version two"
    r = await client.put("/api/v1/agents/" + aid, json={**draft, "revision": 1})
    assert r.status_code == 200, r.text
    r = await client.post("/api/v1/agents/" + aid + "/publish", json={"revision": 2})
    assert r.status_code == 200, r.text
    app, key = await application(client, aid, agent_versions={aid: 1})
    cid = await session(client, key["key"], aid)
    r = await client.post(
        f"{PUBLIC}/conversations/{cid}/messages",
        headers=headers(key["key"]),
        json={"message": "pin", "wait_seconds": 0},
    )
    assert r.status_code == 202, r.text
    run = await services.repository.get(UUID(r.json()["id"]), services.platform.settings.tenant_id)
    assert run["config"]["agent"]["version"] == 1
    assert (await client.get(PUBLIC + "/agents", headers=headers(key["key"]))).json()[0][
        "effective_version"
    ] == 1
    assert (
        await client.put(ROOT + "/" + app["id"], json=body(app, agent_versions={aid: 999}))
    ).status_code == 422
    assert (
        await client.put(ROOT + "/" + app["id"], json=body(app, agent_versions={str(uuid4()): 1}))
    ).status_code == 422


async def test_knowledge_sync_permissions_versions_publish_and_delete(platform):
    client, services, _ = platform
    aid = (await create_agent(client))[0]["id"]
    base = (await client.post("/api/v1/knowledge-bases", json={"name": "Synced"})).json()
    kid = base["id"]
    app, key = await application(client, aid, knowledge_base_ids=[kid])
    _, other = await application(client, aid, knowledge_base_ids=[kid])
    _, denied = await application(client, aid)
    path = f"{PUBLIC}/knowledge-bases/{kid}"
    payload = {
        "title": "Shipping",
        "content": "Shipping policy: goods ship within three days.",
        "expected_version": 0,
    }
    assert (
        await client.put(path + "/documents/doc-1", headers=headers(denied["key"]), json=payload)
    ).status_code == 403
    r = await client.put(path + "/documents/doc-1", headers=headers(key["key"]), json=payload)
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["version"] == 1
    retry = await client.put(path + "/documents/doc-1", headers=headers(key["key"]), json=payload)
    assert retry.json()["unchanged"] is True
    assert (await client.get(path + "/documents", headers=headers(other["key"]))).json() == []
    assert (
        await client.delete(
            path + "/documents/doc-1?expected_version=1", headers=headers(other["key"])
        )
    ).status_code == 404
    assert (
        await client.put(
            path + "/documents/doc-1",
            headers=headers(key["key"]),
            json={**payload, "content": "new"},
        )
    ).status_code == 409
    payload.update(content="Shipping policy: goods ship within two days.", expected_version=1)
    assert (
        await client.put(path + "/documents/doc-1", headers=headers(key["key"]), json=payload)
    ).json()["version"] == 2
    r = await client.post(path + "/publish", headers=headers(key["key"], idem="publish-1"))
    assert r.status_code == 202, r.text
    tid = r.json()["task_id"]
    assert (
        await client.post(path + "/publish", headers=headers(key["key"], idem="publish-1"))
    ).json()["task_id"] == tid
    assert (
        await client.get(f"{PUBLIC}/knowledge-tasks/{tid}", headers=headers(other["key"]))
    ).status_code == 404
    assert await services.platform.knowledge_workspace.run_next(
        services.platform.settings.tenant_id
    )
    status = (
        await client.get(f"{PUBLIC}/knowledge-tasks/{tid}", headers=headers(key["key"]))
    ).json()
    assert status["status"] == "completed", status
    docs = (await client.get(path + "/documents", headers=headers(key["key"]))).json()
    assert docs[0]["published_version"] == 2
    assert (
        await client.delete(
            path + "/documents/doc-1?expected_version=1", headers=headers(key["key"])
        )
    ).status_code == 409
    assert (
        await client.delete(
            path + "/documents/doc-1?expected_version=2", headers=headers(key["key"])
        )
    ).status_code == 200
    assert (
        await client.delete(
            path + "/documents/doc-1?expected_version=2", headers=headers(key["key"])
        )
    ).status_code == 200
    async with services.repository.engine.connect() as c:
        assert (
            await c.scalar(
                text("SELECT enabled FROM knowledge_links WHERE kb_id=:k AND document_id=:d"),
                {"k": kid, "d": doc["document_id"]},
            )
            is False
        )
    restored = await client.put(
        path + "/documents/doc-1",
        headers=headers(key["key"]),
        json={**payload, "expected_version": 2},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["version"] == 3
    docs = (await client.get(path + "/documents", headers=headers(key["key"]))).json()
    assert docs[0]["published_version"] is None
    updated = await client.put(ROOT + "/" + app["id"], json=body(app, knowledge_base_ids=[]))
    assert updated.status_code == 200
    assert (await client.get(path + "/documents", headers=headers(key["key"]))).status_code == 403


async def test_webhook_signature_retry_rotation_isolation(platform, tmp_path):
    client, services, settings = platform
    wh = services.platform.open_platform.webhooks
    wh.stopping.set()
    wh.vault = CredentialVault(tmp_path / "master.key")
    await asyncio.sleep(0.02)
    aid = (await create_agent(client))[0]["id"]
    app, key = await application(client, aid)
    cfg = ROOT + "/" + app["id"] + "/webhook"
    assert (
        await client.put(cfg, json={"url": "http://user:password@example.test", "enabled": True})
    ).status_code == 422
    r = await client.put(cfg, json={"url": "http://internal.test/events", "enabled": True})
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    assert secret not in (await client.get(cfg)).text
    assert (
        await client.put(
            cfg, json={"url": "http://internal.test/events", "enabled": True, "revision": 0}
        )
    ).status_code == 409
    assert (
        await client.put(
            cfg,
            headers={"Authorization": "Bearer platform-viewer"},
            json={"url": "http://internal.test/events"},
        )
    ).status_code == 403
    cid = await session(client, key["key"], aid)
    r = await client.post(
        f"{PUBLIC}/conversations/{cid}/messages",
        headers=headers(key["key"]),
        json={"message": "secret customer content", "wait_seconds": 0},
    )
    rid = r.json()["id"]
    await execute_next(services, settings)
    sent = []

    async def send(url, raw, hs):
        expected = (
            "sha256="
            + hmac.new(
                secret.encode(), hs["X-Webhook-Timestamp"].encode() + b"." + raw, hashlib.sha256
            ).hexdigest()
        )
        assert hmac.compare_digest(expected, hs["X-Webhook-Signature"])
        assert b"secret customer content" not in raw
        sent.append(json.loads(raw))
        return 503 if len(sent) == 1 else 204

    wh.send = send
    assert await wh.run_next()
    rows = (await client.get(ROOT + "/" + app["id"] + "/deliveries")).json()["items"]
    assert rows[0]["status"] == "pending" and rows[0]["attempts"] == 1
    eid = rows[0]["id"]
    async with services.repository.engine.begin() as c:
        await c.execute(
            text("UPDATE open_deliveries SET next_attempt_at=now() WHERE id=:id"), {"id": eid}
        )
    assert await wh.run_next()
    assert sent[0]["event_id"] == sent[1]["event_id"] and sent[0]["data"]["resource_id"] == rid
    assert not await wh.run_next()
    rows = (await client.get(ROOT + "/" + app["id"] + "/deliveries")).json()["items"]
    assert rows[0]["status"] == "succeeded" and rows[0]["http_status"] == 204
    r = await client.put(
        cfg,
        json={
            "url": "http://internal.test/events",
            "enabled": True,
            "revision": 1,
            "rotate_secret": True,
        },
    )
    assert r.status_code == 200 and r.json()["secret"] != secret
    assert (
        await client.post(ROOT + "/" + app["id"] + "/deliveries/" + eid + "/retry")
    ).status_code == 404


async def test_webhook_exhaustion_manual_retry_and_configuration_cancel(platform, tmp_path):
    client, services, _ = platform
    wh = services.platform.open_platform.webhooks
    wh.stopping.set()
    wh.vault = CredentialVault(tmp_path / "key")
    await asyncio.sleep(0.02)
    aid = (await create_agent(client))[0]["id"]
    app, key = await application(client, aid)
    path = ROOT + "/" + app["id"]
    await client.put(path + "/webhook", json={"url": "http://internal.test/hooks", "enabled": True})
    cid = await session(client, key["key"], aid)
    r = await client.post(
        f"{PUBLIC}/conversations/{cid}/messages",
        headers=headers(key["key"]),
        json={"message": "cancel", "wait_seconds": 0},
    )
    await client.post(f"{PUBLIC}/runs/{r.json()['id']}/cancel", headers=headers(key["key"]))

    async def fail(*args):
        return 500

    wh.send = fail
    for _ in range(5):
        assert await wh.run_next()
        async with services.repository.engine.begin() as c:
            await c.execute(
                text("UPDATE open_deliveries SET next_attempt_at=now() WHERE app_id=:a"),
                {"a": app["id"]},
            )
    rows = (await client.get(path + "/deliveries")).json()["items"]
    assert rows[0]["status"] == "failed" and rows[0]["attempts"] == 5
    eid = rows[0]["id"]
    assert (await client.post(path + "/deliveries/" + eid + "/retry")).status_code == 200
    # Simulate a process crash after claiming: an expired lease is reclaimable.
    async with services.repository.engine.begin() as c:
        await c.execute(
            text(
                "UPDATE open_deliveries SET status='running',lease_until=now()-interval '1 second' WHERE id=:id"
            ),
            {"id": eid},
        )

    async def succeed(*args):
        return 200

    wh.send = succeed
    assert await wh.run_next()
    assert (await client.get(path + "/deliveries")).json()["items"][0]["status"] == "succeeded"
    r = await client.post(
        f"{PUBLIC}/conversations/{cid}/messages",
        headers=headers(key["key"], idem="new-run"),
        json={"message": "cancel", "wait_seconds": 0},
    )
    await client.post(f"{PUBLIC}/runs/{r.json()['id']}/cancel", headers=headers(key["key"]))
    await wh.collect()
    await client.put(
        path + "/webhook", json={"url": "http://internal.test/new", "enabled": True, "revision": 1}
    )
    rows = (await client.get(path + "/deliveries")).json()["items"]
    assert (
        rows[0]["status"] == "cancelled"
        and rows[0]["error_code"] == "webhook_configuration_changed"
    )
