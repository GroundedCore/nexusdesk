from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field

from agent_platform.modules.agent_config.service import AgentDraft
from agent_platform.modules.channel.service import IncomingMessage
from agent_platform.modules.evaluation.scoring import ProfileRef, ScoringOptions
from agent_platform.modules.evaluation.service import EvaluationCase
from agent_platform.modules.knowledge.chunking import ChunkingPolicy, chunk_document
from agent_platform.modules.knowledge.service import DocumentInput
from agent_platform.modules.tool_gateway.service import HttpTool, ToolInput
from agent_platform.platform.identity.access import Admin, Operator, Reader
from agent_platform.platform.persistence.store import DomainError, many

router = APIRouter(tags=["platform"])
ingress = APIRouter(tags=["channel-ingress"])


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NameInput(Input):
    name: str = Field(min_length=1, max_length=100)


class RevisionInput(Input):
    revision: int = Field(ge=1)


class DraftUpdate(AgentDraft):
    revision: int = Field(ge=1)


class VersionInput(Input):
    version: int = Field(ge=1)


class EnabledInput(Input):
    enabled: bool


class ArchiveInput(RevisionInput):
    archived: bool


class ToolDraftInput(ToolInput):
    revision: int = Field(ge=1)


class ToolSimulation(Input):
    definition: HttpTool
    arguments: dict
    response: dict | list | str | int | float | bool | None = None


class ChunkPreview(Input):
    content: str = Field(min_length=1, max_length=200000)
    policy: ChunkingPolicy = Field(default_factory=ChunkingPolicy)


class ConversationInput(Input):
    external_id: str = Field(min_length=1, max_length=128)
    agent_id: UUID | None = None
    source: Literal["business", "playground"] = "business"


class MessageInput(Input):
    content: str = Field(min_length=1, max_length=8000)


class HandoffInput(Input):
    reason: str = Field(min_length=1, max_length=1000)


class HandoffTransition(Input):
    operation: Literal["claim", "resume", "close"]
    assignee: str | None = Field(default=None, max_length=100)
    revision: int | None = Field(default=None, ge=1)


class ConversationTransition(RevisionInput):
    operation: Literal["close", "reopen"]


class StaffInput(NameInput):
    role: Literal["operator", "viewer"] = "operator"


class DecisionInput(Input):
    approve: bool


class TicketUpdate(Input):
    status: Literal["open", "in_progress", "waiting_customer", "resolved", "closed"]
    note: str = Field(default="", max_length=8000)
    revision: int = Field(ge=1)


class ChannelInput(NameInput):
    agent_id: UUID
    signing_secret_ref: str | None = Field(
        default=None, pattern=r"^AGENT_CHANNEL_SECRET_[A-Z0-9_]+$"
    )


def services(request):
    return request.app.state.runtime.platform


@router.get("/me")
async def me(user: Reader):
    return {"tenant": user.tenant, "role": user.role, "actor": user.actor}


@router.get("/staff")
async def staff(request: Request, user: Admin):
    return await services(request).staff.list(user.tenant)


@router.post("/staff", status_code=201)
async def create_staff(body: StaffInput, request: Request, user: Admin):
    return await services(request).staff.create(user.tenant, user.actor, body.name, body.role)


@router.patch("/staff/{sid}")
async def enable_staff(sid: UUID, body: EnabledInput, request: Request, user: Admin):
    return await services(request).staff.enable(user.tenant, user.actor, sid, body.enabled)


@router.post("/conversations/{cid}/transition")
async def conversation_transition(
    cid: UUID, body: ConversationTransition, request: Request, user: Operator
):
    return await services(request).conversations.transition(
        user.tenant, user.actor, cid, body.operation, body.revision
    )


@router.get("/agents")
async def agents(
    request: Request,
    user: Reader,
    q: str = Query(default="", max_length=100),
    include_archived: bool = False,
):
    return await services(request).agents.list(user.tenant, q, include_archived)


@router.get("/agents/catalog")
async def agent_catalog(
    request: Request,
    user: Reader,
    q: str = Query(default="", max_length=100),
    include_archived: bool = False,
    status: Literal["all", "published", "unpublished"] = "all",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=12, ge=1, le=100),
    industry: Annotated[list[str] | None, Query(max_length=20)] = None,
    tag: Annotated[list[str] | None, Query(max_length=10)] = None,
    examples_only: bool = False,
    mine_only: bool = False,
):
    return await services(request).agents.catalog(
        user.tenant,
        q,
        include_archived,
        status,
        page,
        page_size,
        industry,
        tag,
        examples_only,
        created_by=user.actor if mine_only else None,
    )


@router.get("/agents/{aid}")
async def agent_detail(aid: UUID, request: Request, user: Reader):
    return await services(request).agents.get(user.tenant, aid)


