import asyncio

from alembic import context

from agent_platform.platform.persistence.database import create_engine
from agent_platform.settings import Settings


def migrate(connection):
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()


async def online():
    engine = create_engine(Settings())
    try:
        async with engine.connect() as connection:
            await connection.run_sync(migrate)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    context.configure(url="postgresql://", literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(online())
