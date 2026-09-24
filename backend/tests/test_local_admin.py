# ruff: noqa: F811
import asyncio

import pytest
from test_platform_postgres import platform  # noqa: F401

from agent_platform.platform.identity.local_admin import (
    LocalAdmin,
    hash_password,
    seed_default_admin,
    verify_password,
)
from agent_platform.platform.persistence.store import DomainError, execute, one

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]
ROOT = "/api/v1/auth/local"
PASSWORD = "test-admin-password-2026"


def service(platform):
    _, runtime, settings = platform
    return LocalAdmin(runtime.platform.engine, settings.tenant_id)


async def login(client, password=PASSWORD, username="rescue.admin"):
    return await client.post(ROOT + "/login", json={"username": username, "password": password})


async def test_login_logout_and_no_secret_leak(platform):
    client, runtime, settings = platform
    admin = await service(platform).initialize("Rescue.Admin", PASSWORD)
    wrong = await login(client, "incorrect-password")
    unknown = await login(client, "incorrect-password", "missing")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    response = await login(client)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert PASSWORD not in response.text and "password_hash" not in response.text
    token = response.json()["access_token"]
    headers = {"Authorization": "Bearer " + token}
    me = await client.get("/api/v1/me", headers=headers)
    assert me.status_code == 200 and me.json()["role"] == "admin"
    assert me.json()["actor"] == "enterprise:" + admin["id"]
    assert (await client.post(ROOT + "/logout", headers=headers)).status_code == 204
    assert (await client.get("/api/v1/me", headers=headers)).status_code == 401
    async with runtime.platform.engine.connect() as c:
        row = await one(
            c,
            "SELECT password_hash FROM local_admin_credentials WHERE tenant_id=:t",
            t=settings.tenant_id,
        )
        assert row["password_hash"] != PASSWORD
        audit = await one(
            c,
            "SELECT string_agg(details::text,'') AS data FROM audit_records WHERE tenant_id=:t",
            t=settings.tenant_id,
        )
        assert PASSWORD not in audit["data"] and token not in audit["data"]


async def test_password_change_revokes_all_sessions(platform):
    client, _, _ = platform
    await service(platform).initialize("rescue.admin", PASSWORD)
    tokens = [(await login(client)).json()["access_token"] for _ in range(2)]
    headers = {"Authorization": "Bearer " + tokens[0]}
    new = "changed-strong-admin-password"
    wrong = await client.post(
        ROOT + "/password",
        headers=headers,
        json={"old_password": "not-the-password", "new_password": new},
    )
    assert wrong.status_code == 401
    changed = await client.post(
        ROOT + "/password", headers=headers, json={"old_password": PASSWORD, "new_password": new}
    )
    assert changed.status_code == 204, changed.text
    for token in tokens:
        assert (
            await client.get("/api/v1/me", headers={"Authorization": "Bearer " + token})
        ).status_code == 401
    assert (await login(client)).status_code == 401
    assert (await login(client, new)).status_code == 200


async def test_initialize_is_atomic_and_blocks_anonymous_development(platform):
    client, _, settings = platform
    settings.api_token = None
    client.headers.pop("authorization")
    status = await client.get(ROOT + "/status")
    assert status.json() == {"initialized": False, "development_access": True}
    assert status.headers["cache-control"] == "no-store"
    assert (await client.get("/api/v1/me")).status_code == 200
    outcomes = await asyncio.gather(
        *[service(platform).initialize("rescue.admin", PASSWORD) for _ in range(2)],
        return_exceptions=True,
    )
    assert sum(isinstance(value, dict) for value in outcomes) == 1
    assert (
        sum(
            isinstance(value, DomainError) and value.code == "local_admin_already_initialized"
            for value in outcomes
        )
        == 1
    )
    assert (await client.get("/api/v1/me")).status_code == 401
    assert (await client.get(ROOT + "/status")).json() == {
        "initialized": True,
        "development_access": False,
    }
    token = (await login(client)).json()["access_token"]
    assert (
        await client.get("/api/v1/me", headers={"Authorization": "Bearer " + token})
    ).status_code == 200


async def test_enterprise_admin_cannot_disable_demote_or_bind_emergency_account(platform):
    client, runtime, _ = platform
    admin = await service(platform).initialize("rescue.admin", PASSWORD)
    root = "/api/v1/open-platform/identity"
    for role, enabled in [("viewer", True), ("admin", False), (None, True)]:
        r = await client.put(
            root + "/users/" + admin["id"],
            json={"name": "Admin", "role": role, "enabled": enabled, "revision": 1},
        )
        assert r.status_code == 409 and r.json()["detail"] == "independent_admin_protected"
    ent = runtime.platform.open_platform.enterprise
    async with ent.engine.begin() as c:
        await ent.resolve(c, "provider:test", "employee", "Employee")
        identity = await one(
            c, "SELECT id FROM enterprise_identities WHERE tenant_id=:t", t=ent.tenant
        )
    r = await client.post(
        root + "/identities/" + str(identity["id"]) + "/link", json={"user_id": admin["id"]}
    )
    assert r.status_code == 409
    assert (await login(client)).status_code == 200