@router.get("/agents/{aid}/conversations")
async def agent_conversations(
    aid: UUID,
    request: Request,
    user: Reader,
    source: Literal["all", "business", "playground"] = "all",
    q: str = Query(default="", max_length=128),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=12, ge=1, le=100),
):
    return await services(request).conversations.agent_records(
        user.tenant, aid, source, q, page, page_size
    )


@router.delete("/agents/{aid}")
async def delete_agent(aid: UUID, request: Request, user: Admin, revision: int = Query(ge=1)):
    return await services(request).agents.delete(user.tenant, user.actor, aid, revision)


@router.patch("/agents/{aid}/archive")
async def archive_agent(aid: UUID, body: ArchiveInput, request: Request, user: Admin):
    return await services(request).agents.archive(
        user.tenant, user.actor, aid, body.archived, body.revision
    )


@router.get("/agents/{aid}/diff")
async def agent_diff(aid: UUID, request: Request, user: Reader, version: int = Query(ge=1)):
    return await services(request).agents.diff(user.tenant, aid, version)


@router.post("/agents", status_code=201)
async def create_agent(body: AgentDraft, request: Request, user: Admin):
    return await services(request).agents.create(user.tenant, user.actor, body)


@router.put("/agents/{aid}")
async def update_agent(aid: UUID, body: DraftUpdate, request: Request, user: Admin):
    return await services(request).agents.update(user.tenant, user.actor, aid, body, body.revision)


@router.get("/agents/{aid}/versions")
async def versions(aid: UUID, request: Request, user: Reader):
    return await services(request).agents.versions(user.tenant, aid)


@router.post("/agents/{aid}/publish")
async def publish(aid: UUID, body: RevisionInput, request: Request, user: Admin):
    return await services(request).agents.publish(user.tenant, user.actor, aid, body.revision)


@router.post("/agents/{aid}/rollback")
async def rollback(aid: UUID, body: VersionInput, request: Request, user: Admin):
    return await services(request).agents.rollback(user.tenant, user.actor, aid, body.version)


@router.get("/tools")
async def tools(request: Request, user: Reader):
    return await services(request).tools.list(user.tenant)


@router.post("/tools", status_code=201)
async def create_tool(body: ToolInput, request: Request, user: Admin):
    return await services(request).tools.create(user.tenant, user.actor, body)


@router.patch("/tools/{tid}")
async def enable_tool(tid: UUID, body: EnabledInput, request: Request, user: Admin):
    return await services(request).tools.toggle(user.tenant, user.actor, tid, body.enabled)


@router.put("/tools/{tid}")
async def update_tool(tid: UUID, body: ToolDraftInput, request: Request, user: Admin):
    spec = ToolInput.model_validate(body.model_dump(exclude={"revision"}))
    return await services(request).tools.update(user.tenant, user.actor, tid, spec, body.revision)


@router.get("/tools/{tid}/versions")
async def tool_versions(tid: UUID, request: Request, user: Reader):
    return await services(request).tools.versions(user.tenant, tid)


@router.post("/tools/{tid}/publish")
async def publish_tool(tid: UUID, body: RevisionInput, request: Request, user: Admin):
    return await services(request).tools.publish(user.tenant, user.actor, tid, body.revision)


@router.post("/tool-simulations")
async def simulate_tool(body: ToolSimulation, user: Admin):
    if not Draft202012Validator(body.definition.parameters).is_valid(body.arguments):
        raise DomainError("invalid_arguments")
    if body.definition.response_schema is not None and not Draft202012Validator(
        body.definition.response_schema
    ).is_valid(body.response):
        raise DomainError("invalid_output_schema")
    return {
        "ok": True,
        "data": body.response,
        "execution_status": "succeeded",
        "simulation": True,
        "upstream_called": False,
    }


@router.get("/tool-calls")
async def tool_calls(request: Request, user: Reader, before: UUID | None = None):
    async with services(request).engine.connect() as c:
        return await many(
            c,
            "SELECT * FROM tool_calls WHERE tenant_id=:t AND (CAST(:before AS uuid) IS NULL OR (created_at,id) < (SELECT created_at,id FROM tool_calls WHERE id=:before AND tenant_id=:t)) ORDER BY created_at DESC,id DESC LIMIT 100",
            t=user.tenant,
            before=before,
        )


@router.get("/knowledge-bases")
async def bases(request: Request, user: Reader):
    return await services(request).knowledge.bases(user.tenant)


@router.post("/knowledge-bases", status_code=201)
async def create_base(body: NameInput, request: Request, user: Admin):
    return await services(request).knowledge.create_base(user.tenant, user.actor, body.name)


