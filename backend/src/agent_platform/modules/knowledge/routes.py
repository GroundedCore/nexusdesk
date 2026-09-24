from typing import Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from agent_platform.platform.identity.access import Admin, Reader
from agent_platform.platform.persistence.store import DomainError, audit, execute

from .chunking import ChunkingPolicy
from .parsers import LOCAL_FORMATS
from .retrieval import LibrarySettings, SearchInput
from .workspace import DraftInput

router = APIRouter(prefix="/knowledge-workspace", tags=["knowledge-workspace"])


def services(request):
    return request.app.state.runtime.platform


class FolderInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    parent_id: UUID | None = None


class LinkInput(BaseModel):
    document_id: UUID


class SettingsInput(BaseModel):
    revision: int = Field(ge=1)
    settings: LibrarySettings


class MoveInput(BaseModel):
    folder_id: UUID | None = None
    enabled: bool | None = None


@router.get("/capabilities")
async def capabilities(request: Request, user: Reader):
    s = services(request).settings
    return {
        "local_formats": sorted(LOCAL_FORMATS),
        "external_parser": bool(s.knowledge_parser_url),
        "max_bytes": s.knowledge_upload_bytes,
        "max_extracted_chars": s.knowledge_extracted_chars,
        "max_document_chunks": 10000,
        "max_files": 10,
        "milvus": bool(s.milvus_url),
        "object_storage": bool(s.knowledge_s3_bucket),
    }


@router.get("/folders")
async def folders(request: Request, user: Reader):
    return await services(request).knowledge_workspace.folders(user.tenant)


@router.post("/folders", status_code=201)
async def create_folder(body: FolderInput, request: Request, user: Admin):
    return await services(request).knowledge_workspace.create_folder(
        user.tenant, user.actor, body.name, body.parent_id
    )


@router.get("/documents")
async def documents(
    request: Request,
    user: Reader,
    q: str = "",
    folder: UUID | None = None,
    offset: int = Query(default=0, ge=0),
    kind: str | None = None,
    status: str | None = None,
):
    return await services(request).knowledge_workspace.list_documents(
        user.tenant, q, folder, offset, kind, status
    )


@router.post("/uploads", status_code=202)
async def upload(
    request: Request,
    user: Admin,
    filename: str = Query(min_length=1, max_length=200),
    folder: UUID | None = None,
    mode: Literal["manual", "automatic"] = "manual",
    kb: UUID | None = None,
):
    content = bytearray()
    async for block in request.stream():
        content.extend(block)
        if len(content) > services(request).settings.knowledge_upload_bytes:
            raise DomainError("document_size_exceeded", 413)
    return await services(request).knowledge_workspace.upload(
        user.tenant, user.actor, filename, bytes(content), folder, mode, kb
    )


@router.get("/documents/{did}")
async def detail(
    did: UUID,
    request: Request,
    user: Reader,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    revision: int | None = Query(None, ge=1),
):
    return await services(request).knowledge_workspace.detail(
        user.tenant, did, offset, limit, revision
    )


@router.get("/documents/{did}/source")
async def source(
    did: UUID,
    request: Request,
    user: Reader,
    offset: int = Query(0, ge=0),
    revision: int | None = Query(None, ge=1),
):
    return await services(request).knowledge_workspace.source(user.tenant, did, offset, revision)


@router.post("/documents/{did}/replace", status_code=202)
async def replace_file(
    did: UUID, request: Request, user: Admin, filename: str = Query(min_length=1, max_length=200)
):
    content = bytearray()
    async for block in request.stream():
        content.extend(block)
        if len(content) > services(request).settings.knowledge_upload_bytes:
            raise DomainError("document_size_exceeded", 413)
    return await services(request).knowledge_workspace.replace_file(
        user.tenant, user.actor, did, filename, bytes(content)
    )


@router.get("/documents/{did}/download")
async def download(did: UUID, request: Request, user: Reader):
    w = services(request).knowledge_workspace
    async with w.engine.connect() as c:
        doc = await w.document(c, user.tenant, did)
        if doc["original"] is None:
            raise DomainError("original_file_unavailable", 404)
    return Response(
        bytes(doc["original"]),
        media_type="application/octet-stream",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(doc["filename"])},
    )


@router.post("/documents/{did}/preview")
async def preview(
    did: UUID,
    body: ChunkingPolicy,
    request: Request,
    user: Reader,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    revision: int | None = Query(None, ge=1),
):
    if body.strategy == "semantic" and user.role != "admin":
        raise DomainError("insufficient_role", 403)
    return await services(request).knowledge_workspace.preview(
        user.tenant, did, body, offset, limit, revision
    )


