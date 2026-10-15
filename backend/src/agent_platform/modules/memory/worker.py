"""Claim loop for memory_tasks, mirroring the RuntimeWorker dispatcher pattern.

Deployment forms (2026-10-10 revision): quickstart/development embed this loop
in the API process lifespan when ``AGENT_EMBEDDED_WORKER=true``; production runs
it as a standalone ``memory-worker`` container (``python -m
agent_platform.apps.worker.memory``) that scales independently of the runtime
worker. Assembly lives in ``agent_platform.modules.memory.bootstrap``.

Multi-replica safety follows the knowledge worker precedent
(``knowledge/ingestion.py``): claiming writes ``owner`` plus a 120-second lease
(``lease_expires_at``), a heartbeat renews the lease while a task executes, and
every claim pass first reaps running tasks whose lease expired, so a hard-killed
replica's rows return to ``pending`` instead of sticking in ``running``. Rows that
already exhausted their attempts are reaped straight to ``failed`` (knowledge
worker's ``attempts`` cap precedent), so a task that hard-kills every replica
cannot be re-claimed forever.
"""

import asyncio
import logging
import time
from uuid import uuid4

from agent_platform.modules.memory.decay import REINFORCE_KIND, daily_migration
from agent_platform.modules.memory.events import EVENT_KIND
from agent_platform.modules.memory.governance import scan_overdue_tickets
from agent_platform.modules.memory.summary import SummaryError, backoff_seconds
from agent_platform.platform.persistence.store import audit, execute, many, one

logger = logging.getLogger(__name__)

LEASE_SECONDS = 120
HEARTBEAT_SECONDS = 30
# Idle-cycle maintenance (R11): no scheduler — when the queue is empty and at
# least scan_interval_seconds passed since the last pass, the worker runs the
# TTL sweep and the embedding retry batch. The first pass runs at startup.
SCAN_INTERVAL_SECONDS = 24 * 3600
EMBED_RETRY_BATCH = 50

# Permanent-failure audit action per task kind (Phase 1 summary, Phase 2 extraction).
FAILURE_AUDIT = {
    "conversation_summary": "memory.summary.failed",
    "memory_extract": "memory.extract.failed",
    EVENT_KIND: "memory.event.failed",
    REINFORCE_KIND: "memory.reinforce.failed",
}


