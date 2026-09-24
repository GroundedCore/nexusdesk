import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from test_model_gateway import setup
from test_platform_postgres import platform as _platform_fixture

from agent_platform.modules.model_gateway.governance import Governance, estimate
from agent_platform.platform.persistence.store import DomainError, one

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]
gateway_platform = _platform_fixture
ROOT = "/api/v1/model-gateway"


def invocation(profile, content="hello"):
    return {
        "profile_id": profile["id"],
        "version": 1,
        "payload": {"operation": "chat", "messages": [{"role": "user", "content": content}]},
    }


async def test_words_review_alerts_and_metadata(gateway_platform):
    client, services, _settings = gateway_platform
    _, model, profile = await setup(client)
    response = await client.post(
        ROOT + "/words/import",
        json={"words": [{"name": "SECRET", "category": "隐私"}, {"name": "secret"}]},
    )
    assert response.json() == {"imported": 1, "skipped": 1}
    assert (await client.post(ROOT + "/words/test", json={"text": "ＳＥＣＲＥＴ"})).json()[
        "blocked"
    ]
    policy = {"input_review": "sensitive_words", "output_review": "off", "pricing": {}}
    assert (
        await client.put(
            ROOT + f"/models/{model['id']}/settings", json={"revision": 0, "spec": policy}
        )
    ).status_code == 200
    rule = await client.post(
        ROOT + "/manage/alert_rules",
        json={"name": "内容拦截告警", "metric": "content_blocked", "cooldown_seconds": 0},
    )
    assert rule.status_code == 201, rule.text
    blocked = await client.post(ROOT + "/invoke", json=invocation(profile, "a secret"))
    assert blocked.status_code == 422 and blocked.json()["detail"] == "content_blocked_input"
    logs = (await client.get(ROOT + "/call-records?status=failed")).json()
    assert logs["total"] == 1
    detail = (await client.get(ROOT + "/calls/" + logs["items"][0]["id"])).json()
    assert detail["attempts"] == [] and "a secret" not in str(detail)
    events = (await client.get(ROOT + "/alert-events?acknowledged=false")).json()
    assert events["total"] == 1
    acknowledged = await client.post(ROOT + f"/alert-events/{events['items'][0]['id']}/acknowledge")
    assert acknowledged.json()["acknowledged"]
    assert (await client.get(ROOT + "/alert-events?acknowledged=false")).json()["total"] == 0
    summary = (await client.get(ROOT + "/statistics")).json()["summary"]
    assert summary["blocked"] == 1 and summary["failed"] == 1
    assert (await client.post(ROOT + "/invoke", json=invocation(profile))).status_code == 200
    assert (await services.platform.gateway.governance.page("another-tenant", "sensitive_words"))[
        "total"
    ] == 0


async def test_scoped_key_rotation_revocation_and_quotas(gateway_platform):
    client, services, settings = gateway_platform
    _, _, profile = await setup(client)
    _, _, other = await setup(client)
    key_response = await client.post(
        ROOT + "/manage/access_keys",
        json={
            "name": "应用密钥",
            "profile_ids": [profile["id"]],
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "concurrency": 1,
        },
    )
    assert key_response.status_code == 201, key_response.text
    key = key_response.json()
    auth = {"Authorization": "Bearer " + key["secret"]}
    assert "token_hash" not in key
    listing = (await client.get(ROOT + "/manage/access_keys")).json()
    assert key["secret"] not in str(listing) and "token_hash" not in str(listing)
    assert (
        await client.post(ROOT + "/external/invoke", json=invocation(other), headers=auth)
    ).status_code == 403
    for _ in range(2):
        assert (
            await client.post(ROOT + "/external/invoke", json=invocation(profile), headers=auth)
        ).status_code == 200
    # A key is not a platform administrator, including development-mode auth fallback.
    assert (
        await client.post(ROOT + "/connections", json={"name": "denied"}, headers=auth)
    ).status_code == 401
    rotation = (await client.post(ROOT + f"/keys/{key['id']}/rotate", json={"revision": 1})).json()
    assert (
        await client.post(ROOT + "/external/invoke", json=invocation(profile), headers=auth)
    ).status_code == 401
    auth = {"Authorization": "Bearer " + rotation["secret"]}
    assert (
        await client.post(ROOT + "/external/invoke", json=invocation(profile), headers=auth)
    ).status_code == 200
    await client.patch(
        ROOT + f"/manage/access_keys/{key['id']}",
        json={"revision": rotation["revision"], "enabled": False},
    )
    assert (
        await client.post(ROOT + "/external/invoke", json=invocation(profile), headers=auth)
    ).status_code == 401
    async with services.platform.engine.connect() as c:
        assert (
            await one(
                c, "SELECT count(*) n FROM gateway_leases WHERE tenant_id=:t", t=settings.tenant_id
            )
        )["n"] == 0
    settings.api_token = None
    assert (
        await client.post(ROOT + "/connections", json={"name": "denied"}, headers=auth)
    ).status_code == 401


