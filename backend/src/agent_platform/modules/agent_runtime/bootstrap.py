import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx

from agent_platform.apps.api.platform_services import PlatformServices, RuntimeFactory
from agent_platform.modules.agent_runtime.engine import RuntimeEngine
from agent_platform.modules.agent_runtime.notify import Listener, Notifier
from agent_platform.modules.agent_runtime.repository import RunRepository
from agent_platform.modules.agent_runtime.worker import RuntimeWorker
from agent_platform.modules.evaluation.service import EvaluationService
from agent_platform.modules.memory.summary import SummaryService
from agent_platform.modules.memory.worker import MemoryWorker
from agent_platform.modules.model_gateway.service import build_model
from agent_platform.modules.tool_gateway.service import HttpTool, ToolGateway
from agent_platform.platform.persistence.database import create_engine


@dataclass
class RuntimeServices:
    repository: RunRepository
    worker: RuntimeWorker
    memory_worker: MemoryWorker
    snapshot: dict
    platform: PlatformServices
    listener: Listener
    notifier: Notifier


@asynccontextmanager
async def runtime_services(settings):
    specs = (
        [
            HttpTool.model_validate(item)
            for item in json.loads(settings.tools_file.read_text(encoding="utf-8"))
        ]
        if settings.tools_file
        else []
    )
    public = settings.model_dump(
        mode="json",
        exclude={
            "knowledge_s3_access_key",
            "knowledge_s3_secret_key",
            "milvus_token",
            "database_url",
            "api_token",
            "operator_api_token",
            "viewer_api_token",
            "model_api_key",
            "embedded_worker",
            "worker_concurrency",
            "database_pool_size",
        },
    )
    public["tools"] = [spec.model_dump(mode="json") for spec in specs]
    fingerprint = hashlib.sha256(json.dumps(public, sort_keys=True).encode()).hexdigest()
    snapshot = {
        "fingerprint": fingerprint,
        "model_backend": settings.model_backend,
        "model_name": settings.model_name,
    }
    engine = create_engine(settings)
    dsn = settings.database_url.get_secret_value()
    listener = Listener(dsn)
    notifier = Notifier(dsn)
    try:
        async with (
            httpx.AsyncClient(
                trust_env=False, limits=httpx.Limits(max_connections=settings.tool_concurrency)
            ) as client,
            httpx.AsyncClient(
                trust_env=False,
                follow_redirects=False,
                timeout=settings.model_timeout_seconds,
                limits=httpx.Limits(max_connections=settings.model_gateway_concurrency * 6),
            ) as model_client,
        ):
            tools = ToolGateway(client, specs, settings.tool_concurrency)
            model = build_model(settings, tools.schemas)
            runtime = RuntimeEngine(model, tools, settings)
            repository = RunRepository(engine)
            platform = PlatformServices(engine, repository, settings, snapshot, specs, model_client)
            factory = RuntimeFactory(runtime, platform, client, settings, tools.limit)
            platform.evaluation = EvaluationService(engine, platform.agents, factory)
            yield RuntimeServices(
                repository,
                RuntimeWorker(repository, factory, settings, fingerprint, notifier, listener),
                # Memory tasks run as a concurrent loop inside the runtime
                # worker process; no extra container role is introduced.
                MemoryWorker(engine, SummaryService(engine, platform.gateway, settings), settings),
                snapshot,
                platform,
                listener,
                notifier,
            )
    finally:
        await engine.dispose()
        await listener.close()
        await notifier.close()
