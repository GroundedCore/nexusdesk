"""Gateway governance, scoped keys, accounting and alert events.

Additive migration; existing resources and published snapshots remain valid.
"""

from alembic import op

revision = "0010_gateway_governance"
down_revision = "0009_ticket_settings"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("connections", "models", "profiles"):
        op.execute(
            f"ALTER TABLE gateway_{table} ADD COLUMN archived BOOLEAN NOT NULL DEFAULT false"
        )
    for table in ("sensitive_words", "access_keys", "alert_rules"):
        op.execute(f"""CREATE TABLE gateway_{table} (
            id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL,
            spec JSONB NOT NULL, revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
            enabled BOOLEAN NOT NULL DEFAULT true, archived BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
        op.execute(f"CREATE INDEX ON gateway_{table}(tenant_id,created_at DESC,id)")
    op.execute("ALTER TABLE gateway_access_keys ADD COLUMN token_hash TEXT UNIQUE NOT NULL")
    op.execute("ALTER TABLE gateway_access_keys ADD COLUMN prefix TEXT NOT NULL")
    op.execute("""CREATE TABLE gateway_model_settings (
        model_id UUID PRIMARY KEY REFERENCES gateway_models(id), tenant_id TEXT NOT NULL,
        spec JSONB NOT NULL, revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    op.execute("""CREATE TABLE gateway_quotas (
        tenant_id TEXT PRIMARY KEY, spec JSONB NOT NULL,
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    op.execute("""CREATE TABLE gateway_quota_counters (
        tenant_id TEXT NOT NULL, scope TEXT NOT NULL, bucket TEXT NOT NULL,
        requests BIGINT NOT NULL DEFAULT 0 CHECK(requests>=0),
        PRIMARY KEY(tenant_id,scope,bucket))""")
    op.execute("""CREATE TABLE gateway_leases (
        id UUID PRIMARY KEY, call_id UUID NOT NULL, tenant_id TEXT NOT NULL, scope TEXT NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL)""")
    op.execute("CREATE INDEX ON gateway_leases(tenant_id,scope,expires_at)")
    op.execute("ALTER TABLE gateway_calls ADD COLUMN actor TEXT")
    op.execute("ALTER TABLE gateway_calls ADD COLUMN access_key_id UUID")
    op.execute("ALTER TABLE gateway_calls ADD COLUMN review JSONB NOT NULL DEFAULT '{}'::jsonb")
    op.execute("""CREATE TABLE gateway_costs (
        attempt_id UUID PRIMARY KEY REFERENCES gateway_attempts(id) ON DELETE CASCADE,
        tenant_id TEXT NOT NULL, model_id UUID NOT NULL, call_id UUID NOT NULL,
        amount NUMERIC(24,8), currency TEXT NOT NULL DEFAULT 'CNY',
        pricing JSONB NOT NULL, usage JSONB,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    op.execute("CREATE INDEX ON gateway_costs(tenant_id,created_at)")
    op.execute("""CREATE TABLE gateway_alert_events (
        id UUID PRIMARY KEY, tenant_id TEXT NOT NULL,
        rule_id UUID NOT NULL REFERENCES gateway_alert_rules(id) ON DELETE CASCADE,
        call_id UUID NOT NULL, rule_name TEXT NOT NULL, metric TEXT NOT NULL,
        detail JSONB NOT NULL, acknowledged BOOLEAN NOT NULL DEFAULT false,
        acknowledged_by TEXT, acknowledged_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(rule_id,call_id))""")
    op.execute("CREATE INDEX ON gateway_alert_events(tenant_id,created_at DESC)")
    op.execute("CREATE INDEX ON gateway_calls(tenant_id,status,created_at DESC,id)")


def downgrade():
    # Do not silently discard keys, policy, accounting or review records in production.
    checks = [
        f"EXISTS(SELECT 1 FROM gateway_{table})"
        for table in (
            "sensitive_words",
            "access_keys",
            "alert_rules",
            "model_settings",
            "quotas",
            "quota_counters",
            "leases",
            "costs",
            "alert_events",
        )
    ]
    checks += [
        f"EXISTS(SELECT 1 FROM gateway_{table} WHERE archived)"
        for table in ("connections", "models", "profiles")
    ]
    checks.append(
        "EXISTS(SELECT 1 FROM gateway_calls WHERE actor IS NOT NULL OR access_key_id IS NOT NULL OR review<>'{}'::jsonb)"
    )
    op.execute(
        "DO $$ BEGIN IF "
        + " OR ".join(checks)
        + " THEN RAISE EXCEPTION 'gateway_governance_downgrade_requires_data_export'; END IF; END $$"
    )
    for table in ("connections", "models", "profiles"):
        op.execute(f"ALTER TABLE gateway_{table} DROP COLUMN archived")
    for table in (
        "alert_events",
        "costs",
        "leases",
        "quota_counters",
        "quotas",
        "model_settings",
        "alert_rules",
        "access_keys",
        "sensitive_words",
    ):
        op.execute(f"DROP TABLE gateway_{table}")
    for column in ("actor", "access_key_id", "review"):
        op.execute(f"ALTER TABLE gateway_calls DROP COLUMN {column}")
    op.execute("DROP INDEX gateway_calls_tenant_id_status_created_at_id_idx")
