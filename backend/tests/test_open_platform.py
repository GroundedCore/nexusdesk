# ruff: noqa: F811
import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from test_platform_postgres import create_agent, execute_next, platform  # noqa: F401

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]
ROOT = "/api/v1/open-platform/applications"
PUBLIC = "/openapi/v1"


async def test_application_status_filter_preserves_total_and_search(platform):
    client, _, _settings = platform
    for name, enabled in [('Filter A', True), ('Filter B', False), ('Other', True)]:
        response = await client.post(ROOT, json={'name':name,'enabled':enabled})
        assert response.status_code == 201
    for enabled, expected in [('true', 'Filter A'), ('false', 'Filter B')]:
        response = await client.get(ROOT, params={'q':'Filter','enabled':enabled})
        assert response.status_code == 200
        assert response.json()['total'] == 1
        assert response.json()['items'][0]['name'] == expected
    assert (await client.get(ROOT)).json()['total'] == 3


async def application(client, aid, **options):
    r = await client.post(ROOT, json={"name": "Enterprise", "agent_ids": [aid], **options})
    assert r.status_code == 201, r.text
    app = r.json()
    r = await client.post(f"{ROOT}/{app['id']}/keys", json={})
    assert r.status_code == 201, r.text
    return app, r.json()


def headers(key, user="employee-1", idem="message-1"):
    return {"Authorization": "Bearer " + key, "X-External-User-ID": user, "Idempotency-Key": idem}


async def session(client, key, aid, user="employee-1", external="session-1"):
    r = await client.post(
        PUBLIC + "/conversations",
        headers=headers(key, user),
        json={"agent_id": aid, "external_session_id": external},
    )
    assert r.status_code == 201, r.text
    return r.json()["conversation_id"]


async def test_identity_keys_scope_rotation_and_disable(platform):
    client, services, _settings = platform
    agent, _ = await create_agent(client)
    app, key = await application(client, agent["id"])
    path = f"{ROOT}/{app['id']}"
    async with services.repository.engine.connect() as c:
        digest = await c.scalar(
            text("SELECT token_hash FROM open_api_keys WHERE id=:id"), {"id": key["id"]}
        )
    assert digest == hashlib.sha256(key["key"].encode()).hexdigest()
    listing = await client.get(path + "/keys")
    assert key["key"] not in listing.text and "token_hash" not in listing.text
    assert (await client.get("/api/v1/agents", headers=headers(key["key"]))).status_code == 401
    assert (await client.get(PUBLIC + "/agents")).status_code == 401
    assert (await client.get(PUBLIC + "/agents", headers=headers(key["key"]))).json()[0][
        "id"
    ] == agent["id"]
    assert (
        await client.post(
            ROOT, headers={"Authorization": "Bearer platform-viewer"}, json={"name": "forbidden"}
        )
    ).status_code == 403
    assert (
        await client.post(
            path + "/keys", json={"expires_at": (datetime.now(UTC) - timedelta(days=1)).isoformat()}
        )
    ).status_code == 422
    rotated = await client.post(path + "/keys", json={"replace_id": key["id"], "grace_minutes": 60})
    assert rotated.status_code == 201, rotated.text
    assert (await client.get(PUBLIC + "/agents", headers=headers(key["key"]))).status_code == 200
    await client.post(path + f"/keys/{key['id']}/revoke")
    assert (await client.get(PUBLIC + "/agents", headers=headers(key["key"]))).status_code == 401
    newkey = rotated.json()
    immediate = await client.post(
        path + "/keys", json={"replace_id": newkey["id"], "grace_minutes": 0}
    )
    assert immediate.status_code == 201
    assert (await client.get(PUBLIC + "/agents", headers=headers(newkey["key"]))).status_code == 401
    body = {
        k: app[k]
        for k in (
            "name",
            "description",
            "enabled",
            "agent_ids",
            "rpm",
            "max_concurrency",
            "revision",
        )
    }
    body["enabled"] = False
    assert (await client.put(path, json=body)).status_code == 200
    assert (
        await client.get(PUBLIC + "/agents", headers=headers(immediate.json()["key"]))
    ).status_code == 403
    assert (await client.put(path, json=body)).status_code == 409