async def test_database_quota_is_atomic_and_releases(gateway_platform):
    client, services, settings = gateway_platform
    _, _, profile = await setup(client)
    response = await client.put(
        ROOT + "/quota",
        json={
            "revision": 0,
            "spec": {"requests_per_minute": 1, "requests_per_day": 1, "concurrency": 1},
        },
    )
    assert response.status_code == 200
    managers = [Governance(services.platform.gateway), Governance(services.platform.gateway)]
    ids = [uuid4(), uuid4()]
    results = await asyncio.gather(
        *(
            manager.admit(settings.tenant_id, identifier, profile["id"], 30)
            for manager, identifier in zip(managers, ids)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, DomainError) for result in results) == 1
    for identifier in ids:
        await managers[0].release(settings.tenant_id, identifier)
    denied = await client.post(ROOT + "/invoke", json=invocation(profile))
    assert denied.status_code == 429 and denied.json()["detail"] == "gateway_request_quota_exceeded"
    assert (await client.put(ROOT + "/quota", json={"revision": 0, "spec": {}})).status_code == 409


async def test_output_review_cost_snapshots_and_pagination(gateway_platform):
    client, services, _settings = gateway_platform
    _, model, profile = await setup(client, protocol="openai_compatible")
    await client.post(ROOT + "/manage/sensitive_words", json={"name": "blocked-output"})
    pricing = {"input_per_million": "2", "output_per_million": "6"}
    policy = {"output_review": "sensitive_words", "pricing": pricing}
    assert (
        await client.put(
            ROOT + f"/models/{model['id']}/settings", json={"revision": 0, "spec": policy}
        )
    ).status_code == 200

    async def handle(request):
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "blocked-output", "role": "assistant"}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as provider:
        services.platform.gateway.client = provider
        response = await client.post(ROOT + "/stream", json=invocation(profile))
        assert "event: error" in response.text and "content_blocked_output" in response.text
        assert "blocked-output" not in response.text and "event: delta" not in response.text
    logs = (await client.get(ROOT + "/call-records?page_size=1")).json()
    assert logs["total"] == 1 and Decimal(logs["items"][0]["estimated_cost"]) == Decimal(
        "0.00002000"
    )
    stats = (await client.get(ROOT + "/statistics")).json()
    assert stats["summary"]["requests"] == 1 and len(stats["daily"]) == 1
    # Editing current rates cannot rewrite historical estimated charges.
    await client.put(
        ROOT + f"/models/{model['id']}/settings",
        json={"revision": 1, "spec": {"pricing": {"input_per_million": "999"}}},
    )
    assert (await client.get(ROOT + "/statistics")).json()["summary"]["estimated_cost"] == stats[
        "summary"
    ]["estimated_cost"]
    assert (
        await client.get(ROOT + "/statistics?since=2026-01-01T00:00:00&until=2026-01-02T00:00:00")
    ).status_code == 400
    assert (await client.get(ROOT + "/catalog/models?page_size=1")).json()["total"] == 1


async def test_archive_roles_conflicts_and_unknown_costs(gateway_platform):
    client, _services, _settings = gateway_platform
    connection, _model, _profile = await setup(client)
    assert (
        await client.delete(ROOT + f"/resources/connections/{connection['id']}?revision=1")
    ).status_code == 409
    for path in ["/manage/access_keys", "/manage/sensitive_words", "/manage/alert_rules"]:
        assert (
            await client.get(ROOT + path, headers={"Authorization": "Bearer platform-viewer"})
        ).status_code == 403
    word = (await client.post(ROOT + "/manage/sensitive_words", json={"name": "词"})).json()
    assert (
        await client.delete(ROOT + f"/resources/sensitive_words/{word['id']}?revision=1")
    ).status_code == 200
    assert (await client.get(ROOT + "/manage/sensitive_words")).json()["total"] == 0
    assert estimate({"input_per_million": "2"}, None) is None
    assert estimate({}, {"prompt_tokens": 4}) is None
    assert estimate({"input_per_million": "0"}, {"prompt_tokens": 4}) == 0
