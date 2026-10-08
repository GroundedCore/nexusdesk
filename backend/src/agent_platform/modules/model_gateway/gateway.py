import asyncio
import json
import math
import time
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
from langchain_core.messages import AIMessage

from agent_platform.modules.agent_runtime.schemas import RuntimeFault
from agent_platform.platform.persistence.store import (
    DomainError,
    audit,
    execute,
    many,
    one,
    required,
)

from .adapters import demo, from_messages, invoke_http, stream_chat_http
from .catalog import Catalog
from .contracts import OPERATIONS, ChatRequest
from .governance import Governance
from .thinking import reasoning_fields

AUDIO = {"audio/wav", "audio/mpeg", "audio/ogg", "audio/webm"}
IMAGES = {"image/png", "image/jpeg"}


class ConnectionLimit:
    """One process-local budget per connection, across all profile versions."""

    def __init__(self):
        self.active = 0
        self.condition = asyncio.Condition()

    @asynccontextmanager
    async def slot(self, capacity):
        async with self.condition:
            await self.condition.wait_for(lambda: self.active < capacity)
            self.active += 1
        try:
            yield
        finally:
            async with self.condition:
                self.active -= 1
                self.condition.notify_all()


class ModelGateway:
    def __init__(self, engine, settings, client):
        self.engine, self.settings, self.client = engine, settings, client
        self.catalog = Catalog(engine, settings)
        self.governance = Governance(self)
        self.limits = {
            op: asyncio.Semaphore(settings.model_gateway_concurrency) for op in OPERATIONS
        }
        self.connections = {}

    async def probe(self, tenant, actor, identifier):
        async with self.engine.connect() as c:
            # Probing is an explicit administrator diagnostic rather than routed
            # traffic, so it also works on a disabled connection: the usual
            # workflow is to verify a connection before enabling it.
            row = await self.catalog.get(c, tenant, "connections", identifier)
        spec, started = row["spec"], time.monotonic()
        self.catalog.validate_address(spec)
        status, error = "available", None
        try:
            if spec["protocol"] != "demo":
                secret = await self.catalog.credential_for(tenant, identifier, spec)
                headers = {"Authorization": "Bearer " + secret} if secret else {}
                suffix = "/models" if spec["protocol"] == "openai_compatible" else "/health"
                async with asyncio.timeout(5):
                    async with self.client.stream(
                        "GET", spec["base_url"].rstrip("/") + suffix, headers=headers
                    ) as response:
                        response.raise_for_status()
        except (httpx.HTTPError, TimeoutError, DomainError):
            status, error = "unavailable", "model_probe_failed"
        result = {
            "status": status,
            "error_code": error,
            "demo": spec["protocol"] == "demo",
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
        async with self.engine.begin() as c:
            await audit(c, tenant, actor, "model.connection.probed", identifier, **result)
        return result

    async def upload(self, tenant, mime, content):
        if mime not in AUDIO | IMAGES:
            raise DomainError("unsupported_media_type", 415)
        if not content or len(content) > self.settings.model_gateway_media_bytes:
            raise DomainError("media_size_exceeded", 413)
        identifier = uuid4()
        async with self.engine.begin() as c:
            # Bounded media storage for the first release; externally schedule bulk cleanup.
            await execute(
                c,
                "DELETE FROM gateway_media WHERE id IN (SELECT id FROM gateway_media WHERE tenant_id=:t AND expires_at<now() LIMIT 100)",
                t=tenant,
            )
            await execute(c, "SELECT pg_advisory_xact_lock(hashtextextended(:t,731))", t=tenant)
            size = (
                await one(
                    c,
                    "SELECT COALESCE(sum(octet_length(content)),0) n FROM gateway_media WHERE tenant_id=:t",
                    t=tenant,
                )
            )["n"]
            if size + len(content) > self.settings.model_gateway_media_bytes * 20:
                raise DomainError("media_storage_quota_exceeded", 429)
            row = await one(
                c,
                "INSERT INTO gateway_media(id,tenant_id,mime_type,content,expires_at) VALUES(:id,:t,:mime,:content,now()+interval '24 hours') RETURNING id,mime_type,expires_at",
                id=identifier,
                t=tenant,
                mime=mime,
                content=content,
            )
        return row

    async def media(self, tenant, identifier):
        async with self.engine.connect() as c:
            return required(
                await one(
                    c,
                    "SELECT id,mime_type,content,expires_at FROM gateway_media WHERE id=:id AND tenant_id=:t AND expires_at>now()",
                    id=identifier,
                    t=tenant,
                ),
                "media_not_found_or_expired",
            )

    def validate_request(self, route, request):
        model, op = route["model"], request["operation"]
        if op not in model["operations"]:
            raise DomainError("model_capability_mismatch")
        if len(json.dumps(request, ensure_ascii=False)) > model["max_input_chars"] + 10000:
            raise DomainError("model_input_too_large", 413)
        if op == "chat":
            if request["tools"] and not model["tool_calling"]:
                raise DomainError("model_tools_not_supported")
            if sum(len(m["content"]) for m in request["messages"]) > model["max_input_chars"]:
                raise DomainError("model_input_too_large", 413)
        items = request.get("inputs", request.get("candidates", request.get("pages", [])))
        if len(items) > model["max_batch"]:
            raise DomainError("model_batch_too_large", 413)
        ids = [x.get("id", x.get("source_id")) for x in items]
        if len(ids) != len(set(ids)):
            raise DomainError("duplicate_input_id")
        if op == "rerank" and request["top_n"] > len(items):
            raise DomainError("top_n_exceeds_candidates")
        if op == "synthesize" and request["voice_id"] not in model["voices"]:
            raise DomainError("voice_not_supported")

    def validate_output(self, route, request, payload):
        op = request["operation"]
        if not isinstance(payload, dict):
            raise TypeError("payload")
        if op == "chat":
            message = AIMessage(
                content=payload["content"], tool_calls=payload.get("tool_calls", [])
            )
            names = {t["function"]["name"] for t in request["tools"]}
            if any(t["name"] not in names for t in message.tool_calls):
                raise ValueError("unknown tool")
            ids = [t["id"] for t in message.tool_calls]
            if len(ids) != len(set(ids)) or (not message.content and not message.tool_calls):
                raise ValueError("invalid chat response")
            return {
                "content": message.content,
                "tool_calls": message.tool_calls,
                **reasoning_fields(route, payload),
            }
        if op == "embed":
            vectors = payload["vectors"]
            if [v["id"] for v in vectors] != [x["id"] for x in request["inputs"]]:
                raise ValueError("embedding IDs")
            for v in vectors:
                if len(v["vector"]) != route["model"]["embedding_dimension"] or any(
                    type(n) not in (int, float) or not math.isfinite(n) for n in v["vector"]
                ):
                    raise ValueError("embedding dimension/value")
            return {
                "vectors": vectors,
                "dimension": route["model"]["embedding_dimension"],
                "vector_space": route["model"]["vector_space"],
            }
        if op == "rerank":
            rows = payload["results"]
            ids = [x["id"] for x in rows]
            if (
                len(ids) != request["top_n"]
                or len(set(ids)) != len(ids)
                or set(ids) - {x["id"] for x in request["candidates"]}
            ):
                raise ValueError("rerank IDs")
            if any(
                type(x["score"]) not in (int, float) or not math.isfinite(x["score"]) for x in rows
            ):
                raise ValueError("rerank scores")
            return {"results": sorted(rows, key=lambda x: x["score"], reverse=True)}
        if op == "transcribe":
            if not isinstance(payload["text"], str):
                raise ValueError("ASR text")
            return {"text": payload["text"]}
        if op == "recognize":
            if [x["source_id"] for x in payload["pages"]] != [
                x["source_id"] for x in request["pages"]
            ] or any(not isinstance(x["text"], str) for x in payload["pages"]):
                raise ValueError("OCR page mapping")
            return {
                "pages": [
                    {"source_id": x["source_id"], "text": x["text"]} for x in payload["pages"]
                ]
            }
        expected = "audio/wav" if request["format"] == "wav" else "audio/mpeg"
        data = payload["audio_bytes"]
        valid = (
            (data.startswith(b"RIFF") and data[8:12] == b"WAVE")
            if expected == "audio/wav"
            else (
                data.startswith(b"ID3")
                or (len(data) > 1 and data[0] == 255 and data[1] & 224 == 224)
            )
        )
        if payload["mime_type"] != expected or not valid:
            raise ValueError("TTS format")
        return payload

    async def records(self, tenant, call_id=None):
        async with self.engine.connect() as c:
            if call_id:
                row = required(
                    await one(
                        c,
                        "SELECT * FROM gateway_calls WHERE tenant_id=:t AND id=:id",
                        t=tenant,
                        id=call_id,
                    )
                )
                row["attempts"] = await many(
                    c,
                    "SELECT * FROM gateway_attempts WHERE call_id=:id ORDER BY created_at",
                    id=call_id,
                )
                return row
            return await many(
                c,
                "SELECT * FROM gateway_calls WHERE tenant_id=:t ORDER BY created_at DESC LIMIT 200",
                t=tenant,
            )

    async def invoke(
        self,
        tenant,
        profile_id,
        version,
        payload,
        run_id=None,
        emit=None,
        actor=None,
        access_key=None,
    ):
        snapshot = await self.catalog.resolve(tenant, profile_id, version)
        request = payload.model_dump(mode="json")
        spec, op = snapshot["spec"], request["operation"]
        if op != spec["operation"]:
            raise DomainError("model_operation_mismatch")
        if emit and (
            op != "chat"
            or any(r["connection"]["protocol"] == "gateway_http" for r in snapshot["routes"])
        ):
            raise DomainError("model_stream_not_supported")
        emitted = False

        async def forward(delta):
            nonlocal emitted
            emitted = True
            await emit(delta)

        for route in snapshot["routes"]:
            self.validate_request(route, request)
        media = {}
        refs = (
            [request["media_id"]]
            if op == "transcribe"
            else [p["media_id"] for p in request.get("pages", [])]
        )
        for ref in refs:
            artifact = await self.media(tenant, ref)
            if artifact["mime_type"] not in (AUDIO if op == "transcribe" else IMAGES):
                raise DomainError("media_operation_mismatch", 415)
            media[ref] = artifact
        call_id, started, status, error = uuid4(), time.monotonic(), "failed", None
        async with self.engine.begin() as c:
            await execute(
                c,
                "INSERT INTO gateway_calls(id,tenant_id,profile_id,profile_version,operation,run_id,status,actor,access_key_id) VALUES(:id,:t,:p,:v,:op,:run,'running',:actor,:key)",
                id=call_id,
                t=tenant,
                p=profile_id,
                v=version,
                op=op,
                run=run_id,
                actor=actor or ("runtime" if run_id else "internal"),
                key=access_key["id"] if access_key else None,
            )
        try:
            await self.governance.admit(
                tenant, call_id, profile_id, spec["timeout_seconds"], access_key
            )
            async with asyncio.timeout(spec["timeout_seconds"]):
                async with self.limits[op]:
                    for route in snapshot["routes"]:
                        policy = (await self.governance.model_settings(tenant, route["model_id"]))[
                            "spec"
                        ]
                        if policy["input_review"] != "off":
                            await self.review_content(tenant, call_id, "input", request)
                        for retry in range(spec["retries"] + 1):
                            try:
                                result, usage, attempt_id = await self.attempt(
                                    tenant,
                                    call_id,
                                    route,
                                    request,
                                    media,
                                    spec,
                                    forward if emit and policy["output_review"] == "off" else None,
                                    policy["pricing"],
                                )
                                if policy["output_review"] != "off":
                                    await self.review_content(tenant, call_id, "output", result)
                                    if emit:
                                        await forward(result)
                                status = "completed"
                                return {
                                    "call_id": call_id,
                                    "attempt_id": attempt_id,
                                    "profile_id": profile_id,
                                    "profile_version": version,
                                    "operation": op,
                                    "model_id": route["model_id"],
                                    "model_name": route["model"]["model_name"],
                                    "demo": route["connection"]["protocol"] == "demo",
                                    "payload": result,
                                    "usage": usage,
                                }
                            except DomainError as exc:
                                if emitted or exc.code not in (
                                    "model_rate_limited",
                                    "model_provider_unavailable",
                                    "model_transport_error",
                                ):
                                    raise
                                error = exc.code
                                if retry < spec["retries"]:
                                    await asyncio.sleep(min(0.25 * 2**retry, 1))
                    raise DomainError(error or "model_provider_unavailable", 503)
        except TimeoutError:
            error = "model_timeout"
            raise DomainError(error, 504) from None
        except asyncio.CancelledError:
            status, error = "cancelled", "model_cancelled"
            raise
        except DomainError as exc:
            error = exc.code
            raise
        finally:
            async with self.engine.begin() as c:
                await execute(
                    c,
                    "UPDATE gateway_calls SET status=:s,error_code=:e,duration_ms=:ms,completed_at=now() WHERE id=:id",
                    id=call_id,
                    s=status,
                    e=None if status == "completed" else error,
                    ms=int((time.monotonic() - started) * 1000),
                )
                await self.governance.alerts(c, tenant, call_id)
            await self.governance.release(tenant, call_id, access_key)

    async def review_content(self, tenant, call_id, direction, value):
        matches = await self.governance.review(tenant, value)
        async with self.engine.begin() as c:
            await execute(
                c,
                "UPDATE gateway_calls SET review=review || CAST(:review AS jsonb) WHERE tenant_id=:t AND id=:id",
                t=tenant,
                id=call_id,
                review=json.dumps({direction: {"blocked": bool(matches), "matches": matches}}),
            )
        if matches:
            raise DomainError("content_blocked_" + direction, 422)

    async def attempt(self, tenant, call_id, route, request, media, spec, emit=None, pricing=None):
        aid, started, status, error, usage = uuid4(), time.monotonic(), "failed", None, None
        async with self.engine.begin() as c:
            await execute(
                c,
                "INSERT INTO gateway_attempts(id,call_id,model_id,model_name,connection_id,status) VALUES(:id,:call,:m,:name,:connection_id,'running')",
                id=aid,
                call=call_id,
                m=route["model_id"],
                name=route["model"]["model_name"],
                connection_id=route["connection_id"],
            )
        try:
            async with self.engine.connect() as c:
                await self.catalog.get(c, tenant, "models", route["model_id"], True)
                current_connection = await self.catalog.get(
                    c, tenant, "connections", route["connection_id"], True
                )
            self.catalog.validate_address(route["connection"])
            limit = self.connections.setdefault(route["connection_id"], ConnectionLimit())
            async with limit.slot(current_connection["spec"]["concurrency"]):
                if route["connection"]["protocol"] == "demo":
                    result, raw_usage = await demo(route, request, media)
                    if emit:
                        await emit(result)
                elif emit:
                    result, raw_usage = await stream_chat_http(
                        self.client,
                        route,
                        request,
                        await self.catalog.credential_for(
                            tenant, route["connection_id"], route["connection"]
                        ),
                        {k: v for k, v in spec["parameters"].items() if v is not None},
                        self.settings.model_gateway_response_bytes,
                        emit,
                    )
                else:
                    result, raw_usage = await invoke_http(
                        self.client,
                        route,
                        request,
                        media,
                        await self.catalog.credential_for(
                            tenant, route["connection_id"], route["connection"]
                        ),
                        {k: v for k, v in spec["parameters"].items() if v is not None},
                        self.settings.model_gateway_response_bytes,
                    )
                if isinstance(raw_usage, dict):
                    # Only documented numeric units enter metadata logs.
                    usage = {
                        k: v
                        for k, v in raw_usage.items()
                        if k
                        in {
                            "prompt_tokens",
                            "completion_tokens",
                            "total_tokens",
                            "input_tokens",
                            "output_tokens",
                            "audio_seconds",
                            "characters",
                            "pages",
                            "texts",
                            "candidates",
                        }
                        and type(v) in (int, float)
                        and math.isfinite(v)
                        and v >= 0
                    }
                result = self.validate_output(route, request, result)
                if request["operation"] == "synthesize":
                    result = await self.upload(tenant, result["mime_type"], result["audio_bytes"])
                status = "completed"
                return result, usage, aid
        except httpx.HTTPStatusError as exc:
            error = (
                "model_rate_limited"
                if exc.response.status_code == 429
                else "model_provider_unavailable"
                if exc.response.status_code >= 500
                else "model_provider_rejected"
            )
            raise DomainError(error, 502) from None
        except httpx.TransportError:
            error = "model_transport_error"
            raise DomainError(error, 502) from None
        except (ValueError, KeyError, TypeError, IndexError):
            error = "invalid_model_response"
            raise DomainError(error, 502) from None
        except asyncio.CancelledError:
            status, error = "cancelled", "model_cancelled"
            raise
        except DomainError as exc:
            error = exc.code
            raise
        finally:
            async with self.engine.begin() as c:
                await execute(
                    c,
                    "UPDATE gateway_attempts SET status=:s,error_code=:e,duration_ms=:ms,usage=CAST(:u AS jsonb) WHERE id=:id",
                    id=aid,
                    s=status,
                    e=error,
                    ms=int((time.monotonic() - started) * 1000),
                    u=json.dumps(usage),
                )
                await self.governance.record_cost(
                    c, tenant, aid, call_id, route["model_id"], pricing or {}, usage
                )


class GatewayChatModel:
    def __init__(self, gateway, tenant, profile_id, version, schemas, run_id=None):
        self.gateway, self.tenant, self.profile_id, self.version = (
            gateway,
            tenant,
            profile_id,
            version,
        )
        self.schemas, self.run_id = schemas, run_id

    async def ainvoke(self, messages):
        try:
            result = await self.gateway.invoke(
                self.tenant,
                self.profile_id,
                self.version,
                ChatRequest(messages=from_messages(messages), tools=self.schemas),
                self.run_id,
            )
        except DomainError as exc:
            raise RuntimeFault(exc.code) from None
        payload, usage = result["payload"], result["usage"] or {}
        metadata = None
        incoming, outgoing = (
            usage.get("prompt_tokens", usage.get("input_tokens")),
            usage.get("completion_tokens", usage.get("output_tokens")),
        )
        if incoming is not None and outgoing is not None:
            metadata = {
                "input_tokens": int(incoming),
                "output_tokens": int(outgoing),
                "total_tokens": int(incoming + outgoing),
            }
        return AIMessage(
            content=payload["content"],
            additional_kwargs={"reasoning_content": payload["reasoning_content"]}
            if "reasoning_content" in payload
            else {},
            tool_calls=payload["tool_calls"],
            usage_metadata=metadata,
            response_metadata={
                "gateway_call_id": str(result["call_id"]),
                "model_name": result["model_name"],
            },
        )
