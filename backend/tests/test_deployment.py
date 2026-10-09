import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from agent_platform.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "deployment_bootstrap", ROOT / "deploy/container/bootstrap.py"
)
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


def configuration(**kwargs):
    return Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://test:test@localhost/test",
        environment="production",
        api_token="a-valid-deployment-token",
        embedded_worker=False,
        model_backend="openai",
        model_name="test-model",
        model_api_key="test-key",
        **kwargs,
    )


def test_local_token_is_persistent_and_never_blank(tmp_path):
    path = tmp_path / "credentials/local-access.token"
    first = bootstrap.local_token(path)
    assert len(first) >= 32
    assert bootstrap.local_token(path) == first
    path.write_text("injected\nnginx directive")
    with pytest.raises(RuntimeError, match="Invalid local"):
        bootstrap.local_token(path)


def test_production_rejects_demo_and_placeholder_credentials():
    settings = configuration()
    bootstrap.validate_production(settings)
    bootstrap.validate_production(
        settings.model_copy(
            update={"model_backend": "unconfigured", "model_name": "", "model_api_key": None}
        )
    )
    for changes in (
        {"model_backend": "demo"},
        {"api_token": "REPLACE_ME"},
        {"embedded_worker": True},
    ):
        with pytest.raises(RuntimeError):
            bootstrap.validate_production(settings.model_copy(update=changes))
    with pytest.raises(ValidationError, match="quickstart_mode requires demo"):
        configuration(quickstart_mode=True)


def test_seed_industries_defaults_off_and_is_env_overridable(monkeypatch):
    # Production must stay clean unless the operator opts in explicitly.
    assert configuration().seed_industries is False
    monkeypatch.setenv("AGENT_SEED_INDUSTRIES", "true")
    assert (
        Settings(
            _env_file=None,
            database_url="postgresql+asyncpg://test:test@localhost/test",
        ).seed_industries
        is True
    )


@pytest.mark.asyncio
async def test_resolve_published_chat_selection_rules():
    from agent_platform.apps.seed_industries import resolve_published_chat

    class Catalog:
        def __init__(self, rows):
            self.rows = rows

        async def list(self, tenant, kind):
            assert kind == "profiles"
            return self.rows

    def platform(rows):
        return SimpleNamespace(gateway=SimpleNamespace(catalog=Catalog(rows)))

    def row(identifier, name, operation, version, enabled=True):
        return {
            "id": identifier,
            "name": name,
            "spec": {"operation": operation},
            "published_version": version,
            "enabled": enabled,
        }

    assert await resolve_published_chat(platform([]), "t") == (None, None)
    # Draft, non-chat, and disabled profiles are all skipped.
    noise = [
        row("draft", "草稿方案", "chat", None),
        row("embed", "嵌入方案", "embedding", 3),
        row("disabled", "停用方案", "chat", 1, enabled=False),
    ]
    assert await resolve_published_chat(platform(noise), "t") == (None, None)
    # The demo profile seed.py publishes wins over newer published Chat profiles.
    rows = [row("newer", "生产方案", "chat", 9), row("demo", "示例客服模型方案", "chat", 1)]
    assert await resolve_published_chat(platform(rows), "t") == ("demo", 1)
    # With no demo profile named, the newest published Chat profile is used.
    assert await resolve_published_chat(
        platform([row("only", "某个 Chat 方案", "chat", 4)]), "t"
    ) == (
        "only",
        4,
    )


@pytest.mark.asyncio
async def test_unconfigured_model_never_falls_back_to_demo():
    from agent_platform.modules.agent_runtime.schemas import RuntimeFault
    from agent_platform.modules.model_gateway.service import build_model

    settings = configuration().model_copy(
        update={"model_backend": "unconfigured", "model_name": "", "model_api_key": None}
    )
    with pytest.raises(RuntimeFault, match="model_not_configured"):
        await build_model(settings, []).ainvoke([])


@pytest.mark.asyncio
async def test_existing_encrypted_credentials_require_original_key(tmp_path, monkeypatch):
    class Engine:
        @asynccontextmanager
        async def connect(self):
            yield self

        async def scalar(self, statement):
            return 1

        async def dispose(self):
            pass

    monkeypatch.setattr(bootstrap, "create_engine", lambda _: Engine())
    with pytest.raises(RuntimeError, match="original master.key"):
        await bootstrap.initialize_key(
            SimpleNamespace(model_credential_key_file=tmp_path / "missing.key")
        )
    assert not (tmp_path / "missing.key").exists()


def test_deployment_topologies_and_no_secret_copy():
    import yaml

    quick = yaml.safe_load((ROOT / "deploy/quickstart/compose.yaml").read_text())
    prod = yaml.safe_load((ROOT / "deploy/production/compose.yaml").read_text())
    assert set(quick["services"]) == {"postgres", "app"}
    # Quickstart must default to loopback-only; NEXUSDESK_BIND may widen it.
    assert quick["services"]["app"]["ports"][0].startswith("${NEXUSDESK_BIND:-127.0.0.1}:")
    assert "ports" not in quick["services"]["postgres"]
    assert set(prod["services"]) == {"migrate", "api", "web", "runtime-worker", "knowledge-worker"}
    for name in ("api", "runtime-worker", "knowledge-worker"):
        assert prod["services"][name]["environment"]["AGENT_EMBEDDED_WORKER"] == "false"
        assert (
            prod["services"][name]["depends_on"]["migrate"]["condition"]
            == "service_completed_successfully"
        )
    assert "__LOCAL_TOKEN__" not in (ROOT / "deploy/production/nginx.conf").read_text()
    # Quickstart loads the industry case set; production stays clean unless opted in.
    assert quick["services"]["app"]["environment"]["AGENT_SEED_INDUSTRIES"] == "true"
    for name in ("api", "runtime-worker", "knowledge-worker"):
        assert "AGENT_SEED_INDUSTRIES" not in prod["services"][name]["environment"]
    assert "AGENT_SEED_INDUSTRIES=false" in (ROOT / "deploy/production/.env.example").read_text()
    quick_nginx = (ROOT / "deploy/quickstart/nginx.conf.template").read_text()
    assert "proxy_set_header Authorization $http_authorization;" in quick_nginx
    # Quickstart injects the local admin token only as a placeholder that the
    # container bootstrap renders from AGENT_API_TOKEN; no real token is copied.
    assert quick_nginx.count("__LOCAL_TOKEN__") == 1
    assert 'set $nexusdesk_auth "Bearer __LOCAL_TOKEN__";' in quick_nginx
    assert "**/*.key" in (ROOT / ".dockerignore").read_text()
