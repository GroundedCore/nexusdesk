"""Persistent runtime queue and events."""

from alembic import op

revision = "0001_runtime"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE runtime_conversations (
            id UUID PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            external_id TEXT NOT NULL,
            history JSONB NOT NULL DEFAULT '[]',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, external_id)
        )
    """)
    op.execute("""
        CREATE TABLE runtime_runs (
            id UUID PRIMARY KEY,
            conversation_id UUID NOT NULL REFERENCES runtime_conversations(id),
            tenant_id TEXT NOT NULL,
            input TEXT NOT NULL,
            config JSONB NOT NULL,
            status TEXT NOT NULL CHECK (status IN
                ('queued', 'running', 'completed', 'failed', 'cancelled')),
            cancel_requested BOOLEAN NOT NULL DEFAULT false,
            output TEXT,
            error_code TEXT,
            event_seq INTEGER NOT NULL DEFAULT 0,
            owner UUID,
            lease_until TIMESTAMPTZ,
            queue_expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            started_at TIMESTAMPTZ,
            finished_at TIMESTAMPTZ
        )
    """)
    op.execute("""CREATE UNIQUE INDEX runtime_one_active_conversation
        ON runtime_runs(conversation_id) WHERE status IN ('queued', 'running')""")
    op.execute("CREATE INDEX runtime_queue ON runtime_runs(status, created_at)")
    op.execute("CREATE INDEX runtime_tenant ON runtime_runs(tenant_id, status)")
    op.execute("""
        CREATE TABLE runtime_events (
            run_id UUID NOT NULL REFERENCES runtime_runs(id) ON DELETE CASCADE,
            seq INTEGER NOT NULL,
            type TEXT NOT NULL,
            data JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (run_id, seq)
        )
    """)


def downgrade():
    op.drop_table("runtime_events")
    op.drop_table("runtime_runs")
    op.drop_table("runtime_conversations")
