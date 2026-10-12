"""Customer entity, identity bindings and profile/preference memory items.

Phase 2 of agent memory. Purely additive: three new tables plus a nullable
``customer_id`` column on ``runtime_conversations``. ``tenant_id`` stays TEXT
to match every existing table (the design doc's UUID was idealised).
"""

from alembic import op

revision = "0035_memory_customers"
down_revision = "0034_channel_access_tokens"
branch_labels = None
depends_on = None


def upgrade():
    # Customer entity. One row per recognised real-world customer.
    op.execute("""
        CREATE TABLE memory_customers (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    # Identity bindings: one customer may carry several sources (cross-channel
    # merge arrives in Phase 4; the unique key keeps one binding per key).
    op.execute("""
        CREATE TABLE memory_identities (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id TEXT NOT NULL,
            customer_id UUID NOT NULL REFERENCES memory_customers(id) ON DELETE CASCADE,
            source VARCHAR(30) NOT NULL,
            external_key VARCHAR(200) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, source, external_key)
        )
    """)
    # Memory items. Phase 2 uses type profile/preference; event (Phase 3) and
    # flag (Phase 4) are intentionally not in a CHECK so later phases need no
    # constraint migration. The same-key unique constraint prevents stacking;
    # prev_content keeps single-level history for rollback/audit.
    op.execute("""
        CREATE TABLE memory_items (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id TEXT NOT NULL,
            customer_id UUID NOT NULL REFERENCES memory_customers(id) ON DELETE CASCADE,
            type VARCHAR(20) NOT NULL,
            key VARCHAR(100) NOT NULL,
            content TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 1.0,
            status VARCHAR(20) NOT NULL DEFAULT 'active'
                CHECK (status IN ('pending', 'active', 'expired', 'deleted')),
            source_type VARCHAR(20) NOT NULL,
            source_ref VARCHAR(100),
            prev_content TEXT,
            revision BIGINT NOT NULL DEFAULT 1,
            expires_at TIMESTAMPTZ,
            last_referenced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            reference_count INT NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, customer_id, type, key)
        )
    """)
    op.execute(
        "CREATE INDEX memory_items_lookup ON memory_items (tenant_id, customer_id, type, status)"
    )
    # Conversation -> customer link. NULL for playground, legacy and
    # unresolved conversations; no backfill is required.
    op.execute(
        "ALTER TABLE runtime_conversations ADD COLUMN customer_id UUID REFERENCES memory_customers(id)"
    )
    op.execute(
        "CREATE INDEX conversations_customer ON runtime_conversations (tenant_id, customer_id)"
    )


def downgrade():
    op.execute("DROP INDEX conversations_customer")
    op.execute("ALTER TABLE runtime_conversations DROP COLUMN customer_id")
    op.execute("DROP TABLE memory_items")
    op.execute("DROP TABLE memory_identities")
    op.execute("DROP TABLE memory_customers")
