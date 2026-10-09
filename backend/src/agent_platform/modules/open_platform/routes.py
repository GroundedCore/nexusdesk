"""Separate management and application-key APIs; no administrative fallback."""

import asyncio
import json
import logging
import time
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.routing import APIRoute
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from agent_platform.modules.agent_runtime.schemas import TERMINAL, BusyError, CapacityError, RunView
from agent_platform.modules.open_platform.service import (
    AppIdentity,
    ApplicationInput,
    ApplicationUpdate,
)
from agent_platform.platform.identity.access import Admin, Reader
from agent_platform.platform.persistence.store import DomainError, execute, many, one

logger = logging.getLogger(__name__)


def service(request):
    return request.app.state.runtime.platform.open_platform


class PublicRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            request.state.open_request_id = uuid4()
            started = time.monotonic()
            code = None
            try:
                response = await original(request)
            except (
                DomainError,
                BusyError,
                CapacityError,
                RequestValidationError,
                SQLAlchemyError,
            ) as exc:
                if isinstance(exc, DomainError):
                    code, status = exc.code, exc.status
                elif isinstance(exc, BusyError):
                    code, status = "conversation_busy", 409
                elif isinstance(exc, CapacityError):
                    code, status = "runtime_capacity_exceeded", 429
                elif isinstance(exc, RequestValidationError):
                    code, status = "invalid_request", 422
                else:
                    code, status = "database_unavailable", 503
                response = JSONResponse(
                    status_code=status,
                    content={
                        "error": {"code": code},
                        "request_id": str(request.state.open_request_id),
                    },
                )
                if status == 429:
                    response.headers["Retry-After"] = (
                        "60" if code == "application_rate_limit" else "2"
                    )
            response.headers["X-Request-ID"] = str(request.state.open_request_id)
            identity = getattr(request.state, "open_identity", None)
            # Only known applications produce tenant-visible logs. Never persist bodies, headers or credentials.
            if identity:
                try:
                    async with service(request).engine.begin() as c:
                        await execute(
                            c,
                            """INSERT INTO open_request_logs(id,tenant_id,app_id,key_id,route,method,status,duration_ms,error_code,run_id)
                            VALUES(:id,:t,:a,:key,:route,:method,:status,:duration,:error,:run)""",
                            id=request.state.open_request_id,
                            t=identity.tenant,
                            a=identity.app_id,
                            key=identity.key_id,
                            route=self.path,
                            method=request.method,
                            status=response.status_code,
                            duration=int((time.monotonic() - started) * 1000),
                            error=code,
                            run=getattr(request.state, "open_run_id", None),
                        )
                except SQLAlchemyError:
                    logger.warning("open_platform_request_log_unavailable")
            return response

        return handler


management = APIRouter(prefix="/open-platform", tags=["open-platform-management"])
public = APIRouter(tags=["enterprise-api"], route_class=PublicRoute)


class KeyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expires_at: AwareDatetime | None = None
    replace_id: UUID | None = None
    grace_minutes: int = Field(default=0, ge=0, le=1440)


@management.get("/applications")
async def applications(
    request: Request,
    user: Reader,
    q: str = Query(default="", max_length=100),
    page: int = Query(default=1, ge=1),
    enabled: bool | None = None,
):
    async with service(request).engine.connect() as c:
        rows = await many(
            c,
            "SELECT * FROM open_applications WHERE tenant_id=:t AND name ILIKE :q AND (CAST(:enabled AS boolean) IS NULL OR enabled=:enabled) ORDER BY created_at DESC,id LIMIT 20 OFFSET :off",
            t=user.tenant,
            q="%" + q + "%",
            enabled=enabled,
            off=(page - 1) * 20,
        )
        total = await one(
            c,
            "SELECT count(*) AS n FROM open_applications WHERE tenant_id=:t AND name ILIKE :q AND (CAST(:enabled AS boolean) IS NULL OR enabled=:enabled)",
            t=user.tenant,
            q="%" + q + "%",
            enabled=enabled,
        )
        return {"items": rows, "total": total["n"]}


@management.get("/agents")
async def eligible_agents(request: Request, user: Reader):
    async with service(request).engine.connect() as c:
        return await many(
            c,
            "SELECT id,name,published_version FROM agents WHERE tenant_id=:t AND NOT archived AND published_version IS NOT NULL ORDER BY name,id",
            t=user.tenant,
        )


