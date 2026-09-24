from fastapi.testclient import TestClient

from agent_platform.apps.api.main import create_app

client = TestClient(create_app())


def test_health_contract():
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0"}


def test_module_catalog_contract():
    response = client.get("/api/v1/modules")
    assert response.status_code == 200
    modules = response.json()
    assert len(modules) == 12
    assert len({module["id"] for module in modules}) == len(modules)
    assert all(module["status"] == "mvp" for module in modules)
