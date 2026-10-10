import asyncio

from agent_platform.modules.agent_runtime.bootstrap import runtime_services
from agent_platform.settings import Settings


async def main():
    settings = Settings()
    async with runtime_services(settings) as services:
        loops = [services.worker.serve()]
        if settings.summary_enabled:
            loops.append(services.memory_worker.serve())
        try:
            async with asyncio.TaskGroup() as group:
                for loop in loops:
                    group.create_task(loop)
        finally:
            services.worker.stop()
            services.memory_worker.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
