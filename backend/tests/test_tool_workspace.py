import json
from uuid import UUID

import httpx
import pytest
from test_platform_postgres import create_agent
from test_platform_postgres import platform as _platform

from agent_platform.modules.tool_gateway.service import HttpTool, ToolGateway
from agent_platform.platform.persistence.store import DomainError, one
from agent_platform.platform.secrets.vault import CredentialVault

platform = _platform
pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]
ROOT = "/api/v1/tool-workspace"


def collection(**kw):
    return {
        "name": "订单物流",
        "description": "查询订单",
        "base_url": "http://localhost/v1",
        "auth_kind": "bearer",
        "header_secrets": {"Authorization": "Bearer fake-collection-key"},
        **kw,
    }


def api_definition(name="order_lookup", **kw):
    return {
        "display_name": "查询订单",
        "relative_path": "/orders",
        "auth_mode": "inherit",
        "definition": {
            "name": name,
            "description": "查询订单详情",
            "url": "http://localhost/v1/orders",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
                "additionalProperties": False,
            },
        },
        **kw,
    }


async def setup(platform, tmp_path):
    client, services, _ = platform
    services.platform.tools.vault = CredentialVault(tmp_path / "master.key")
    response = await client.post(ROOT + "/collections", json=collection())
    assert response.status_code == 201, response.text
    group = response.json()
    response = await client.post(ROOT + f"/collections/{group['id']}/apis", json=api_definition())
    assert response.status_code == 201, response.text
    return group, response.json()


async def test_inheritance_publication_snapshots_and_live_disable(platform, tmp_path):
    client, services, settings = platform
    group, row = await setup(platform, tmp_path)
    gid, tid = group["id"], row["id"]
    registry = services.platform.tools
    assert row["published_version"] is None
    assert "fake-collection-key" not in json.dumps(row) + json.dumps(group)
    with pytest.raises(DomainError):
        await registry.resolve(settings.tenant_id, [row["name"]])
    preview = (await client.get(ROOT + f"/apis/{tid}/preview")).json()
    response = await client.post(
        ROOT + f"/apis/{tid}/publish", json={"revision": 1, "collection_revision": 1}
    )
    assert response.status_code == 200, response.text
    assert "credential_id" not in preview["definition"]
    specs = await registry.resolve(settings.tenant_id, [row["name"]])
    spec1 = HttpTool.model_validate(specs[0]["spec"])
    assert spec1.url == "http://localhost/v1/orders"
    assert spec1.auth_kind == "bearer"
    unchanged = (await client.get(ROOT + f"/apis/{tid}/preview")).json()
    assert unchanged["changes"] == []
    assert (await registry.credential_headers(settings.tenant_id, spec1))[
        "Authorization"
    ] == "Bearer fake-collection-key"
    agent, _ = await create_agent(client, [row["name"]])
    refs = (await client.get(ROOT + f"/collections/{gid}/references")).json()
    assert refs[0]["is_current"] and refs[0]["agent_id"] == agent["id"]
    rotated = await client.put(
        ROOT + f"/collections/{gid}",
        json=collection(revision=1, header_secrets={"Authorization": "Bearer fake-rotated-key"}),
    )
    assert rotated.status_code == 200, rotated.text
    listing = (await client.get(ROOT + f"/collections/{gid}")).json()
    assert listing["items"][0]["collection_changed"] and listing["items"][0]["changed"]
    # A stale confirmation cannot publish new collection settings silently.
    assert (
        await client.post(
            ROOT + f"/apis/{tid}/publish", json={"revision": 1, "collection_revision": 1}
        )
    ).status_code == 409
    assert (
        await client.post(
            ROOT + f"/apis/{tid}/publish", json={"revision": 1, "collection_revision": 2}
        )
    ).status_code == 200
    assert (await registry.credential_headers(settings.tenant_id, spec1))[
        "Authorization"
    ] == "Bearer fake-collection-key"
    async with registry.engine.connect() as c:
        snapshot = await services.platform.agents.snapshot(settings.tenant_id, UUID(agent["id"]), c)
    assert snapshot["tools"][0]["version"] == 1
    seen = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: (
                seen.append(req.headers.get("Authorization"))
                or httpx.Response(200, json={"ok": True})
            )
        )
    ) as http:
        spec2 = HttpTool.model_validate(
            (await registry.resolve(settings.tenant_id, [row["name"]]))[0]["spec"]
        )
        gateway = ToolGateway(
            http,
            [spec2],
            credential_resolver=lambda s: registry.credential_headers(settings.tenant_id, s),
        )
        assert (await gateway.execute(row["name"], {"order_id": "A"}))["ok"]
    assert seen == ["Bearer fake-rotated-key"]
    assert (
        await client.patch(ROOT + f"/collections/{gid}", json={"revision": 2, "enabled": False})
    ).status_code == 200
    assert not await registry.enabled(settings.tenant_id, row["name"])
    assert (await client.delete(ROOT + f"/collections/{gid}?revision=3")).status_code == 409


