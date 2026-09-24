# ruff: noqa: F811
import json
import re
import time
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from test_open_platform import PUBLIC, ROOT, application
from test_platform_postgres import create_agent, execute_next, platform  # noqa: F401

from agent_platform.modules.open_platform.idp import IdentityProviders
from agent_platform.platform.persistence.store import DomainError

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]
IDENTITY = "/api/v1/open-platform/identity"


async def provider(client, kind="oidc", **changes):
    body = {
        "name": kind,
        "kind": kind,
        "config": {
            "platform_origin": "http://127.0.0.1:5173",
            "client_id": "client",
            "issuer": "https://id.example",
            "organization": "corp",
            "agent_id": "123",
        },
        "secret": "secret-value",
        "enabled": True,
        "workbench": True,
        **changes,
    }
    r = await client.post(IDENTITY + "/providers", json=body)
    assert r.status_code == 201, r.text
    assert "secret-value" not in r.text and "encrypted_secret" not in r.text
    return r.json(), body


async def mock_flow(
    client,
    services,
    p,
    monkeypatch,
    app_id=None,
    parent="https://oa.example",
    subject="subject-1",
    cookie=True,
):
    async def authorize(*args):
        return "https://identity.example/auth?state=" + args[1]

    async def exchange(*args):
        return subject, "Employee"

    e = services.platform.open_platform.enterprise
    monkeypatch.setattr(e.idp, "authorize", authorize)
    monkeypatch.setattr(e.idp, "exchange", exchange)
    params = {"channel": "a" * 40}
    if app_id:
        params.update(app_id=app_id, parent_origin=parent)
    r = await client.get("/api/v1/sso/start/" + p["id"], params=params, follow_redirects=False)
    assert r.status_code == 302, r.text
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    if not cookie:
        client.cookies.clear()
    r = await client.get("/api/v1/sso/callback/" + p["id"], params={"state": state, "code": "code"})
    return r, state


def result(response):
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return json.loads(re.search(r'postMessage\((.*),"http', response.text).group(1))


async def test_workbench_pending_role_state_and_revocation(platform, monkeypatch):
    client, services, _settings = platform
    p, body = await provider(client)
    r, state = await mock_flow(client, services, p, monkeypatch)
    assert result(r)["error"] == "workbench_access_pending"
    users = (await client.get(IDENTITY + "/users")).json()["items"]
    assert len(users) == 1 and users[0]["role"] is None
    replay = await client.get(
        "/api/v1/sso/callback/" + p["id"], params={"state": state, "code": "code"}
    )
    assert replay.status_code == 401
    u = users[0]
    assert (
        await client.put(
            IDENTITY + "/users/" + u["id"],
            json={"name": u["name"], "role": "viewer", "enabled": True, "revision": 1},
        )
    ).status_code == 200
    r, _ = await mock_flow(client, services, p, monkeypatch)
    session = result(r)["session"]
    h = {"Authorization": "Bearer " + session["access_token"]}
    me = await client.get("/api/v1/me", headers=h)
    assert me.json()["role"] == "viewer"
    assert (await client.get(IDENTITY + "/users", headers=h)).status_code == 403
    assert (await client.get(PUBLIC + "/agents", headers=h)).status_code == 401
    r, _ = await mock_flow(client, services, p, monkeypatch, cookie=False)
    assert r.status_code == 401
    # A provider revision change invalidates all prior sessions immediately.
    assert (
        await client.put(
            IDENTITY + "/providers/" + p["id"],
            json={**body, "revision": 1, "secret": None, "enabled": False},
        )
    ).status_code == 200
    assert (await client.get("/api/v1/me", headers=h)).status_code == 401


