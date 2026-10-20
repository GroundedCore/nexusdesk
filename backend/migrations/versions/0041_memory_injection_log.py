"""Phase 6 of agent memory: injection explainability log.

Purely additive: one new table ``memory_injection_log`` recording run × item
injection detail (which memory items entered which run, their section and
ranking score), plus its three lookup indexes.

- D1 (no foreign keys): items may be deleted/evicted and customers deleted
  or merged without the log blocking governance; cleanup is the governance
  cascade (delete/merge) plus the daily TTL pass
  (``memory_injection_log_retention_days``, default 90). ``run_id`` likewise
  has no FK — runtime data has its own lifecycle.
- D2: the rolling summary never logs a row (it has no item_id), so
  ``section`` covers only the four item types.
- Only ids/section/score persist, never memory content (Phase 4 C4 red
  line); rendering joins the current item row at query time and physically
  deleted items surface as "条目已删除".
- ``tenant_id`` stays TEXT to match every existing table.
"""

from alembic import op

revision = "0041_memory_injection_log"
down_revision = "0040_access_control"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE memory_injection_log (
            id BIGSERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            run_id UUID NOT NULL,
            customer_id UUID NOT NULL,
            item_id UUID NOT NULL,
            section VARCHAR(20) NOT NULL
                CHECK (section IN ('flag', 'profile', 'preference', 'event')),
            score REAL,
            injected_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX ix_injlog_customer"
        " ON memory_injection_log (tenant_id, customer_id, injected_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_injlog_item"
        " ON memory_injection_log (tenant_id, item_id, injected_at DESC)"
    )
    op.execute("CREATE INDEX ix_injlog_run ON memory_injection_log (run_id)")


def downgrade():
    op.execute("DROP TABLE memory_injection_log")
