"""Encrypted, version-pinned HTTP tool credentials outside public snapshots."""

from alembic import op

revision = "0021_tool_credentials"
down_revision = "0020_knowledge_lifecycle"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE tool_credentials (
        id UUID PRIMARY KEY,
        tool_id UUID NOT NULL REFERENCES registered_tools(id) ON DELETE CASCADE,
        tenant_id TEXT NOT NULL,
        encrypted_secret TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    op.execute("CREATE INDEX tool_credentials_tenant ON tool_credentials(tenant_id)")


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM tool_credentials) THEN
            RAISE EXCEPTION 'tool_credentials_downgrade_requires_data_export';
        END IF;
    END $$""")
    op.execute("DROP TABLE tool_credentials")