@router.put("/documents/{did}/draft")
async def save_draft(did: UUID, body: DraftInput, request: Request, user: Admin):
    return await services(request).knowledge_workspace.save_draft(
        user.tenant, user.actor, did, body
    )


@router.patch("/documents/{did}")
async def move(did: UUID, body: MoveInput, request: Request, user: Admin):
    return await services(request).knowledge_workspace.mutate_document(
        user.tenant, user.actor, did, body.folder_id, body.enabled
    )


@router.delete("/documents/{did}")
async def delete(did: UUID, request: Request, user: Admin):
    return await services(request).knowledge_workspace.mutate_document(
        user.tenant, user.actor, did, delete=True
    )


@router.get("/libraries/{kid}")
async def library(kid: UUID, request: Request, user: Reader):
    return await services(request).knowledge_workspace.library(user.tenant, kid)


@router.post("/libraries/{kid}/documents")
async def link(kid: UUID, body: LinkInput, request: Request, user: Admin):
    return await services(request).knowledge_workspace.link(
        user.tenant, user.actor, kid, body.document_id
    )


@router.delete("/libraries/{kid}/documents/{did}")
async def unlink(kid: UUID, did: UUID, request: Request, user: Admin):
    w = services(request).knowledge_workspace
    async with w.engine.begin() as c:
        await w.base(c, user.tenant, kid, True)
        await execute(
            c, "DELETE FROM knowledge_links WHERE kb_id=:k AND document_id=:d", k=kid, d=did
        )
        await execute(c, "UPDATE knowledge_bases SET revision=revision+1 WHERE id=:k", k=kid)
        await audit(c, user.tenant, user.actor, "knowledge.document_unlinked", did, kb_id=str(kid))
    return {"ok": True}


@router.put("/libraries/{kid}/settings")
async def save_settings(kid: UUID, body: SettingsInput, request: Request, user: Admin):
    w = services(request).knowledge_workspace
    chunking = body.settings.chunking
    if chunking.strategy == "semantic":
        snapshot = await w.gateway.catalog.resolve(
            user.tenant, chunking.semantic_profile_id, chunking.semantic_profile_version
        )
        if snapshot["spec"]["operation"] != "embed":
            raise DomainError("semantic_embedding_profile_required", 422)
    if body.settings.mode != "keyword" and not body.settings.embedding:
        raise DomainError("embedding_profile_required")
    for ref, operation in [(body.settings.embedding, "embed"), (body.settings.rerank, "rerank")]:
        if ref:
            snapshot = await w.gateway.catalog.resolve(user.tenant, ref.id, ref.version)
            if snapshot["spec"]["operation"] != operation:
                raise DomainError("invalid_knowledge_model_operation")
    async with w.engine.begin() as c:
        base = await w.base(c, user.tenant, kid, True)
        if base["revision"] != body.revision:
            raise DomainError("knowledge_settings_conflict", 409)
        await execute(
            c,
            "UPDATE knowledge_bases SET settings=CAST(:s AS jsonb),revision=revision+1 WHERE id=:k",
            k=kid,
            s=body.settings.model_dump_json(),
        )
        await audit(c, user.tenant, user.actor, "knowledge.settings_changed", kid)
    return {"revision": body.revision + 1}


@router.post("/libraries/{kid}/publish", status_code=202)
async def publish(kid: UUID, request: Request, user: Admin):
    return await services(request).knowledge_workspace.publish(user.tenant, user.actor, kid)


@router.post("/search")
async def search(body: SearchInput, request: Request, user: Reader):
    return await services(request).retrieval.search(user.tenant, body)


@router.get("/tasks")
async def tasks(request: Request, user: Reader):
    return await services(request).knowledge_workspace.tasks(user.tenant)


@router.post("/tasks/{tid}/{action}")
async def task_action(tid: UUID, action: Literal["retry", "cancel"], request: Request, user: Admin):
    return await services(request).knowledge_workspace.task_action(
        user.tenant, user.actor, tid, action
    )


@router.post("/documents/{did}/preview-tasks")
async def start_preview_task(
    did: UUID, body: ChunkingPolicy, request: Request, user: Admin, revision: int = Query(..., ge=1)
):
    return await services(request).knowledge_workspace.start_preview(
        user.tenant, user.actor, did, body, revision
    )


@router.get("/documents/{did}/tasks")
async def document_tasks(did: UUID, request: Request, user: Reader):
    return await services(request).knowledge_workspace.tasks(user.tenant, did)


@router.get("/tasks/{tid}/result")
async def preview_task_result(
    tid: UUID,
    request: Request,
    user: Reader,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    return await services(request).knowledge_workspace.preview_result(
        user.tenant, tid, offset, limit
    )