@management.post("/applications", status_code=201)
async def create_application(body: ApplicationInput, request: Request, user: Admin):
    return await service(request).save(user.tenant, user.actor, body)


@management.get("/applications/{app_id}")
async def application(app_id: UUID, request: Request, user: Reader):
    async with service(request).engine.connect() as c:
        return await service(request).application(c, user.tenant, app_id)


@management.put("/applications/{app_id}")
async def update_application(app_id: UUID, body: ApplicationUpdate, request: Request, user: Admin):
    return await service(request).save(user.tenant, user.actor, body, app_id)


@management.get("/applications/{app_id}/keys")
async def keys(app_id: UUID, request: Request, user: Reader):
    return await service(request).keys(user.tenant, app_id)


@management.post("/applications/{app_id}/keys", status_code=201)
async def issue_key(app_id: UUID, body: KeyInput, request: Request, user: Admin):
    return await service(request).issue_key(
        user.tenant, user.actor, app_id, body.expires_at, body.replace_id, body.grace_minutes
    )


@management.post("/applications/{app_id}/keys/{key_id}/revoke")
async def revoke_key(app_id: UUID, key_id: UUID, request: Request, user: Admin):
    return await service(request).revoke(user.tenant, user.actor, app_id, key_id)


@management.get("/applications/{app_id}/logs")
async def logs(
    app_id: UUID,
    request: Request,
    user: Reader,
    page: int = Query(default=1, ge=1),
    errors_only: bool = False,
    request_id: UUID | None = None,
):
    async with service(request).engine.connect() as c:
        await service(request).application(c, user.tenant, app_id)
        where = "l.app_id=:a AND l.tenant_id=:t AND (NOT :errors OR l.status>=400) AND (CAST(:id AS uuid) IS NULL OR l.id=:id)"
        params = {"a": app_id, "t": user.tenant, "errors": errors_only, "id": request_id}
        rows = await many(
            c,
            """SELECT l.*,r.status AS run_status,r.error_code AS run_error,
            EXTRACT(EPOCH FROM (r.finished_at-r.started_at))*1000 AS run_duration_ms
            FROM open_request_logs l LEFT JOIN runtime_runs r ON r.id=l.run_id AND r.tenant_id=l.tenant_id WHERE """
            + where
            + " ORDER BY l.created_at DESC,l.id LIMIT 20 OFFSET :off",
            **params,
            off=(page - 1) * 20,
        )
        total = await one(
            c, "SELECT count(*) AS n FROM open_request_logs l WHERE " + where, **params
        )
        return {"items": rows, "total": total["n"]}


async def authenticate(request: Request, authorization: Annotated[str | None, Header()] = None):
    return await service(request).authenticate(authorization, request)


Identity = Annotated[AppIdentity, Depends(authenticate)]


async def external_user(
    identity: Identity,
    value: Annotated[
        str | None,
        Header(alias="X-External-User-ID", min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$"),
    ] = None,
):
    if identity.external_user_id:
        if value is not None and value != identity.external_user_id:
            raise DomainError("external_user_mismatch", 403)
        return identity.external_user_id
    if not value:
        raise DomainError("external_user_required", 422)
    return value


ExternalUser = Annotated[str, Depends(external_user)]


class SessionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    agent_id: UUID
    external_session_id: str = Field(min_length=1, max_length=128)


class MessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=8000)
    stream: bool = False
    wait_seconds: int = Field(default=30, ge=0, le=30)


@public.get("/agents")
async def authorized_agents(request: Request, identity: Identity):
    async with service(request).engine.connect() as c:
        app = await service(request).check(c, identity)
        rows = await many(
            c,
            "SELECT id,name,description,published_version FROM agents WHERE tenant_id=:t AND id=ANY(:ids) AND NOT archived AND published_version IS NOT NULL ORDER BY name,id",
            t=identity.tenant,
            ids=app["agent_ids"],
        )

        return [
            {
                **row,
                "effective_version": app["agent_versions"].get(
                    str(row["id"]), row["published_version"]
                ),
            }
            for row in rows
        ]


@public.post("/conversations", status_code=201)
async def create_conversation(
    body: SessionInput, request: Request, identity: Identity, user: ExternalUser
):
    """Idempotently create a conversation in the application + external user namespace."""
    return await service(request).create_session(
        identity, user, body.agent_id, body.external_session_id
    )