async def test_override_copy_clear_and_atomic_import(platform, tmp_path):
    client, services, settings = platform
    group, _row = await setup(platform, tmp_path)
    gid = group["id"]
    custom = api_definition("custom_lookup", auth_mode="custom")
    custom["definition"].update(
        header_secrets={"X-API-Key": "fake-private-key"}, headers={"X-Locale": "zh-CN"}
    )
    response = await client.post(ROOT + f"/collections/{gid}/apis", json=custom)
    assert response.status_code == 201, response.text
    tid = response.json()["id"]
    assert (
        await client.post(
            ROOT + f"/apis/{tid}/publish", json={"revision": 1, "collection_revision": 1}
        )
    ).status_code == 200
    spec = HttpTool.model_validate(
        (await services.platform.tools.resolve(settings.tenant_id, ["custom_lookup"]))[0]["spec"]
    )
    assert await services.platform.tools.credential_headers(settings.tenant_id, spec) == {
        "X-API-Key": "fake-private-key"
    }
    response = await client.put(
        ROOT + f"/collections/{gid}",
        json=collection(revision=1, header_secrets={"Authorization": "new-key"}),
    )
    assert response.status_code == 200
    rows = (await client.get(ROOT + f"/collections/{gid}")).json()["items"]
    assert not next(r for r in rows if r["id"] == tid)["collection_changed"]
    copied = await client.post(ROOT + f"/collections/{gid}/copy", json={"name": "订单副本"})
    assert copied.status_code == 201, copied.text
    cg = copied.json()
    assert cg["spec"]["credential_id"] is None
    clone = (await client.get(ROOT + f"/collections/{cg['id']}")).json()
    assert len(clone["items"]) == 2
    assert all(
        r["published_version"] is None
        and not r["spec"]["credential_id"]
        and not r["spec"]["headers_from_env"]
        for r in clone["items"]
    )
    exported = await client.get(ROOT + f"/collections/{cg['id']}/export")
    # Identical HTTP paths cannot be represented twice by OpenAPI; report a conflict.
    assert exported.status_code == 409
    before = (await client.get(ROOT + "/collections")).json()["total"]
    bad = await client.post(
        ROOT + "/import",
        json={
            "collection": collection(name="回滚导入", header_secrets={}),
            "apis": [api_definition("new_unique"), api_definition("order_lookup")],
        },
    )
    assert bad.status_code == 409
    assert (await client.get(ROOT + "/collections")).json()["total"] == before
    assert (await client.delete(ROOT + f"/collections/{cg['id']}?revision=1")).status_code == 200


async def test_permissions_validation_simulation_and_live_test(platform, tmp_path):
    client, services, settings = platform
    group, row = await setup(platform, tmp_path)
    gid, tid = group["id"], row["id"]
    viewer = {"Authorization": "Bearer platform-viewer"}
    assert (await client.get(ROOT + "/collections", headers=viewer)).status_code == 200
    assert (
        await client.post(ROOT + "/collections", json=collection(), headers=viewer)
    ).status_code == 403
    assert (
        await client.get(ROOT + f"/collections/{gid}/export", headers=viewer)
    ).status_code == 403
    for body in [
        collection(base_url="http://other.test"),
        collection(headers={"Authorization": "fake-secret"}),
        collection(headers={"Host": "other.test"}),
    ]:
        r = await client.post(ROOT + "/collections", json=body)
        assert r.status_code in {400, 422} and "fake-secret" not in r.text
    for path in ["//evil.test/orders", "/../orders", "/%2e%2e/orders", "/orders?key=secret"]:
        r = await client.post(
            ROOT + f"/collections/{gid}/apis", json=api_definition("bad_path", relative_path=path)
        )
        assert r.status_code == 422, r.text
    seen = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: (
                seen.append(req.headers.get("Authorization"))
                or httpx.Response(200, json={"status": "ok"})
            )
        )
    ) as http:
        services.platform.tool_workspace.client = http
        body = {
            "revision": 1,
            "collection_revision": 1,
            "arguments": {"order_id": "A"},
            "response": {"status": "mock"},
            "mode": "simulation",
        }
        response = await client.post(ROOT + f"/apis/{tid}/test", json=body)
        assert response.status_code == 200 and not response.json()["upstream_called"] and not seen
        response = await client.post(ROOT + f"/apis/{tid}/test", json={**body, "mode": "live"})
        assert response.status_code == 200 and response.json()["ok"]
        assert seen == ["Bearer fake-collection-key"]
    async with services.platform.engine.connect() as c:
        assert await one(
            c,
            "SELECT id FROM audit_records WHERE tenant_id=:t AND action='tool.live_test'",
            t=settings.tenant_id,
        )
    assert (
        await client.patch(ROOT + f"/collections/{gid}", json={"revision": 1, "archived": True})
    ).status_code == 200
    assert not await services.platform.tools.enabled(settings.tenant_id, row["name"])


