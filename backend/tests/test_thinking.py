import json

import httpx
import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from test_model_gateway import ROOT, setup
from test_platform_postgres import platform as _platform_fixture

from agent_platform.modules.model_gateway.adapters import stream_chat_http
from agent_platform.modules.model_gateway.gateway import GatewayChatModel
from agent_platform.modules.model_gateway.thinking import provider_messages, provider_parameters
from agent_platform.platform.persistence.store import DomainError

gateway_platform = _platform_fixture
ROUTE = {
    "connection": {"protocol": "openai_compatible", "base_url": "https://api.deepseek.com"},
    "model": {"model_name": "deepseek-v4-pro"},
}
TOOL = {
    "type": "function",
    "function": {"name": "lookup", "parameters": {"type": "object", "properties": {}}},
}


@pytest.mark.parametrize("mode", [None, "enabled", "disabled"])
def test_thinking_mapping_and_default(mode):
    result = provider_parameters(ROUTE, {"thinking": mode, "max_tokens": 1024})
    assert result == {"max_tokens": 1024, **({"thinking": {"type": mode}} if mode else {})}
    other = {
        **ROUTE,
        "connection": {**ROUTE["connection"], "base_url": "https://api.openai.com/v1"},
    }
    if mode:
        with pytest.raises(DomainError, match="model_thinking_not_supported"):
            provider_parameters(other, {"thinking": mode})
    else:
        assert provider_parameters(other, {"thinking": None}) == {}
    assert (
        "reasoning_content"
        not in provider_messages(
            other,
            [{"role": "assistant", "content": "answer", "reasoning_content": "private trace"}],
            [TOOL],
        )[0]
    )


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_thinking_published_profile_tool_round_trip(gateway_platform):
    client, services, settings = gateway_platform
    connection, model, profile = await setup(client, protocol="openai_compatible")
    unsupported = await client.put(
        ROOT + f"/profiles/{profile['id']}?revision=1",
        json={**profile["spec"], "parameters": {"thinking": "enabled"}},
    )
    assert unsupported.status_code == 422
    assert unsupported.json()["detail"] == "model_thinking_not_supported"
    await client.put(
        ROOT + f"/connections/{connection['id']}?revision=1",
        json={**connection["spec"], "base_url": "https://api.deepseek.com"},
    )
    await client.put(
        ROOT + f"/models/{model['id']}?revision=1",
        json={**model["spec"], "model_name": "deepseek-v4-pro"},
    )
    saved = await client.put(
        ROOT + f"/profiles/{profile['id']}?revision=1",
        json={**profile["spec"], "parameters": {"thinking": "enabled"}},
    )
    assert saved.status_code == 200, saved.text
    assert (
        await client.post(ROOT + f"/profiles/{profile['id']}/publish", json={"revision": 2})
    ).status_code == 200
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        assert body["thinking"] == {"type": "enabled"}
        if len(calls) == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": "",
                                "reasoning_content": "provider-tool-trace",
                                "tool_calls": [
                                    {
                                        "id": "call1",
                                        "function": {"name": "lookup", "arguments": "{}"},
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        assert body["messages"][1]["reasoning_content"] == "provider-tool-trace"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "done", "reasoning_content": "provider-final-trace"}}
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mock:
        services.platform.gateway.client = mock
        chat = GatewayChatModel(
            services.platform.gateway, settings.tenant_id, profile["id"], 2, [TOOL]
        )
        first = await chat.ainvoke([HumanMessage(content="hi")])
        assert first.additional_kwargs["reasoning_content"] == "provider-tool-trace"
        final = await chat.ainvoke(
            [HumanMessage(content="hi"), first, ToolMessage(content="ok", tool_call_id="call1")]
        )
        assert (
            final.content == "done"
            and final.additional_kwargs["reasoning_content"] == "provider-final-trace"
        )
    logs = await client.get(ROOT + "/calls")
    assert "provider-tool-trace" not in logs.text and "provider-final-trace" not in logs.text
    # Explicit mode is rejected for a model whose provider has not been adapted.
    invalid = await client.put(
        ROOT + f"/profiles/{profile['id']}?revision=2",
        json={**profile["spec"], "parameters": {"thinking": "invalid"}},
    )
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_stream_preserves_reasoning_separately_from_visible_text():
    events = []

    async def emit(value):
        events.append(value)

    def handler(request):
        body = json.loads(request.content)
        assert body["thinking"] == {"type": "disabled"}
        data = (
            "".join(
                "data: " + json.dumps({"choices": [{"delta": delta}]}) + "\n\n"
                for delta in [
                    {"reasoning_content": "trace-"},
                    {"reasoning_content": "part"},
                    {"content": "answer"},
                ]
            )
            + "data: [DONE]\n\n"
        )
        return httpx.Response(200, content=data, headers={"content-type": "text/event-stream"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result, _ = await stream_chat_http(
            client,
            ROUTE,
            {"messages": [{"role": "user", "content": "hi"}], "tools": []},
            None,
            {"thinking": "disabled"},
            10000,
            emit,
        )
    assert result["content"] == "answer" and result["reasoning_content"] == "trace-part"
    # reasoning_content is emitted for live streaming in addition to being
    # accumulated into the final result.
    assert events == [
        {"reasoning_content": "trace-"},
        {"reasoning_content": "part"},
        {"content": "answer"},
    ]
