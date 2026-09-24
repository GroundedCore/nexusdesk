"""Persist semantic previews so pagination and draft saves reuse model results."""

from alembic import op

revision = "0023_semantic_chunk_cache"
down_revision = "0022_tool_workspace"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE knowledge_semantic_cache (
        document_id UUID NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
        cache_key TEXT NOT NULL,
        chunks JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY(document_id, cache_key)
    )""")


def downgrade():
    op.drop_table("knowledge_semantic_cache")