@public.get("/conversations/{conversation_id}/messages")
async def history(
    conversation_id: UUID,
    request: Request,
    identity: Identity,
    user: ExternalUser,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    async with service(request).engine.connect() as c:
        await service(request).session(c, identity, user, conversation_id)
        rows = await many(
            c,
            "SELECT id,seq,role,content,run_id,created_at FROM conversation_messages WHERE conversation_id=:id AND seq>:after ORDER BY seq LIMIT :lim",
            id=conversation_id,
            after=after,
            lim=limit + 1,
        )
        return {
            "items": rows[:limit],
            "has_more": len(rows) > limit,
            "next_after": rows[min(limit, len(rows)) - 1]["seq"] if rows else after,
        }


def view(row):
    return RunView.model_validate(row).model_dump(mode="json")


def stream_response(request, identity, user, run_id, after=0):
    async def stream():
        cursor = after
        listener = request.app.state.runtime.listener
        queue = await listener.subscribe(run_id)
        try:
            yield (
                "event: accepted\ndata: "
                + json.dumps(
                    {"run_id": str(run_id), "request_id": str(request.state.open_request_id)}
                )
                + "\n\n"
            )
            ticks = 0
            while not await request.is_disconnected():
                # Token deltas arrive transiently. Only the visible text is exposed;
                # reasoning and tool-call fragments stay internal.
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    message = None
                while message is not None:
                    payload = json.loads(message)
                    if payload.get("k") != "delta":
                        break
                    if payload.get("text"):
                        yield (
                            "event: model.delta\ndata: "
                            + json.dumps(
                                {
                                    "run_id": str(run_id),
                                    "round": payload.get("round"),
                                    "text": payload["text"],
                                },
                                ensure_ascii=False,
                            )
                            + "\n\n"
                        )
                    try:
                        message = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        message = None
                try:
                    row = await service(request).run(identity, user, run_id)
                except DomainError as exc:
                    yield "event: error\ndata: " + json.dumps({"code": exc.code}) + "\n\n"
                    return
                events = await service(request).p.repository.events(run_id, identity.tenant, cursor)
                for event in events:
                    cursor = event["seq"]
                    kind = event["type"]
                    # Public stream deliberately excludes tool inputs, retrieval chunks and internal gateway metadata.
                    data = {"run_id": str(run_id)}
                    if kind in {"model.started", "model.completed"}:
                        # Round boundaries let a client tell the answer from the preamble:
                        # the answer is the last round that did not end in tool calls.
                        data["round"] = event["data"].get("round")
                        if kind == "model.completed":
                            data["tool_calls"] = event["data"].get("tool_calls", 0)
                    if kind in {"run.completed", "run.failed", "run.cancelled"}:
                        final = await service(request).run(identity, user, run_id)
                        data.update(view(final))
                    yield f"id: {cursor}\nevent: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                if row["status"] in TERMINAL and cursor >= row["event_seq"]:
                    return
                if len(events) == 200:
                    continue
                ticks += 1
                if ticks % 30 == 0:
                    yield ": heartbeat\n\n"
        finally:
            await listener.unsubscribe(run_id, queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@public.post("/conversations/{conversation_id}/messages")
async def message(
    conversation_id: UUID,
    body: MessageInput,
    request: Request,
    identity: Identity,
    user: ExternalUser,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
):
    """Submit once per Idempotency-Key; stream=true emits progress and a final complete answer (not token deltas)."""
    row = await service(request).submit(
        identity,
        user,
        conversation_id,
        body.message,
        idempotency_key,
        request.state.open_request_id,
    )
    request.state.open_run_id = row["id"]
    if body.stream:
        return stream_response(request, identity, user, row["id"])
    deadline = time.monotonic() + body.wait_seconds
    while row["status"] not in TERMINAL and time.monotonic() < deadline:
        if await request.is_disconnected():
            break
        await asyncio.sleep(0.25)
        row = await service(request).run(identity, user, row["id"])
    return JSONResponse(
        content=jsonable_encoder(view(row)), status_code=200 if row["status"] in TERMINAL else 202
    )


@public.get("/runs/{run_id}")
async def run(run_id: UUID, request: Request, identity: Identity, user: ExternalUser):
    row = await service(request).run(identity, user, run_id)
    request.state.open_run_id = run_id
    return view(row)


@public.post("/runs/{run_id}/cancel")
async def cancel(run_id: UUID, request: Request, identity: Identity, user: ExternalUser):
    await service(request).run(identity, user, run_id)
    request.state.open_run_id = run_id
    return view(await service(request).p.repository.cancel(run_id, identity.tenant))


@public.get("/runs/{run_id}/events")
async def events(
    run_id: UUID,
    request: Request,
    identity: Identity,
    user: ExternalUser,
    after: int = Query(default=0, ge=0, le=2147483647),
    last_event_id: Annotated[int | None, Header(ge=0, le=2147483647)] = None,
):
    await service(request).run(identity, user, run_id)
    request.state.open_run_id = run_id
    return stream_response(request, identity, user, run_id, max(after, last_event_id or 0))


from agent_platform.modules.open_platform.knowledge import SyncInput
from agent_platform.modules.open_platform.webhooks import WebhookInput


@management.get("/knowledge-bases")
async def eligible_knowledge(request: Request, user: Reader):
    async with service(request).engine.connect() as c:
        return await many(
            c,
            "SELECT id,name FROM knowledge_bases WHERE tenant_id=:t AND enabled ORDER BY name,id",
            t=user.tenant,
        )


@management.get("/agents/{agent_id}/versions")
async def available_versions(agent_id: UUID, request: Request, user: Reader):
    async with service(request).engine.connect() as c:
        return await many(
            c,
            "SELECT v.version,v.created_at FROM agent_versions v JOIN agents a ON a.id=v.agent_id WHERE a.id=:id AND a.tenant_id=:t AND NOT a.archived ORDER BY v.version DESC",
            id=agent_id,
            t=user.tenant,
        )


@management.get("/applications/{app_id}/webhook")
async def webhook_config(app_id: UUID, request: Request, user: Reader):
    return await service(request).webhooks.config(user.tenant, app_id)


@management.put("/applications/{app_id}/webhook")
async def configure_webhook(app_id: UUID, body: WebhookInput, request: Request, user: Admin):
    return await service(request).webhooks.save(user.tenant, user.actor, app_id, body)


@management.get("/applications/{app_id}/deliveries")
async def deliveries(
    app_id: UUID, request: Request, user: Reader, page: int = Query(default=1, ge=1)
):
    return await service(request).webhooks.deliveries(user.tenant, app_id, page)


@management.post("/applications/{app_id}/deliveries/{event_id}/retry")
async def retry_delivery(app_id: UUID, event_id: UUID, request: Request, user: Admin):
    return await service(request).webhooks.retry(user.tenant, user.actor, app_id, event_id)


@public.get("/knowledge-bases")
async def authorized_knowledge(request: Request, identity: Identity):
    async with service(request).engine.connect() as c:
        app = await service(request).check(c, identity)
        return await many(
            c,
            "SELECT id,name FROM knowledge_bases WHERE id=ANY(:ids) AND tenant_id=:t AND enabled ORDER BY name,id",
            ids=app["knowledge_base_ids"],
            t=identity.tenant,
        )


@public.get("/knowledge-bases/{base_id}/documents")
async def synced_documents(
    base_id: UUID, request: Request, identity: Identity, offset: int = Query(default=0, ge=0)
):
    return await service(request).knowledge.documents(identity, base_id, offset)


@public.put("/knowledge-bases/{base_id}/documents/{external_id}")
async def sync_document(
    base_id: UUID, external_id: str, body: SyncInput, request: Request, identity: Identity
):
    if not 1 <= len(external_id) <= 128:
        raise DomainError("invalid_external_id", 422)
    return await service(request).knowledge.save(identity, base_id, external_id, body)


@public.delete("/knowledge-bases/{base_id}/documents/{external_id}")
async def remove_synced_document(
    base_id: UUID,
    external_id: str,
    request: Request,
    identity: Identity,
    expected_version: int = Query(ge=1),
):
    if not 1 <= len(external_id) <= 128:
        raise DomainError("invalid_external_id", 422)
    return await service(request).knowledge.delete(identity, base_id, external_id, expected_version)


@public.post("/knowledge-bases/{base_id}/publish", status_code=202)
async def publish_knowledge(
    base_id: UUID,
    request: Request,
    identity: Identity,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
):
    return await service(request).knowledge.publish(identity, base_id, idempotency_key)


@public.get("/knowledge-tasks/{task_id}")
async def knowledge_task(task_id: UUID, request: Request, identity: Identity):
    return await service(request).knowledge.task(identity, task_id)
