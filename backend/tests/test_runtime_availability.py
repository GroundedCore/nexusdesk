import httpx
import pytest

from agent_platform.apps.api.main import create_app
from agent_platform.settings import Settings


@pytest.mark.asyncio
async def test_database_outage_returns_503_without_exposing_connection_details():
    app = create_app(
        Settings(
            database_url="postgresql+asyncpg://agent:private-test-value@127.0.0.1:1/missing",
            embedded_worker=False,
        )
    )
    async with app.router.lifespan_context(app):  # noqa: SIM117
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            ready = await client.get("/api/v1/ready")
            assert ready.status_code == 503
            response = await client.post(
                "/api/v1/runs", json={"conversation_id": "outage", "message": "hello"}
            )
            assert response.status_code == 503
            assert "private-test-value" not in response.text
