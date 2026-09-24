from uuid import uuid4

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


class HandoffService:
    def __init__(self, engine, repository):
        self.engine, self.repository = engine, repository

    async def list(self, tenant):
        async with self.engine.connect() as c:
            return await many(
                c,
                """SELECT h.*,v.external_id FROM handoffs h
                JOIN runtime_conversations v ON v.id=h.conversation_id WHERE h.tenant_id=:t
                ORDER BY h.created_at DESC LIMIT 100""",
                t=tenant,
            )

    async def request(self, tenant, actor, cid, reason, connection=None):
        async with transaction(self.engine, connection) as c:
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
            existing = await one(
                c,
                "SELECT * FROM handoffs WHERE conversation_id=:id AND status IN ('waiting','active')",
                id=cid,
            )
            if existing:
                return existing
            messages = await many(
                c,
                "SELECT role,content FROM conversation_messages WHERE conversation_id=:id ORDER BY seq DESC LIMIT 10",
                id=cid,
            )
            summary = "\n".join(f"{m['role']}: {m['content'][:300]}" for m in reversed(messages))
            row = await one(
                c,
                """INSERT INTO handoffs(id,tenant_id,conversation_id,reason,summary)
                VALUES(:id,:t,:cid,:reason,:summary) RETURNING *""",
                id=uuid4(),
                t=tenant,
                cid=cid,
                reason=reason,
                summary=summary,
            )
            await execute(
                c,
                "UPDATE runtime_conversations SET mode='waiting',updated_at=now() WHERE id=:id",
                id=cid,
            )
            runs = await many(
                c,
                "SELECT id,status FROM runtime_runs WHERE conversation_id=:id AND status IN ('queued','running') FOR UPDATE",
                id=cid,
            )
            for run in runs:
                if run["status"] == "queued":
                    await execute(
                        c,
                        "UPDATE runtime_runs SET cancel_requested=true,status='cancelled',finished_at=now() WHERE id=:id",
                        id=run["id"],
                    )
                    await self.repository._event(
                        c, run["id"], "run.cancelled", {"reason": "human_handoff"}
                    )
                else:
                    await execute(
                        c,
                        "UPDATE runtime_runs SET cancel_requested=true WHERE id=:id",
                        id=run["id"],
                    )
                    await self.repository._event(
                        c, run["id"], "run.cancel_requested", {"reason": "human_handoff"}
                    )
            await add_message(c, cid, "system", "已申请人工服务，自动回复暂停。")
            await audit(c, tenant, actor, "handoff.requested", row["id"])
            return row

    async def transition(
        self, tenant, actor, hid, operation, assignee=None, revision=None, administrator=False
    ):
        async with self.engine.begin() as c:
            ref = required(
                await one(
                    c,
                    "SELECT conversation_id FROM handoffs WHERE id=:id AND tenant_id=:t",
                    id=hid,
                    t=tenant,
                )
            )
            await one(
                c,
                "SELECT id FROM runtime_conversations WHERE id=:id FOR UPDATE",
                id=ref["conversation_id"],
            )
            handoff = required(
                await one(
                    c,
                    "SELECT * FROM handoffs WHERE id=:id AND tenant_id=:t FOR UPDATE",
                    id=hid,
                    t=tenant,
                )
            )
            if revision is not None and handoff["revision"] != revision:
                raise DomainError("handoff_revision_conflict", 409)
            if not administrator and (
                (operation == "claim" and assignee not in (None, actor))
                or (operation != "claim" and handoff["assignee"] != actor)
            ):
                raise DomainError("handoff_assignee_required", 403)
            if operation == "claim":
                if handoff["status"] != "waiting":
                    raise DomainError("handoff_not_waiting", 409)
                row = await one(
                    c,
                    "UPDATE handoffs SET status='active',assignee=:who WHERE id=:id RETURNING *",
                    id=hid,
                    who=assignee or actor,
                )
                await execute(
                    c,
                    "UPDATE runtime_conversations SET mode='human',assigned_to=:who,updated_at=now() WHERE id=:id",
                    id=ref["conversation_id"],
                    who=assignee or actor,
                )
            else:
                if handoff["status"] != "active":
                    raise DomainError("handoff_not_active", 409)
                row = await one(
                    c,
                    "UPDATE handoffs SET status='resolved',closed_at=now() WHERE id=:id RETURNING *",
                    id=hid,
                )
                await execute(
                    c,
                    "UPDATE runtime_conversations SET mode=:mode,assigned_to=NULL,updated_at=now() WHERE id=:id",
                    id=ref["conversation_id"],
                    mode="bot" if operation == "resume" else "closed",
                )
                await add_message(
                    c,
                    ref["conversation_id"],
                    "system",
                    "人工服务结束，已恢复自动服务。" if operation == "resume" else "会话已结束。",
                )
            await audit(c, tenant, actor, "handoff." + operation, hid)
            return row
