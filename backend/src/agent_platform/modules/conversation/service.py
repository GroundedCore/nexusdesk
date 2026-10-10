import json
from typing import Optional
from uuid import uuid4

from agent_platform.platform.persistence.store import (
    DomainError,
    audit,
    execute,
    many,
    one,
    required,
    transaction,
)


async def add_message(c, cid, role, content, run_id=None):
    await execute(
        c,
        """INSERT INTO conversation_messages(id,conversation_id,role,content,run_id)
        VALUES(:id,:cid,:role,:content,:run)""",
        id=uuid4(),
        cid=cid,
        role=role,
        content=content,
        run=run_id,
    )
    await execute(c, "UPDATE runtime_conversations SET updated_at=now() WHERE id=:id", id=cid)


class ConversationService:
    def __init__(self, engine, history_turns=10):
        self.engine = engine
        self.history_limit = history_turns * 2

    async def list(self, tenant, limit=50, offset=0):
        async with self.engine.connect() as c:
            return await many(
                c,
                """SELECT id,external_id,mode,agent_id,deleted_agent,assigned_to,created_at,updated_at
                FROM runtime_conversations WHERE tenant_id=:t ORDER BY updated_at DESC LIMIT :lim OFFSET :off""",
                t=tenant,
                lim=limit,
                off=offset,
            )

    async def list_paginated(
        self,
        tenant,
        page=1,
        page_size=10,
        agent_id: Optional[str | None] = None,
        status: str = "all",
    ):
        page = max(page, 1)
        page_size = max(1, min(page_size, 100))
        where = "tenant_id=:t"
        params = {"t": tenant, "lim": page_size, "off": (page - 1) * page_size}
        if agent_id:
            where += " AND agent_id=:aid"
            params["aid"] = agent_id
        if status and status != "all":
            where += " AND mode=:status"
            params["status"] = status
        async with self.engine.connect() as c:
            total = await one(
                c,
                "SELECT count(*) AS total FROM runtime_conversations WHERE " + where,
                **{k: v for k, v in params.items() if k not in ("lim", "off")},
            )
            items = await many(
                c,
                """SELECT id,external_id,mode,agent_id,deleted_agent,assigned_to,created_at,updated_at
                FROM runtime_conversations WHERE """ + where + " ORDER BY updated_at DESC,id DESC LIMIT :lim OFFSET :off",
                **params,
            )
            return {
                "items": items,
                "total": total["total"],
                "page": page,
                "page_size": page_size,
            }

    async def agent_counts(self, tenant, agent_id: Optional[str | None] = None):
        where = "tenant_id=:t"
        params = {"t": tenant}
        if agent_id:
            where += " AND agent_id=:aid"
            params["aid"] = agent_id
        async with self.engine.connect() as c:
            row = await one(
                c,
                """SELECT
                    count(*)::int AS total,
                    count(*) FILTER (WHERE mode='bot')::int AS bot,
                    count(*) FILTER (WHERE mode='waiting')::int AS waiting,
                    count(*) FILTER (WHERE mode='human')::int AS human,
                    count(*) FILTER (WHERE mode='closed')::int AS closed
                FROM runtime_conversations WHERE """ + where,
                **params,
            )
            return row

    async def counts_by_agent(self, tenant):
        async with self.engine.connect() as c:
            rows = await many(
                c,
                """SELECT
                    r.agent_id,
                    COALESCE(a.name, r.agent_id::text) AS name,
                    count(*)::int AS total,
                    count(*) FILTER (WHERE r.mode='bot')::int AS bot,
                    count(*) FILTER (WHERE r.mode='waiting')::int AS waiting,
                    count(*) FILTER (WHERE r.mode='human')::int AS human,
                    count(*) FILTER (WHERE r.mode='closed')::int AS closed
                FROM runtime_conversations r
                LEFT JOIN agents a ON a.id = r.agent_id AND a.tenant_id = r.tenant_id
                WHERE r.tenant_id=:t
                GROUP BY r.agent_id, a.name
                ORDER BY total DESC, r.agent_id""",
                t=tenant,
            )
            return {"items": rows, "total": len(rows)}

    async def agent_records(self, tenant, aid, source="all", q="", page=1, page_size=12):
        where = """tenant_id=:t AND agent_id=:aid AND (:source='all' OR source=:source)
                   AND (:q='' OR external_id ILIKE :pattern)"""
        params = {"t": tenant, "aid": aid, "source": source, "q": q, "pattern": "%" + q + "%"}
        async with self.engine.connect() as c:
            required(
                await one(
                    c, "SELECT id FROM agents WHERE id=:aid AND tenant_id=:t", t=tenant, aid=aid
                ),
                "agent_not_found",
            )
            total = await one(
                c, "SELECT count(*) AS total FROM runtime_conversations WHERE " + where, **params
            )
            items = await many(
                c,
                """SELECT id,external_id,mode,agent_id,source,created_at,updated_at,
                (SELECT content FROM conversation_messages WHERE conversation_id=runtime_conversations.id
                 ORDER BY seq DESC LIMIT 1) AS last_message
                FROM runtime_conversations WHERE """
                + where
                + " ORDER BY updated_at DESC,id DESC LIMIT :lim OFFSET :off",
                **params,
                lim=page_size,
                off=(page - 1) * page_size,
            )
            return {"items": items, "total": total["total"], "page": page, "page_size": page_size}

    async def history(self, tenant, cid, before=None, limit=50):
        async with self.engine.connect() as c:
            required(
                await one(
                    c,
                    "SELECT id FROM runtime_conversations WHERE id=:id AND tenant_id=:t",
                    id=cid,
                    t=tenant,
                )
            )
            rows = await many(
                c,
                """SELECT * FROM conversation_messages WHERE conversation_id=:id
                AND (:before=0 OR seq<:before) ORDER BY seq DESC LIMIT :lim""",
                id=cid,
                before=before or 0,
                lim=limit + 1,
            )
            return {"items": list(reversed(rows[:limit])), "has_more": len(rows) > limit}

    async def create(
        self, tenant, actor, external_id, agent_id=None, connection=None, source="business"
    ):
        if source not in {"business", "playground"}:
            raise DomainError("invalid_conversation_source", 422)
        if source == "playground" and not agent_id:
            raise DomainError("playground_agent_required", 422)
        async with transaction(self.engine, connection) as c:
            if source == "playground":
                agent = required(
                    await one(
                        c,
                        "SELECT published_version,archived FROM agents WHERE id=:id AND tenant_id=:t FOR SHARE",
                        id=agent_id,
                        t=tenant,
                    ),
                    "agent_not_found",
                )
                if agent["archived"] or agent["published_version"] is None:
                    raise DomainError("agent_not_available", 409)
            if agent_id:
                required(
                    await one(
                        c,
                        "SELECT id FROM agents WHERE id=:id AND tenant_id=:t FOR KEY SHARE",
                        id=agent_id,
                        t=tenant,
                    ),
                    "agent_not_found",
                )
            row = await one(
                c,
                """INSERT INTO runtime_conversations(id,tenant_id,external_id,agent_id,source)
                VALUES(:id,:t,:external,:aid,:source) ON CONFLICT(tenant_id,external_id) DO NOTHING RETURNING *""",
                id=uuid4(),
                t=tenant,
                external=external_id,
                aid=agent_id,
                source=source,
            )
            if not row:
                raise DomainError("conversation_exists", 409)
            await audit(c, tenant, actor, "conversation.created", row["id"])
            return row

    async def detail(self, tenant, cid, after=0):
        async with self.engine.connect() as c:
            row = required(
                await one(
                    c,
                    "SELECT * FROM runtime_conversations WHERE id=:id AND tenant_id=:t",
                    id=cid,
                    t=tenant,
                )
            )
            row["messages"] = await many(
                c,
                (
                    """SELECT * FROM (SELECT * FROM conversation_messages
                WHERE conversation_id=:id AND seq>:after ORDER BY seq DESC LIMIT 200) recent ORDER BY seq"""
                    if after == 0
                    else """SELECT * FROM conversation_messages
                WHERE conversation_id=:id AND seq>:after ORDER BY seq LIMIT 200"""
                ),
                id=cid,
                after=after,
            )
            row["runs"] = await many(
                c,
                """SELECT id,status,output,error_code,created_at,cancel_requested FROM runtime_runs
                WHERE conversation_id=:id ORDER BY created_at DESC LIMIT 20""",
                id=cid,
            )
            row["actions"] = await many(
                c,
                """SELECT * FROM pending_actions WHERE conversation_id=:id
                ORDER BY created_at DESC LIMIT 30""",
                id=cid,
            )
            return row

    async def human_reply(self, tenant, actor, cid, content, administrator=False):
        async with self.engine.begin() as c:
            row = required(
                await one(
                    c,
                    "SELECT * FROM runtime_conversations WHERE id=:id AND tenant_id=:t FOR UPDATE",
                    id=cid,
                    t=tenant,
                )
            )
            if row["mode"] != "human":
                raise DomainError("conversation_not_claimed", 409)
            if not administrator and row["assigned_to"] != actor:
                raise DomainError("conversation_assignee_required", 403)
            await add_message(c, cid, "human", content)
            await execute(
                c,
                """UPDATE runtime_conversations SET history=CAST(:msg AS jsonb)
                WHERE id=:id""",
                id=cid,
                msg=json.dumps(
                    (
                        [*row["history"], {"role": "assistant", "content": content}][
                            -self.history_limit :
                        ]
                    )
                    if self.history_limit
                    else []
                ),
            )
            await audit(c, tenant, actor, "conversation.human_reply", cid)
            return {"sent": True}

    async def transition(self, tenant, actor, cid, operation, revision):
        async with self.engine.begin() as c:
            row = required(
                await one(
                    c,
                    "SELECT * FROM runtime_conversations WHERE id=:id AND tenant_id=:t FOR UPDATE",
                    id=cid,
                    t=tenant,
                )
            )
            if row.get("deleted_agent"):
                raise DomainError("conversation_agent_deleted", 409)
            if row["revision"] != revision:
                raise DomainError("conversation_revision_conflict", 409)
            if (
                operation == "close"
                and row["mode"] != "bot"
                or operation == "reopen"
                and row["mode"] != "closed"
            ):
                raise DomainError("invalid_conversation_transition", 409)
            if await one(
                c,
                "SELECT id FROM runtime_runs WHERE conversation_id=:id AND status IN ('queued','running')",
                id=cid,
            ):
                raise DomainError("conversation_busy", 409)
            updated = await one(
                c,
                "UPDATE runtime_conversations SET mode=:mode,updated_at=now() WHERE id=:id RETURNING *",
                id=cid,
                mode="closed" if operation == "close" else "bot",
            )
            await add_message(
                c, cid, "system", "会话已关闭。" if operation == "close" else "会话已重新打开。"
            )
            await audit(c, tenant, actor, "conversation." + operation, cid)
            return updated
