import pytest
from test_ticket_migrations import migrate, query
from test_ticket_migrations import migration_database as _migration_database

migration_database = _migration_database
pytestmark = pytest.mark.postgres


def test_channel_adapter_migration_adds_provider_state_and_rolls_back(migration_database):
    url = migration_database
    migrate(url, "0030_conversation_summary")
    migrate(url, "head")
    columns = {
        row[0]
        for row in query(
            url,
            """SELECT column_name FROM information_schema.columns
            WHERE table_name='channels'""",
        )
    }
    assert {
        "provider",
        "config",
        "encrypted_credentials",
        "last_error_code",
        "last_activity_at",
    }.issubset(columns)
    tables = {
        row[0]
        for row in query(
            url,
            """SELECT tablename FROM pg_tables
            WHERE schemaname='public'""",
        )
    }
    assert {
        "channel_conversations",
        "channel_deliveries",
        "channel_delivery_attempts",
        "channel_inbound_events",
    }.issubset(tables)
    migrate(url, "0030_conversation_summary", "downgrade")
    remaining = {
        row[0]
        for row in query(
            url,
            """SELECT tablename FROM pg_tables
            WHERE schemaname='public'""",
        )
    }
    assert "channel_deliveries" not in remaining
    assert "channel_inbound_events" not in remaining
    migrate(url, "head")
