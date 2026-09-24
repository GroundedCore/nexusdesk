from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.routing import APIRoute
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_platform.platform.identity.access import Admin, Reader
from agent_platform.platform.persistence.store import DomainError, audit, many, one, required

from .openapi import export_document, preview
from .service import HttpTool, ToolGateway
from .workspace import ApiInput, CollectionInput, StateInput


class WorkspaceRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def checked(request):
            try:
                return await handler(request)
            except ValidationError:
                raise DomainError("invalid_tool_configuration", 422) from None

        return checked


router = APIRouter(prefix="/tool-workspace", tags=["tool-workspace"], route_class=WorkspaceRoute)


def workspace(request):
    return request.app.state.runtime.platform.tool_workspace


class PublishInput(BaseModel):
    revision: int = Field(ge=1)
    collection_revision: int = Field(ge=1)


class CopyInput(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class ImportPreview(BaseModel):
    content: str = Field(max_length=1_000_000)


class ImportInput(BaseModel):
    collection: CollectionInput | None = None
    collection_id: UUID | None = None
    apis: list[ApiInput] = Field(min_length=1, max_length=100)


class TestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = Field(pattern="^(simulation|live)$")
    arguments: dict
    confirmed: bool = False
    response: dict | list | str | int | float | bool | None = None
    revision: int
    collection_revision: int


@router.get("/collections")
async def catalog(
    request: Request,
    user: Reader,
    q: str = "",
    archived: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=100),
):
    return await workspace(request).catalog(user.tenant, q, archived, page, page_size)


@router.post("/collections", status_code=201)
async def create(body: CollectionInput, request: Request, user: Admin):
    return await workspace(request).save_group(user.tenant, user.actor, body)


@router.put("/collections/{gid}")
async def update(gid: UUID, body: CollectionInput, request: Request, user: Admin):
    return await workspace(request).save_group(user.tenant, user.actor, body, gid)


@router.get("/collections/{gid}")
async def detail(
    gid: UUID,
    request: Request,
    user: Reader,
    q: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    include_archived: bool = False,
):
    return await workspace(request).detail(user.tenant, gid, q, page, page_size, include_archived)


@router.patch("/collections/{gid}")
async def state(gid: UUID, body: StateInput, request: Request, user: Admin):
    return await workspace(request).state(user.tenant, user.actor, gid, body)


@router.delete("/collections/{gid}")
async def delete(gid: UUID, request: Request, user: Admin, revision: int = Query(ge=1)):
    return await workspace(request).delete(user.tenant, user.actor, gid, revision)


@router.post("/collections/{gid}/copy", status_code=201)
async def copy(gid: UUID, body: CopyInput, request: Request, user: Admin):
    return await workspace(request).copy(user.tenant, user.actor, gid, body.name)


@router.get("/collections/{gid}/references")
async def references(gid: UUID, request: Request, user: Reader, tool: UUID | None = None):
    w = workspace(request)
    async with w.engine.connect() as c:
        await w.group(c, user.tenant, gid)
        return await w.references(c, user.tenant, gid, tool)


@router.post("/collections/{gid}/apis", status_code=201)
async def add_api(gid: UUID, body: ApiInput, request: Request, user: Admin):
    return await workspace(request).save_api(user.tenant, user.actor, gid, body)


@router.put("/collections/{gid}/apis/{tid}")
async def edit_api(gid: UUID, tid: UUID, body: ApiInput, request: Request, user: Admin):
    return await workspace(request).save_api(user.tenant, user.actor, gid, body, tid)


@router.patch("/collections/{gid}/apis/{tid}")
async def api_state(gid: UUID, tid: UUID, body: StateInput, request: Request, user: Admin):
    return await workspace(request).state(user.tenant, user.actor, gid, body, tid)


@router.delete("/collections/{gid}/apis/{tid}")
async def delete_api(
    gid: UUID, tid: UUID, request: Request, user: Admin, revision: int = Query(ge=1)
):
    return await workspace(request).delete(user.tenant, user.actor, gid, revision, tid)


