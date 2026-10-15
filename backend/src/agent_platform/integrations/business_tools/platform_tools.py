import asyncio
import json
import time
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_platform.modules.customer_service.service import TicketProposal
from agent_platform.platform.persistence.store import DomainError, execute


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=500)


class MemorySearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=500)
    # The deployment-level memory_search_limit_max clamps this further.
    limit: int = Field(default=5, ge=1, le=50)


class TicketLookup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticket_id: UUID


BUILTIN_SCHEMAS = {
    "ticket_lookup": {
        "type": "function",
        "function": {
            "name": "ticket_lookup",
            "description": "查询当前会话内已创建工单的实际状态，不执行办理。",
            "parameters": TicketLookup.model_json_schema(),
        },
    },
    "knowledge_search": {
        "type": "function",
        "function": {
            "name": "knowledge_search",
            "description": "搜索授权知识库，返回标题、版本、片段和来源。回答请引用标题及版本。",
            "parameters": SearchInput.model_json_schema(),
        },
    },
    "propose_ticket": {
        "type": "function",
        "function": {
            "name": "propose_ticket",
            "description": "拟定工单，返回待确认操作。需要人工确认后才能创建，不能声称已创建。",
            "parameters": TicketProposal.model_json_schema(),
        },
    },
    "memory_search": {
        "type": "function",
        "function": {
            "name": "memory_search",
            "description": "检索当前客户的历史服务记录与过往事项（工单事件、待办承诺）。",
            "parameters": MemorySearchInput.model_json_schema(),
        },
    },
}


class PlatformTools:
    def __init__(self, http, platform, row, allowed, kb_ids, emit):
        self.http, self.platform, self.row = http, platform, row
        self.allowed, self.kb_ids, self.emit = allowed, [UUID(k) for k in kb_ids], emit

    def _memory_search_registered(self):
        """memory_search registration gate (R15, P3-UT-10): the tool only
        appears when the draft kept it, the run is customer-bound and the
        agent snapshot has memory enabled. Playground and customerless runs
        never see it, so the model cannot call it either.
        """
        if "memory_search" not in self.allowed or not self.row.get("customer_id"):
            return False
        agent = (self.row.get("config", {}).get("agent") or {}).get("config") or {}
        return agent.get("memory_enabled", True) is not False

    @property
    def schemas(self):
        return self.http.schemas + [
            schema
            for name, schema in BUILTIN_SCHEMAS.items()
            if name in self.allowed and (name != "memory_search" or self._memory_search_registered())
        ]

    async def execute(self, name, args):
        call_id, started, result = uuid4(), time.monotonic(), None
        effect = "proposal" if name == "propose_ticket" else "read"
        adapter = "builtin" if name in BUILTIN_SCHEMAS else "http"
        if adapter == "http" and name in self.http.specs:
            effect = self.http.specs[name].effect
        version = next(
            (
                s.get("version")
                for s in self.row.get("config", {}).get("agent", {}).get("tools", [])
                if s["spec"]["name"] == name
            ),
            None,
        )
        status, error = "failed", None
        try:
            result = await self._execute(name, args)
            status = (
                "requires_confirmation"
                if result.get("ok") and effect == "proposal"
                else "succeeded"
                if result.get("ok")
                else "failed"
            )
            error = result.get("error")
            return {
                **result,
                "call_id": str(call_id),
                "execution_status": status,
                "retryable": False,
                "effect": effect,
                "tool_version": version,
            }
        except asyncio.CancelledError:
            status, error = "cancelled", "tool_cancelled"
            raise
        finally:
            async with self.platform.engine.begin() as c:
                await execute(
                    c,
                    "INSERT INTO tool_calls(id,tenant_id,run_id,tool_name,tool_version,adapter_type,effect,status,error_code,duration_ms) VALUES(:id,:t,:run,:name,:v,:adapter,:effect,:status,:error,:ms)",
                    id=call_id,
                    t=self.row["tenant_id"],
                    run=self.row["id"],
                    name=name,
                    v=version,
                    adapter=adapter,
                    effect=effect,
                    status=status,
                    error=error,
                    ms=int((time.monotonic() - started) * 1000),
                )

    async def _execute(self, name, args):
        try:
            await self.platform.policy.check(self.row["tenant_id"], name, self.allowed)
            if name == "knowledge_search":
                query = SearchInput.model_validate(args).query
                results = await self.platform.retrieval.for_agent(
                    self.row["tenant_id"], query, self.kb_ids
                )
                results = json.loads(json.dumps(results, default=str))
                await self.emit(
                    "knowledge.retrieved",
                    {
                        "sources": [
                            {k: r[k] for k in ("document_id", "title", "version", "chunk_id")}
                            for r in results
                        ]
                    },
                )
                return {"ok": True, "data": results}
            if name == "propose_ticket":
                proposal = TicketProposal.model_validate(args)
                data = await self.platform.customer.propose(
                    self.row["tenant_id"], self.row["conversation_id"], self.row["id"], proposal
                )
                return {"ok": True, "data": data}
            if name == "ticket_lookup":
                ticket = TicketLookup.model_validate(args)
                result = await self.platform.customer.lookup(
                    self.row["tenant_id"], self.row["conversation_id"], ticket.ticket_id
                )
                return {"ok": True, "data": json.loads(json.dumps(result, default=str))}
            if name == "memory_search":
                # Not in schemas => invisible to the model; this guard covers
                # direct/hallucinated calls on customerless runs (P3-UT-10).
                if not self._memory_search_registered():
                    return {"ok": False, "error": "memory_search_unavailable"}
                params = MemorySearchInput.model_validate(args)
                hits = await self.platform.memory_store.search(
                    self.row["tenant_id"], self.row["customer_id"], params.query, params.limit
                )
                # L2 reference signal (R11): hit ids feed the reinforce task
                # dispatched at run finish; they stay internal and never reach
                # the model-facing output below.
                reinforce = self.row.setdefault("_memory_reinforce", {})
                for hit in hits:
                    if hit.get("id"):
                        hit_id = str(hit["id"])
                        reinforce[hit_id] = reinforce.get(hit_id, 0) + 1
                # R10 telemetry: call volume and the empty-result rate
                # (hits=0) aggregate from this event. Production always wires
                # emit; the None guard keeps legacy ad-hoc constructions
                # (unit tests) working.
                if self.emit is not None:
                    await self.emit("memory_search.completed", {"hits": len(hits)})
                data = [
                    {
                        "content": hit["content"],
                        "key": hit["key"],
                        "updated_at": str(hit["updated_at"]),
                        "score": round(hit["score"], 6),
                    }
                    for hit in hits
                ]
                if not data:
                    return {"ok": True, "data": [], "message": "未找到相关历史记录"}
                return {"ok": True, "data": data}
            return await self.http.execute(name, args)
        except ValidationError:
            return {"ok": False, "error": "invalid_arguments"}
        except DomainError as exc:
            return {"ok": False, "error": exc.code}
