"""Preserve attribution for conversations belonging to permanently deleted agents."""

from alembic import op

revision = "0012_agent_deletion"
down_revision = "0011_agent_workspace"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""ALTER TABLE runtime_conversations ADD COLUMN deleted_agent JSONB
        CHECK (deleted_agent IS NULL OR
            (jsonb_typeof(deleted_agent)='object' AND agent_id IS NULL AND mode='closed'))""")


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM runtime_conversations WHERE deleted_agent IS NOT NULL) THEN
            RAISE EXCEPTION 'agent_deletion_downgrade_requires_history_export';
        END IF;
    END $$""")
    op.execute("ALTER TABLE runtime_conversations DROP COLUMN deleted_agent")