@router.get("/knowledge-bases/{kid}/documents")
async def documents(kid: UUID, request: Request, user: Reader):
    return await services(request).knowledge.documents(user.tenant, kid)


@router.post("/knowledge/chunk-preview")
async def chunk_preview(body: ChunkPreview, user: Admin):
    return {"chunks": chunk_document(body.content, body.policy), "policy": body.policy.model_dump()}


@router.post("/knowledge-bases/{kid}/uploads", status_code=202)
async def knowledge_upload(
    kid: UUID, request: Request, user: Admin, filename: str = Query(min_length=1, max_length=255)
):
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > services(request).settings.knowledge_upload_bytes:
            raise DomainError("document_size_exceeded", 413)
    return await services(request).ingestion.create(
        user.tenant, user.actor, kid, filename, bytes(content)
    )


@router.get("/knowledge-bases/{kid}/jobs")
async def knowledge_jobs(kid: UUID, request: Request, user: Reader):
    return await services(request).ingestion.list(user.tenant, kid)


@router.post("/knowledge-jobs/{jid}/cancel")
async def cancel_knowledge(jid: UUID, request: Request, user: Admin):
    return await services(request).ingestion.cancel(user.tenant, user.actor, jid)


@router.post("/knowledge-jobs/{jid}/retry")
async def retry_knowledge(jid: UUID, request: Request, user: Admin):
    return await services(request).ingestion.retry(user.tenant, user.actor, jid)


@router.get("/knowledge-jobs/{jid}/pages")
async def knowledge_pages(jid: UUID, request: Request, user: Reader):
    return await services(request).ingestion.pages(user.tenant, jid)


@router.post("/knowledge-bases/{kid}/vector-index")
async def build_vector_index(kid: UUID, body: ProfileRef, request: Request, user: Admin):
    return await services(request).vector_index.build(user.tenant, user.actor, kid, body)


@router.get("/knowledge-bases/{kid}/vector-search")
async def vector_search(
    kid: UUID, request: Request, user: Reader, q: str = Query(min_length=1, max_length=500)
):
    return await services(request).vector_index.search(user.tenant, kid, q)


@router.post("/knowledge-bases/{kid}/documents", status_code=201)
async def create_document(kid: UUID, body: DocumentInput, request: Request, user: Admin):
    return await services(request).knowledge.save_document(user.tenant, user.actor, kid, body)


@router.put("/knowledge-bases/{kid}/documents/{did}")
async def update_document(
    kid: UUID,
    did: UUID,
    body: DocumentInput,
    request: Request,
    user: Admin,
    version: Annotated[int, Query(ge=1)],
):
    return await services(request).knowledge.save_document(
        user.tenant, user.actor, kid, body, did, version
    )


@router.patch("/documents/{did}")
async def enable_document(did: UUID, body: EnabledInput, request: Request, user: Admin):
    return await services(request).knowledge.toggle(user.tenant, user.actor, did, body.enabled)


@router.get("/knowledge/search")
async def search(
    request: Request,
    user: Reader,
    q: Annotated[str, Query(min_length=1, max_length=500)],
    kb: UUID | None = None,
):
    return await services(request).knowledge.search(user.tenant, q, [kb] if kb else None)


@router.get("/conversations")
async def conversations(request: Request, user: Reader, offset: Annotated[int, Query(ge=0)] = 0):
    return await services(request).conversations.list(user.tenant, offset=offset)


@router.post("/conversations", status_code=201)
async def create_conversation(body: ConversationInput, request: Request, user: Operator):
    return await services(request).conversations.create(
        user.tenant, user.actor, body.external_id, body.agent_id, source=body.source
    )


@router.get("/conversations/{cid}")
async def conversation(
    cid: UUID, request: Request, user: Reader, after: Annotated[int, Query(ge=0)] = 0
):
    return await services(request).conversations.detail(user.tenant, cid, after)


@router.get("/conversations/{cid}/history")
async def conversation_history(
    cid: UUID,
    request: Request,
    user: Reader,
    before: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
):
    return await services(request).conversations.history(user.tenant, cid, before, limit)


@router.post("/conversations/{cid}/messages", status_code=202)
async def message(cid: UUID, body: MessageInput, request: Request, user: Operator):
    return await services(request).customer.send(user.tenant, user.actor, cid, body.content)


@router.post("/conversations/{cid}/human-replies")
async def human_reply(cid: UUID, body: MessageInput, request: Request, user: Operator):
    return await services(request).conversations.human_reply(
        user.tenant, user.actor, cid, body.content, administrator=user.role == "admin"
    )


@router.post("/conversations/{cid}/handoffs")
async def request_handoff(cid: UUID, body: HandoffInput, request: Request, user: Operator):
    return await services(request).handoffs.request(user.tenant, user.actor, cid, body.reason)


