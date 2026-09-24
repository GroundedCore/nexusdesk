import httpx
import pytest
from test_model_gateway import ROOT, setup
from test_platform_postgres import platform as _platform_fixture

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]
gateway_platform = _platform_fixture


async def test_discovery_auth_normalization_and_access(gateway_platform, monkeypatch):
    client, services, _ = gateway_platform
    connection, _, _ = await setup(client, protocol="openai_compatible")
    ref = "AGENT_MODEL_SECRET_DISCOVERY"
    monkeypatch.setenv(ref, "fake-discovery-secret")
    await client.put(
        ROOT + f"/connections/{connection['id']}?revision=1",
        json={**connection["spec"], "credential_ref": ref},
    )
    path = ROOT + f"/connections/{connection['id']}/available-models"

    def handler(request):
        assert request.url.path == "/v1/models"
        assert request.headers["Authorization"] == "Bearer fake-discovery-secret"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "model-b"},
                    {"id": "model-a", "internal": "private"},
                    {"id": "model-a"},
                    {"id": 1},
                    {"id": ""},
                ],
                "has_more": True,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mock:
        services.platform.gateway.client = mock
        response = await client.get(path)
        assert response.status_code == 200
        assert response.json() == {
            "items": [{"id": "model-a"}, {"id": "model-b"}],
            "truncated": True,
        }
        assert response.headers["cache-control"] == "no-store"
        for role in ["platform-viewer", "platform-operator"]:
            assert (
                await client.get(path, headers={"Authorization": "Bearer " + role})
            ).status_code == 403
        await client.patch(
            ROOT + f"/connections/{connection['id']}", json={"revision": 2, "enabled": False}
        )
        assert (await client.get(path)).status_code == 409


@pytest.mark.parametrize(
    "status,data,code",
    [
        (401, {"error": "private-provider-error"}, "model_discovery_unauthorized"),
        (404, {}, "model_discovery_not_supported"),
        (429, {"error": "private-provider-error"}, "model_discovery_unavailable"),
        (200, [], "model_discovery_invalid_response"),
        (200, {"data": []}, None),
    ],
)
async def test_discovery_errors_and_empty(gateway_platform, status, data, code):
    client, services, _ = gateway_platform
    connection, _, _ = await setup(client, protocol="openai_compatible")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(status, json=data))
    ) as mock:
        services.platform.gateway.client = mock
        response = await client.get(ROOT + f"/connections/{connection['id']}/available-models")
    if code:
        assert response.status_code >= 400 and response.json()["detail"] == code
        assert "private-provider-error" not in response.text
    else:
        assert response.json() == {"items": [], "truncated": False}
