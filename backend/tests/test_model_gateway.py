import asyncio
import io
import json
import wave
from uuid import uuid4

import httpx
import pytest
from langchain_core.messages import HumanMessage
from test_platform_postgres import conversation, execute_next
from test_platform_postgres import platform as _platform_fixture

from agent_platform.modules.model_gateway.contracts import ChatRequest
from agent_platform.modules.model_gateway.gateway import GatewayChatModel
from agent_platform.platform.persistence.store import DomainError

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]
gateway_platform = _platform_fixture
ROOT = "/api/v1/model-gateway"


async def setup(client, op="chat", protocol="demo", model_extra=None, profile_extra=None):
    response = await client.post(
        ROOT + "/connections",
        json={
            "name": "test",
            "protocol": protocol,
            "base_url": "http://localhost/v1" if protocol != "demo" else "",
        },
    )
    assert response.status_code == 201, response.text
    connection = response.json()
    body = {
        "name": "model",
        "connection_id": connection["id"],
        "model_name": "demo",
        "operations": [op],
        "tool_calling": True,
        "embedding_dimension": 8,
        "vector_space": "demo-v1",
        "voices": ["demo"],
        **(model_extra or {}),
    }
    response = await client.post(ROOT + "/models", json=body)
    assert response.status_code == 201, response.text
    model = response.json()
    response = await client.post(
        ROOT + "/profiles",
        json={"name": "test", "operation": op, "model_id": model["id"], **(profile_extra or {})},
    )
    assert response.status_code == 201, response.text
    profile = response.json()
    response = await client.post(ROOT + f"/profiles/{profile['id']}/publish", json={"revision": 1})
    assert response.status_code == 200, response.text
    return connection, model, profile


def wav():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\0\0" * 100)
    return buffer.getvalue()


@pytest.mark.parametrize("op", ["chat", "embed", "rerank", "transcribe", "synthesize", "recognize"])
async def test_six_operations_and_metadata_only_logs(gateway_platform, op):
    client, services, _settings = gateway_platform
    _, _, profile = await setup(client, op)
    audio = (
        await client.post(ROOT + "/media", content=wav(), headers={"Content-Type": "audio/wav"})
    ).json()
    image = (
        await client.post(
            ROOT + "/media",
            content=b"\x89PNG\r\n\x1a\nimage",
            headers={"Content-Type": "image/png"},
        )
    ).json()
    payloads = {
        "chat": {"messages": [{"role": "user", "content": "private-content"}]},
        "embed": {
            "inputs": [{"id": "a", "text": "private-content"}, {"id": "b", "text": "second"}]
        },
        "rerank": {
            "query": "退货",
            "candidates": [{"id": "a", "text": "退货流程"}, {"id": "b", "text": "物流"}],
            "top_n": 1,
        },
        "transcribe": {"media_id": audio["id"]},
        "synthesize": {"text": "private-content", "voice_id": "demo", "format": "wav"},
        "recognize": {"pages": [{"source_id": "p1", "media_id": image["id"]}]},
    }
    response = await client.post(
        ROOT + "/invoke",
        json={
            "profile_id": profile["id"],
            "version": 1,
            "payload": {"operation": op, **payloads[op]},
        },
    )
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["demo"] and value["usage"] is None
    detail = (await client.get(ROOT + "/calls/" + value["call_id"])).json()
    assert detail["status"] == "completed" and len(detail["attempts"]) == 1
    assert "private-content" not in json.dumps(detail)
    if op == "embed":
        assert [v["id"] for v in value["payload"]["vectors"]] == ["a", "b"]
        assert value["payload"]["dimension"] == 8
    if op == "synthesize":
        output = await client.get(ROOT + "/media/" + value["payload"]["id"])
        with wave.open(io.BytesIO(output.content)) as decoded:
            assert decoded.getnframes() > 0
    with pytest.raises(DomainError):
        await services.platform.gateway.catalog.resolve("another-tenant", profile["id"], 1)
    with pytest.raises(DomainError):
        await services.platform.gateway.media("another-tenant", audio["id"])


