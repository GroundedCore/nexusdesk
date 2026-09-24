"""Create explicitly labelled demo profiles; never modifies business Agent bindings."""

import asyncio

from agent_platform.modules.agent_runtime.bootstrap import runtime_services
from agent_platform.modules.model_gateway.contracts import OPERATIONS, Connection, Model, Profile
from agent_platform.platform.persistence.store import execute
from agent_platform.settings import Settings


async def main():
    settings = Settings()
    async with runtime_services(settings) as services:
        catalog = services.platform.gateway.catalog
        tenant, actor = settings.tenant_id, "seed-models"
        async with services.platform.engine.connect() as lock:
            await execute(lock, "SELECT pg_advisory_lock(71432021)")
            try:
                connections = await catalog.list(tenant, "connections")
                connection = next(
                    (x for x in connections if x["name"] == "演示供应商（非真实模型）"), None
                )
                if connection is None:
                    connection = await catalog.save(
                        tenant, actor, "connections", Connection(name="演示供应商（非真实模型）")
                    )
                models = await catalog.list(tenant, "models")
                model = next((x for x in models if x["name"] == "演示多能力模型"), None)
                if model is None:
                    model = await catalog.save(
                        tenant,
                        actor,
                        "models",
                        Model(
                            name="演示多能力模型",
                            connection_id=connection["id"],
                            model_name="demo-v1",
                            operations=list(OPERATIONS),
                            tool_calling=True,
                            embedding_dimension=8,
                            vector_space="demo-hash-v1",
                            voices=["demo"],
                        ),
                    )
                profiles = await catalog.list(tenant, "profiles")
                for operation in OPERATIONS:
                    name = "演示 / " + operation
                    row = next((x for x in profiles if x["name"] == name), None)
                    if row is None:
                        row = await catalog.save(
                            tenant,
                            actor,
                            "profiles",
                            Profile(
                                name=name,
                                operation=operation,
                                model_id=model["id"],
                                require_tools=operation == "chat",
                            ),
                        )
                        await catalog.publish(tenant, actor, row["id"], row["revision"])
                    print(name, row["id"])
            finally:
                await execute(lock, "SELECT pg_advisory_unlock(71432021)")


if __name__ == "__main__":
    asyncio.run(main())
