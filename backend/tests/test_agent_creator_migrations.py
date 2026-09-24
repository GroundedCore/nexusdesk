from uuid import uuid4

import pytest
from test_ticket_migrations import migrate, query
from test_ticket_migrations import migration_database as _migration_database

migration_database = _migration_database
pytestmark = pytest.mark.postgres


def test_creator_backfill_uses_original_creation_in_same_tenant(migration_database):
    url = migration_database
    migrate(url, "0014_agent_locale_codes")
    known, unknown = uuid4(), uuid4()
    for aid in [known, unknown]:
        query(
            url,
            "INSERT INTO agents(id,tenant_id,name,draft) VALUES($1,'demo','原始内容','{}')",
            aid,
        )
    for tenant, actor, action, aid in [
        ("other", "wrong", "agent.created", known),
        ("demo", "editor", "agent.updated", known),
        ("demo", "staff:original", "agent.created", known),
        ("demo", "later", "agent.created", known),
        ("other", "wrong", "agent.created", unknown),
    ]:
        query(
            url,
            "INSERT INTO audit_records(tenant_id,actor,action,resource) VALUES($1,$2,$3,$4)",
            tenant,
            actor,
            action,
            str(aid),
        )
    migrate(url, "head")
    assert query(url, "SELECT created_by,name FROM agents WHERE id=$1", known)[0] == (
        "staff:original",
        "原始内容",
    )
    assert query(url, "SELECT created_by FROM agents WHERE id=$1", unknown)[0][0] is None
    migrate(url, "0014_agent_locale_codes", "downgrade")
    assert query(url, "SELECT count(*) FROM agents")[0][0] == 2
    migrate(url, "head")
    assert query(url, "SELECT created_by FROM agents WHERE id=$1", known)[0][0] == "staff:original"