async def test_expired_session_and_cross_tenant_rejected(platform):
    client, runtime, settings = platform
    await service(platform).initialize("rescue.admin", PASSWORD)
    token = (await login(client)).json()["access_token"]
    h = {"Authorization": "Bearer " + token}
    original = settings.tenant_id
    settings.tenant_id = "different-tenant"
    assert (await client.get("/api/v1/me", headers=h)).status_code == 401
    settings.tenant_id = original
    async with runtime.platform.engine.begin() as c:
        await execute(
            c,
            "UPDATE local_admin_sessions SET expires_at=now()-interval '1 second' WHERE tenant_id=:t",
            t=original,
        )
    assert (await client.get("/api/v1/me", headers=h)).status_code == 401


async def test_rate_limit_and_password_validation(platform):
    client, runtime, settings = platform
    with pytest.raises(DomainError):
        await service(platform).initialize("rescue.admin", "short")
    await service(platform).initialize("rescue.admin", PASSWORD)
    async with runtime.platform.engine.begin() as c:
        await execute(
            c,
            "INSERT INTO local_login_limits(tenant_id,bucket,attempts,expires_at) VALUES(:t,'unrelated',1,now()-interval '1 hour')",
            t=settings.tenant_id,
        )
    for _ in range(10):
        assert (await login(client, "incorrect-password")).status_code == 401
    assert (await login(client)).status_code == 429
    invalid = await client.post(
        ROOT + "/login", json={"username": "rescue.admin", "password": "s" * 129}
    )
    assert invalid.status_code == 422 and "s" * 129 not in invalid.text
    async with runtime.platform.engine.begin() as c:
        await execute(c, "DELETE FROM local_login_limits WHERE tenant_id=:t", t=settings.tenant_id)
    assert (await login(client)).status_code == 200
    first, second = hash_password(PASSWORD), hash_password(PASSWORD)
    assert first != second and verify_password(PASSWORD, first)


async def test_deployment_sql_default_password_rotation_and_idempotency(platform):
    client, runtime, settings = platform
    await asyncio.gather(
        *[seed_default_admin(runtime.platform.engine, settings.tenant_id) for _ in range(2)]
    )
    response = await login(client, "nexusdesk", "admin")
    assert response.status_code == 200, response.text
    assert response.json()["must_change_password"] is True
    headers = {"Authorization": "Bearer " + response.json()["access_token"]}
    for path in ("/api/v1/me", "/api/v1/staff", "/api/v1/open-platform/identity/users"):
        denied = await client.get(path, headers=headers)
        assert (
            denied.status_code == 403
            and denied.json()["detail"] == "default_password_change_required"
        )
    short = await client.post(
        ROOT + "/password",
        headers=headers,
        json={"old_password": "nexusdesk", "new_password": "another"},
    )
    assert short.status_code == 422
    changed = await client.post(
        ROOT + "/password",
        headers=headers,
        json={"old_password": "nexusdesk", "new_password": PASSWORD},
    )
    assert changed.status_code == 204, changed.text
    assert (await client.get("/api/v1/me", headers=headers)).status_code == 401
    await seed_default_admin(runtime.platform.engine, settings.tenant_id)
    assert (await login(client, "nexusdesk", "admin")).status_code == 401
    response = await login(client, PASSWORD, "admin")
    assert response.json()["must_change_password"] is False
    assert (
        await client.get(
            "/api/v1/me", headers={"Authorization": "Bearer " + response.json()["access_token"]}
        )
    ).status_code == 200
    async with runtime.platform.engine.connect() as c:
        count = await one(
            c,
            "SELECT count(*) AS n FROM local_admin_credentials WHERE tenant_id=:t",
            t=settings.tenant_id,
        )
        assert count["n"] == 1


async def test_deployment_seed_preserves_existing_custom_admin(platform):
    client, runtime, settings = platform
    await service(platform).initialize("rescue.admin", PASSWORD)
    await seed_default_admin(runtime.platform.engine, settings.tenant_id)
    response = await login(client)
    assert response.status_code == 200 and not response.json()["must_change_password"]
    assert (await login(client, "nexusdesk", "admin")).status_code == 401
