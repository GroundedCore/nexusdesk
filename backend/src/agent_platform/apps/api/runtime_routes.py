import asyncio
import json
from typing import Annotated
from uuid import UUID

from asyncpg import PostgresError
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from agent_platform.modules.agent_runtime.schemas import (
    TERMINAL,
    BusyError,
    CapacityError,
    RunRequest,
    RunView,
)
from agent_platform.platform.identity.access import Reader, require


def authorize(user: Reader):
    return user.tenant


router = APIRouter(tags=["runtime"], dependencies=[Depends(authorize)])


@router.get("/ready")
async def ready(request: Request):
    try:
        async with request.app.state.runtime.repository.engine.connect() as conn:
            await conn.execute(text("SELECT id FROM agents LIMIT 0"))
            await conn.execute(text("SELECT id FROM gateway_profiles LIMIT 0"))
    except (SQLAlchemyError, PostgresError, OSError, TimeoutError):
        raise HTTPException(503, "database_unavailable_or_not_migrated") from None
    return {"status": "ready", "model_backend": request.app.state.settings.model_backend}


@router.post(
    "/runs",
    response_model=RunView,
    status_code=202,
    dependencies=[Depends(require("admin", "operator"))],
)
async def start_run(body: RunRequest, request: Request, tenant: Annotated[str, Depends(authorize)]):
    services = request.app.state.runtime
    try:
        return await services.platform.legacy_submit(tenant, body)
    except BusyError:
        raise HTTPException(409, "conversation_busy") from None
    except CapacityError:
        raise HTTPException(
            429, "runtime_capacity_exceeded", headers={"Retry-After": "2"}
        ) from None


@router.get("/runs/{run_id}", response_model=RunView)
async def get_run(run_id: UUID, request: Request, tenant: Annotated[str, Depends(authorize)]):
    row = await request.app.state.runtime.repository.get(run_id, tenant)
    if row is None:
        raise HTTPException(404, "run_not_found")
    return row


@router.post(
    "/runs/{run_id}/cancel",
    response_model=RunView,
    dependencies=[Depends(require("admin", "operator"))],
)
async def cancel_run(run_id: UUID, request: Request, tenant: Annotated[str, Depends(authorize)]):
    row = await request.app.state.runtime.repository.cancel(run_id, tenant)
    if row is None:
        raise HTTPException(404, "run_not_found")
    return row


@router.get("/runs/{run_id}/events")
async def stream_events(
    run_id: UUID,
    request: Request,
    tenant: Annotated[str, Depends(authorize)],
    after: Annotated[int, Query(ge=0, le=2147483647)] = 0,
    last_event_id: Annotated[str | None, Header()] = None,
):
    repository = request.app.state.runtime.repository
    if last_event_id is not None:
        try:
            last_id = int(last_event_id)
            if not 0 <= last_id <= 2147483647:
                raise ValueError()
            after = max(after, last_id)
        except ValueError:
            raise HTTPException(400, "invalid_last_event_id") from None
        if after < 0:
            raise HTTPException(400, "invalid_last_event_id")
    if await repository.get(run_id, tenant) is None:
        raise HTTPException(404, "run_not_found")

    async def stream():
        cursor = after
        heartbeat = 0
        while not await request.is_disconnected():
            # Read status first: terminal status and final event commit atomically.
            run = await repository.get(run_id, tenant)
            events = await repository.events(run_id, tenant, cursor)
            for event in events:
                cursor = event["seq"]
                payload = json.dumps(event["data"], ensure_ascii=False)
                yield f"id: {cursor}\nevent: {event['type']}\ndata: {payload}\n\n"
            if run["status"] in TERMINAL and cursor >= run["event_seq"]:
                return
            if len(events) == 200:
                continue
            heartbeat += 1
            if heartbeat >= 30:
                yield ": heartbeat\n\n"
                heartbeat = 0
            await asyncio.sleep(0.5)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
