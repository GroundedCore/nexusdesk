"""Independent local administrator credentials and sessions."""

from alembic import op

revision = "0028_local_admin"
down_revision = "0027_enterprise_identity"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE local_admin_credentials (
        user_id uuid PRIMARY KEY REFERENCES enterprise_users(id) ON DELETE CASCADE,
        tenant_id text NOT NULL, username text NOT NULL,
        password_hash text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(tenant_id,username))""")
    op.execute("""CREATE TABLE local_admin_sessions (
        token_hash text PRIMARY KEY,
        user_id uuid NOT NULL REFERENCES local_admin_credentials(user_id) ON DELETE CASCADE,
        tenant_id text NOT NULL, expires_at timestamptz NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now())""")
    op.execute("CREATE INDEX local_admin_sessions_user ON local_admin_sessions(user_id)")
    op.execute("""CREATE TABLE local_login_limits (
        tenant_id text NOT NULL, bucket text NOT NULL, attempts integer NOT NULL,
        expires_at timestamptz NOT NULL, PRIMARY KEY(tenant_id,bucket))""")


def downgrade():
    for table in ("local_login_limits", "local_admin_sessions", "local_admin_credentials"):
        op.execute(f"DROP TABLE {table}")