async def test_embed_scope_user_isolation_parent_key_and_user_revocation(platform):
    client, services, settings = platform
    agent, _ = await create_agent(client)
    app, key = await application(client, agent["id"])
    endpoint = ROOT + "/" + app["id"] + "/embed"
    cfg = {"enabled": True, "origins": ["https://oa.example"], "provider_ids": []}
    assert (await client.put(endpoint, json=cfg)).status_code == 200
    assert (
        await client.get(
            "/api/v1/sso/embed/" + app["id"], params={"parent_origin": "https://evil.example"}
        )
    ).status_code == 403
    assert (
        await client.put(endpoint, json={**cfg, "origins": ["*"], "revision": 1})
    ).status_code == 422

    async def mint(user):
        r = await client.post(
            PUBLIC + "/chat/token",
            headers={"Authorization": "Bearer " + key["key"]},
            json={"external_user_id": user, "parent_origin": "https://oa.example"},
        )
        assert r.status_code == 200, r.text
        return r.json()

    alice, bob = await mint("alice"), await mint("bob")
    h = {"Authorization": "Bearer " + alice["access_token"]}
    bh = {"Authorization": "Bearer " + bob["access_token"]}
    assert (await client.get("/api/v1/me", headers=h)).status_code == 401
    assert (await client.get(PUBLIC + "/knowledge-bases", headers=h)).status_code == 403
    assert (
        await client.post(
            PUBLIC + "/chat/token",
            headers=h,
            json={"external_user_id": "admin", "parent_origin": "https://oa.example"},
        )
    ).status_code == 403
    r = await client.post(
        PUBLIC + "/conversations",
        headers=h,
        json={"agent_id": agent["id"], "external_session_id": "one"},
    )
    assert r.status_code == 201, r.text
    cid = r.json()["conversation_id"]
    assert (
        await client.get(PUBLIC + f"/conversations/{cid}/messages", headers=bh)
    ).status_code == 404
    assert (
        await client.get(
            PUBLIC + f"/conversations/{cid}/messages", headers={**h, "X-External-User-ID": "bob"}
        )
    ).status_code == 403
    r = await client.post(
        PUBLIC + f"/conversations/{cid}/messages",
        headers={**h, "Idempotency-Key": "one"},
        json={"message": "Hello", "wait_seconds": 0},
    )
    assert r.status_code == 202, r.text
    await execute_next(services, settings)
    assert (await client.get(PUBLIC + "/runs/" + r.json()["id"], headers=h)).json()[
        "status"
    ] == "completed"
    users = (await client.get(IDENTITY + "/users")).json()["items"]
    u = next(u for u in users if u["id"] == alice["user"]["id"])
    await client.put(
        IDENTITY + "/users/" + u["id"],
        json={"name": u["name"], "role": None, "enabled": False, "revision": u["revision"]},
    )
    assert (await client.get(PUBLIC + "/agents", headers=h)).status_code == 401
    await client.post(ROOT + f"/{app['id']}/keys/{key['id']}/revoke")
    assert (await client.get(PUBLIC + "/agents", headers=bh)).status_code == 401


async def test_provider_chat_identity_link_and_config_boundaries(platform, monkeypatch):
    client, services, _settings = platform
    agent, _ = await create_agent(client)
    app, _ = await application(client, agent["id"])
    p, body = await provider(client, "dingtalk")
    p2, _ = await provider(client, "feishu")
    cfg = {"enabled": True, "origins": ["https://oa.example"], "provider_ids": [p["id"], p2["id"]]}
    await client.put(ROOT + "/" + app["id"] + "/embed", json=cfg)
    r, _ = await mock_flow(client, services, p, monkeypatch, app["id"])
    a = result(r)["session"]
    r, _ = await mock_flow(client, services, p2, monkeypatch, app["id"])
    b = result(r)["session"]
    assert a["user"]["id"] != b["user"]["id"]
    rows = (await client.get(IDENTITY + "/users")).json()["items"]
    other = next(u for u in rows if u["id"] == b["user"]["id"])
    r = await client.post(
        IDENTITY + "/identities/" + other["identities"][0]["id"] + "/link",
        json={"user_id": a["user"]["id"]},
    )
    assert r.status_code == 200
    assert (
        await client.get(
            PUBLIC + "/agents", headers={"Authorization": "Bearer " + b["access_token"]}
        )
    ).status_code == 401
    r, _ = await mock_flow(client, services, p2, monkeypatch, app["id"])
    assert result(r)["session"]["user"]["id"] == a["user"]["id"]
    changed = await client.put(
        IDENTITY + "/providers/" + p["id"],
        json={**body, "revision": 1, "config": {**body["config"], "client_id": "different"}},
    )
    assert changed.status_code == 409
    await client.put(
        ROOT + "/" + app["id"] + "/embed", json={**cfg, "revision": 1, "enabled": False}
    )
    assert (
        await client.get(
            PUBLIC + "/agents", headers={"Authorization": "Bearer " + a["access_token"]}
        )
    ).status_code == 401


