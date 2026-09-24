"""Application-scoped enterprise API access."""

from alembic import op

revision = "0025_open_platform"
down_revision = "0024_chunk_preview_tasks"
branch_labels = None
depends_on = None


def upgrade():
    statements = """CREATE TABLE open_applications (
        id uuid PRIMARY KEY, tenant_id text NOT NULL, name text NOT NULL,
        description text NOT NULL DEFAULT '', enabled boolean NOT NULL DEFAULT true,
        agent_ids uuid[] NOT NULL DEFAULT '{}', rpm integer NOT NULL CHECK(rpm BETWEEN 1 AND 10000),
        max_concurrency integer NOT NULL CHECK(max_concurrency BETWEEN 1 AND 100),
        revision integer NOT NULL DEFAULT 1, created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now());
        CREATE INDEX open_apps_tenant ON open_applications(tenant_id,created_at);
        CREATE TABLE open_api_keys (
        id uuid PRIMARY KEY, app_id uuid NOT NULL REFERENCES open_applications(id) ON DELETE CASCADE,
        token_hash text NOT NULL UNIQUE, prefix text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz,
        revoked_at timestamptz);
        CREATE TABLE open_sessions (
        conversation_id uuid PRIMARY KEY REFERENCES runtime_conversations(id) ON DELETE CASCADE,
        app_id uuid NOT NULL REFERENCES open_applications(id) ON DELETE CASCADE,
        external_user_id text NOT NULL, external_session_id text NOT NULL, agent_id uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(app_id,external_user_id,external_session_id));
        CREATE TABLE open_runs (
        run_id uuid PRIMARY KEY REFERENCES runtime_runs(id) ON DELETE CASCADE,
        app_id uuid NOT NULL REFERENCES open_applications(id) ON DELETE CASCADE,
        external_user_id text NOT NULL, conversation_id uuid NOT NULL REFERENCES open_sessions(conversation_id) ON DELETE CASCADE,
        idempotency_key text NOT NULL, payload_hash text NOT NULL, request_id uuid NOT NULL,
        UNIQUE(app_id,external_user_id,idempotency_key));
        CREATE TABLE open_rate_windows (
        app_id uuid PRIMARY KEY REFERENCES open_applications(id) ON DELETE CASCADE,
        window_start timestamptz NOT NULL, requests integer NOT NULL);
        CREATE TABLE open_request_logs (
        id uuid PRIMARY KEY, tenant_id text NOT NULL,
        app_id uuid REFERENCES open_applications(id) ON DELETE CASCADE,
        key_id uuid, route text NOT NULL, method text NOT NULL, status integer NOT NULL,
        duration_ms integer NOT NULL, error_code text, run_id uuid,
        created_at timestamptz NOT NULL DEFAULT now());
        CREATE INDEX open_logs_app_time ON open_request_logs(tenant_id,app_id,created_at DESC);
    """
    for statement in statements.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade():
    for table in (
        "open_request_logs",
        "open_rate_windows",
        "open_runs",
        "open_sessions",
        "open_api_keys",
        "open_applications",
    ):
        op.execute(f"DROP TABLE {table}")
