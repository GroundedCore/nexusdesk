"""Agent workspace conversation source and catalog indexes."""

from alembic import op

revision = "0011_agent_workspace"
down_revision = "0010_gateway_governance"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE runtime_conversations ADD COLUMN source TEXT NOT NULL DEFAULT 'business' CHECK(source IN ('business','playground'))"
    )
    op.execute(
        "CREATE INDEX ix_conversations_agent_source ON runtime_conversations(tenant_id,agent_id,source,updated_at DESC,id DESC)"
    )
    op.execute("CREATE INDEX ix_agents_catalog ON agents(tenant_id,created_at DESC,id DESC)")


def downgrade():
    op.execute("DROP INDEX ix_agents_catalog")
    op.execute("DROP INDEX ix_conversations_agent_source")
    op.execute("ALTER TABLE runtime_conversations DROP COLUMN source")
