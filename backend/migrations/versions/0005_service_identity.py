"""Named staff credentials and service state revisions."""

from alembic import op

revision = "0005_service_identity"
down_revision = "0004_tool_versions"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE staff_accounts(id UUID PRIMARY KEY,tenant_id TEXT NOT NULL,name TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('operator','viewer')),token_hash TEXT NOT NULL UNIQUE,
        enabled BOOLEAN NOT NULL DEFAULT true,created_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    op.execute("ALTER TABLE runtime_conversations ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
    op.execute("ALTER TABLE handoffs ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
    op.execute("ALTER TABLE channels ADD COLUMN signing_secret_ref TEXT")
    op.execute("""CREATE FUNCTION increment_service_revision() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN NEW.revision=OLD.revision+1; RETURN NEW; END $$""")
    op.execute(
        "CREATE TRIGGER conversation_revision BEFORE UPDATE OF mode,assigned_to,agent_id ON runtime_conversations FOR EACH ROW EXECUTE FUNCTION increment_service_revision()"
    )
    op.execute(
        "CREATE TRIGGER handoff_revision BEFORE UPDATE OF status,assignee ON handoffs FOR EACH ROW EXECUTE FUNCTION increment_service_revision()"
    )


def downgrade():
    op.execute("DROP TRIGGER conversation_revision ON runtime_conversations")
    op.execute("DROP TRIGGER handoff_revision ON handoffs")
    op.execute("DROP FUNCTION increment_service_revision()")
    op.execute("ALTER TABLE channels DROP COLUMN signing_secret_ref")
    op.execute("ALTER TABLE handoffs DROP COLUMN revision")
    op.execute("ALTER TABLE runtime_conversations DROP COLUMN revision")
    op.execute("DROP TABLE staff_accounts")
