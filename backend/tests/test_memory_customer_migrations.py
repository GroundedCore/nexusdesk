"""P2-CM-01: migration 0035 is online-safe and reversible.

It only adds three new tables plus a nullable ``customer_id`` column on
``runtime_conversations``; legacy rows keep ``customer_id = NULL``.
"""

from uuid import uuid4

import pytest
from test_ticket_migrations import migrate, query
from test_ticket_migrations import migration_database as _migration_database

migration_database = _migration_database
pytestmark = pytest.mark.postgres


def test_customer_memory_migration_preserves_existing_conversations(migration_database):
    url = migration_database
    migrate(url, "0030_conversation_summary")
    cid = uuid4()
    query(
        url,
        "INSERT INTO runtime_conversations(id,tenant_id,external_id,history) VALUES($1,'legacy','s1','[]')",
        cid,
    )
    migrate(url, "head")
    # The legacy conversation carries no customer; the column is nullable.
    row = query(url, "SELECT customer_id FROM runtime_conversations WHERE id=$1", cid)[0]
    assert row[0] is None
    # All three new tables exist, plus the lookup index.
    tables = {
        row[0]
        for row in query(
            url,
            "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'memory_%'",
        )
    }
    assert {"memory_customers", "memory_identities", "memory_items"} <= tables
    assert query(
        url,
        "SELECT indexname FROM pg_indexes WHERE tablename='memory_items' AND indexname='memory_items_lookup'",
    )
    # Identity uniqueness is per (tenant, source, external_key).
    customer = uuid4()
    query(url, "INSERT INTO memory_customers(id,tenant_id) VALUES($1,'t')", customer)
    query(
        url,
        "INSERT INTO memory_identities(tenant_id,customer_id,source,external_key) VALUES('t',$1,'channel','k1')",
        customer,
    )
    with pytest.raises(Exception, match="unique"):
        query(
            url,
            "INSERT INTO memory_identities(tenant_id,customer_id,source,external_key) VALUES('t',$1,'channel','k1')",
            customer,
        )
    # Item uniqueness is per (tenant, customer, type, key); the status
    # vocabulary is enforced.
    query(
        url,
        "INSERT INTO memory_items(tenant_id,customer_id,type,key,content,source_type) VALUES('t',$1,'profile','name','张三','extracted')",
        customer,
    )
    with pytest.raises(Exception, match="unique"):
        query(
            url,
            "INSERT INTO memory_items(tenant_id,customer_id,type,key,content,source_type) VALUES('t',$1,'profile','name','李四','extracted')",
            customer,
        )
    with pytest.raises(Exception, match="check constraint"):
        query(
            url,
            "INSERT INTO memory_items(tenant_id,customer_id,type,key,content,source_type,status) VALUES('t',$1,'profile','phone','138','extracted','bogus')",
            customer,
        )
    item = query(
        url,
        "SELECT status,revision,confidence,source_type,reference_count FROM memory_items",
    )[0]
    assert item[:] == ("active", 1, 1.0, "extracted", 0)
    # The conversation can reference the customer (FK), and starts NULL-able.
    query(url, "UPDATE runtime_conversations SET customer_id=$1 WHERE id=$2", customer, cid)
    assert query(url, "SELECT customer_id FROM runtime_conversations WHERE id=$1", cid)[0][
        0
    ] == customer
    # Downgrade removes the column and all three tables.
    query(url, "UPDATE runtime_conversations SET customer_id=NULL WHERE id=$1", cid)
    migrate(url, "0034_channel_access_tokens", "downgrade")
    assert not query(
        url,
        "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename IN ('memory_customers','memory_identities','memory_items')",
    )
    assert not query(
        url,
        "SELECT column_name FROM information_schema.columns WHERE table_name='runtime_conversations' AND column_name='customer_id'",
    )
    assert query(url, "SELECT id FROM runtime_conversations WHERE id=$1", cid)
    migrate(url, "head")