class MemoryWorker:
    def __init__(
        self,
        engine,
        service,
        settings,
        poll_seconds=0.5,
        lease_seconds=LEASE_SECONDS,
        heartbeat_seconds=HEARTBEAT_SECONDS,
        store=None,
        scan_interval_seconds=SCAN_INTERVAL_SECONDS,
    ):
        self.engine, self.service, self.settings = engine, service, settings
        self.poll_seconds = poll_seconds
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        # Phase 3 store: needed for the embedding retry pass; None disables
        # retrying (unit tests and legacy wirings keep working).
        self.store = store
        self.scan_interval_seconds = scan_interval_seconds
        self.last_scan = None
        # Phase 4 (R14): the built-in rule scan runs on its own timer,
        # independent of the daily maintenance pass; first pass at startup.
        self.last_rule_scan = None
        self.owner = uuid4()
        self.stopping = asyncio.Event()

    async def claim(self):
        async with self.engine.begin() as c:
            # Reaper: a replica that died mid-execution lets its lease lapse;
            # those rows become claimable again instead of sticking in running.
            # Past the attempts cap the row is failed outright (knowledge
            # worker precedent: attempts>=3 -> failed, error_code set), so a
            # task that hard-kills every replica cannot loop forever.
            await execute(
                c,
                """UPDATE memory_tasks SET
                status=CASE WHEN attempts>=max_attempts THEN 'failed' ELSE 'pending' END,
                error='memory_worker_lost',owner=NULL,
                lease_expires_at=NULL,updated_at=now()
                WHERE status='running' AND lease_expires_at<now() AND tenant_id=:tenant""",
                tenant=self.settings.tenant_id,
            )
            return await one(
                c,
                """UPDATE memory_tasks SET status='running',owner=:owner,
                lease_expires_at=now()+:lease*interval '1 second',
                attempts=attempts+1,updated_at=now()
                WHERE id = (
                    SELECT id FROM memory_tasks
                    WHERE status='pending' AND run_after<=now() AND tenant_id=:tenant
                    ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED
                ) RETURNING *""",
                owner=self.owner,
                lease=self.lease_seconds,
                tenant=self.settings.tenant_id,
            )

    async def renew(self, task):
        """Heartbeat: extend the lease while the task is still executing."""
        async with self.engine.begin() as c:
            renewed = await one(
                c,
                """UPDATE memory_tasks SET lease_expires_at=now()+:lease*interval '1 second',
                updated_at=now()
                WHERE id=:id AND owner=:owner AND status='running' RETURNING id""",
                id=task["id"],
                owner=self.owner,
                lease=self.lease_seconds,
            )
        if not renewed:
            logger.warning(
                "Memory task %s lease lost; another replica may re-run it", task["id"]
            )

    async def _heartbeat(self, task):
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            try:
                await self.renew(task)
            except asyncio.CancelledError:
                raise
            except Exception:  # a failed heartbeat must not kill execution
                logger.exception("Memory task %s heartbeat failed", task["id"])

    async def _execute(self, task):
        """Run the service with a heartbeat renewing the lease in the background."""
        heartbeat = asyncio.create_task(self._heartbeat(task))
        try:
            return await self.service.execute(task)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _finish(self, task, status, error=None, retry_in=None):
        async with self.engine.begin() as c:
            # The owner guard keeps a worker whose lease was reaped from
            # clobbering a row another replica has since picked up.
            updated = await one(
                c,
                """UPDATE memory_tasks SET status=:status,error=:error,owner=NULL,
                lease_expires_at=NULL,
                run_after=CASE WHEN :retry THEN now() + :seconds * interval '1 second'
                    ELSE run_after END,
                updated_at=now() WHERE id=:id AND owner=:owner RETURNING id""",
                id=task["id"],
                owner=self.owner,
                status=status,
                error=error,
                retry=retry_in is not None,
                seconds=retry_in or 0,
            )
            if not updated:
                logger.warning(
                    "Memory task %s finish skipped: lease was reaped mid-execution",
                    task["id"],
                )
                return
            if status == "failed":
                await audit(
                    c,
                    task["tenant_id"],
                    "memory",
                    FAILURE_AUDIT.get(task["kind"], "memory.task.failed"),
                    task["payload"].get("conversation_id") or task["id"],
                    task_id=str(task["id"]),
                    error=error,
                    attempts=task["attempts"],
                )

    async def run_once(self):
        """Claim and execute at most one task. Returns True when a task ran."""
        task = await self.claim()
        if not task:
            return False
        try:
            await self._execute(task)
        except SummaryError as exc:
            retryable = exc.retryable and task["attempts"] < task["max_attempts"]
            if retryable:
                await self._finish(
                    task, "pending", error=exc.code, retry_in=backoff_seconds(task["attempts"])
                )
            else:
                logger.warning(
                    "Memory task %s failed permanently: %s", task["id"], exc.code
                )
                await self._finish(task, "failed", error=exc.code)
        except Exception:  # isolate failures at the worker boundary
            logger.exception("Memory task %s crashed", task["id"])
            if task["attempts"] < task["max_attempts"]:
                await self._finish(
                    task,
                    "pending",
                    error="summary_execution_error",
                    retry_in=backoff_seconds(task["attempts"]),
                )
            else:
                await self._finish(task, "failed", error="summary_execution_error")
        else:
            await self._finish(task, "done")
        return True

    def maintenance_due(self):
        """Idle-cycle maintenance gate: first pass at startup, then daily."""
        return (
            self.last_scan is None
            or time.monotonic() - self.last_scan >= self.scan_interval_seconds
        )

    def rule_scan_due(self):
        """Built-in rule scan gate (R14): 0 minutes disables the scan."""
        interval = getattr(self.settings, "memory_rule_scan_interval_minutes", 15)
        if interval <= 0:
            return False
        return (
            self.last_rule_scan is None
            or time.monotonic() - self.last_rule_scan >= interval * 60
        )

    async def rule_scan(self):
        """One built-in rule pass: overdue tickets become complaint-risk flags.

        Attempt-stamped up front like the maintenance pass, so a failing scan
        retries at the next interval rather than on every idle cycle.
        """
        self.last_rule_scan = time.monotonic()
        await scan_overdue_tickets(self.engine, self.settings)

    async def maintenance(self):
        """One maintenance pass: TTL soft-expiry plus bounded embedding retry.

        Both statements are single-tenant and idempotent, so concurrent
        replicas can run them safely (R11). The pass is attempt-stamped up
        front: a persistently failing pass retries at the next interval, not
        on every idle cycle. Phase 4 (R14): the daily decay migration and
        capacity eviction ride along, gated by ``memory_decay_enabled`` —
        with the switch off the pass is exactly the Phase 3 one (AC-13).
        """
        self.last_scan = time.monotonic()
        tenant = self.settings.tenant_id
        async with self.engine.begin() as c:
            expired = await many(
                c,
                """UPDATE memory_items SET status='expired',updated_at=now()
                WHERE tenant_id=:t AND type='event' AND status='active'
                AND expires_at<now() RETURNING id""",
                t=tenant,
            )
            if expired:
                logger.info("Memory TTL pass expired %d event items", len(expired))
            retry = []
            if self.store is not None and self.store.available() == "vector":
                retry = await many(
                    c,
                    """SELECT id,content FROM memory_items
                    WHERE tenant_id=:t AND embedding_failed AND status='active'
                    ORDER BY updated_at LIMIT :lim""",
                    t=tenant,
                    lim=EMBED_RETRY_BATCH,
                )
        # Embedding runs outside the sweep transaction; per-item failures keep
        # the embedding_failed flag and wait for the next pass (P3-IT-10).
        for row in retry:
            await self.store.embed_and_save(row["id"], row["content"])
        # Phase 4 decay rides the same daily window (R14).
        if getattr(self.settings, "memory_decay_enabled", False):
            await daily_migration(self.engine, self.settings)

    async def reset_running(self):
        """Graceful shutdown: tasks claimed by this worker become claimable again."""
        async with self.engine.begin() as c:
            await execute(
                c,
                """UPDATE memory_tasks SET status='pending',owner=NULL,
                lease_expires_at=NULL,updated_at=now()
                WHERE status='running' AND owner=:owner""",
                owner=self.owner,
            )

    async def serve(self):
        try:
            while not self.stopping.is_set():
                try:
                    if not await self.run_once():
                        # Rule scan first (R14): the cheaper, more frequent
                        # timer; the daily maintenance pass follows.
                        if self.rule_scan_due():
                            await self.rule_scan()
                        if self.maintenance_due():
                            await self.maintenance()
                        await asyncio.sleep(self.poll_seconds)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - isolate failures at the worker boundary
                    logger.error("Memory worker dispatcher failed; retrying")
                    await asyncio.sleep(self.poll_seconds)
        finally:
            await self.reset_running()

    def stop(self):
        self.stopping.set()