@router.get("/apis/{tid}/preview")
async def publish_preview(tid: UUID, request: Request, user: Reader):
    return await workspace(request).preview(user.tenant, tid)


@router.post("/apis/{tid}/publish")
async def publish(tid: UUID, body: PublishInput, request: Request, user: Admin):
    return await workspace(request).publish(
        user.tenant, user.actor, tid, body.revision, body.collection_revision
    )


@router.get("/collections/{gid}/versions")
async def history(
    gid: UUID,
    request: Request,
    user: Reader,
    tool: UUID | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    return await workspace(request).history(user.tenant, gid, tool, page, page_size)


@router.get("/collections/{gid}/calls")
async def calls(
    gid: UUID,
    request: Request,
    user: Reader,
    name: str = "",
    status: str = "",
    since: datetime | None = None,
    until: datetime | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    return await workspace(request).calls(
        user.tenant, gid, name, status, since, until, page, page_size
    )


@router.post("/import/preview")
async def import_preview(body: ImportPreview, user: Admin):
    return preview(body.content)


@router.post("/import", status_code=201)
async def import_apis(body: ImportInput, request: Request, user: Admin):
    w = workspace(request)
    if bool(body.collection) == bool(body.collection_id):
        raise DomainError("select_import_collection", 422)
    async with w.engine.begin() as c:
        group = (
            await w.save_group(user.tenant, user.actor, body.collection, connection=c)
            if body.collection
            else await w.group(c, user.tenant, body.collection_id, True)
        )
        for item in body.apis:
            await w.save_api(user.tenant, user.actor, group["id"], item, connection=c)
        return {"collection_id": group["id"], "imported": len(body.apis)}


@router.get("/collections/{gid}/export")
async def export(gid: UUID, request: Request, user: Admin):
    w = workspace(request)
    async with w.engine.connect() as c:
        group = await w.group(c, user.tenant, gid)
        rows = await many(
            c,
            "SELECT * FROM registered_tools WHERE tenant_id=:t AND collection_id=:g AND NOT archived ORDER BY name",
            t=user.tenant,
            g=gid,
        )
        return export_document(group, rows)


@router.post("/apis/{tid}/test")
async def test(tid: UUID, body: TestInput, request: Request, user: Admin):
    w = workspace(request)
    async with w.engine.begin() as c:
        row = required(
            await one(
                c,
                "SELECT * FROM registered_tools WHERE tenant_id=:t AND id=:id",
                t=user.tenant,
                id=tid,
            )
        )
        group = await w.group(c, user.tenant, row["collection_id"])
        if (
            row["revision"] != body.revision
            or group["revision"] != body.collection_revision
            or row["archived"]
            or group["archived"]
            or not row["enabled"]
            or not group["enabled"]
        ):
            raise DomainError("tool_revision_conflict_or_disabled", 409)
        spec = w.composed(group, row)
        w.validate(spec)
        if not Draft202012Validator(spec.parameters).is_valid(body.arguments):
            raise DomainError("invalid_arguments", 422)
        if body.mode == "simulation":
            if spec.response_schema and not Draft202012Validator(spec.response_schema).is_valid(
                body.response
            ):
                raise DomainError("invalid_output_schema", 422)
            return {"ok": True, "data": body.response, "simulation": True, "upstream_called": False}
        if spec.method == "POST" and not body.confirmed:
            raise DomainError("post_test_confirmation_required", 422)
        await audit(c, user.tenant, user.actor, "tool.live_test", tid, method=spec.method)

    async def credentials(current):
        source = (
            HttpTool.model_validate(group["spec"])
            if row["workspace_managed"] and row["auth_mode"] == "inherit"
            else current
        )
        return await w.registry.credential_headers(user.tenant, source)

    gateway = ToolGateway(w.client, [spec], credential_resolver=credentials)
    # Share the same process limit across all workbench tests.
    gateway.limit = w.test_limit
    return {
        **await gateway.execute(spec.name, body.arguments),
        "simulation": False,
        "upstream_called": True,
    }
