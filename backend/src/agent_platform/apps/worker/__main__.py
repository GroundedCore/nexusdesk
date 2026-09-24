import asyncio

from agent_platform.modules.agent_runtime.bootstrap import runtime_services
from agent_platform.settings import Settings


async def main():
    async with runtime_services(Settings()) as services:
        await services.worker.serve()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
