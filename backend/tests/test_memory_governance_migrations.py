"""Phase 4 migration 0037 — P4-CM-01 (online migration on existing data).

Assert per image (the migration itself is image-agnostic; run on both the
alpine and pgvector containers):

- ``memory_items.protected`` exists, NOT NULL DEFAULT false;
- backfill: pre-existing ``source_type='human'`` rows become protected,
  extracted rows stay unprotected (R18 invariant from day one);
- the flag controlled-key CHECK accepts vip/complaint_risk/blacklist and
  rejects any other flag key, while non-flag keys stay free-form;
- downgrade removes both pieces and re-upgrade is clean.
"""

from uuid import uuid4

import pytest
from test_ticket_migrations import head_revision, migrate, query
from test_ticket_migrations import migration_database as _migration_database

migration_database = _migration_database
pytestmark = pytest.mark.postgres


def _columns(url, table):
    return {
        row[0]
        for row in query(
            url,
            "SELECT column_name FROM information_schema.columns WHERE table_name=$1",
            table,
        )
    }


def _insert_item(url, item_type, key, source_type, content="x"):
    item = uuid4()
    customer = query(url, "SELECT id FROM memory_customers WHERE tenant_id='t' LIMIT 1")[0][0]
    query(
        url,
        "INSERT INTO memory_items(id,tenant_id,customer_id,type,key,content,source_type)"
        " VALUES($1,'t',$2,$3,$4,$5,$6)",
        item,
        customer,
        item_type,
        key,
        content,
        source_type,
    )
    return item


def test_0037_protected_column_backfill_and_flag_check(migration_database):
    url = migration_database
    migrate(url, "0036_memory_events")
    query(url, "INSERT INTO memory_customers(id,tenant_id) VALUES($1,'t')", uuid4())
    human = _insert_item(url, "profile", "name", "human", "张女士")
    extracted = _insert_item(url, "preference", "language", "extracted", "中文")

    migrate(url, "head")
    assert query(url, "SELECT version_num FROM alembic_version")[0][0] == head_revision()

    # Column exists with the right nullability/default.
    assert "protected" in _columns(url, "memory_items")
    col = query(
        url,
        "SELECT is_nullable, column_default FROM information_schema.columns"
        " WHERE table_name='memory_items' AND column_name='protected'",
    )[0]
    assert col[0] == "NO" and col[1] == "false"

    # R18 backfill: human rows protected, extracted rows not.
    assert query(url, "SELECT protected FROM memory_items WHERE id=$1", human)[0][0] is True
    assert query(url, "SELECT protected FROM memory_items WHERE id=$1", extracted)[0][0] is False

    # CHECK: the three controlled flag keys are legal, anything else is not.
    for key in ("vip", "complaint_risk", "blacklist"):
        _insert_item(url, "flag", key, "human", "标记说明")
    import asyncpg

    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        _insert_item(url, "flag", "custom_flag", "extracted")
    # Non-flag keys stay free-form (constraint is conditional on type='flag').
    free = _insert_item(url, "profile", "custom_whatever", "extracted")
    assert query(url, "SELECT 1 FROM memory_items WHERE id=$1", free)

    # Downgrade removes both pieces; re-upgrade is clean and re-backfills.
    migrate(url, "0036_memory_events", "downgrade")
    assert "protected" not in _columns(url, "memory_items")
    assert not query(
        url,
        "SELECT conname FROM pg_constraint WHERE conname='memory_items_flag_key_check'",
    )
    migrate(url, "head")
    assert query(url, "SELECT protected FROM memory_items WHERE id=$1", human)[0][0] is True
