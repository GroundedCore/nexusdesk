"""Standalone channel worker for the production topology.

Runs the inbound outbox claim loop (``channel_inbound_events``) and the
outbound delivery loop (``channel_deliveries``) in their own process so external
chat traffic scales and fails independently of the API container. Replicas are
safe: both loops claim rows with ``FOR UPDATE SKIP LOCKED`` plus a lease, so
several channel-worker containers can run side by side.

Quickstart/development embed the same two loops in the API process; see
``agent_platform.apps.api.main`` and ``Settings.channel_worker_embedded``.
"""

import asyncio
import logging

from agent_platform.modules.agent_runtime.bootstrap import runtime_services
from agent_platform.settings import Settings

logger = logging.getLogger(__name__)


async def main(settings=None):
    settings = settings or Settings()
    async with runtime_services(settings) as services:
        channels = services.platform.channels
        inbound = asyncio.create_task(channels.serve_inbound())
        delivery = asyncio.create_task(channels.serve_deliveries())
        try:
            await asyncio.gather(inbound, delivery)
        finally:
            channels.inbound_stopping.set()
            channels.delivery_stopping.set()
            await asyncio.gather(inbound, delivery, return_exceptions=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
