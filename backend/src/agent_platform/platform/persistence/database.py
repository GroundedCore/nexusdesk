from sqlalchemy.ext.asyncio import create_async_engine

from agent_platform.settings import Settings


def create_engine(settings: Settings):
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_size=settings.database_pool_size,
        max_overflow=0,
        pool_timeout=5,
        connect_args={"timeout": 5, "command_timeout": 10},
    )
