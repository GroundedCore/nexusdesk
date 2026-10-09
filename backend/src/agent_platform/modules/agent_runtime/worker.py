import asyncio
import logging
from uuid import uuid4

from agent_platform.modules.agent_runtime.notify import QUEUE_CHANNEL
from agent_platform.modules.agent_runtime.schemas import RuntimeFault

logger = logging.getLogger(__name__)


class RuntimeWorker:
    def __init__(self, repository, runtime, settings, fingerprint, notifier=None, listener=None):
        self.repository, self.runtime, self.settings = repository, runtime, settings
        self.fingerprint = fingerprint
        self.owner = uuid4()
        self.tasks = set()
        self.stopping = asyncio.Event()
        self.notifier = notifier
        self.listener = listener

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
            execution = asyncio.create_task(runtime.run(row["input"], row["history"], emit))
            watcher = asyncio.create_task(monitor(execution))
            output, history = await execution
            await self.repository.finish(
                row["id"], self.owner, "completed", output=output, history=history
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
