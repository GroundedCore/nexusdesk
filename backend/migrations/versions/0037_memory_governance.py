"""Phase 4 of agent memory: governance, decay and flag memories.

Purely additive (08-implementation-breakdown R1/R3/R18):

- ``memory_items.protected``: redundant fast-filter marker for
  ``source_type='human'`` rows (FR-1/FR-6.1). Backfilled from the existing
  data so the invariant ``protected <=> source_type='human'`` holds from the
  start; the three write paths (edit_item / release_item / this backfill)
  keep it in step afterwards (R18).
- flag controlled-key CHECK constraint: only ``vip`` / ``complaint_risk`` /
  ``blacklist`` are legal flag keys (FR-2). The constraint is conditional on
  ``type='flag'`` so profile/preference/event keys stay free-form.

Everything else Phase 4 needs already exists: ``expires_at`` /
``last_referenced_at`` / ``reference_count`` / the ``expired`` status value
came with 0035, and ``memory_tasks.kind`` is an unconstrained VARCHAR so the
new ``reinforce`` kind needs no migration (Phase 3 ``event_write``
precedent).
"""

from alembic import op

revision = "0037_memory_governance"
down_revision = "0036_memory_events"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE memory_items ADD COLUMN protected BOOLEAN NOT NULL DEFAULT false")
    # R18 backfill: human-maintained rows are protected from day one.
    op.execute("UPDATE memory_items SET protected=true WHERE source_type='human'")
    # No extra index: decay/capacity scans are already per-customer through
    # memory_items_lookup (tenant_id, customer_id, type, status).
    op.execute(
        "ALTER TABLE memory_items ADD CONSTRAINT memory_items_flag_key_check "
        "CHECK (type <> 'flag' OR key IN ('vip', 'complaint_risk', 'blacklist'))"
    )


def downgrade():
    op.execute("ALTER TABLE memory_items DROP CONSTRAINT memory_items_flag_key_check")
    op.execute("ALTER TABLE memory_items DROP COLUMN protected")