@pytest.mark.parametrize("invalid", [None, "aud", "iss", "nonce", "exp", "signature", "alg", "azp"])
async def test_oidc_crypto_and_claim_validation(invalid, monkeypatch):
    adapter = IdentityProviders()
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    jwk["kid"] = "key"
    claims = {
        "sub": "user",
        "name": "Name",
        "iss": "https://id.example",
        "aud": "client",
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
        "nonce": "nonce",
    }
    if invalid in {"aud", "iss", "nonce", "azp"}:
        claims[invalid] = "wrong"
    if invalid == "exp":
        claims["exp"] = int(time.time()) - 600
    key = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        if invalid == "signature"
        else private
    )
    encoded = jwt.encode(
        claims,
        "not-a-public-key-32-characters-long" if invalid == "alg" else key,
        algorithm="HS256" if invalid == "alg" else "RS256",
        headers={"kid": "key"},
    )

    async def fetch(method, url, **kwargs):
        if "well-known" in url:
            return {
                "issuer": "https://id.example",
                "code_challenge_methods_supported": ["S256"],
                "authorization_endpoint": "https://id.example/auth",
                "token_endpoint": "https://id.example/token",
                "jwks_uri": "https://id.example/keys",
            }
        if url.endswith("keys"):
            return {"keys": [jwk]}
        assert kwargs["data"]["code_verifier"] == "verifier"
        return {"id_token": encoded}

    monkeypatch.setattr(adapter, "fetch", fetch)
    args = (
        {"kind": "oidc", "config": {"issuer": "https://id.example", "client_id": "client"}},
        "secret",
        "code",
        {"nonce": "nonce", "verifier": "verifier"},
        "https://platform.example/callback",
    )
    if invalid:
        with pytest.raises(DomainError):
            await adapter.exchange(*args)
    else:
        assert await adapter.exchange(*args) == ("user", "Name")


@pytest.mark.parametrize("kind", ["wecom", "dingtalk", "feishu"])
@pytest.mark.parametrize("member", [True, False])
async def test_vendor_membership_checks(kind, member, monkeypatch):
    adapter = IdentityProviders()
    calls = []

    async def fetch(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith("gettoken"):
            return {"access_token": "token"}
        if url.endswith("getuserinfo"):
            return {"userid": "member"} if member else {"openid": "outsider"}
        if url.endswith(("userAccessToken", "accessToken")):
            return {"accessToken": "token"}
        if url.endswith("/me"):
            return {"unionId": "union", "nick": "Name"}
        if url.endswith("getbyunionid"):
            return {"result": {"userid": "member"}} if member else {"result": {}}
        if url.endswith("/token"):
            return {"access_token": "token"}
        if url.endswith("user_info"):
            return {
                "data": {
                    "tenant_key": "corp" if member else "other",
                    "open_id": "member",
                    "name": "Name",
                }
            }
        raise AssertionError(url)

    monkeypatch.setattr(adapter, "fetch", fetch)
    args = (
        {"kind": kind, "config": {"client_id": "client", "organization": "corp"}},
        "secret",
        "code",
        {},
        "https://platform.example/callback",
    )
    if member:
        assert (await adapter.exchange(*args))[0] == "member"
    else:
        with pytest.raises(DomainError):
            await adapter.exchange(*args)
    if kind == "dingtalk":
        assert any("getbyunionid" in url for _, url, _ in calls)