async def test_openapi_preview_export_pagination_and_isolation(platform, tmp_path):
    client, services, _settings = platform
    group, _row = await setup(platform, tmp_path)
    content = """openapi: 3.0.3
info: {title: Import}
servers: [{url: 'http://localhost'}]
paths:
  /orders:
    get:
      operationId: imported_orders
      parameters: [{name: order_id, in: query, required: true, schema: {type: string}}]
    put: {summary: Unsupported}
  /orders/{id}:
    get: {summary: Dynamic path}
"""
    r = await client.post(ROOT + "/import/preview", json={"content": content})
    assert r.status_code == 200, r.text
    p = r.json()
    assert len(p["items"]) == 1 and len(p["skipped"]) == 2
    exported = await client.get(ROOT + f"/collections/{group['id']}/export")
    assert exported.status_code == 200, exported.text
    assert "credential" not in exported.text and "fake-collection-key" not in exported.text
    again = await client.post(ROOT + "/import/preview", json={"content": exported.text})
    assert again.status_code == 200 and len(again.json()["items"]) == 1
    for i in range(13):
        assert (
            await client.post(
                ROOT + "/collections", json=collection(name=f"组-{i}", header_secrets={})
            )
        ).status_code == 201
    page1 = (await client.get(ROOT + "/collections?page_size=12")).json()
    page2 = (await client.get(ROOT + "/collections?page_size=12&page=2")).json()
    assert page1["total"] == 14 and len(page1["items"]) == 12 and len(page2["items"]) == 2
    assert not {r["id"] for r in page1["items"]} & {r["id"] for r in page2["items"]}
    assert not (await services.platform.tool_workspace.catalog("other-tenant"))["items"]
    with pytest.raises(DomainError):
        await services.platform.tool_workspace.detail("other-tenant", UUID(group["id"]))


async def test_post_release_inherited_secret_and_confirmed_live_test(platform, tmp_path):
    client, services, settings = platform
    group, row = await setup(platform, tmp_path)
    gid, tid = group["id"], row["id"]
    assert (
        await client.post(
            ROOT + f"/apis/{tid}/publish", json={"revision": 1, "collection_revision": 1}
        )
    ).status_code == 200
    body = api_definition(revision=1)
    body["definition"].update(
        method="POST",
        parameters={
            "type": "object",
            "properties": {"items": {"type": "array", "items": {"type": "integer"}}},
            "required": ["items"],
            "additionalProperties": False,
        },
    )
    updated = await client.put(ROOT + f"/collections/{gid}/apis/{tid}", json=body)
    assert updated.status_code == 200, updated.text
    assert updated.json()["spec"]["method"] == "POST"
    registry = services.platform.tools
    assert (await registry.resolve(settings.tenant_id, [row["name"]]))[0]["spec"]["method"] == "GET"
    assert (
        await client.post(
            ROOT + f"/apis/{tid}/publish", json={"revision": 2, "collection_revision": 1}
        )
    ).status_code == 200
    assert (await registry.resolve(settings.tenant_id, [row["name"]]))[0]["spec"][
        "method"
    ] == "POST"
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(201, json={"created": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        services.platform.tool_workspace.client = http
        test = {
            "revision": 2,
            "collection_revision": 1,
            "arguments": {"items": [1, 2]},
            "mode": "live",
        }
        assert (await client.post(ROOT + f"/apis/{tid}/test", json=test)).status_code == 422
        assert not seen
        response = await client.post(ROOT + f"/apis/{tid}/test", json={**test, "confirmed": True})
        assert response.status_code == 200 and response.json()["ok"], response.text
    assert len(seen) == 1 and seen[0].method == "POST"
    assert json.loads(seen[0].content) == {"items": [1, 2]}
    assert seen[0].headers["Authorization"] == "Bearer fake-collection-key"
    versions = (await client.get(ROOT + f"/collections/{gid}/versions")).json()["items"]
    assert [v["spec"]["method"] for v in versions] == ["POST", "GET"]
