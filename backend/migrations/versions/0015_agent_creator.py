"""Persist agent creators and backfill from tenant-scoped creation audit records."""

from alembic import op

revision = "0015_agent_creator"
down_revision = "0014_agent_locale_codes"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE agents ADD COLUMN created_by TEXT")
    op.execute("""UPDATE agents a SET created_by=(
        SELECT NULLIF(r.actor,'') FROM audit_records r
        WHERE r.tenant_id=a.tenant_id AND r.resource=CAST(a.id AS text)
          AND r.action='agent.created'
        ORDER BY r.id LIMIT 1
    )""")
    op.execute("""CREATE INDEX agent_tenant_creator_created
        ON agents(tenant_id,created_by,created_at DESC,id DESC)""")


def downgrade():
    op.execute("DROP INDEX agent_tenant_creator_created")
    op.execute("ALTER TABLE agents DROP COLUMN created_by")
