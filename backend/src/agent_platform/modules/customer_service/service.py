import hashlib
import json
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from agent_platform.modules.agent_runtime.schemas import RunRequest
from agent_platform.modules.conversation.service import add_message
from agent_platform.platform.persistence.store import (
    DomainError,
    audit,
    execute,
    many,
    one,
    required,
    transaction,
)


class TicketProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=8000)


TICKET_VIEW = """SELECT t.*,d.description,d.resolution,
    COALESCE((SELECT l.content FROM ticket_process_log l WHERE l.ticket_id=t.id
        AND l.tenant_id=t.tenant_id AND l.action_type IN ('remark','legacy_note')
        ORDER BY l.id DESC LIMIT 1),'') AS note
    FROM tickets t JOIN tickets_detail d ON d.ticket_id=t.id AND d.tenant_id=t.tenant_id """


class CustomerService:
    def __init__(self, engine, repository, agents, handoffs, settings, snapshot):
        self.engine, self.repository, self.agents, self.handoffs = (
            engine,
            repository,
            agents,
            handoffs,
        )
        self.settings, self.snapshot = settings, snapshot

    async def send(self, tenant, actor, cid, message, connection=None):
        async with transaction(self.engine, connection) as c:
            # Admission lock precedes conversation lock everywhere to avoid inverse lock ordering.
            await execute(c, "SELECT pg_advisory_xact_lock(71432019)")
            conv = required(
                await one(
                    c,
                    "SELECT * FROM runtime_conversations WHERE id=:id AND tenant_id=:t FOR UPDATE",
                    id=cid,
                    t=tenant,
                )
            )
            if conv["mode"] == "closed":
                raise DomainError("conversation_closed", 409)
            if message.strip() in {"转人工", "人工客服"}:
                await add_message(c, cid, "user", message)
                handoff = await self.handoffs.request(tenant, actor, cid, "用户请求人工服务", c)
                return {"conversation_id": cid, "run": None, "handoff": handoff}
            if conv["mode"] in {"human", "waiting"}:
                await add_message(c, cid, "user", message)
                await execute(
                    c,
                    "UPDATE runtime_conversations SET history=CAST(:msg AS jsonb) WHERE id=:id",
                    id=cid,
                    msg=json.dumps(
                        (
                            [*conv["history"], {"role": "user", "content": message}][
                                -self.settings.history_turns * 2 :
                            ]
                        )
                        if self.settings.history_turns
                        else []
                    ),
                )
                return {"conversation_id": cid, "run": None}
            snapshot = dict(self.snapshot)
            if conv["agent_id"]:
                snapshot["agent"] = await self.agents.snapshot(tenant, conv["agent_id"], c)
            elif self.settings.model_backend == "unconfigured":
                raise DomainError("model_not_configured", 409)
            row = await self.repository.submit(
                tenant,
                RunRequest(conversation_id=conv["external_id"], message=message),
                snapshot,
                self.settings.queue_capacity,
                self.settings.tenant_capacity,
                self.settings.queue_timeout_seconds,
                connection=c,
            )
            await audit(c, tenant, actor, "run.submitted", row["id"])
            return {"conversation_id": cid, "run": {"id": row["id"], "status": row["status"]}}

    async def propose(self, tenant, cid, run_id, body):
        payload = body.model_dump_json()
        key = hashlib.sha256((str(run_id) + payload).encode()).hexdigest()
        async with self.engine.begin() as c:
            required(
                await one(
                    c,
                    "SELECT id FROM runtime_conversations WHERE id=:id AND tenant_id=:t",
                    id=cid,
                    t=tenant,
                )
            )
            row = await one(
                c,
                """INSERT INTO pending_actions(id,tenant_id,conversation_id,kind,payload,dedup_key,expires_at)
                VALUES(:id,:t,:cid,'create_ticket',CAST(:payload AS jsonb),:key,now()+interval '30 minutes')
                ON CONFLICT(tenant_id,dedup_key) DO UPDATE SET dedup_key=EXCLUDED.dedup_key RETURNING *""",
                id=uuid4(),
                t=tenant,
                cid=cid,
                payload=payload,
                key=key,
            )
            await audit(c, tenant, "runtime", "ticket.proposed", row["id"])
            return {
                "action_id": str(row["id"]),
                "status": row["status"],
                "requires_confirmation": True,
                "message": "工单仅已拟定，需在工作台确认后才会创建。",
            }

    async def decide(self, tenant, actor, aid, approve):
        async with self.engine.begin() as c:
            ref = required(
                await one(
                    c,
                    "SELECT conversation_id FROM pending_actions WHERE id=:id AND tenant_id=:t",
                    id=aid,
                    t=tenant,
                )
            )
            conv = await one(
                c,
                "SELECT * FROM runtime_conversations WHERE id=:id FOR UPDATE",
                id=ref["conversation_id"],
            )
            action = required(
                await one(
                    c,
                    "SELECT *,expires_at>now() AS valid FROM pending_actions WHERE id=:id AND tenant_id=:t FOR UPDATE",
                    id=aid,
                    t=tenant,
                )
            )
            existing = await one(
                c, TICKET_VIEW + " WHERE t.action_id=:id AND t.tenant_id=:t", id=aid, t=tenant
            )
            if existing:
                return existing
            if action["status"] != "pending":
                raise DomainError("action_already_decided", 409)
            if not action["valid"]:
                raise DomainError("action_expired", 409)
            if await one(
                c,
                "SELECT id FROM runtime_runs WHERE conversation_id=:id AND status IN ('queued','running')",
                id=conv["id"],
            ):
                raise DomainError("conversation_busy", 409)
            if not approve:
                await execute(
                    c, "UPDATE pending_actions SET status='rejected' WHERE id=:id", id=aid
                )
                await audit(c, tenant, actor, "ticket.rejected", aid)
                return {"id": aid, "status": "rejected"}
            body = TicketProposal.model_validate(action["payload"])
            kind = await one(
                c,
                """INSERT INTO ticket_types(tenant_id,code,name)
                VALUES(:t,'general','通用工单') ON CONFLICT(tenant_id,code)
                DO UPDATE SET code=EXCLUDED.code RETURNING id,enabled""",
                t=tenant,
            )
            if not kind["enabled"]:
                raise DomainError("ticket_type_disabled", 409)
            await execute(
                c,
                """INSERT INTO ticket_type_versions(tenant_id,type_id,version,name,definition,created_by)
                VALUES(:t,:id,1,'通用工单','{"fields":[]}',:actor) ON CONFLICT DO NOTHING""",
                t=tenant,
                id=kind["id"],
                actor=actor,
            )
            await execute(
                c,
                "UPDATE ticket_types SET published_version=1 WHERE id=:id AND published_version IS NULL",
                id=kind["id"],
            )
            tid = uuid4()
            await execute(
                c,
                """INSERT INTO tickets(id,tenant_id,conversation_id,action_id,title,ticket_no,type_id,type_version,created_by)
                VALUES(:id,:t,:cid,:aid,:title,:number,:kind,1,:actor)""",
                id=tid,
                t=tenant,
                cid=conv["id"],
                aid=aid,
                title=body.title,
                number="TK-" + tid.hex,
                kind=kind["id"],
                actor=actor,
            )
            await execute(
                c,
                "INSERT INTO tickets_detail(ticket_id,tenant_id,description) VALUES(:id,:t,:description)",
                id=tid,
                t=tenant,
                description=body.description,
            )
            await execute(
                c,
                """INSERT INTO ticket_process_log(tenant_id,ticket_id,actor_type,actor_id,action_type,content)
                VALUES(:t,:id,'staff',:actor,'created',:title)""",
                t=tenant,
                id=tid,
                actor=actor,
                title=body.title,
            )
            row = required(
                await one(c, TICKET_VIEW + " WHERE t.id=:id AND t.tenant_id=:t", id=tid, t=tenant)
            )
            await execute(c, "UPDATE pending_actions SET status='confirmed' WHERE id=:id", id=aid)
            content = f"工单 {row['id']} 已创建：{body.title}"
            await add_message(c, conv["id"], "system", content)
            history = (
                (
                    [*conv["history"], {"role": "assistant", "content": content}][
                        -self.settings.history_turns * 2 :
                    ]
                )
                if self.settings.history_turns
                else []
            )
            await execute(
                c,
                "UPDATE runtime_conversations SET history=CAST(:h AS jsonb) WHERE id=:id",
                id=conv["id"],
                h=json.dumps(history),
            )
            await audit(c, tenant, actor, "ticket.created", row["id"], action_id=str(aid))
            return row

    async def tickets(self, tenant):
        async with self.engine.connect() as c:
            return await many(
                c,
                TICKET_VIEW
                + " WHERE t.tenant_id=:t ORDER BY t.created_at DESC,t.id DESC LIMIT 100",
                t=tenant,
            )

    async def lookup(self, tenant, cid, tid):
        async with self.engine.connect() as c:
            return required(
                await one(
                    c,
                    "SELECT t.id,t.title,d.description,t.status,t.revision,t.updated_at FROM tickets t JOIN tickets_detail d ON d.ticket_id=t.id AND d.tenant_id=t.tenant_id WHERE t.tenant_id=:t AND t.conversation_id=:cid AND t.id=:id",
                    t=tenant,
                    cid=cid,
                    id=tid,
                ),
                "ticket_not_found",
            )

    async def update_ticket(self, tenant, actor, tid, status, note, revision):
        transitions = {
            "open": {"in_progress", "closed"},
            "in_progress": {"waiting_customer", "resolved", "closed"},
            "waiting_customer": {"in_progress", "closed"},
            "resolved": {"in_progress", "closed"},
            "closed": set(),
        }
        async with self.engine.begin() as c:
            row = required(
                await one(
                    c,
                    "SELECT * FROM tickets WHERE id=:id AND tenant_id=:t FOR UPDATE",
                    id=tid,
                    t=tenant,
                )
            )
            if row["revision"] != revision:
                raise DomainError("ticket_revision_conflict", 409)
            if status != row["status"] and status not in transitions[row["status"]]:
                raise DomainError("invalid_ticket_transition", 409)
            if row["status"] == "closed":
                raise DomainError("ticket_closed", 409)
            if status != row["status"] and status in {"resolved", "closed"} and not note.strip():
                raise DomainError("ticket_resolution_or_close_reason_required")
            await execute(
                c,
                """UPDATE tickets SET status=:status,revision=revision+1,updated_at=now(),
                resolved_at=CASE WHEN :status='resolved' THEN COALESCE(resolved_at,now())
                    WHEN :status='in_progress' THEN NULL ELSE resolved_at END,
                closed_at=CASE WHEN :status='closed' THEN now() ELSE closed_at END
                WHERE id=:id""",
                id=tid,
                status=status,
            )
            if status == "resolved":
                await execute(
                    c,
                    "UPDATE tickets_detail SET resolution=:note WHERE ticket_id=:id AND tenant_id=:t",
                    note=note,
                    id=tid,
                    t=tenant,
                )
            if status != row["status"]:
                await execute(
                    c,
                    """INSERT INTO ticket_process_log(tenant_id,ticket_id,actor_type,actor_id,action_type,old_status,new_status,content)
                    VALUES(:t,:id,'staff',:actor,'status_changed',:old,:new,:note)""",
                    t=tenant,
                    id=tid,
                    actor=actor,
                    old=row["status"],
                    new=status,
                    note=note,
                )
            if note.strip():
                await execute(
                    c,
                    """INSERT INTO ticket_process_log(tenant_id,ticket_id,actor_type,actor_id,action_type,content)
                    VALUES(:t,:id,'staff',:actor,'remark',:note)""",
                    t=tenant,
                    id=tid,
                    actor=actor,
                    note=note,
                )
            result = required(
                await one(c, TICKET_VIEW + " WHERE t.id=:id AND t.tenant_id=:t", id=tid, t=tenant)
            )
            await audit(c, tenant, actor, "ticket.updated", tid, status=status)
            return result
