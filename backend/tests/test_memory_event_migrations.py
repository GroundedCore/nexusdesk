"""Phase 3 migration 0036 — deployment/migration cases (P3-DP-01/02/03).

The pgvector probe block makes the same migration behave differently per
server image; assertions branch on what ``pg_available_extensions`` offers:

- pgvector image (P3-DP-01): ``embedding vector(1536)`` + HNSW index, vector
  extension installed by the probe.
- plain postgres image (P3-DP-02): no embedding column, migration still
  succeeds; ``content_tsv`` + GIN index and ``embedding_failed`` exist either
  way, as does ``ticket_process_log.is_commitment``.
- in-place image switch (P3-DP-03, migration-level part): rows written before
  the migration survive it; downgrade removes the additive pieces and a
  re-upgrade restores them.
"""

from uuid import uuid4

import pytest
from test_ticket_migrations import head_revision, migrate, query
from test_ticket_migrations import migration_database as _migration_database

migration_database = _migration_database
pytestmark = pytest.mark.postgres


def _server_has_pgvector(url) -> bool:
    return bool(query(url, "SELECT 1 FROM pg_available_extensions WHERE name='vector'"))


def _columns(url, table):
    return {
        row[0]
        for row in query(
            url,
            "SELECT column_name FROM information_schema.columns WHERE table_name=$1",
            table,
        )
    }


def test_0036_probe_block_matches_server_capability(migration_database):
    url = migration_database
    migrate(url, "head")
    assert query(url, "SELECT version_num FROM alembic_version")[0][0] == head_revision()

    memory_columns = _columns(url, "memory_items")
    # Always-on pieces: keyword fallback column + retry marker.
    assert "content_tsv" in memory_columns
    assert "embedding_failed" in memory_columns
    assert "is_commitment" in _columns(url, "ticket_process_log")
    assert query(
        url,
        "SELECT indexname FROM pg_indexes WHERE tablename='memory_items' AND indexname='memory_items_tsv'",
    )

    if _server_has_pgvector(url):
        # P3-DP-01: the probe created the extension, column and HNSW index.
        assert "embedding" in memory_columns
        assert query(url, "SELECT extname FROM pg_extension WHERE extname='vector'")
        assert query(
            url,
            "SELECT indexname FROM pg_indexes WHERE tablename='memory_items' AND indexname='memory_items_embedding'",
        )
        width = query(
            url,
            "SELECT atttypmod FROM pg_attribute WHERE attrelid='memory_items'::regclass AND attname='embedding'",
        )[0][0]
        assert width == 1536
    else:
        # P3-DP-02: plain image migrates cleanly with no vector artifacts.
        assert "embedding" not in memory_columns
        assert not query(
            url,
            "SELECT indexname FROM pg_indexes WHERE tablename='memory_items' AND indexname='memory_items_embedding'",
        )


def test_0036_preserves_existing_rows_and_downgrades(migration_database):
    """In-place image switch (P3-DP-03 at the migration level): data survives
    the upgrade; downgrade + re-upgrade keep it too."""
    url = migration_database
    migrate(url, "0035_memory_customers")
    customer = uuid4()
    query(url, "INSERT INTO memory_customers(id,tenant_id) VALUES($1,'t')", customer)
    item = uuid4()
    query(
        url,
        "INSERT INTO memory_items(id,tenant_id,customer_id,type,key,content,source_type)"
        " VALUES($1,'t',$2,'event','ticket:TK-1:created','工单 TK-1 已创建：测试','ticket')",
        item,
        customer,
    )
    migrate(url, "head")
    row = query(url, "SELECT content,embedding_failed FROM memory_items WHERE id=$1", item)[0]
    assert row[0] == "工单 TK-1 已创建：测试" and row[1] is False
    # The generated keyword column reflects existing content.
    assert query(url, "SELECT content_tsv IS NOT NULL FROM memory_items WHERE id=$1", item)[0][0]

    migrate(url, "0035_memory_customers", "downgrade")
    memory_columns = _columns(url, "memory_items")
    assert "embedding" not in memory_columns
    assert "content_tsv" not in memory_columns
    assert "embedding_failed" not in memory_columns
    assert "is_commitment" not in _columns(url, "ticket_process_log")
    assert query(url, "SELECT content FROM memory_items WHERE id=$1", item)[0][0] == (
        "工单 TK-1 已创建：测试"
    )

    # Re-upgrade is clean (the DO-block probe is re-runnable).
    migrate(url, "head")
    assert query(url, "SELECT version_num FROM alembic_version")[0][0] == head_revision()
    assert query(url, "SELECT content FROM memory_items WHERE id=$1", item)[0][0] == (
        "工单 TK-1 已创建：测试"
    )
