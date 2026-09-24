"""Versioned model catalog and metadata-only call records."""

from alembic import op

revision = "0003_model_gateway"
down_revision = "0002_platform"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("connections", "models", "profiles"):
        op.execute(f"""CREATE TABLE gateway_{table} (
            id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL,
            spec JSONB NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
            enabled BOOLEAN NOT NULL DEFAULT true, published_version INTEGER,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
        op.execute(f"CREATE INDEX ON gateway_{table}(tenant_id, created_at DESC)")
    op.execute("""CREATE TABLE gateway_profile_versions (
        profile_id UUID REFERENCES gateway_profiles(id) ON DELETE CASCADE,
        version INTEGER NOT NULL, snapshot JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(profile_id,version))""")
    op.execute("""CREATE TABLE gateway_calls (
        id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, profile_id UUID NOT NULL,
        profile_version INTEGER NOT NULL, operation TEXT NOT NULL, run_id UUID,
        status TEXT NOT NULL, error_code TEXT, duration_ms INTEGER,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), completed_at TIMESTAMPTZ)""")
    op.execute("CREATE INDEX ON gateway_calls(tenant_id,created_at DESC)")
    op.execute("""CREATE TABLE gateway_attempts (
        id UUID PRIMARY KEY, call_id UUID REFERENCES gateway_calls(id) ON DELETE CASCADE,
        model_id UUID NOT NULL, model_name TEXT NOT NULL, connection_id UUID NOT NULL,
        status TEXT NOT NULL, error_code TEXT, duration_ms INTEGER,
        usage JSONB, created_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    op.execute("CREATE INDEX ON gateway_attempts(call_id)")
    op.execute("""CREATE TABLE gateway_media (
        id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, mime_type TEXT NOT NULL,
        content BYTEA NOT NULL, expires_at TIMESTAMPTZ NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    op.execute("CREATE INDEX ON gateway_media(tenant_id,expires_at)")


def downgrade():
    for table in (
        "media",
        "attempts",
        "calls",
        "profile_versions",
        "profiles",
        "models",
        "connections",
    ):
        op.execute(f"DROP TABLE gateway_{table}")
