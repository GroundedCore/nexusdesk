"""Conversation rolling summary column and the generic memory task queue."""

from alembic import op

revision = "0030_conversation_summary"
down_revision = "0029_default_admin"
branch_labels = None
depends_on = None


def upgrade():
    # The rolling summary lives on the conversation row, so it inherits tenant
    # isolation and the row lifecycle (a deleted conversation drops its summary).
    op.execute("ALTER TABLE runtime_conversations ADD COLUMN summary TEXT")
    # Generic async memory task table; Phase 2+ extraction tasks reuse it with a
    # different kind. Follows the runtime_runs claim pattern (SKIP LOCKED).
    op.execute("""
        CREATE TABLE memory_tasks (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id TEXT NOT NULL,
            kind VARCHAR(30) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'running', 'done', 'failed')),
            payload JSONB NOT NULL,
            attempts INT NOT NULL DEFAULT 0,
            max_attempts INT NOT NULL DEFAULT 2,
            error TEXT,
            owner UUID,
            run_after TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX memory_tasks_claim ON memory_tasks (status, run_after) WHERE status = 'pending'"
    )


def downgrade():
    op.execute("DROP TABLE memory_tasks")
    op.execute("ALTER TABLE runtime_conversations DROP COLUMN summary")
