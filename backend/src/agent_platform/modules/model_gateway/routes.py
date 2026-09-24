import asyncio
import json
from contextlib import suppress
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from agent_platform.platform.identity.access import Admin, Operator, Reader
from agent_platform.platform.persistence.store import DomainError

from .catalog import SCHEMAS
from .contracts import Invocation, Revision, Rollback, Toggle
from .discovery import discover_models

router = APIRouter(prefix="/model-gateway", tags=["model-gateway"])
Kind = Literal["connections", "models", "profiles"]


def gateway(request):
    return request.app.state.runtime.platform.gateway


@router.get("/calls")
async def calls(request: Request, user: Reader):
    return await gateway(request).records(user.tenant)


@router.get("/calls/{identifier}")
async def call_detail(identifier: UUID, request: Request, user: Reader):
    return await gateway(request).records(user.tenant, identifier)


@router.post("/invoke")
async def invoke(body: Invocation, request: Request, user: Operator):
    return await gateway(request).invoke(
        user.tenant, body.profile_id, body.version, body.payload, actor=user.actor
    )


@router.post("/stream")
async def stream(body: Invocation, request: Request, user: Operator):
    service = gateway(request)
    snapshot = await service.catalog.resolve(user.tenant, body.profile_id, body.version)
    if (
        body.payload.operation != "chat"
        or snapshot["spec"]["operation"] != "chat"
        or any(r["connection"]["protocol"] == "gateway_http" for r in snapshot["routes"])
    ):
        raise DomainError("model_stream_not_supported")

    async def events():
        queue = asyncio.Queue(maxsize=1)

        async def emit(delta):
            await queue.put(("delta", delta))

        async def produce():
            try:
                result = await service.invoke(
                    user.tenant,
                    body.profile_id,
                    body.version,
                    body.payload,
                    emit=emit,
                    actor=user.actor,
                )
                await queue.put(("completed", result))
            except DomainError as exc:
                await queue.put(("error", {"code": exc.code}))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 -- the SSE boundary must redact internal failures.
                await queue.put(("error", {"code": "model_gateway_unavailable"}))

        task = asyncio.create_task(produce())
        try:
            while True:
                kind, data = await queue.get()
                yield f"event: {kind}\ndata: {json.dumps(jsonable_encoder(data), ensure_ascii=False)}\n\n"
                if kind in ("completed", "error"):
                    break
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/media", status_code=201)
async def upload(request: Request, user: Operator):
    service = gateway(request)
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > service.settings.model_gateway_media_bytes:
            raise DomainError("media_size_exceeded", 413)
    return await service.upload(
        user.tenant, request.headers.get("content-type", "").split(";")[0], bytes(content)
    )


@router.get("/media/{identifier}")
async def download(identifier: UUID, request: Request, user: Operator):
    artifact = await gateway(request).media(user.tenant, identifier)
    return Response(
        bytes(artifact["content"]),
        media_type=artifact["mime_type"],
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "attachment",
        },
    )


@router.get("/profiles/{identifier}/versions")
async def versions(identifier: UUID, request: Request, user: Reader):
    return await gateway(request).catalog.versions(user.tenant, identifier)


@router.post("/connections/{identifier}/probe")
async def probe(identifier: UUID, request: Request, user: Admin):
    return await gateway(request).probe(user.tenant, user.actor, identifier)


@router.get("/connections/{identifier}/available-models")
async def available_models(identifier: UUID, request: Request, response: Response, user: Admin):
    response.headers["Cache-Control"] = "no-store"
    return await discover_models(gateway(request), user.tenant, identifier)


@router.post("/profiles/{identifier}/publish")
async def publish(identifier: UUID, body: Revision, request: Request, user: Admin):
    return await gateway(request).catalog.publish(
        user.tenant, user.actor, identifier, body.revision
    )


@router.post("/profiles/{identifier}/rollback")
async def rollback(identifier: UUID, body: Rollback, request: Request, user: Admin):
    return await gateway(request).catalog.rollback(
        user.tenant, user.actor, identifier, body.version
    )


@router.get("/{kind}")
async def listing(kind: Kind, request: Request, user: Reader):
    return await gateway(request).catalog.list(user.tenant, kind)


def parse(kind, body):
    try:
        return SCHEMAS[kind].model_validate(body)
    except ValidationError as exc:
        # Strip arbitrary input values from errors (users may mistakenly paste secrets).
        raise RequestValidationError(
            [
                {"loc": ["body", *e["loc"]], "msg": e["msg"], "type": e["type"]}
                for e in exc.errors(include_input=False, include_context=False)
            ]
        ) from None


@router.post("/{kind}", status_code=201)
async def create(kind: Kind, body: dict, request: Request, user: Admin):
    return await gateway(request).catalog.save(user.tenant, user.actor, kind, parse(kind, body))


@router.put("/{kind}/{identifier}")
async def update(
    kind: Kind,
    identifier: UUID,
    body: dict,
    request: Request,
    user: Admin,
    revision: int = Query(ge=1),
):
    return await gateway(request).catalog.save(
        user.tenant, user.actor, kind, parse(kind, body), identifier, revision
    )


@router.patch("/{kind}/{identifier}")
async def toggle(kind: Kind, identifier: UUID, body: Toggle, request: Request, user: Admin):
    return await gateway(request).catalog.toggle(user.tenant, user.actor, kind, identifier, body)
