import json

import httpx
import pytest
from test_model_gateway import ROOT, setup
from test_platform_postgres import platform as _platform_fixture

from agent_platform.modules.model_gateway.credentials import CredentialVault
from agent_platform.platform.persistence.store import DomainError, one

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]
gateway_platform = _platform_fixture


async def test_stored_credentials_rotation_clear_and_isolation(gateway_platform, tmp_path):
    client, services, settings = gateway_platform
    gateway = services.platform.gateway
    gateway.catalog.vault = CredentialVault(tmp_path / "master.key")
    connection, _, profile = await setup(client, protocol="openai_compatible")
    url = ROOT + "/connections/" + connection["id"]
    secret = "fake-provider-key-one"
    response = await client.put(url + "?revision=1", json={**connection["spec"], "api_key": secret})
    assert response.status_code == 200, response.text
    assert response.json()["credential_source"] == "stored"
    assert "api_key" not in response.json()["spec"]
    async with gateway.engine.connect() as c:
        encrypted = await one(
            c,
            "SELECT encrypted_secret FROM gateway_credentials WHERE connection_id=:id",
            id=connection["id"],
        )
    assert secret not in encrypted["encrypted_secret"]
    for path in ["/connections", "/catalog/connections", f"/profiles/{profile['id']}/versions"]:
        public = await client.get(ROOT + path)
        assert public.status_code == 200
        assert secret not in public.text and encrypted["encrypted_secret"] not in public.text
    with pytest.raises(DomainError):
        await gateway.catalog.credential_for("other-tenant", connection["id"], connection["spec"])
    headers = []

    def handler(request):
        headers.append(request.headers.get("Authorization"))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    body = {
        "profile_id": profile["id"],
        "version": 1,
        "payload": {"operation": "chat", "messages": [{"role": "user", "content": "hi"}]},
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mock:
        gateway.client = mock
        assert (await client.post(ROOT + "/invoke", json=body)).status_code == 200
        assert (await client.post(url + "/probe")).json()["status"] == "available"
        # Blank/omitted input preserves the saved key; rotation works on the same published version.
        assert (await client.put(url + "?revision=2", json=connection["spec"])).status_code == 200
        assert (
            await gateway.catalog.credential_for(
                settings.tenant_id, connection["id"], connection["spec"]
            )
            == secret
        )
        response = await client.put(
            url + "?revision=3", json={**connection["spec"], "api_key": "fake-provider-key-two"}
        )
        assert response.status_code == 200
        assert (await client.post(ROOT + "/invoke", json=body)).status_code == 200
        assert headers == ["Bearer " + secret, "Bearer " + secret, "Bearer fake-provider-key-two"]
    # Changing endpoints cannot silently carry credentials to a different service.
    response = await client.put(
        url + "?revision=4", json={**connection["spec"], "base_url": "http://localhost/other"}
    )
    assert response.status_code == 409
    response = await client.put(
        url + "?revision=4", json={**connection["spec"], "clear_api_key": True}
    )
    assert response.status_code == 200 and not response.json()["credential_configured"]
    assert (
        await gateway.catalog.credential_for(
            settings.tenant_id, connection["id"], connection["spec"]
        )
        is None
    )


async def test_credentials_validation_permissions_and_missing_master(gateway_platform, tmp_path):
    client, services, settings = gateway_platform
    catalog = services.platform.gateway.catalog
    path = tmp_path / "master.key"
    catalog.vault = CredentialVault(path)
    secret = "fake-secret-must-never-echo"
    body = {"name": "direct", "api_key": secret}
    denied = await client.post(
        ROOT + "/connections", json=body, headers={"Authorization": "Bearer platform-viewer"}
    )
    assert denied.status_code == 403
    bad = await client.post(ROOT + "/connections", json={**body, "concurrency": secret})
    assert bad.status_code == 422 and secret not in bad.text
    conflict = await client.post(
        ROOT + "/connections", json={**body, "credential_ref": "AGENT_MODEL_SECRET_TEST"}
    )
    assert conflict.status_code == 422 and secret not in conflict.text
    response = await client.post(ROOT + "/connections", json=body)
    assert response.status_code == 201, response.text
    row = response.json()
    # A fresh vault can read the persisted key (restart), but missing keys fail closed.
    catalog.vault = CredentialVault(path)
    assert await catalog.credential_for(settings.tenant_id, row["id"], row["spec"]) == secret
    path.unlink()
    with pytest.raises(DomainError, match="model_credential_key_unavailable"):
        await catalog.credential_for(settings.tenant_id, row["id"], row["spec"])
    replaced = await client.put(ROOT + f"/connections/{row['id']}?revision=1", json=body)
    assert replaced.status_code == 503 and not path.exists()
    assert secret not in json.dumps(replaced.json())