async def test_version_freeze_conflicts_role_and_disable(gateway_platform):
    client, services, settings = gateway_platform
    connection, model, profile = await setup(client)
    changed_operation = {**profile["spec"], "operation": "embed"}
    assert (
        await client.put(ROOT + f"/profiles/{profile['id']}?revision=1", json=changed_operation)
    ).status_code == 400
    edited = {**model["spec"], "model_name": "changed"}
    assert (
        await client.put(ROOT + f"/models/{model['id']}?revision=1", json=edited)
    ).status_code == 200
    assert (
        await client.put(ROOT + f"/models/{model['id']}?revision=1", json=edited)
    ).status_code == 409
    assert (
        await client.post(ROOT + f"/profiles/{profile['id']}/publish", json={"revision": 1})
    ).json()["version"] == 2
    original = await services.platform.gateway.catalog.resolve(settings.tenant_id, profile["id"], 1)
    latest = await services.platform.gateway.catalog.resolve(settings.tenant_id, profile["id"], 2)
    assert original["routes"][0]["model"]["model_name"] == "demo"
    assert latest["routes"][0]["model"]["model_name"] == "changed"
    assert (
        await client.post(ROOT + f"/profiles/{profile['id']}/rollback", json={"version": 1})
    ).status_code == 200
    body = {
        "profile_id": profile["id"],
        "version": 1,
        "payload": {"operation": "chat", "messages": [{"role": "user", "content": "你好"}]},
    }
    assert (
        await client.post(
            ROOT + "/invoke", json=body, headers={"Authorization": "Bearer platform-viewer"}
        )
    ).status_code == 403
    assert (
        await client.post(
            ROOT + "/connections",
            json={"name": "denied"},
            headers={"Authorization": "Bearer platform-operator"},
        )
    ).status_code == 403
    await client.patch(
        ROOT + f"/connections/{connection['id']}", json={"revision": 1, "enabled": False}
    )
    assert (await client.post(ROOT + "/invoke", json=body)).status_code == 409


async def test_runtime_binding_and_capability_validation(gateway_platform):
    client, services, settings = gateway_platform
    _, _, profile = await setup(client)
    body = {
        "name": "bound",
        "config": {
            "system_prompt": "test",
            "model_profile_id": profile["id"],
            "model_profile_version": 1,
        },
    }
    agent = (await client.post("/api/v1/agents", json=body)).json()
    assert (
        await client.post(f"/api/v1/agents/{agent['id']}/publish", json={"revision": 1})
    ).status_code == 200
    model = GatewayChatModel(services.platform.gateway, settings.tenant_id, profile["id"], 1, [])
    assert "演示" in (await model.ainvoke([HumanMessage(content="hello")])).content
    conv = await conversation(client, agent["id"])
    submitted = await client.post(
        f"/api/v1/conversations/{conv['id']}/messages",
        json={"content": "你好"},
    )
    assert submitted.status_code in (200, 202), submitted.text
    run = await execute_next(services, settings)
    assert run["status"] == "completed"
    records = await services.platform.gateway.records(settings.tenant_id)
    assert any(str(r["run_id"]) == str(run["id"]) for r in records)
    _, _, embed = await setup(client, "embed")
    body["config"]["model_profile_id"] = embed["id"]
    agent = (await client.post("/api/v1/agents", json=body)).json()
    assert (
        await client.post(f"/api/v1/agents/{agent['id']}/publish", json={"revision": 1})
    ).status_code == 400


