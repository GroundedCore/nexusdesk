"""Immutable HTTP tool releases and metadata-only execution records."""

from alembic import op

revision = "0004_tool_versions"
down_revision = "0003_model_gateway"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE registered_tools ADD COLUMN published_version INTEGER NOT NULL DEFAULT 1"
    )
    op.execute("""CREATE TABLE tool_versions (
        tool_id UUID REFERENCES registered_tools(id) ON DELETE CASCADE,
        version INTEGER NOT NULL, spec JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(tool_id,version))""")
    op.execute(
        "INSERT INTO tool_versions(tool_id,version,spec) SELECT id,1,spec FROM registered_tools"
    )
    op.execute("ALTER TABLE agent_versions ADD COLUMN tool_snapshot JSONB")
    op.execute("ALTER TABLE agents ADD COLUMN archived BOOLEAN NOT NULL DEFAULT false")
    op.execute("""CREATE TABLE tool_calls (
        id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, run_id UUID,
        tool_name TEXT NOT NULL, tool_version INTEGER, adapter_type TEXT NOT NULL,
        effect TEXT NOT NULL, status TEXT NOT NULL, error_code TEXT, duration_ms INTEGER,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    op.execute("CREATE INDEX ON tool_calls(tenant_id,created_at DESC)")


def downgrade():
    op.execute("DROP TABLE tool_calls")
    op.execute("ALTER TABLE agents DROP COLUMN archived")
    op.execute("ALTER TABLE agent_versions DROP COLUMN tool_snapshot")
    op.execute("DROP TABLE tool_versions")
    op.execute("ALTER TABLE registered_tools DROP COLUMN published_version")
