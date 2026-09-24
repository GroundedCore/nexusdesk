"""Enterprise identities, SSO and short-lived embedded chat sessions."""

from alembic import op

revision = "0027_enterprise_identity"
down_revision = "0026_open_integrations"
branch_labels = None
depends_on = None


def upgrade():
    for statement in ["\n    CREATE TABLE enterprise_providers (\n      id uuid PRIMARY KEY, tenant_id text NOT NULL, name text NOT NULL, kind text NOT NULL,\n      config jsonb NOT NULL, encrypted_secret text NOT NULL, enabled boolean NOT NULL DEFAULT false,\n      workbench boolean NOT NULL DEFAULT false, revision integer NOT NULL DEFAULT 1,\n      created_at timestamptz NOT NULL DEFAULT now())", "\n    CREATE INDEX enterprise_providers_tenant ON enterprise_providers(tenant_id)", "\n    CREATE TABLE enterprise_users (\n      id uuid PRIMARY KEY, tenant_id text NOT NULL, name text NOT NULL, enabled boolean NOT NULL DEFAULT true,\n      role text CHECK(role IN ('viewer','operator','admin')), revision integer NOT NULL DEFAULT 1,\n      created_at timestamptz NOT NULL DEFAULT now())", "\n    CREATE TABLE enterprise_identities (\n      id uuid PRIMARY KEY, tenant_id text NOT NULL, source text NOT NULL, subject text NOT NULL,\n      user_id uuid NOT NULL REFERENCES enterprise_users(id) ON DELETE CASCADE,\n      created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,source,subject))", "\n    CREATE TABLE enterprise_login_states (\n      state_hash text PRIMARY KEY, tenant_id text NOT NULL,\n      provider_id uuid NOT NULL REFERENCES enterprise_providers(id) ON DELETE CASCADE,\n      provider_revision integer NOT NULL, browser_hash text NOT NULL, payload jsonb NOT NULL,\n      expires_at timestamptz NOT NULL DEFAULT now()+interval '5 minutes')", "\n    CREATE TABLE open_embed_configs (\n      app_id uuid PRIMARY KEY REFERENCES open_applications(id) ON DELETE CASCADE,\n      enabled boolean NOT NULL DEFAULT false, origins text[] NOT NULL DEFAULT '{}',\n      provider_ids uuid[] NOT NULL DEFAULT '{}', title text NOT NULL DEFAULT '企业助手',\n      color text NOT NULL DEFAULT '#6356d9', revision integer NOT NULL DEFAULT 1)", "\n    CREATE TABLE enterprise_sessions (\n      id uuid PRIMARY KEY, tenant_id text NOT NULL,\n      user_id uuid NOT NULL REFERENCES enterprise_users(id) ON DELETE CASCADE,\n      app_id uuid REFERENCES open_applications(id) ON DELETE CASCADE,\n      provider_id uuid REFERENCES enterprise_providers(id) ON DELETE CASCADE, provider_revision integer,\n      key_id uuid REFERENCES open_api_keys(id) ON DELETE CASCADE,\n      embed_revision integer, token_hash text UNIQUE NOT NULL,\n      expires_at timestamptz NOT NULL, revoked boolean NOT NULL DEFAULT false,\n      created_at timestamptz NOT NULL DEFAULT now())", "\n    CREATE INDEX enterprise_sessions_expiry ON enterprise_sessions(expires_at)", "\n    "]:
        if statement.strip():
            op.execute(statement)


def downgrade():
    for table in [
        "enterprise_sessions",
        "open_embed_configs",
        "enterprise_login_states",
        "enterprise_identities",
        "enterprise_users",
        "enterprise_providers",
    ]:
        op.execute(f"DROP TABLE {table}")
