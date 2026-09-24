import asyncio
import json
import os
import re
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from agent_platform.platform.persistence.store import DomainError


class HttpTool(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")
    description: str = Field(min_length=1, max_length=1000)
    url: str
    method: Literal["GET", "POST"] = "GET"
    parameters: dict[str, Any]
    headers_from_env: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    auth_kind: Literal["none", "bearer", "api_key", "custom"] = "custom"
    credential_id: UUID | None = None
    stored_headers: list[str] = Field(default_factory=list)
    timeout_seconds: float = Field(default=10, gt=0, le=60)
    max_response_bytes: int = Field(default=16000, ge=128, le=100000)
    adapter_type: Literal["http"] = "http"
    effect: Literal["read", "write"] = "read"
    response_schema: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_tool(self):
        self.effect = "write" if self.method == "POST" else "read"
        names = list(self.headers)
        if len({n.lower() for n in names}) != len(names):
            raise ValueError("duplicate_tool_header")
        for name, value in self.headers.items():
            if (
                not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name)
                or name.lower()
                in {
                    "authorization",
                    "proxy-authorization",
                    "cookie",
                    "set-cookie",
                    "host",
                    "content-length",
                    "transfer-encoding",
                    "connection",
                }
                or any(k in name.lower() for k in ("api-key", "apikey", "token", "secret"))
                or len(value) > 8192
                or any(ord(c) < 32 or ord(c) > 126 for c in value)
            ):
                raise ValueError("invalid_public_header_use_authentication_for_secrets")
        if {n.lower() for n in names} & {
            n.lower() for n in list(self.headers_from_env) + self.stored_headers
        }:
            raise ValueError("duplicate_tool_header")
        url = urlsplit(self.url)
        if url.scheme not in {"https", "http"} or not url.hostname or url.username or url.password:
            raise ValueError("Tool URL must be a fixed HTTP(S) endpoint without credentials")
        if url.query or url.fragment:
            raise ValueError("Use validated parameters instead of query strings in tool URL")
        try:
            Draft202012Validator.check_schema(self.parameters)
            if self.response_schema is not None:
                Draft202012Validator.check_schema(self.response_schema)
        except SchemaError:
            raise ValueError("invalid_tool_schema") from None
        if (
            self.parameters.get("type") != "object"
            or self.parameters.get("additionalProperties") is not False
        ):
            raise ValueError("Tool parameters require type=object and additionalProperties=false")
        for schema in (
            self.parameters.get("properties", {}).values() if self.method == "GET" else []
        ):
            if not isinstance(schema, dict) or schema.get("type") not in {
                "string",
                "integer",
                "number",
                "boolean",
            }:
                raise ValueError("GET tools only support scalar query parameters")
        if self.method == "POST":
            content_type = next(
                (v for k, v in self.headers.items() if k.lower() == "content-type"), None
            )
            if content_type and content_type.split(";", 1)[0].strip().lower() != "application/json":
                raise ValueError("POST tools require application/json")
        return self

    def model_schema(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolInput(HttpTool):
    # None preserves existing credentials; an empty mapping clears them.
    # A null value preserves the current value of that specific header.
    header_secrets: dict[str, SecretStr | None] | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def validate_headers(self):
        names = list(self.headers) + list(self.headers_from_env) + list(self.header_secrets or {})
        if len({name.lower() for name in names}) != len(names):
            raise ValueError("duplicate_tool_header")
        if any(not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) for name in names):
            raise ValueError("invalid_tool_header")
        if any(
            n.lower()
            in {"host", "content-length", "transfer-encoding", "connection", "proxy-authorization"}
            for n in names
        ):
            raise ValueError("invalid_tool_header")
        for secret in (self.header_secrets or {}).values():
            if secret is not None:
                value = secret.get_secret_value()
                if (
                    not value.strip()
                    or len(value) > 8192
                    or any(ord(c) < 32 or ord(c) > 126 for c in value)
                ):
                    raise ValueError("invalid_tool_credential")
        return self


class ToolGateway:
    def __init__(
        self,
        client: httpx.AsyncClient,
        specs: list[HttpTool],
        concurrency=8,
        credential_resolver=None,
    ):
        self.client = client
        self.specs = {tool.name: tool for tool in specs}
        if len(self.specs) != len(specs):
            raise ValueError("Duplicate tool names")
        self.limit = asyncio.Semaphore(concurrency)
        self.credential_resolver = credential_resolver

    @property
    def schemas(self):
        return [tool.model_schema() for tool in self.specs.values()]

    async def execute(self, name: str, arguments: dict):
        tool = self.specs.get(name)
        if tool is None:
            return {"ok": False, "error": "tool_not_allowed"}
        if not Draft202012Validator(tool.parameters).is_valid(arguments):
            return {"ok": False, "error": "invalid_arguments"}
        if (
            tool.method == "POST"
            and len(json.dumps(arguments, ensure_ascii=False).encode()) > 100_000
        ):
            return {"ok": False, "error": "request_too_large"}
        headers = dict(tool.headers)
        if tool.credential_id:
            if self.credential_resolver is None:
                return {"ok": False, "error": "tool_credential_unavailable"}
            try:
                headers.update(await self.credential_resolver(tool))
            except DomainError as exc:
                return {"ok": False, "error": exc.code}
        for header, env in tool.headers_from_env.items():
            value = os.environ.get(env)
            if not value:
                return {"ok": False, "error": "tool_credential_unavailable"}
            headers[header] = value
        try:
            # No automatic retries and no redirect following. URL and headers are server-owned.
            async with asyncio.timeout(tool.timeout_seconds), self.limit:  # noqa: SIM117
                async with self.client.stream(
                    tool.method,
                    tool.url,
                    **({"json": arguments} if tool.method == "POST" else {"params": arguments}),
                    headers=headers,
                    follow_redirects=False,
                    timeout=tool.timeout_seconds,
                ) as response:
                    if not 200 <= response.status_code < 300:
                        return {
                            "ok": False,
                            "error": "upstream_http_error",
                            "status_code": response.status_code,
                        }
                    if response.status_code in {204, 205}:
                        return {"ok": True, "data": None}
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        body.extend(chunk)
                        if len(body) > tool.max_response_bytes:
                            return {"ok": False, "error": "response_too_large"}
                    try:
                        data = json.loads(body)
                        if tool.response_schema is not None and not Draft202012Validator(
                            tool.response_schema
                        ).is_valid(data):
                            return {"ok": False, "error": "invalid_output_schema"}
                        return {"ok": True, "data": data}
                    except (ValueError, UnicodeDecodeError):
                        return {"ok": False, "error": "invalid_json_response"}
        except (TimeoutError, httpx.TimeoutException):
            return {"ok": False, "error": "tool_timeout"}
        except httpx.HTTPError:
            return {"ok": False, "error": "tool_connection_error"}
