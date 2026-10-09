from uuid import uuid4

import pytest
from test_ticket_migrations import migrate, query
from test_ticket_migrations import migration_database as _migration_database

pytestmark = pytest.mark.postgres
migration_database = _migration_database


def test_credentials_migration_guards_saved_keys(migration_database):
    url = migration_database
    migrate(url, "0015_agent_creator")
    identifier = uuid4()
    query(
        url,
        "INSERT INTO gateway_connections(id,tenant_id,name,spec) VALUES($1,'legacy','preserved','{}')",
        identifier,
    )
    migrate(url, "head")
    query(
        url,
        "INSERT INTO gateway_credentials(connection_id,tenant_id,encrypted_secret) VALUES($1,'legacy','test-ciphertext')",
        identifier,
    )
    result = migrate(url, "0015_agent_creator", "downgrade", success=False)
    assert "gateway_credentials_downgrade_requires_data_export" in result.stderr
    assert query(url, "SELECT encrypted_secret FROM gateway_credentials")[0][0] == "test-ciphertext"
    query(url, "DELETE FROM gateway_credentials")
    migrate(url, "0015_agent_creator", "downgrade")
    migrate(url, "head")
    assert (
        query(url, "SELECT name FROM gateway_connections WHERE id=$1", identifier)[0][0]
        == "preserved"
    )


def test_gateway_upgrade_preserves_resources_and_guards_downgrade(migration_database):
    url = migration_database
    migrate(url, "0009_ticket_settings")
    identifier = uuid4()
    query(
        url,
        "INSERT INTO gateway_connections(id,tenant_id,name,spec) VALUES($1,'legacy','existing connection','{\"protocol\":\"demo\"}')",
        identifier,
    )
    migrate(url, "head")
    assert query(url, "SELECT name,archived FROM gateway_connections WHERE id=$1", identifier)[0][
        :
    ] == ("existing connection", False)
    word = uuid4()
    query(
        url,
        "INSERT INTO gateway_sensitive_words(id,tenant_id,name,spec) VALUES($1,'legacy','test','{}')",
        word,
    )
    result = migrate(url, "0009_ticket_settings", "downgrade", success=False)
    assert "gateway_governance_downgrade_requires_data_export" in result.stderr
    # Only the fixture's disposable database is cleared, never a configured business DB.
    query(url, "DELETE FROM gateway_sensitive_words WHERE id=$1", word)
    migrate(url, "0009_ticket_settings", "downgrade")
    assert (
        query(url, "SELECT name FROM gateway_connections WHERE id=$1", identifier)[0][0]
        == "existing connection"
    )
    migrate(url, "head")
    assert query(url, "SELECT version_num FROM alembic_version")[0][0] == "0029_default_admin"