@router.get("/handoffs")
async def handoffs(request: Request, user: Reader):
    return await services(request).handoffs.list(user.tenant)


@router.post("/handoffs/{hid}/transition")
async def transition(hid: UUID, body: HandoffTransition, request: Request, user: Operator):
    return await services(request).handoffs.transition(
        user.tenant,
        user.actor,
        hid,
        body.operation,
        body.assignee,
        body.revision,
        administrator=user.role == "admin",
    )


@router.post("/actions/{aid}/decision")
async def decision(aid: UUID, body: DecisionInput, request: Request, user: Operator):
    return await services(request).customer.decide(user.tenant, user.actor, aid, body.approve)


@router.get("/tickets")
async def tickets(request: Request, user: Reader):
    return await services(request).customer.tickets(user.tenant)


@router.patch("/tickets/{tid}")
async def ticket(tid: UUID, body: TicketUpdate, request: Request, user: Operator):
    return await services(request).customer.update_ticket(
        user.tenant, user.actor, tid, body.status, body.note, body.revision
    )


@router.get("/channels")
async def channels(request: Request, user: Reader):
    return await services(request).channels.list(user.tenant)


@router.post("/channels", status_code=201)
async def channel(body: ChannelInput, request: Request, user: Admin):
    return await services(request).channels.create(
        user.tenant, user.actor, body.name, body.agent_id, body.signing_secret_ref
    )


@router.patch("/channels/{cid}")
async def toggle_channel(cid: UUID, body: EnabledInput, request: Request, user: Admin):
    return await services(request).channels.toggle(user.tenant, user.actor, cid, body.enabled)


@router.post("/channels/{cid}/rotate-token")
async def rotate_channel(cid: UUID, request: Request, user: Admin):
    return await services(request).channels.rotate(user.tenant, user.actor, cid)


@ingress.post("/ingress/channels/{cid}/messages", status_code=202)
async def ingest(
    cid: UUID,
    body: IncomingMessage,
    request: Request,
    x_channel_token: Annotated[str, Header()],
    x_channel_timestamp: Annotated[str | None, Header()] = None,
    x_channel_signature: Annotated[str | None, Header()] = None,
):
    await services(request).channels.verify_signature(
        cid, await request.body(), x_channel_timestamp, x_channel_signature
    )
    return await services(request).channels.ingest(cid, x_channel_token, body)


@ingress.get("/ingress/channels/{cid}/replies")
async def channel_replies(
    cid: UUID,
    request: Request,
    x_channel_token: Annotated[str, Header()],
    session_id: Annotated[str, Query(min_length=1, max_length=128)],
    after: Annotated[int, Query(ge=0)] = 0,
):
    return await services(request).channels.replies(cid, x_channel_token, session_id, after)


@router.get("/observability/summary")
async def summary(request: Request, user: Reader):
    return await services(request).observability.summary(user.tenant)


@router.get("/observability/gateways")
async def gateway_metrics(request: Request, user: Reader):
    return await services(request).observability.gateways(user.tenant)


@router.get("/observability/runs/{rid}/trace")
async def unified_trace(rid: UUID, request: Request, user: Reader):
    return await services(request).observability.trace(user.tenant, rid)


@router.get("/observability/runs")
async def runs(request: Request, user: Reader):
    return await services(request).observability.runs(user.tenant)


@router.get("/audit")
async def audit(request: Request, user: Admin):
    return await services(request).observability.audit(user.tenant)


@router.get("/agents/{aid}/evaluation-cases")
async def cases(aid: UUID, request: Request, user: Reader):
    return await services(request).evaluation.cases(user.tenant, aid)


@router.post("/agents/{aid}/evaluation-cases", status_code=201)
async def create_case(aid: UUID, body: EvaluationCase, request: Request, user: Admin):
    return await services(request).evaluation.create(user.tenant, user.actor, aid, body)


@router.get("/agents/{aid}/evaluation-reports")
async def reports(aid: UUID, request: Request, user: Reader):
    return await services(request).evaluation.reports(user.tenant, aid)


@router.post("/agents/{aid}/evaluate")
async def evaluate(aid: UUID, request: Request, user: Admin, body: ScoringOptions | None = None):
    return await services(request).evaluation.run(user.tenant, user.actor, aid, body)


@router.get("/policy")
async def effective_policy(request: Request, user: Reader):
    settings = request.app.state.settings
    return {
        key: getattr(settings, key)
        for key in [
            "model_backend",
            "model_name",
            "max_model_rounds",
            "max_tool_calls",
            "worker_concurrency",
            "tool_allowed_hosts",
        ]
    }
