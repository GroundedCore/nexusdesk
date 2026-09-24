from alembic import op

revision = "0007_vector_index"
down_revision = "0006_knowledge_jobs"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE knowledge_vector_indexes(kb_id UUID PRIMARY KEY REFERENCES knowledge_bases(id),
        tenant_id TEXT NOT NULL,collection_name TEXT NOT NULL,profile_id UUID NOT NULL,profile_version INTEGER NOT NULL,
        dimension INTEGER NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now())""")


def downgrade():
    op.execute("DROP TABLE knowledge_vector_indexes")
