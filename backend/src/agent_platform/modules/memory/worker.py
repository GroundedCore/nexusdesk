"""Claim loop for memory_tasks, mirroring the RuntimeWorker dispatcher pattern."""

import asyncio
import logging
from uuid import uuid4

from agent_platform.modules.memory.summary import SummaryError, backoff_seconds
from agent_platform.platform.persistence.store import audit, execute, one

logger = logging.getLogger(__name__)


class MemoryWorker:
    def __init__(self, engine, service, settings, poll_seconds=0.5):
        self.engine, self.service, self.settings = engine, service, settings
        self.poll_seconds = poll_seconds
        self.owner = uuid4()
        self.stopping = asyncio.Event()

    async def claim(self):
        async with self.engine.begin() as c:
            return await one(
                c,
                """UPDATE memory_tasks SET status='running',owner=:owner,
                attempts=attempts+1,updated_at=now()
                WHERE id = (
                    SELECT id FROM memory_tasks
                    WHERE status='pending' AND run_after<=now() AND tenant_id=:tenant
                    ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED
                ) RETURNING *""",
                owner=self.owner,
                tenant=self.settings.tenant_id,
            )

    async def _finish(self, task, status, error=None, retry_in=None):
        async with self.engine.begin() as c:
            await execute(
                c,
                """UPDATE memory_tasks SET status=:status,error=:error,owner=NULL,
                run_after=CASE WHEN :retry THEN now() + :seconds * interval '1 second'
                    ELSE run_after END,
                updated_at=now() WHERE id=:id""",
                id=task["id"],
                status=status,
                error=error,
                retry=retry_in is not None,
                seconds=retry_in or 0,
            )
            if status == "failed":
                await audit(
                    c,
                    task["tenant_id"],
                    "memory",
                    "memory.summary.failed",
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
            await self.service.execute(task)
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

    async def reset_running(self):
        """Graceful shutdown: tasks claimed by this worker become claimable again."""
        async with self.engine.begin() as c:
            await execute(
                c,
                """UPDATE memory_tasks SET status='pending',owner=NULL,updated_at=now()
                WHERE status='running' AND owner=:owner""",
                owner=self.owner,
            )

    async def serve(self):
        try:
            while not self.stopping.is_set():
                try:
                    if not await self.run_once():
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