async def test_http_retry_redaction_and_usage(gateway_platform):
    client, services, _settings = gateway_platform
    _, _, profile = await setup(client, protocol="openai_compatible", profile_extra={"retries": 1})
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        if len(seen) == 1:
            return httpx.Response(429, json={"error": "secret-provider-response"})
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 3,
                    "total_tokens": 5,
                    "secret": "must-not-log",
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mock:
        services.platform.gateway.client = mock
        response = await client.post(
            ROOT + "/invoke",
            json={
                "profile_id": profile["id"],
                "version": 1,
                "payload": {
                    "operation": "chat",
                    "messages": [{"role": "user", "content": "private"}],
                },
            },
        )
    assert response.status_code == 200, response.text
    detail = (await client.get(ROOT + "/calls/" + response.json()["call_id"])).json()
    assert [a["status"] for a in detail["attempts"]] == ["failed", "completed"]
    assert "secret" not in json.dumps(detail) and "private" not in json.dumps(detail)
    assert response.json()["usage"]["total_tokens"] == 5


@pytest.mark.parametrize(
    "op,raw,payload",
    [
        (
            "embed",
            {"data": [{"index": 0, "embedding": [1.0]}]},
            {"inputs": [{"id": "a", "text": "test"}]},
        ),
        (
            "rerank",
            {"payload": {"results": [{"id": "unknown", "score": 1}]}},
            {"query": "test", "candidates": [{"id": "a", "text": "test"}], "top_n": 1},
        ),
        ("recognize", {"payload": {"pages": [{"source_id": "unknown", "text": "test"}]}}, {}),
    ],
)
async def test_malformed_provider_results_rejected(gateway_platform, op, raw, payload):
    client, services, _settings = gateway_platform
    _, _, profile = await setup(
        client, op, protocol="openai_compatible" if op == "embed" else "gateway_http"
    )
    if op == "recognize":
        media = (
            await client.post(
                ROOT + "/media", content=b"\x89PNG\r\n\x1a\n", headers={"Content-Type": "image/png"}
            )
        ).json()
        payload = {"pages": [{"source_id": "p1", "media_id": media["id"]}]}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=raw))
    ) as mock:
        services.platform.gateway.client = mock
        response = await client.post(
            ROOT + "/invoke",
            json={
                "profile_id": profile["id"],
                "version": 1,
                "payload": {"operation": op, **payload},
            },
        )
    assert response.status_code == 502, response.text
    assert response.json()["detail"] == "invalid_model_response"


async def test_timeout_and_cancellation_release_slots(gateway_platform):
    client, services, settings = gateway_platform
    _, _, profile = await setup(
        client, protocol="openai_compatible", profile_extra={"timeout_seconds": 0.08}
    )
    entered = asyncio.Event()

    async def handler(request):
        entered.set()
        await asyncio.sleep(10)
        return httpx.Response(200)

    gateway = services.platform.gateway
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mock:
        gateway.client = mock
        request = ChatRequest(messages=[{"role": "user", "content": "hello"}])
        with pytest.raises(DomainError, match="model_timeout"):
            await gateway.invoke(settings.tenant_id, profile["id"], 1, request)
        entered.clear()
        task = asyncio.create_task(gateway.invoke(settings.tenant_id, profile["id"], 1, request))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert gateway.limits["chat"]._value == settings.model_gateway_concurrency
    records = await gateway.records(settings.tenant_id)
    assert {r["status"] for r in records} == {"failed", "cancelled"}


async def test_invalid_address_reference_and_embedding_fallback(gateway_platform):
    client, _services, _settings = gateway_platform
    denied = await client.post(
        ROOT + "/connections",
        json={
            "name": "bad",
            "protocol": "openai_compatible",
            "base_url": "https://untrusted.example/v1",
        },
    )
    assert denied.status_code == 400
    denied = await client.post(
        ROOT + "/connections", json={"name": "bad", "credential_ref": "PLAINTEXT_SECRET"}
    )
    assert denied.status_code == 422 and "PLAINTEXT_SECRET" not in denied.text
    _, first, _ = await setup(client, "embed")
    _, second, _ = await setup(client, "embed", model_extra={"vector_space": "other-space"})
    denied = await client.post(
        ROOT + "/profiles",
        json={
            "name": "bad",
            "operation": "embed",
            "model_id": first["id"],
            "fallback_model_ids": [second["id"]],
        },
    )
    assert denied.status_code == 400 and denied.json()["detail"] == "embedding_space_mismatch"
    missing = await client.post(
        ROOT + "/models",
        json={
            "name": "bad",
            "connection_id": str(uuid4()),
            "model_name": "x",
            "operations": ["chat"],
        },
    )
    assert missing.status_code == 404


