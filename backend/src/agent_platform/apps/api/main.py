import asyncio
from contextlib import asynccontextmanager

from asyncpg import PostgresError
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from agent_platform.apps.api.platform_routes import ingress
from agent_platform.apps.api.platform_routes import router as platform_router
from agent_platform.apps.api.routes import router
from agent_platform.apps.api.runtime_routes import router as runtime_router
from agent_platform.modules.agent_runtime.bootstrap import runtime_services
from agent_platform.modules.agent_runtime.schemas import BusyError, CapacityError
from agent_platform.modules.knowledge.routes import router as knowledge_router
from agent_platform.modules.memory.bootstrap import embedded_memory_worker
from agent_platform.modules.model_gateway.management_routes import (
    router as gateway_management_router,
)
from agent_platform.modules.model_gateway.routes import router as model_router
from agent_platform.modules.open_platform.routes import management as open_management
from agent_platform.modules.open_platform.routes import public as open_public
from agent_platform.modules.tool_gateway.routes import router as tool_workspace_router
from agent_platform.platform.identity.local_routes import router as local_auth_router
from agent_platform.platform.persistence.store import DomainError
from agent_platform.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        async with runtime_services(settings) as services:
            app.state.runtime = services
            task = (
                asyncio.create_task(services.worker.serve()) if settings.embedded_worker else None
            )
            # Quickstart/development embed the memory claim loop in this process;
            # production runs it as the standalone memory-worker container.
            memory_worker = embedded_memory_worker(services, settings)
            memory_task = (
                asyncio.create_task(memory_worker.serve()) if memory_worker else None
            )
            webhook_task = asyncio.create_task(services.platform.open_platform.webhooks.serve())
            try:
                yield
            finally:
                services.platform.open_platform.webhooks.stopping.set()
                webhook_task.cancel()
                await asyncio.gather(webhook_task, return_exceptions=True)
                services.worker.stop()
                if memory_worker:
                    memory_worker.stop()
                if memory_task:
                    await memory_task
                if task:
                    await task

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.state.settings = settings

    @app.middleware("http")
    async def private_identity_responses(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(
            (
                "/api/v1/auth/local/",
                "/api/v1/sso/",
                "/api/v1/open-platform/identity/",
                "/openapi/v1/chat/",
            )
        ):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    app.include_router(router, prefix="/api/v1")
    app.include_router(local_auth_router, prefix="/api/v1")
    app.include_router(tool_workspace_router, prefix="/api/v1")
    app.include_router(knowledge_router, prefix="/api/v1")
    app.include_router(runtime_router, prefix="/api/v1")
    app.include_router(platform_router, prefix="/api/v1")
    app.include_router(ingress, prefix="/api/v1")
    app.include_router(gateway_management_router, prefix="/api/v1")
    app.include_router(model_router, prefix="/api/v1")
    app.include_router(open_management, prefix="/api/v1")
    app.include_router(open_public, prefix="/openapi/v1")
    from agent_platform.modules.open_platform.enterprise_routes import (
        management as enterprise_management,
    )
    from agent_platform.modules.open_platform.enterprise_routes import (
        public as sso_public,
    )
    from agent_platform.modules.open_platform.enterprise_routes import (
        tokens as chat_tokens,
    )

    app.include_router(enterprise_management, prefix="/api/v1")
    app.include_router(sso_public, prefix="/api/v1")
    app.include_router(chat_tokens, prefix="/openapi/v1")

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # Validation responses must not echo write-only credentials or the request body.
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {key: error[key] for key in ("type", "loc", "msg")} for error in exc.errors()
                ]
            },
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError):
        return JSONResponse(status_code=503, content={"detail": "database_unavailable"})

    app.add_exception_handler(PostgresError, database_error)
    app.add_exception_handler(OSError, database_error)
    app.add_exception_handler(TimeoutError, database_error)

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse(status_code=exc.status, content={"detail": exc.code})

    @app.exception_handler(BusyError)
    async def busy_error(request, exc):
        return JSONResponse(status_code=409, content={"detail": "conversation_busy"})

    @app.exception_handler(CapacityError)
    async def capacity_error(request, exc):
        return JSONResponse(
            status_code=429,
            content={"detail": "runtime_capacity_exceeded"},
            headers={"Retry-After": "2"},
        )

    return app


app = create_app()
