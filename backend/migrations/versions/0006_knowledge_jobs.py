"""Document-level ingestion queue with page checkpoints."""

from alembic import op

revision = "0006_knowledge_jobs"
down_revision = "0005_service_identity"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE knowledge_jobs(id UUID PRIMARY KEY,tenant_id TEXT NOT NULL,
        kb_id UUID REFERENCES knowledge_bases(id),filename TEXT NOT NULL,content BYTEA NOT NULL,
        content_hash TEXT NOT NULL,config JSONB NOT NULL,status TEXT NOT NULL DEFAULT 'queued',
        attempts INTEGER NOT NULL DEFAULT 0,owner UUID,lease_until TIMESTAMPTZ,cancel_requested BOOLEAN NOT NULL DEFAULT false,
        error_code TEXT,document_id UUID,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE(tenant_id,kb_id,content_hash))""")
    op.execute("CREATE INDEX ON knowledge_jobs(tenant_id,status,created_at)")
    op.execute("""CREATE TABLE knowledge_job_pages(job_id UUID REFERENCES knowledge_jobs(id) ON DELETE CASCADE,
        page_num INTEGER NOT NULL,payload JSONB NOT NULL,PRIMARY KEY(job_id,page_num))""")
    op.execute("ALTER TABLE knowledge_versions ADD COLUMN chunking JSONB")


def downgrade():
    op.execute("ALTER TABLE knowledge_versions DROP COLUMN chunking")
    op.execute("DROP TABLE knowledge_job_pages")
    op.execute("DROP TABLE knowledge_jobs")
