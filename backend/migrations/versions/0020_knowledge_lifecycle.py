"""Upload identity, follow-up publication and durable vector cleanup."""

from alembic import op

revision = "0020_knowledge_lifecycle"
down_revision = "0019_knowledge_workspace"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE knowledge_documents ADD COLUMN content_hash TEXT")
    op.execute(
        "CREATE UNIQUE INDEX knowledge_upload_identity ON knowledge_documents(tenant_id,content_hash) WHERE content_hash IS NOT NULL AND processing<>'deleted'"
    )
    op.execute(
        "ALTER TABLE knowledge_bases ADD COLUMN index_requested BOOLEAN NOT NULL DEFAULT false"
    )
    op.execute("""CREATE TABLE knowledge_collection_cleanup (
        collection_name TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
        delete_after TIMESTAMPTZ NOT NULL DEFAULT now(), attempts INTEGER NOT NULL DEFAULT 0)""")


def downgrade():
    op.drop_table("knowledge_collection_cleanup")
    op.execute("ALTER TABLE knowledge_bases DROP COLUMN index_requested")
    op.execute("ALTER TABLE knowledge_documents DROP COLUMN content_hash")