@pytest.mark.parametrize("complete", [True, False])
async def test_streaming_output_and_no_retry_after_delta(gateway_platform, complete):
    client, services, _ = gateway_platform
    _, _, profile = await setup(client, protocol="openai_compatible", profile_extra={"retries": 2})
    requests = []

    def handler(request):
        requests.append(request)
        event = {"choices": [{"index": 0, "delta": {"content": "hello"}}]}
        body = "data: " + json.dumps(event) + "\r\n\r\n"
        if complete:
            body += 'data: {"choices":[],"usage":{"prompt_tokens":2,"completion_tokens":1}}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, content=body, headers={"Content-Type": "text/event-stream"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mock:
        services.platform.gateway.client = mock
        response = await client.post(
            ROOT + "/stream",
            json={
                "profile_id": profile["id"],
                "version": 1,
                "payload": {
                    "operation": "chat",
                    "messages": [{"role": "user", "content": "private"}],
                },
            },
        )
    assert response.status_code == 200
    assert "event: delta" in response.text
    assert ("event: completed" if complete else "model_stream_interrupted") in response.text
    assert len(requests) == 1


@pytest.mark.parametrize("op", ["embed", "transcribe", "synthesize", "rerank", "recognize"])
async def test_provider_wire_contracts(gateway_platform, op):
    client, services, _ = gateway_platform
    protocol = "gateway_http" if op in ("rerank", "recognize") else "openai_compatible"
    _, _, profile = await setup(client, op, protocol=protocol)
    audio = (
        await client.post(ROOT + "/media", content=wav(), headers={"Content-Type": "audio/wav"})
    ).json()
    image = (
        await client.post(
            ROOT + "/media", content=b"\x89PNG\r\n\x1a\n", headers={"Content-Type": "image/png"}
        )
    ).json()
    bodies = {
        "embed": {"inputs": [{"id": "a", "text": "first"}, {"id": "b", "text": "second"}]},
        "transcribe": {"media_id": audio["id"], "language": "zh"},
        "synthesize": {"text": "hello", "voice_id": "demo", "format": "wav"},
        "rerank": {"query": "query", "candidates": [{"id": "a", "text": "answer"}], "top_n": 1},
        "recognize": {"pages": [{"source_id": "p1", "media_id": image["id"]}]},
    }

    def handler(request):
        if op == "embed":
            assert request.url.path == "/v1/embeddings"
            assert json.loads(request.content)["input"] == ["first", "second"]
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": 1, "embedding": [0.2] * 8},
                        {"index": 0, "embedding": [0.1] * 8},
                    ]
                },
            )
        if op == "transcribe":
            assert request.url.path == "/v1/audio/transcriptions"
            assert b'filename="audio.wav"' in request.content and b"RIFF" in request.content
            return httpx.Response(200, json={"text": ""})
        if op == "synthesize":
            assert request.url.path == "/v1/audio/speech"
            assert json.loads(request.content)["voice"] == "demo"
            return httpx.Response(200, content=wav(), headers={"Content-Type": "audio/wav"})
        body = json.loads(request.content)
        assert request.url.path == "/v1/" + op
        if op == "recognize":
            assert body["media"][image["id"]]["base64"]
            return httpx.Response(
                200, json={"payload": {"pages": [{"source_id": "p1", "text": "parsed"}]}}
            )
        return httpx.Response(200, json={"payload": {"results": [{"id": "a", "score": 0.7}]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mock:
        services.platform.gateway.client = mock
        response = await client.post(
            ROOT + "/invoke",
            json={
                "profile_id": profile["id"],
                "version": 1,
                "payload": {"operation": op, **bodies[op]},
            },
        )
    assert response.status_code == 200, response.text


async def test_credentials_rotation_and_connection_probe(gateway_platform, monkeypatch):
    client, services, _ = gateway_platform
    connection, _, profile = await setup(client, protocol="openai_compatible")
    updated = {**connection["spec"], "credential_ref": "AGENT_MODEL_SECRET_TEST"}
    assert (
        await client.put(ROOT + f"/connections/{connection['id']}?revision=1", json=updated)
    ).status_code == 200
    await client.post(ROOT + f"/profiles/{profile['id']}/publish", json={"revision": 1})
    headers = []

    def handler(request):
        headers.append(request.headers.get("Authorization"))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mock:
        services.platform.gateway.client = mock
        body = {
            "profile_id": profile["id"],
            "version": 2,
            "payload": {"operation": "chat", "messages": [{"role": "user", "content": "hi"}]},
        }
        monkeypatch.delenv("AGENT_MODEL_SECRET_TEST", raising=False)
        assert (await client.post(ROOT + "/invoke", json=body)).status_code == 503
        for secret in ("first-secret", "second-secret"):
            monkeypatch.setenv("AGENT_MODEL_SECRET_TEST", secret)
            assert (await client.post(ROOT + "/invoke", json=body)).status_code == 200
        assert headers == ["Bearer first-secret", "Bearer second-secret"]
        probe = await client.post(ROOT + f"/connections/{connection['id']}/probe")
        assert probe.json()["status"] == "available"
    rows = await client.get(ROOT + "/connections")
    assert "second-secret" not in rows.text and rows.json()[0]["credential_configured"]


async def test_fallback_records_actual_model(gateway_platform):
    client, services, _ = gateway_platform
    _, first, profile = await setup(
        client, protocol="openai_compatible", model_extra={"model_name": "primary"}
    )
    _, second, _ = await setup(
        client, protocol="openai_compatible", model_extra={"model_name": "secondary"}
    )
    changed = {**profile["spec"], "fallback_model_ids": [second["id"]]}
    assert (
        await client.put(ROOT + f"/profiles/{profile['id']}?revision=1", json=changed)
    ).status_code == 200
    await client.post(ROOT + f"/profiles/{profile['id']}/publish", json={"revision": 2})

    def handler(request):
        if json.loads(request.content)["model"] == "primary":
            return httpx.Response(503)
        return httpx.Response(200, json={"choices": [{"message": {"content": "fallback result"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mock:
        services.platform.gateway.client = mock
        response = await client.post(
            ROOT + "/invoke",
            json={
                "profile_id": profile["id"],
                "version": 2,
                "payload": {"operation": "chat", "messages": [{"role": "user", "content": "hi"}]},
            },
        )
    assert response.status_code == 200 and response.json()["model_id"] == second["id"]
    detail = (await client.get(ROOT + "/calls/" + response.json()["call_id"])).json()
    assert [a["model_id"] for a in detail["attempts"]] == [first["id"], second["id"]]


async def test_embedding_queue_does_not_block_chat(gateway_platform):
    client, services, settings = gateway_platform
    _, _, embed = await setup(client, "embed", protocol="openai_compatible")
    _, _, chat = await setup(client)
    gateway = services.platform.gateway
    gateway.limits["embed"] = asyncio.Semaphore(1)
    entered, release = asyncio.Event(), asyncio.Event()

    async def handler(request):
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1] * 8}]})

    from agent_platform.modules.model_gateway.contracts import EmbedRequest

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mock:
        gateway.client = mock
        task = asyncio.create_task(
            gateway.invoke(
                settings.tenant_id,
                embed["id"],
                1,
                EmbedRequest(inputs=[{"id": "a", "text": "test"}]),
            )
        )
        try:
            await entered.wait()
            async with asyncio.timeout(2):
                result = await gateway.invoke(
                    settings.tenant_id,
                    chat["id"],
                    1,
                    ChatRequest(messages=[{"role": "user", "content": "hi"}]),
                )
            assert result["demo"]
        finally:
            release.set()
            await task
