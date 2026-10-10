"""P1-CM-01: migration 0030 is online-safe — it only adds a column and a table."""

from uuid import uuid4

import pytest
from test_ticket_migrations import migrate, query
from test_ticket_migrations import migration_database as _migration_database

migration_database = _migration_database
pytestmark = pytest.mark.postgres


def test_summary_migration_preserves_existing_conversations(migration_database):
    url = migration_database
    migrate(url, "0029_default_admin")
    cid = uuid4()
    query(
        url,
        "INSERT INTO runtime_conversations(id,tenant_id,external_id,history) VALUES($1,'legacy','s1','[]')",
        cid,
    )
    migrate(url, "head")
    row = query(url, "SELECT summary FROM runtime_conversations WHERE id=$1", cid)[0]
    assert row[0] is None
    assert query(
        url,
        "SELECT indexname FROM pg_indexes WHERE tablename='memory_tasks' AND indexname='memory_tasks_claim'",
    )
    # The task table enforces its status vocabulary.
    with pytest.raises(Exception, match="check constraint"):
        query(
            url,
            "INSERT INTO memory_tasks(tenant_id,kind,status,payload) VALUES('t','conversation_summary','bogus','{}')",
        )
    query(
        url,
        "INSERT INTO memory_tasks(tenant_id,kind,payload) VALUES('t','conversation_summary','{}')",
    )
    task = query(url, "SELECT status,attempts,max_attempts FROM memory_tasks")[0]
    assert task[:] == ("pending", 0, 2)
    migrate(url, "0029_default_admin", "downgrade")
    assert not query(
        url,
        "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename='memory_tasks'",
    )
    assert not query(
        url,
        "SELECT column_name FROM information_schema.columns WHERE table_name='runtime_conversations' AND column_name='summary'",
    )
    migrate(url, "head")
