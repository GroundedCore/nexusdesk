"""Document workspace, immutable chunk drafts and per-library publication."""

from alembic import op

revision = "0019_knowledge_workspace"
down_revision = "0018_global_channels"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE knowledge_folders (
        id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL,
        parent_id UUID REFERENCES knowledge_folders(id), UNIQUE(tenant_id,id))""")
    op.execute("ALTER TABLE knowledge_bases ADD COLUMN settings JSONB NOT NULL DEFAULT '{}'")
    op.execute(
        "ALTER TABLE knowledge_bases ADD COLUMN published_settings JSONB NOT NULL DEFAULT '{}'"
    )
    op.execute("ALTER TABLE knowledge_bases ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
    op.execute("ALTER TABLE knowledge_documents ALTER COLUMN kb_id DROP NOT NULL")
    op.execute("ALTER TABLE knowledge_documents ADD COLUMN tenant_id TEXT")
    op.execute(
        "UPDATE knowledge_documents d SET tenant_id=k.tenant_id FROM knowledge_bases k WHERE k.id=d.kb_id"
    )
    op.execute("ALTER TABLE knowledge_documents ALTER COLUMN tenant_id SET NOT NULL")
    op.execute("""ALTER TABLE knowledge_documents
        ADD COLUMN folder_id UUID REFERENCES knowledge_folders(id),
        ADD COLUMN filename TEXT,
        ADD COLUMN file_size BIGINT NOT NULL DEFAULT 0,
        ADD COLUMN original BYTEA,
        ADD COLUMN processing TEXT NOT NULL DEFAULT 'ready',
        ADD COLUMN error_code TEXT,
        ADD COLUMN created_by TEXT NOT NULL DEFAULT 'system',
        ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now()""")
    op.execute(
        "CREATE INDEX knowledge_document_tenant ON knowledge_documents(tenant_id,updated_at DESC)"
    )
    op.execute("ALTER TABLE knowledge_versions ADD COLUMN pages JSONB NOT NULL DEFAULT '[]'")
    op.execute("ALTER TABLE knowledge_chunks ADD COLUMN source JSONB NOT NULL DEFAULT '{}'")
    op.execute("ALTER TABLE knowledge_chunks ADD COLUMN enabled BOOLEAN NOT NULL DEFAULT true")
    op.execute("""CREATE TABLE knowledge_links (
        kb_id UUID REFERENCES knowledge_bases(id) ON DELETE CASCADE,
        document_id UUID REFERENCES knowledge_documents(id) ON DELETE CASCADE,
        draft_version INTEGER NOT NULL, published_version INTEGER,
        enabled BOOLEAN NOT NULL DEFAULT true,
        PRIMARY KEY(kb_id,document_id),
        FOREIGN KEY(document_id,draft_version) REFERENCES knowledge_versions(document_id,version),
        FOREIGN KEY(document_id,published_version) REFERENCES knowledge_versions(document_id,version))""")
    op.execute(
        "INSERT INTO knowledge_links SELECT kb_id,id,current_version,current_version,true FROM knowledge_documents"
    )
    op.execute("""CREATE TABLE knowledge_tasks (
        id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('parse','index')),
        resource_id UUID NOT NULL, config JSONB NOT NULL DEFAULT '{}',
        status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','completed','failed','cancelled')),
        owner UUID, lease_until TIMESTAMPTZ, attempts INTEGER NOT NULL DEFAULT 0,
        cancel_requested BOOLEAN NOT NULL DEFAULT false, error_code TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    op.execute(
        "CREATE UNIQUE INDEX knowledge_active_task ON knowledge_tasks(tenant_id,kind,resource_id) WHERE status IN ('queued','running')"
    )
    op.execute("CREATE INDEX knowledge_task_queue ON knowledge_tasks(status,created_at)")


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM knowledge_documents WHERE kb_id IS NULL)
        OR EXISTS(SELECT 1 FROM knowledge_tasks) THEN RAISE EXCEPTION 'knowledge workspace contains data'; END IF; END $$""")
    op.drop_table("knowledge_tasks")
    op.drop_table("knowledge_links")
    op.execute("ALTER TABLE knowledge_chunks DROP COLUMN source, DROP COLUMN enabled")
    op.execute("ALTER TABLE knowledge_versions DROP COLUMN pages")
    op.execute("""ALTER TABLE knowledge_documents DROP COLUMN tenant_id, DROP COLUMN folder_id,
        DROP COLUMN filename, DROP COLUMN file_size, DROP COLUMN original, DROP COLUMN processing,
        DROP COLUMN error_code, DROP COLUMN created_by, DROP COLUMN updated_at""")
    op.execute("ALTER TABLE knowledge_documents ALTER COLUMN kb_id SET NOT NULL")
    op.execute(
        "ALTER TABLE knowledge_bases DROP COLUMN settings, DROP COLUMN published_settings, DROP COLUMN revision"
    )
    op.drop_table("knowledge_folders")
