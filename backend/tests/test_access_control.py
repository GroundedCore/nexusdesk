# ruff: noqa: F811
import pytest
from test_platform_postgres import platform  # noqa: F401

from agent_platform.platform.identity.permissions import (
    ROLE_PERMISSIONS,
    permissions_for,
)

pytestmark = pytest.mark.postgres


async def invite(client, username, role, name="Member"):
    response = await client.post(
        "/api/v1/members/invite",
        json={"username": username, "name": name, "role": role},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def activate(client, username, temporary_password, password="member-password-2026"):
    login = await client.post(
        "/api/v1/auth/local/login",
        json={"username": username, "password": temporary_password},
    )
    assert login.status_code == 200, login.text
    assert login.json()["must_change_password"] is True
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    assert (await client.get("/api/v1/me", headers=headers)).status_code == 403
    changed = await client.post(
        "/api/v1/auth/local/password",
        headers=headers,
        json={"old_password": temporary_password, "new_password": password},
    )
    assert changed.status_code == 204, changed.text
    login = await client.post(
        "/api/v1/auth/local/login",
        json={"username": username, "password": password},
    )
    assert login.status_code == 200, login.text
    return login.json(), {"Authorization": "Bearer " + login.json()["access_token"]}


async def login(client, username, password="member-password-2026"):
    response = await client.post(
        "/api/v1/auth/local/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json(), {"Authorization": "Bearer " + response.json()["access_token"]}


def test_fixed_role_permission_mapping():
    assert permissions_for("admin") == ROLE_PERMISSIONS["admin"]
    assert "member.manage" in permissions_for("admin")
    assert "member.manage" not in permissions_for("operator")
    assert "conversation.write" in permissions_for("operator")
    assert "conversation.write" not in permissions_for("viewer")
    assert "session.self" in permissions_for("viewer")
    assert permissions_for(None) == frozenset()


@pytest.mark.asyncio
async def test_member_invite_role_sessions_and_audit(platform):
    client, runtime, settings = platform
    created = await invite(client, "alice", "operator", "Alice")
    member = created["member"]
    temporary_password = created["temporary_password"]
    assert member["has_local_credential"] is True
    assert member["is_emergency"] is False

    listed = (await client.get("/api/v1/members", params={"q": "alice"})).json()
    assert listed["total"] == 1
    assert listed["items"][0]["username"] == "alice"
    assert listed["counts"]["total"] == 1

    authenticated, headers = await activate(client, "alice", temporary_password)
    assert authenticated["user"]["role"] == "operator"
    me = (await client.get("/api/v1/me", headers=headers)).json()
    assert me["user_id"] == member["id"]
    assert me["auth_source"] == "local"
    assert "conversation.write" in me["permissions"]
    assert (await client.get("/api/v1/members", headers=headers)).status_code == 403

    sessions = (
        await client.get(f"/api/v1/members/{member['id']}/sessions")
    ).json()["items"]
    assert len(sessions) == 1
    assert sessions[0]["auth_source"] == "local"
    assert sessions[0]["current"] is False

    own_sessions = (await client.get("/api/v1/sessions", headers=headers)).json()["items"]
    assert len(own_sessions) == 1 and own_sessions[0]["current"] is True

    updated = await client.patch(
        f"/api/v1/members/{member['id']}",
        json={
            "name": "Alice Updated",
            "role": "viewer",
            "enabled": True,
            "revision": member["revision"],
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["role"] == "viewer"
    assert (await client.get("/api/v1/me", headers=headers)).status_code == 401

    _, headers = await login(client, "alice")
    revoked = await client.post(
        f"/api/v1/members/{member['id']}/sessions/revoke",
        json={"all": True},
    )
    assert revoked.status_code == 200 and revoked.json()["revoked"] == 1
    assert (await client.get("/api/v1/me", headers=headers)).status_code == 401

    async with runtime.platform.engine.connect() as c:
        from agent_platform.platform.persistence.store import many

        actions = {
            row["action"]
            for row in await many(
                c,
                "SELECT action FROM audit_records WHERE tenant_id=:t",
                t=settings.tenant_id,
            )
        }
    assert {"member.created", "member.updated", "member.sessions_revoked"} <= actions


@pytest.mark.asyncio
async def test_last_admin_and_self_protection(platform):
    client, _, _ = platform
    first = (await invite(client, "first.admin", "admin"))["member"]
    demote = await client.patch(
        f"/api/v1/members/{first['id']}",
        json={
            "name": first["name"],
            "role": "viewer",
            "enabled": True,
            "revision": first["revision"],
        },
    )
    assert demote.status_code == 409
    assert demote.json()["detail"] == "last_admin_protected"

    second_created = await invite(client, "second.admin", "admin")
    second = second_created["member"]
    assert (
        await client.patch(
            f"/api/v1/members/{first['id']}",
            json={
                "name": first["name"],
                "role": "viewer",
                "enabled": True,
                "revision": first["revision"],
            },
        )
    ).status_code == 200

    # A member cannot demote or disable their own account through the console.
    activated, headers = await activate(
        client,
        "second.admin",
        second_created["temporary_password"],
    )
    assert activated["user"]["role"] == "admin"
    denied = await client.patch(
        f"/api/v1/members/{second['id']}",
        headers=headers,
        json={
            "name": second["name"],
            "role": "viewer",
            "enabled": True,
            "revision": second["revision"],
        },
    )
    assert denied.status_code == 409
    assert denied.json()["detail"] == "cannot_modify_self"
