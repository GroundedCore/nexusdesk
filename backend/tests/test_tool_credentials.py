import json
from uuid import UUID

import httpx
import pytest
from test_platform_postgres import platform as _platform_fixture

from agent_platform.modules.tool_gateway.service import HttpTool, ToolGateway
from agent_platform.platform.persistence.store import DomainError, many
from agent_platform.platform.secrets.vault import CredentialVault

platform = _platform_fixture
pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


def definition(**updates):
    return {
        "name": "private_orders",
        "description": "Private orders",
        "url": "http://localhost/orders",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        **updates,
    }


async def test_tool_secret_lifecycle_and_pinned_execution(platform, tmp_path):
    client, services, settings = platform
    registry = services.platform.tools
    registry.vault = CredentialVault(tmp_path / "master.key")
    secret = "Bearer fake-tool-secret-one"
    body = definition(header_secrets={"Authorization": secret, "X-API-Key": "fake-second-key"})
    created = await client.post("/api/v1/tools", json=body)
    assert created.status_code == 201, created.text
    row = created.json()
    tid = row["id"]
    spec1 = HttpTool.model_validate(row["spec"])
    assert row["spec"]["headers_from_env"] == {}
    assert "header_secrets" not in row["spec"]
    assert set(spec1.stored_headers) == {"Authorization", "X-API-Key"}
    async with registry.engine.connect() as c:
        encrypted = await many(
            c, "SELECT encrypted_secret FROM tool_credentials WHERE tool_id=:id", id=UUID(tid)
        )
    assert secret not in json.dumps(encrypted)
    for path in ["/tools", f"/tools/{tid}/versions", "/audit"]:
        response = await client.get("/api/v1" + path)
        assert response.status_code == 200
        assert secret not in response.text and encrypted[0]["encrypted_secret"] not in response.text
    assert secret not in created.text
    with pytest.raises(DomainError, match="tool_credential_unavailable"):
        await registry.credential_headers("other-tenant", spec1)
    # A client cannot borrow another tool's credential reference.
    forged = await client.post(
        "/api/v1/tools",
        json=definition(
            name="another", credential_id=str(spec1.credential_id), stored_headers=["Authorization"]
        ),
    )
    assert forged.status_code == 201 and forged.json()["spec"]["credential_id"] is None
    preserved = await client.put(
        f"/api/v1/tools/{tid}",
        json={
            **row["spec"],
            "revision": 1,
            "header_secrets": {"Authorization": None, "X-API-Key": None},
        },
    )
    assert preserved.status_code == 200, preserved.text
    assert (
        await registry.credential_headers(
            settings.tenant_id, HttpTool.model_validate(preserved.json()["spec"])
        )
    )["Authorization"] == secret
    changed = await client.put(
        f"/api/v1/tools/{tid}",
        json=definition(
            revision=2, header_secrets={"Authorization": "Bearer fake-tool-secret-two"}
        ),
    )
    assert changed.status_code == 200, changed.text
    published = await client.post(f"/api/v1/tools/{tid}/publish", json={"revision": 3})
    assert published.status_code == 200
    versions = (await client.get(f"/api/v1/tools/{tid}/versions")).json()
    captured = []

    def upstream(request):
        captured.append(dict(request.headers))
        return httpx.Response(200, json={"status": "ok"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        for spec in [spec1, HttpTool.model_validate(versions[0]["spec"])]:
            gateway = ToolGateway(
                http,
                [spec],
                credential_resolver=lambda s: registry.credential_headers(settings.tenant_id, s),
            )
            assert (await gateway.execute(spec.name, {}))["ok"]
            assert secret not in json.dumps(gateway.schemas)
    assert captured[0]["authorization"] == secret
    assert captured[0]["x-api-key"] == "fake-second-key"
    assert captured[1]["authorization"] == "Bearer fake-tool-secret-two"
    assert "x-api-key" not in captured[1]
    cleared = await client.put(
        f"/api/v1/tools/{tid}", json=definition(revision=3, header_secrets={})
    )
    assert cleared.status_code == 200
    assert cleared.json()["spec"]["credential_id"] is None
    assert cleared.json()["spec"]["stored_headers"] == []
    # Clearing a draft does not rewrite an immutable published version.
    assert (await registry.credential_headers(settings.tenant_id, spec1))["Authorization"] == secret


async def test_tool_secret_validation_binding_and_missing_key(platform, tmp_path):
    client, services, settings = platform
    registry = services.platform.tools
    key = tmp_path / "master.key"
    registry.vault = CredentialVault(key)
    secret = "fake-tool-secret-do-not-echo"
    body = definition(header_secrets={"X-API-Key": secret})
    denied = await client.post(
        "/api/v1/tools", json=body, headers={"Authorization": "Bearer platform-viewer"}
    )
    assert denied.status_code == 403 and secret not in denied.text
    for invalid in [
        {**body, "timeout_seconds": secret},
        definition(header_secrets={"Authorization": secret, "authorization": secret}),
        definition(header_secrets={"Bad\r\nHeader": secret}),
        definition(header_secrets={"Authorization": "bad\r\n" + secret}),
        definition(header_secrets={"Authorization": ""}),
        definition(
            header_secrets={"Authorization": secret},
            headers_from_env={"authorization": "AGENT_TOOL_SECRET_OLD"},
        ),
    ]:
        response = await client.post("/api/v1/tools", json=invalid)
        assert response.status_code == 422 and secret not in response.text
    row = (await client.post("/api/v1/tools", json=body)).json()
    tid = row["id"]
    for payload in [
        {"url": "http://localhost/other"},
        {"url": "http://localhost/other", "header_secrets": {"X-API-Key": None}},
        {"header_secrets": {"X-New-Key": None}},
    ]:
        response = await client.put(
            f"/api/v1/tools/{tid}", json={**row["spec"], "revision": 1, **payload}
        )
        assert response.status_code == 409 and secret not in response.text
    conflict = await client.put(
        f"/api/v1/tools/{tid}",
        json={**row["spec"], "revision": 999, "header_secrets": {"X-API-Key": "replacement"}},
    )
    assert conflict.status_code == 409
    spec = HttpTool.model_validate(row["spec"])
    with pytest.raises(DomainError, match="tool_credential_binding_mismatch"):
        await registry.credential_headers(
            settings.tenant_id, spec.model_copy(update={"url": "http://localhost/other"})
        )
    key.unlink()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("must not contact upstream"))
    ) as http:
        gateway = ToolGateway(
            http,
            [spec],
            credential_resolver=lambda s: registry.credential_headers(settings.tenant_id, s),
        )
        assert (await gateway.execute(spec.name, {}))["error"] == "tool_credential_key_unavailable"
    replaced = await client.put(f"/api/v1/tools/{tid}", json={**body, "revision": 1})
    assert replaced.status_code == 503 and not key.exists()
    assert secret not in replaced.text
