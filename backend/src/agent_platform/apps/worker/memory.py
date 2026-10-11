"""Standalone memory-worker entry for the production topology.

Runs the memory_tasks claim loop as its own process so production scales it
independently of the runtime worker (``AGENT_EMBEDDED_WORKER=false`` there).
Replicas are safe: claiming uses ``FOR UPDATE SKIP LOCKED`` plus a 120-second
lease with heartbeat renewal and a reaper. Quickstart/development embed the
same loop in the API process instead; see
``agent_platform.modules.memory.bootstrap``.
"""

import asyncio
import logging

from agent_platform.modules.agent_runtime.bootstrap import runtime_services
from agent_platform.modules.memory.bootstrap import build_memory_worker
from agent_platform.settings import Settings

logger = logging.getLogger(__name__)


async def main(settings=None):
    settings = settings or Settings()
    if not settings.summary_enabled:
        # Idle rather than exit: the container runs with
        # `restart: unless-stopped`, so exiting would crash-loop while the
        # feature is deliberately switched off.
        logger.info("summary_enabled=false; memory worker idling")
        await asyncio.Event().wait()
        return
    async with runtime_services(settings) as services:
        worker = build_memory_worker(services, settings)
        try:
            await worker.serve()
        finally:
            worker.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
