"""Track cancellable chunk previews and durable task progress."""

from alembic import op

revision = "0024_chunk_preview_tasks"
down_revision = "0023_semantic_chunk_cache"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE knowledge_tasks DROP CONSTRAINT knowledge_tasks_kind_check")
    op.execute(
        "ALTER TABLE knowledge_tasks ADD CONSTRAINT knowledge_tasks_kind_check CHECK(kind IN ('parse','index','preview'))"
    )
    op.execute(
        "ALTER TABLE knowledge_tasks ADD COLUMN progress JSONB NOT NULL DEFAULT '{}', ADD COLUMN result JSONB"
    )


def downgrade():
    op.execute("DELETE FROM knowledge_tasks WHERE kind='preview'")
    op.execute("ALTER TABLE knowledge_tasks DROP COLUMN progress, DROP COLUMN result")
    op.execute("ALTER TABLE knowledge_tasks DROP CONSTRAINT knowledge_tasks_kind_check")
    op.execute(
        "ALTER TABLE knowledge_tasks ADD CONSTRAINT knowledge_tasks_kind_check CHECK(kind IN ('parse','index'))"
    )
