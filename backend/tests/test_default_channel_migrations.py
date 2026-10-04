import json
from uuid import uuid4

import pytest

from agent_platform.modules.model_gateway.catalog import Catalog
from agent_platform.modules.model_gateway.contracts import Connection
from agent_platform.settings import Settings
from test_ticket_migrations import migrate, query
from test_ticket_migrations import migration_database as _migration_database

pytestmark = pytest.mark.postgres
migration_database = _migration_database


def test_default_channels_fresh_install_preserve_edits_and_archives(
    migration_database, monkeypatch
):
    tenant = "fresh-channel-test"
    monkeypatch.setenv("AGENT_TENANT_ID", tenant)
    url = migration_database
    migrate(url, "head")
    rows = query(
        url, "SELECT id,name,spec,enabled FROM gateway_connections WHERE tenant_id=$1", tenant
    )
    assert {row[1] for row in rows} == {
        "DeepSeek",
        "豆包 (Doubao)",
        "千问 (Qwen)",
        "Kimi",
        "智谱 GLM",
        "腾讯混元",
        "小米",
        "Claude (Anthropic)",
        "GPT (OpenAI)",
        "Gemini (Google)",
    }
    # Seed data must satisfy the built-in allowlist, not a local .env override.
    catalog = Catalog(
        None,
        Settings(
            model_gateway_allowed_hosts=Settings.model_fields[
                "model_gateway_allowed_hosts"
            ].default
        ),
    )
    for row in rows:
        spec = json.loads(row[2])
        assert not row[3] and spec["credential_ref"] is None and "api_key" not in spec
        catalog.validate_address(Connection.model_validate(spec).model_dump())
    identifier = rows[0][0]
    query(
        url,
        "UPDATE gateway_connections SET name='renamed',revision=2,archived=true,spec=jsonb_set(spec,'{name}','\"renamed\"') WHERE id=$1",
        identifier,
    )
    query(
        url,
        "INSERT INTO gateway_credentials(connection_id,tenant_id,encrypted_secret) VALUES($1,$2,'preserved-ciphertext')",
        identifier,
        tenant,
    )
    migrate(url, "0016_gateway_credentials", "downgrade")
    migrate(url, "head")
    assert (
        query(url, "SELECT count(*) FROM gateway_connections WHERE tenant_id=$1", tenant)[0][0] == 10
    )
    assert query(
        url, "SELECT name,revision,archived FROM gateway_connections WHERE id=$1", identifier
    )[0][:] == ("renamed", 2, True)
    assert (
        query(
            url,
            "SELECT encrypted_secret FROM gateway_credentials WHERE connection_id=$1",
            identifier,
        )[0][0]
        == "preserved-ciphertext"
    )


def test_existing_tenant_channel_is_not_duplicated_or_overwritten(migration_database, monkeypatch):
    monkeypatch.setenv("AGENT_TENANT_ID", "new-workspace")
    url = migration_database
    migrate(url, "0016_gateway_credentials")
    identifier = uuid4()
    spec = json.dumps(
        {
            "name": "Our DeepSeek",
            "protocol": "openai_compatible",
            "base_url": "https://api.deepseek.com/",
            "credential_ref": "AGENT_MODEL_SECRET_CUSTOM",
            "concurrency": 3,
        }
    )
    query(
        url,
        "INSERT INTO gateway_connections(id,tenant_id,name,spec,enabled) VALUES($1,'existing','Our DeepSeek',$2::jsonb,true)",
        identifier,
        spec,
    )
    migrate(url, "head")
    assert (
        query(url, "SELECT count(*) FROM gateway_connections WHERE tenant_id='existing'")[0][0] == 10
    )
    assert (
        query(url, "SELECT count(*) FROM gateway_connections WHERE tenant_id='new-workspace'")[0][0]
        == 10
    )
    row = query(url, "SELECT name,spec,enabled FROM gateway_connections WHERE id=$1", identifier)[0]
    assert row[0] == "Our DeepSeek" and json.loads(row[1]) == json.loads(spec) and row[2]
