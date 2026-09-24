import asyncio

from agent_platform.modules.agent_runtime.bootstrap import runtime_services
from agent_platform.settings import Settings


async def main():
    settings = Settings()
    async with runtime_services(settings) as services:
        while True:
            if not await services.platform.knowledge_workspace.run_next(
                settings.tenant_id
            ) and not await services.platform.ingestion.run_next(settings.tenant_id):
                await asyncio.sleep(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
