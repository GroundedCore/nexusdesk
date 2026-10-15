import asyncio
import logging
import random
from uuid import uuid4

from agent_platform.modules.agent_runtime.notify import QUEUE_CHANNEL
from agent_platform.modules.agent_runtime.schemas import RuntimeFault
from agent_platform.modules.memory.service import (
    overlap_ids,
    render_event_injection,
    render_flag_injection,
    render_memory_injection,
)

logger = logging.getLogger(__name__)


class RuntimeWorker:
    def __init__(
        self, repository, runtime, settings, fingerprint, notifier=None, listener=None, memory=None
    ):
        self.repository, self.runtime, self.settings = repository, runtime, settings
        self.fingerprint = fingerprint
        self.owner = uuid4()
        self.tasks = set()
        self.stopping = asyncio.Event()
        self.notifier = notifier
        self.listener = listener
        self.memory = memory

    async def _execute(self, row):
        monitor_failure = False

        async def emit(kind, data):
            if kind == "model.delta":
                # Transient best-effort telemetry; never touches the durable log.
                if self.notifier is not None:
                    await self.notifier.send(row["id"], {"k": "delta", **data})
                return
            await self.repository.append(row["id"], self.owner, kind, data)

        async def monitor(task):
            nonlocal monitor_failure
            while True:
                await asyncio.sleep(1)
                try:
                    stop = await self.repository.heartbeat(
                        row["id"], self.owner, self.settings.lease_seconds
                    )
                except Exception:  # noqa: BLE001 - isolate failures at the worker boundary
                    stop = "lost"  # Fail closed if lease ownership cannot be established.
                if stop != "ok":
                    monitor_failure = stop == "lost"
                    task.cancel()
                    return

        execution = None
        watcher = None
        try:
            if row["config"]["fingerprint"] != self.fingerprint:
                raise RuntimeFault("configuration_changed")
            runtime = (
                await self.runtime.for_run(row, emit)
                if hasattr(self.runtime, "for_run")
                else self.runtime
            )
            execution = asyncio.create_task(
                runtime.run(
                    row["input"],
                    row["history"],
                    emit,
                    summary=row.get("summary"),
                    customer_memory=await self._customer_memory(row),
                )
            )
            watcher = asyncio.create_task(monitor(execution))
            output, history, dropped = await execution
            await self.repository.finish(
                row["id"],
                self.owner,
                "completed",
                output=output,
                history=history,
                memory=self._memory_context(row, dropped),
            )
        except asyncio.CancelledError:
            if execution:
                execution.cancel()
            await self.repository.finish(
                row["id"],
                self.owner,
                "failed" if monitor_failure else "cancelled",
                error="worker_lost" if monitor_failure else None,
            )
        except RuntimeFault as exc:
            await self.repository.finish(row["id"], self.owner, "failed", error=exc.code)
        except TimeoutError:
            await self.repository.finish(row["id"], self.owner, "failed", error="run_timeout")
        except Exception:  # noqa: BLE001 - isolate failures at the worker boundary
            # Provider errors may contain credentials or customer data; never expose raw messages.
            logger.error("Run %s failed", row["id"])
            await self.repository.finish(row["id"], self.owner, "failed", error="execution_error")
        finally:
            if watcher:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
            if execution:
                await asyncio.gather(execution, return_exceptions=True)

    async def _customer_memory(self, row):
        """Fetch and render the customer's active memories for injection (FR-3).

        One system message, sections in a fixed order: flags first (Phase 4
        FR-2, 【服务标记】 ahead of the profile), then profile/preference,
        then the Phase 3 pending-events section. With no flags the message
        body stays byte-identical to Phase 3. Gated by the per-agent
        ``memory_enabled`` switch (a legacy run without an agent snapshot
        defaults to on). A lookup failure must never break the run itself, so
        it degrades to no injection with a warning.

        Phase 4 side collections on the shared run dict (R10/R11): injected
        items overlapping the user input count as L3 references for the
        reinforce task, and the injected-section counts ride to finish() for
        the ``memory.injected`` runtime event.
        """
        if self.memory is None or not row.get("customer_id"):
            return None
        agent = (row["config"].get("agent") or {}).get("config") or {}
        if not agent.get("memory_enabled", True):
            return None
        try:
            items = await self.memory.injection_items(
                row["tenant_id"], row["customer_id"], self.settings.memory_inject_max_items
            )
            events = await self.memory.pending_event_items(
                row["tenant_id"], row["customer_id"], self.settings.memory_event_inject_max
            )
            flags = await self.memory.active_flags(
                row["tenant_id"],
                row["customer_id"],
                getattr(self.settings, "memory_flag_inject_max", 3),
            )
        except Exception:
            logger.warning(
                "Customer memory lookup failed for run %s; continuing without it",
                row["id"],
                exc_info=True,
            )
            return None
        # L3 reference signal: an injected profile/preference/event item whose
        # key/content overlaps the user input counts as a reference (flags are
        # decay-immune and never need reinforcement).
        reinforce = row.setdefault("_memory_reinforce", {})
        for item_id in overlap_ids([*items, *events], row.get("input") or ""):
            key = str(item_id)
            reinforce[key] = reinforce.get(key, 0) + 1
        sections = [
            section
            for section in (
                render_flag_injection(flags),
                render_memory_injection(items),
                render_event_injection(events),
            )
            if section
        ]
        if sections:
            row["_memory_injected"] = {
                "items": len(items),
                "flags": len(flags),
                "events": len(events),
            }
        return "\n\n".join(sections) or None

    def _memory_context(self, row, dropped):
        """Memory-task dispatch gates for finish().

        Summary: dropped messages plus both kill switches (global and per-agent
        from the run's config snapshot; a legacy run without a snapshot follows
        the global switch only). Extraction (Phase 2): global switch AND
        per-agent ``memory_write``, with example agents kept out of the
        pipeline entirely; the customer link and playground exclusion are
        checked in ``finish()`` where the conversation row is locked.

        Phase 4 (R10/R11): the reference hits collected during injection and
        memory_search become a reinforce task — sampled here at dispatch time
        (``memory_reinforce_sample_rate=0`` never writes) — and the injected
        section counts become the ``memory.injected`` runtime event.
        """
        agent = row["config"].get("agent") or {}
        config = agent.get("config") or {}
        memory = {}
        if dropped and self.settings.summary_enabled and config.get("summary_enabled", True):
            memory["dropped"] = dropped
            memory["max_input"] = self.settings.summary_max_input_messages
        if (
            self.settings.memory_extract_enabled
            and config.get("memory_write", True)
            and not agent.get("is_example")
        ):
            memory["extract"] = True
            memory["extract_debounce"] = self.settings.memory_extract_debounce_seconds
        hits = row.get("_memory_reinforce")
        if hits and random.random() < getattr(self.settings, "memory_reinforce_sample_rate", 1.0):
            memory["reinforce"] = hits
        if row.get("_memory_injected"):
            memory["injected"] = row["_memory_injected"]
        return memory or None

    def _done(self, task):
        self.tasks.discard(task)
        if not task.cancelled() and task.exception():
            logger.error("Worker could not persist final state; lease reaper will recover it")

    async def serve(self):
        # A submitted run is claimed as soon as it is queued rather than at the next
        # poll, which removes most of the queue wait a customer sits through. The
        # timeout stays as the safety net for a missed notification.
        wake = asyncio.Event()

        def notified(_payload):
            wake.set()

        if self.listener is not None:
            await self.listener.on(QUEUE_CHANNEL, notified)
        try:
            while not self.stopping.is_set():
                # Cleared before claiming so a run queued while we are claiming still
                # wakes the wait below.
                wake.clear()
                try:
                    await self.repository.reap(self.settings.tenant_id)
                    while len(self.tasks) < self.settings.worker_concurrency:
                        row = await self.repository.claim(
                            self.owner, self.settings.lease_seconds, self.settings.tenant_id
                        )
                        if row is None:
                            break
                        task = asyncio.create_task(self._execute(row))
                        self.tasks.add(task)
                        task.add_done_callback(self._done)
                except Exception:  # noqa: BLE001 - isolate failures at the worker boundary
                    logger.error("Runtime database unavailable; retrying dispatcher")
                # Wake on a queue notification, on stop, or after the safety-net
                # timeout. Cancelling an Event waiter is safe: the set flag persists.
                pending = {
                    asyncio.ensure_future(wake.wait()),
                    asyncio.ensure_future(self.stopping.wait()),
                }
                _done, waiting = await asyncio.wait(
                    pending, timeout=0.5, return_when=asyncio.FIRST_COMPLETED
                )
                for task in waiting:
                    task.cancel()
                await asyncio.gather(*waiting, return_exceptions=True)
        finally:
            if self.listener is not None:
                await self.listener.off(QUEUE_CHANNEL, notified)
            tasks = list(self.tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def stop(self):
        self.stopping.set()
