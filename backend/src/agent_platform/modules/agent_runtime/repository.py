import asyncio
import json
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from agent_platform.modules.agent_runtime.schemas import TERMINAL, BusyError, CapacityError
from agent_platform.modules.conversation.service import add_message
from agent_platform.platform.persistence.store import DomainError, transaction


class RunRepository:
    def __init__(self, engine):
        self.engine = engine

    async def _event(self, conn, run_id, kind, data):
        seq = await conn.scalar(
            text("""UPDATE runtime_runs SET event_seq=event_seq+1
            WHERE id=:id RETURNING event_seq"""),
            {"id": run_id},
        )
        await conn.execute(
            text("""INSERT INTO runtime_events(run_id,seq,type,data)
            VALUES (:id,:seq,:type,CAST(:data AS jsonb))"""),
            {"id": run_id, "seq": seq, "type": kind, "data": json.dumps(data)},
        )

    async def submit(
        self, tenant, request, config, capacity, tenant_capacity, queue_timeout, connection=None
    ):
        try:
            async with transaction(self.engine, connection) as conn:
                # Serialize only short admission transactions, across API replicas.
                await conn.execute(text("SELECT pg_advisory_xact_lock(71432019)"))
                counts = (
                    (
                        await conn.execute(
                            text("""SELECT count(*) AS total,
                    count(*) FILTER (WHERE tenant_id=:tenant) AS tenant_count
                    FROM runtime_runs WHERE status IN ('queued','running')"""),
                            {"tenant": tenant},
                        )
                    )
                    .mappings()
                    .one()
                )
                if counts["total"] >= capacity or counts["tenant_count"] >= tenant_capacity:
                    raise CapacityError()
                cid = await conn.scalar(
                    text("""INSERT INTO runtime_conversations
                    (id,tenant_id,external_id) VALUES (:id,:tenant,:external)
                    ON CONFLICT(tenant_id,external_id) DO NOTHING RETURNING id"""),
                    {"id": uuid4(), "tenant": tenant, "external": request.conversation_id},
                )
                if cid is None:
                    cid = await conn.scalar(
                        text(
                            "SELECT id FROM runtime_conversations WHERE tenant_id=:tenant AND external_id=:external"
                        ),
                        {"tenant": tenant, "external": request.conversation_id},
                    )
                mode = await conn.scalar(
                    text("SELECT mode FROM runtime_conversations WHERE id=:id FOR UPDATE"),
                    {"id": cid},
                )
                if mode != "bot":
                    raise DomainError("conversation_not_in_bot_mode", 409)
                run_id = uuid4()
                row = (
                    (
                        await conn.execute(
                            text("""INSERT INTO runtime_runs
                    (id,conversation_id,tenant_id,input,config,status,queue_expires_at)
                    VALUES (:id,:cid,:tenant,:input,CAST(:config AS jsonb),'queued',
                    now() + :seconds * interval '1 second') RETURNING *"""),
                            {
                                "id": run_id,
                                "cid": cid,
                                "tenant": tenant,
                                "input": request.message,
                                "config": json.dumps(config),
                                "seconds": queue_timeout,
                            },
                        )
                    )
                    .mappings()
                    .one()
                )
                await add_message(conn, cid, "user", request.message, run_id)
                await self._event(conn, run_id, "run.queued", {})
                return dict(row)
        except IntegrityError as exc:
            if "runtime_one_active_conversation" in str(exc.orig):
                raise BusyError() from exc
            raise

    async def get(self, run_id: UUID, tenant: str):
        async with self.engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        text("""SELECT *, lease_until>now() AS lease_valid FROM runtime_runs
                WHERE id=:id AND tenant_id=:tenant"""),
                        {"id": run_id, "tenant": tenant},
                    )
                )
                .mappings()
                .first()
            )
            return dict(row) if row else None

    async def events(self, run_id, tenant, after=0):
        async with self.engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        text("""SELECT e.* FROM runtime_events e
                JOIN runtime_runs r ON r.id=e.run_id
                WHERE e.run_id=:id AND r.tenant_id=:tenant AND e.seq>:after
                ORDER BY e.seq LIMIT 200"""),
                        {"id": run_id, "tenant": tenant, "after": after},
                    )
                )
                .mappings()
                .all()
            )
            return [dict(row) for row in rows]

    async def claim(self, owner, lease_seconds, tenant):
        async with self.engine.begin() as conn:
            row = (
                (
                    await conn.execute(
                        text("""SELECT *, lease_until>now() AS lease_valid FROM runtime_runs
                WHERE status='queued' AND tenant_id=:tenant AND queue_expires_at>now()
                ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"""),
                        {"tenant": tenant},
                    )
                )
                .mappings()
                .first()
            )
            if not row:
                return None
            row = (
                (
                    await conn.execute(
                        text("""UPDATE runtime_runs SET status='running',
                owner=:owner, started_at=now(),
                lease_until=now()+:lease * interval '1 second' WHERE id=:id RETURNING *"""),
                        {"id": row["id"], "owner": owner, "lease": lease_seconds},
                    )
                )
                .mappings()
                .one()
            )
            history = await conn.scalar(
                text("SELECT history FROM runtime_conversations WHERE id=:id"),
                {"id": row["conversation_id"]},
            )
            await self._event(conn, row["id"], "run.started", {})
            return {**dict(row), "history": history}

    async def append(self, run_id, owner, kind, data):
        async with self.engine.begin() as conn:
            current = (
                (
                    await conn.execute(
                        text("""SELECT *, lease_until>now() AS lease_valid FROM runtime_runs
                WHERE id=:id FOR UPDATE"""),
                        {"id": run_id},
                    )
                )
                .mappings()
                .one()
            )
            if current["status"] != "running" or current["owner"] != owner:
                raise asyncio.CancelledError()
            if current["cancel_requested"] or not current["lease_valid"]:
                raise asyncio.CancelledError()
            await self._event(conn, run_id, kind, data)

    async def heartbeat(self, run_id, owner, lease_seconds):
        async with self.engine.begin() as conn:
            cancelled = await conn.scalar(
                text("""UPDATE runtime_runs SET
                lease_until=now()+:lease * interval '1 second'
                WHERE id=:id AND owner=:owner AND status='running' AND lease_until>now()
                RETURNING cancel_requested"""),
                {"id": run_id, "owner": owner, "lease": lease_seconds},
            )
            return "lost" if cancelled is None else ("cancel" if cancelled else "ok")

    async def finish(self, run_id, owner, status, output=None, error=None, history=None):
        async with self.engine.begin() as conn:
            cid = await conn.scalar(
                text("SELECT conversation_id FROM runtime_runs WHERE id=:id"), {"id": run_id}
            )
            await conn.execute(
                text("SELECT id FROM runtime_conversations WHERE id=:id FOR UPDATE"), {"id": cid}
            )
            row = (
                (
                    await conn.execute(
                        text(
                            "SELECT *, lease_until>now() AS lease_valid FROM runtime_runs WHERE id=:id FOR UPDATE"
                        ),
                        {"id": run_id},
                    )
                )
                .mappings()
                .one()
            )
            if row["status"] in TERMINAL or row["owner"] != owner or not row["lease_valid"]:
                return
            if row["cancel_requested"]:
                status, output, error = "cancelled", None, None
            await conn.execute(
                text("""UPDATE runtime_runs SET status=:status,output=:output,
                error_code=:error,finished_at=now(),lease_until=NULL WHERE id=:id"""),
                {"id": run_id, "status": status, "output": output, "error": error},
            )
            if status == "completed":
                await add_message(conn, row["conversation_id"], "assistant", output, run_id)
                await conn.execute(
                    text("""UPDATE runtime_conversations
                    SET history=CAST(:history AS jsonb) WHERE id=:id"""),
                    {"id": row["conversation_id"], "history": json.dumps(history or [])},
                )
            await self._event(
                conn, run_id, "run." + status, {"output": output, "error_code": error}
            )

    async def cancel(self, run_id, tenant):
        async with self.engine.begin() as conn:
            row = (
                (
                    await conn.execute(
                        text("""SELECT *, lease_until>now() AS lease_valid FROM runtime_runs
                WHERE id=:id AND tenant_id=:tenant FOR UPDATE"""),
                        {"id": run_id, "tenant": tenant},
                    )
                )
                .mappings()
                .first()
            )
            if not row:
                return None
            if row["status"] not in TERMINAL and not row["cancel_requested"]:
                await conn.execute(
                    text("UPDATE runtime_runs SET cancel_requested=true WHERE id=:id"),
                    {"id": run_id},
                )
                if row["status"] == "queued":
                    await conn.execute(
                        text("""UPDATE runtime_runs SET status='cancelled',
                        finished_at=now() WHERE id=:id"""),
                        {"id": run_id},
                    )
                    await self._event(conn, run_id, "run.cancelled", {})
                else:
                    await self._event(conn, run_id, "run.cancel_requested", {})
        return await self.get(run_id, tenant)

    async def reap(self, tenant):
        async with self.engine.begin() as conn:
            rows = (
                (
                    await conn.execute(
                        text("""SELECT * FROM runtime_runs WHERE tenant_id=:tenant
                AND ((status='running' AND lease_until<now())
                OR (status='queued' AND queue_expires_at<now()))
                FOR UPDATE SKIP LOCKED LIMIT 100"""),
                        {"tenant": tenant},
                    )
                )
                .mappings()
                .all()
            )
            for row in rows:
                status = "cancelled" if row["cancel_requested"] else "failed"
                error = "worker_lost" if row["status"] == "running" else "queue_timeout"
                await conn.execute(
                    text("""UPDATE runtime_runs SET status=:status,error_code=:error,
                    finished_at=now(),lease_until=NULL WHERE id=:id"""),
                    {"id": row["id"], "status": status, "error": error},
                )
                await self._event(conn, row["id"], "run." + status, {"error_code": error})
