"""Encrypted provider credentials kept outside public configuration snapshots."""

from alembic import op

revision = "0016_gateway_credentials"
down_revision = "0015_agent_creator"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE gateway_credentials (
        connection_id UUID PRIMARY KEY REFERENCES gateway_connections(id) ON DELETE CASCADE,
        tenant_id TEXT NOT NULL,
        encrypted_secret TEXT NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    op.execute("CREATE INDEX gateway_credentials_tenant ON gateway_credentials(tenant_id)")


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM gateway_credentials) THEN
            RAISE EXCEPTION 'gateway_credentials_downgrade_requires_data_export';
        END IF;
    END $$""")
    op.execute("DROP TABLE gateway_credentials")
