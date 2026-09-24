from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request

from agent_platform.platform.identity.access import Admin, Reader
from agent_platform.platform.persistence.store import DomainError, audit, many, one, required

from .contracts import Invocation, Revision, Toggle
from .governance_contracts import (
    AccessKey,
    AlertRule,
    ModelSettingsUpdate,
    QuotaUpdate,
    ReviewTest,
    SensitiveWord,
    WordImport,
)
from .reporting import call_page, statistics

router = APIRouter(prefix="/model-gateway", tags=["gateway-management"])
Managed = Literal["sensitive_words", "access_keys", "alert_rules"]
Resource = Literal[
    "connections", "models", "profiles", "sensitive_words", "access_keys", "alert_rules"
]


def service(request):
    return request.app.state.runtime.platform.gateway.governance


@router.get("/catalog/{kind}")
async def catalog_page(
    kind: Literal["connections", "models", "profiles"],
    request: Request,
    user: Reader,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    q: str = Query("", max_length=200),
    enabled: bool | None = None,
    operation: str | None = None,
    connection_id: UUID | None = None,
):
    return await service(request).page(
        user.tenant, kind, page, page_size, q, enabled, operation, connection_id
    )


@router.get("/manage/{kind}")
async def managed_page(
    kind: Managed,
    request: Request,
    user: Admin,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    q: str = Query("", max_length=200),
    enabled: bool | None = None,
):
    return await service(request).page(user.tenant, kind, page, page_size, q, enabled)


def parse(kind, body):
    from fastapi.exceptions import RequestValidationError
    from pydantic import ValidationError

    try:
        return {
            "sensitive_words": SensitiveWord,
            "access_keys": AccessKey,
            "alert_rules": AlertRule,
        }[kind].model_validate(body)
    except ValidationError as exc:
        raise RequestValidationError(
            [
                {"loc": ["body", *error["loc"]], "msg": error["msg"], "type": error["type"]}
                for error in exc.errors(include_input=False, include_context=False)
            ]
        ) from None


@router.post("/manage/{kind}", status_code=201)
async def managed_create(kind: Managed, body: dict, request: Request, user: Admin):
    return await service(request).save(user.tenant, user.actor, kind, parse(kind, body))


@router.put("/manage/{kind}/{identifier}")
async def managed_update(
    kind: Managed,
    identifier: UUID,
    body: dict,
    request: Request,
    user: Admin,
    revision: int = Query(ge=1),
):
    return await service(request).save(
        user.tenant, user.actor, kind, parse(kind, body), identifier, revision
    )


@router.patch("/manage/{kind}/{identifier}")
async def managed_toggle(
    kind: Managed, identifier: UUID, body: Toggle, request: Request, user: Admin
):
    return await service(request).toggle(
        user.tenant, user.actor, kind, identifier, body.revision, body.enabled
    )


@router.delete("/resources/{kind}/{identifier}")
async def archive(
    kind: Resource, identifier: UUID, request: Request, user: Admin, revision: int = Query(ge=1)
):
    return await service(request).archive(user.tenant, user.actor, kind, identifier, revision)


@router.post("/keys/{identifier}/rotate")
async def rotate(identifier: UUID, body: Revision, request: Request, user: Admin):
    return await service(request).rotate(user.tenant, user.actor, identifier, body.revision)


@router.post("/words/import")
async def import_words(body: WordImport, request: Request, user: Admin):
    return await service(request).import_words(user.tenant, user.actor, body.words)


@router.post("/words/test")
async def test_words(body: ReviewTest, request: Request, user: Admin):
    matches = await service(request).review(user.tenant, body.text)
    return {
        "blocked": bool(matches),
        "matches": matches,
        "algorithm": "NFKC + casefold literal substring",
    }


@router.get("/models/{identifier}/settings")
async def model_settings(identifier: UUID, request: Request, user: Reader):
    return await service(request).model_settings(user.tenant, identifier)


@router.put("/models/{identifier}/settings")
async def save_model_settings(
    identifier: UUID, body: ModelSettingsUpdate, request: Request, user: Admin
):
    return await service(request).settings_save(user.tenant, user.actor, body, identifier)


@router.get("/quota")
async def quota(request: Request, user: Reader):
    return await service(request).quota(user.tenant)


@router.put("/quota")
async def save_quota(body: QuotaUpdate, request: Request, user: Admin):
    return await service(request).settings_save(user.tenant, user.actor, body)


@router.get("/call-records")
async def records(
    request: Request,
    user: Reader,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    q: str = Query("", max_length=200),
    status: Literal["completed", "failed", "running", "cancelled"] | None = None,
    operation: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    profile_id: UUID | None = None,
    access_key_id: UUID | None = None,
):
    return await call_page(
        service(request).engine,
        user.tenant,
        page,
        page_size,
        q,
        status,
        operation,
        since,
        until,
        profile_id,
        access_key_id,
    )


@router.get("/statistics")
async def stats(
    request: Request, user: Reader, since: datetime | None = None, until: datetime | None = None
):
    return await statistics(service(request).engine, user.tenant, since, until)


@router.get("/alert-events")
async def events(
    request: Request,
    user: Reader,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    acknowledged: bool | None = None,
):
    where = "tenant_id=:t"
    params = {"t": user.tenant, "limit": page_size, "offset": (page - 1) * page_size}
    if acknowledged is not None:
        where += " AND acknowledged=:ack"
        params["ack"] = acknowledged
    async with service(request).engine.connect() as c:
        count = (
            await one(c, f"SELECT count(*) n FROM gateway_alert_events WHERE {where}", **params)
        )["n"]
        rows = await many(
            c,
            f"SELECT * FROM gateway_alert_events WHERE {where} ORDER BY created_at DESC,id LIMIT :limit OFFSET :offset",
            **params,
        )
    return {"items": rows, "total": count, "page": page, "page_size": page_size}


@router.post("/alert-events/{identifier}/acknowledge")
async def acknowledge(identifier: UUID, request: Request, user: Admin):
    async with service(request).engine.begin() as c:
        row = required(
            await one(
                c,
                "UPDATE gateway_alert_events SET acknowledged=true,acknowledged_by=:actor,acknowledged_at=COALESCE(acknowledged_at,now()) WHERE tenant_id=:t AND id=:id RETURNING *",
                actor=user.actor,
                t=user.tenant,
                id=identifier,
            )
        )
        await audit(c, user.tenant, user.actor, "gateway.alert.acknowledged", identifier)
    return row


@router.post("/external/invoke")
async def external_invoke(
    body: Invocation, request: Request, authorization: Annotated[str | None, Header()] = None
):
    if not authorization or not authorization.startswith("Bearer "):
        raise DomainError("gateway_key_required", 401)
    manager = service(request)
    key = await manager.authenticate(authorization[7:])
    if str(body.profile_id) not in key["spec"]["profile_ids"]:
        raise DomainError("gateway_key_profile_forbidden", 403)
    return await manager.gateway.invoke(
        key["tenant_id"],
        body.profile_id,
        body.version,
        body.payload,
        actor="key:" + str(key["id"]),
        access_key=key,
    )
