"""P0 organization and access-control state."""

from alembic import op

revision = "0040_access_control"
down_revision = "0039_audit_ip"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE enterprise_users ADD COLUMN last_login_at timestamptz, "
        "ADD COLUMN last_active_at timestamptz, "
        "ADD COLUMN disabled_reason text, "
        "ADD COLUMN auth_version integer NOT NULL DEFAULT 1"
    )
    op.execute(
        "ALTER TABLE local_admin_credentials "
        "ADD COLUMN is_emergency boolean NOT NULL DEFAULT true"
    )
    op.execute(
        "ALTER TABLE local_admin_sessions "
        "ADD COLUMN id uuid NOT NULL DEFAULT gen_random_uuid(), "
        "ADD COLUMN ip text, "
        "ADD COLUMN user_agent text, "
        "ADD COLUMN last_seen_at timestamptz NOT NULL DEFAULT now()"
    )
    op.execute(
        "ALTER TABLE local_admin_sessions "
        "ADD CONSTRAINT local_admin_sessions_id_key UNIQUE(id)"
    )
    op.execute(
        "CREATE INDEX local_admin_sessions_tenant_user "
        "ON local_admin_sessions(tenant_id,user_id)"
    )
    op.execute(
        "ALTER TABLE enterprise_sessions "
        "ADD COLUMN ip text, "
        "ADD COLUMN user_agent text, "
        "ADD COLUMN last_seen_at timestamptz NOT NULL DEFAULT now()"
    )
    op.execute(
        "CREATE INDEX enterprise_sessions_tenant_user "
        "ON enterprise_sessions(tenant_id,user_id)"
    )


def downgrade():
    op.execute("DROP INDEX enterprise_sessions_tenant_user")
    op.execute(
        "ALTER TABLE enterprise_sessions "
        "DROP COLUMN last_seen_at, DROP COLUMN user_agent, DROP COLUMN ip"
    )
    op.execute("DROP INDEX local_admin_sessions_tenant_user")
    op.execute(
        "ALTER TABLE local_admin_sessions DROP CONSTRAINT local_admin_sessions_id_key"
    )
    op.execute(
        "ALTER TABLE local_admin_sessions "
        "DROP COLUMN last_seen_at, DROP COLUMN user_agent, DROP COLUMN ip, DROP COLUMN id"
    )
    op.execute("ALTER TABLE local_admin_credentials DROP COLUMN is_emergency")
    op.execute(
        "ALTER TABLE enterprise_users "
        "DROP COLUMN auth_version, "
        "DROP COLUMN disabled_reason, "
        "DROP COLUMN last_active_at, "
        "DROP COLUMN last_login_at"
    )
