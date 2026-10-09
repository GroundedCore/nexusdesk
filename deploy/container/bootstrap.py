"""Container initialization. Never print connection strings or credentials."""

import asyncio
import os
import secrets
import subprocess
import sys
from pathlib import Path

from sqlalchemy import text

from agent_platform.apps.local_tls import ensure_certificate
from agent_platform.platform.identity.local_admin import seed_default_admin
from agent_platform.platform.persistence.database import create_engine
from agent_platform.platform.secrets.vault import CredentialVault
from agent_platform.settings import Settings


def local_token(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            os.chmod(path, 0o600)
            stream.write(secrets.token_urlsafe(48))
    except FileExistsError:
        pass
    value = path.read_text(encoding="utf-8").strip()
    if len(value) < 32 or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        for c in value
    ):
        raise RuntimeError("Invalid local access token file")
    return value


def validate_production(settings):
    if (
        settings.environment != "production"
        or settings.quickstart_mode
        or settings.embedded_worker
    ):
        raise RuntimeError(
            "Production requires production environment and separate workers"
        )
    for name in (
        "database_url",
        "api_token",
        "operator_api_token",
        "viewer_api_token",
        "model_name",
        "model_api_key",
        "milvus_token",
        "knowledge_s3_access_key",
        "knowledge_s3_secret_key",
    ):
        value = getattr(settings, name)
        value = (
            value.get_secret_value() if hasattr(value, "get_secret_value") else value
        )
        if value and "REPLACE" in str(value):
            raise RuntimeError("Replace deployment placeholders before starting")
    if settings.model_backend == "demo":
        raise RuntimeError("Production deployment must not use the demo default model")
    if not settings.require_password_change:
        # Quickstart turns this off so its shared demo account signs straight in.
        # Production must never inherit that, or the seeded default stays valid.
        raise RuntimeError(
            "Production must require the first-login password change"
        )


async def wait_database(settings):
    engine = create_engine(settings)
    try:
        for attempt in range(30):
            try:
                async with engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
                return
            except Exception:  # noqa: BLE001 -- bounded startup retry, credentials never logged
                if attempt == 29:
                    raise RuntimeError(
                        "Database connection failed; check deployment configuration"
                    ) from None
                await asyncio.sleep(2)
    finally:
        await engine.dispose()


async def initialize_key(settings):
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            existing = await connection.scalar(
                text(
                    "SELECT (SELECT count(*) FROM gateway_credentials) + (SELECT count(*) FROM tool_credentials)"
                )
            )
        if existing and not settings.model_credential_key_file.exists():
            raise RuntimeError(
                "Existing encrypted credentials require the original master.key; restore it before deployment"
            )
        CredentialVault(settings.model_credential_key_file).cipher(create=True)
    finally:
        await engine.dispose()


async def initialize_administrator(settings):
    engine = create_engine(settings)
    try:
        await seed_default_admin(engine, settings.tenant_id)
    finally:
        await engine.dispose()


def seed_industry_cases(settings):
    """Load the full industry demo case set. Idempotent; preserves manual edits."""
    command = [sys.executable, "-m", "agent_platform.apps.seed_industries"]
    if settings.quickstart_mode:
        command.append("--bind-published-chat")
    subprocess.run(command, check=True)


async def check_storage(settings):
    if settings.milvus_url:
        import httpx

        async with httpx.AsyncClient(trust_env=False, timeout=15) as client:
            response = await client.post(
                settings.milvus_url.rstrip("/") + "/v2/vectordb/collections/list",
                headers={
                    "Authorization": "Bearer "
                    + settings.milvus_token.get_secret_value()
                }
                if settings.milvus_token
                else {},
                json={},
            )
            response.raise_for_status()
            if response.json().get("code") != 0:
                raise RuntimeError("Milvus access check failed")
    if settings.knowledge_s3_bucket:
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "s3",
            endpoint_url=settings.knowledge_s3_endpoint,
            aws_access_key_id=settings.knowledge_s3_access_key.get_secret_value()
            if settings.knowledge_s3_access_key
            else None,
            aws_secret_access_key=settings.knowledge_s3_secret_key.get_secret_value()
            if settings.knowledge_s3_secret_key
            else None,
            config=Config(
                connect_timeout=5,
                read_timeout=10,
                retries={"max_attempts": 1},
                s3={"addressing_style": "path"},
            ),
        )
        await asyncio.to_thread(client.head_bucket, Bucket=settings.knowledge_s3_bucket)


def render_nginx_config(settings):
    """Render the quickstart nginx config, including its TLS material.

    Quickstart serves HTTPS so that a browser reaching the demo over a LAN address
    gets a secure context; plain HTTP on a non-loopback host would not be one, and
    the console relies on secure-only APIs. See agent_platform.apps.local_tls.
    """
    certificate, key, authority = ensure_certificate(
        Path("/data/tls"), settings.tls_hosts
    )
    config = (
        Path("/app/deploy/nginx.conf.template")
        .read_text()
        .replace("__TLS_CERTIFICATE__", str(certificate))
        .replace("__TLS_KEY__", str(key))
    )
    target = Path("/tmp/nexusdesk-nginx.conf")
    target.write_text(config)
    target.chmod(0o600)
    return authority


def main(mode):
    if mode == "quickstart":
        # Still generated even though nothing injects it any more: a configured
        # service token switches off the anonymous development fallback in
        # agent_platform.platform.identity.access, and it leaves an operator a
        # break-glass credential for the login page's service-token field.
        os.environ["AGENT_API_TOKEN"] = local_token(
            Path("/data/credentials/local-access.token")
        )
    settings = Settings()
    if mode == "quickstart":
        if not settings.quickstart_mode or settings.model_backend != "demo":
            raise RuntimeError("Quickstart requires explicit demo mode")
    elif mode == "migrate":
        validate_production(settings)
    else:
        raise RuntimeError("Unknown deployment mode")
    asyncio.run(wait_database(settings))
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
    asyncio.run(initialize_administrator(settings))
    asyncio.run(initialize_key(settings))
    if mode == "migrate":
        asyncio.run(check_storage(settings))
        if settings.seed_industries:
            seed_industry_cases(settings)
        print("Migrations and configured storage checks completed.", flush=True)
        return
    subprocess.run([sys.executable, "-m", "agent_platform.apps.seed"], check=True)
    if settings.seed_industries:
        seed_industry_cases(settings)
    authority = render_nginx_config(settings)
    print(
        "NexusDesk quickstart: https://localhost:8080 — local demonstration only\n"
        f"Self-signed certificate. Import {authority} as a trusted root to avoid the\n"
        "browser warning. Reaching it over a LAN address needs that address in\n"
        "AGENT_TLS_HOSTS, for example AGENT_TLS_HOSTS=192.168.1.50.",
        flush=True,
    )
    os.execvp("supervisord", ["supervisord", "-c", "/app/deploy/supervisord.conf"])


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except Exception as exc:  # noqa: BLE001 -- redact deployment secrets from diagnostics
        print(
            "Deployment initialization failed: "
            + (str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__)
            + ". See deployment README.",
            file=sys.stderr,
        )
        sys.exit(1)