async def test_conversation_run_isolation_idempotency_and_sse(platform):
    client, services, settings = platform
    agent, _ = await create_agent(client)
    app, key = await application(client, agent["id"], max_concurrency=1)
    _, other = await application(client, agent["id"])
    k = key["key"]
    cid = await session(client, k, agent["id"])
    assert await session(client, k, agent["id"]) == cid
    assert await session(client, k, agent["id"], user="employee-2") != cid
    url = f"{PUBLIC}/conversations/{cid}/messages"
    assert (await client.get(url, headers=headers(k, "employee-2"))).status_code == 404
    assert (await client.get(url, headers=headers(other["key"]))).status_code == 404
    response = await client.post(
        url, headers=headers(k), json={"message": "Hello secret-body", "wait_seconds": 0}
    )
    assert response.status_code == 202, response.text
    rid = response.json()["id"]
    request_id = response.headers["x-request-id"]
    assert "config" not in response.json()
    duplicate = await client.post(
        url, headers=headers(k), json={"message": "Hello secret-body", "wait_seconds": 0}
    )
    assert duplicate.json()["id"] == rid
    assert (
        await client.post(url, headers=headers(k), json={"message": "different", "wait_seconds": 0})
    ).status_code == 409
    cid2 = await session(client, k, agent["id"], external="session-2")
    limited = await client.post(
        f"{PUBLIC}/conversations/{cid2}/messages",
        headers=headers(k, idem="other-message"),
        json={"message": "Hi", "wait_seconds": 0},
    )
    assert limited.status_code == 429, limited.text
    assert (
        await client.get(f"{PUBLIC}/runs/{rid}", headers=headers(other["key"]))
    ).status_code == 404
    assert (
        await client.post(f"{PUBLIC}/runs/{rid}/cancel", headers=headers(k, "employee-2"))
    ).status_code == 404
    final = await execute_next(services, settings)
    assert final["status"] == "completed", final["error_code"]
    events = await client.get(f"{PUBLIC}/runs/{rid}/events", headers=headers(k))
    assert events.status_code == 200, events.text
    assert "event: run.completed" in events.text and final["output"] in events.text
    assert "gateway_call_id" not in events.text and "tool_snapshot" not in events.text
    replay = await client.get(
        f"{PUBLIC}/runs/{rid}/events",
        headers={**headers(k), "Last-Event-ID": str(final["event_seq"])},
    )
    assert "event: run.completed" not in replay.text
    assert (await client.get(url, headers=headers(k))).json()["items"][-1]["role"] == "assistant"
    logs = await client.get(f"{ROOT}/{app['id']}/logs?request_id={request_id}")
    assert logs.status_code == 200, logs.text
    assert logs.json()["items"][0]["run_id"] == rid
    assert k not in logs.text and "secret-body" not in logs.text
    response = await client.post(
        url, headers=headers(k, idem="cancel-me"), json={"message": "cancel", "wait_seconds": 0}
    )
    rid = response.json()["id"]
    assert (await client.post(f"{PUBLIC}/runs/{rid}/cancel", headers=headers(k))).json()[
        "status"
    ] == "cancelled"


async def test_rate_limit_and_authorization_revocation(platform):
    client, services, _ = platform
    agent, _ = await create_agent(client)
    app, key = await application(client, agent["id"], rpm=2)
    h = headers(key["key"])
    assert (await client.get(PUBLIC + "/agents", headers=h)).status_code == 200
    assert (await client.get(PUBLIC + "/agents", headers=h)).status_code == 200
    for _ in range(2):
        r = await client.get(PUBLIC + "/agents", headers=h)
        assert r.status_code == 429 and r.headers["retry-after"] == "60"
    unpublished, _ = await create_agent(client, publish=False)
    assert (
        await client.post(ROOT, json={"name": "bad", "agent_ids": [unpublished["id"]]})
    ).status_code == 422
    app, key = await application(client, agent["id"])
    cid = await session(client, key["key"], agent["id"])
    body = {
        k: app[k]
        for k in (
            "name",
            "description",
            "enabled",
            "agent_ids",
            "rpm",
            "max_concurrency",
            "revision",
        )
    }
    body["agent_ids"] = []
    await client.put(f"{ROOT}/{app['id']}", json=body)
    assert (
        await client.get(f"{PUBLIC}/conversations/{cid}/messages", headers=headers(key["key"]))
    ).status_code == 403
    async with services.repository.engine.begin() as c:
        await c.execute(
            text("UPDATE open_applications SET tenant_id=:t WHERE id=:id"),
            {"t": "other-" + str(uuid4()), "id": app["id"]},
        )
    assert (await client.get(PUBLIC + "/agents", headers=headers(key["key"]))).status_code == 401
    async with services.repository.engine.begin() as c:
        await c.execute(
            text("UPDATE open_applications SET tenant_id=:t WHERE id=:id"),
            {"t": services.platform.settings.tenant_id, "id": app["id"]},
        )


import asyncio


async def test_stream_submission_and_terminal_failures(platform):
    client, services, settings = platform
    agent, _ = await create_agent(client)
    _app, key = await application(client, agent["id"])
    cid = await session(client, key["key"], agent["id"])
    task = asyncio.create_task(
        client.post(
            f"{PUBLIC}/conversations/{cid}/messages",
            headers=headers(key["key"]),
            json={"message": "stream hello", "stream": True},
        )
    )
    try:
        row = None
        for _ in range(100):
            row = await services.repository.claim(services.worker.owner, 30, settings.tenant_id)
            if row:
                break
            await asyncio.sleep(0.05)
        assert row is not None
        await services.worker._execute(row)
        r = await asyncio.wait_for(task, 10)
        assert (
            r.status_code == 200
            and "event: accepted" in r.text
            and "event: run.completed" in r.text
        )
        r = await client.post(
            f"{PUBLIC}/conversations/{cid}/messages",
            headers=headers(key["key"], idem="cancel-stream"),
            json={"message": "cancel", "wait_seconds": 0},
        )
        rid = r.json()["id"]
        await client.post(f"{PUBLIC}/runs/{rid}/cancel", headers=headers(key["key"]))
        events = await client.get(f"{PUBLIC}/runs/{rid}/events", headers=headers(key["key"]))
        assert "event: run.cancelled" in events.text and '"status": "cancelled"' in events.text
    finally:
        task.cancel()


async def test_concurrent_idempotent_submissions(platform):
    client, services, _ = platform
    agent, _ = await create_agent(client)
    _app, key = await application(client, agent["id"])
    cid = await session(client, key["key"], agent["id"])
    responses = await asyncio.gather(
        *[
            client.post(
                f"{PUBLIC}/conversations/{cid}/messages",
                headers=headers(key["key"]),
                json={"message": "concurrent", "wait_seconds": 0},
            )
            for _ in range(4)
        ]
    )
    assert all(r.status_code == 202 for r in responses)
    assert len({r.json()["id"] for r in responses}) == 1
    async with services.repository.engine.connect() as c:
        count = await c.scalar(
            text("SELECT count(*) FROM conversation_messages WHERE conversation_id=:id"),
            {"id": cid},
        )
        assert count == 1
