import json

import httpx
import pytest
from pydantic import ValidationError

from agent_platform.modules.tool_gateway.openapi import export_document, preview
from agent_platform.modules.tool_gateway.service import HttpTool, ToolGateway


def post_tool(**changes):
    return HttpTool(
        **{
            "name": "create_order",
            "description": "Create an order",
            "url": "https://service.example/orders",
            "method": "POST",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer": {"type": "object", "properties": {"name": {"type": "string"}}},
                    "items": {"type": "array", "items": {"type": "integer"}, "minItems": 1},
                },
                "required": ["items"],
                "additionalProperties": False,
            },
            **changes,
        }
    )


@pytest.mark.asyncio
async def test_post_json_body_headers_and_invalid_arguments(monkeypatch):
    seen = []
    monkeypatch.setenv("AGENT_TOOL_SECRET_POST", "fake-test-key")

    def respond(request):
        seen.append(request)
        return httpx.Response(201, json={"created": True})

    spec = post_tool(headers_from_env={"Authorization": "AGENT_TOOL_SECRET_POST"})
    assert spec.effect == "write"
    args = {"customer": {"name": "客户"}, "items": [1, 2]}
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        gateway = ToolGateway(client, [spec])
        assert (await gateway.execute(spec.name, args))["ok"]
        assert (await gateway.execute(spec.name, {"items": ["bad"]}))[
            "error"
        ] == "invalid_arguments"
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST" and not request.url.query
    assert json.loads(request.content) == args
    assert request.headers["content-type"] == "application/json"
    assert request.headers["authorization"] == "fake-test-key"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [204, 302, 500])
async def test_post_no_retry_no_redirect_and_empty_success(status):
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(status, headers={"Location": "https://other.example/"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await ToolGateway(client, [post_tool()]).execute("create_order", {"items": [1]})
    assert len(seen) == 1
    assert result["ok"] == (status == 204)
    if status == 204:
        assert result["data"] is None


@pytest.mark.asyncio
async def test_post_size_limit_and_get_compatibility():
    with pytest.raises(ValidationError):
        post_tool(method="GET")  # Existing GET contract remains scalar query parameters.
    with pytest.raises(ValidationError):
        post_tool(headers={"Content-Type": "application/x-www-form-urlencoded"})
    seen = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: seen.append(r))) as client:
        result = await ToolGateway(client, [post_tool()]).execute(
            "create_order", {"customer": {"name": "x" * 100001}, "items": [1]}
        )
    assert result["error"] == "request_too_large" and not seen


def test_openapi_get_post_same_path_roundtrip_and_local_schema_ref():
    group = {"name": "Orders", "description": "Orders", "spec": {"url": "https://service.example"}}
    post = post_tool().model_dump(mode="json")
    get = {
        **post,
        "name": "list_orders",
        "method": "GET",
        "parameters": {
            "type": "object",
            "properties": {"limit": {"type": "integer"}},
            "additionalProperties": False,
        },
    }
    rows = [
        {"name": s["name"], "display_name": s["name"], "relative_path": "/orders", "spec": s}
        for s in [get, post]
    ]
    document = export_document(group, rows)
    assert set(document["paths"]["/orders"]) == {"get", "post"}
    document["components"] = {"schemas": {"Order": post["parameters"]}}
    document["paths"]["/orders"]["post"]["requestBody"]["content"]["application/json"]["schema"] = {
        "$ref": "#/components/schemas/Order"
    }
    imported = preview(json.dumps(document))
    assert not imported["skipped"]
    assert {a["method"] for a in imported["items"]} == {"GET", "POST"}
    assert (
        next(a for a in imported["items"] if a["method"] == "POST")["parameters"]
        == post["parameters"]
    )
