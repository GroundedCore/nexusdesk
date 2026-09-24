"""Minimal platform and tenant ticket settings; Agent configuration remains in agents."""

from alembic import op

revision = "0009_ticket_settings"
down_revision = "0008_ticketing"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE platform_ticket_policy (
        id SMALLINT PRIMARY KEY DEFAULT 1 CHECK(id=1),
        available BOOLEAN NOT NULL DEFAULT true,
        allowed_modes TEXT[] NOT NULL DEFAULT ARRAY['internal']::text[],
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
        updated_by TEXT NOT NULL DEFAULT 'migration',
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CHECK(allowed_modes <@ ARRAY['internal','external']::text[]),
        CHECK(array_position(allowed_modes,NULL) IS NULL))""")
    op.execute("INSERT INTO platform_ticket_policy(id) VALUES(1)")
    op.execute("""CREATE TABLE tenant_ticket_settings (
        tenant_id TEXT PRIMARY KEY,
        mode TEXT NOT NULL DEFAULT 'internal' CHECK(mode IN ('internal','external','disabled')),
        lifecycle TEXT NOT NULL DEFAULT 'enabled' CHECK(lifecycle IN ('enabled','draining','disabled','emergency_disabled')),
        integration_id UUID,
        previous_mode TEXT CHECK(previous_mode IN ('internal','external')),
        previous_integration_id UUID,
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
        updated_by TEXT NOT NULL DEFAULT 'migration',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CHECK((mode='external' AND integration_id IS NOT NULL) OR (mode<>'external' AND integration_id IS NULL)),
        CHECK((mode='disabled' AND lifecycle='disabled') OR
              (mode<>'disabled' AND lifecycle IN ('enabled','draining','emergency_disabled'))),
        CHECK((previous_mode='external' AND previous_integration_id IS NOT NULL) OR
              (previous_mode IS DISTINCT FROM 'external' AND previous_integration_id IS NULL)))""")
    op.execute("""INSERT INTO tenant_ticket_settings(tenant_id)
        SELECT tenant_id FROM tickets UNION SELECT tenant_id FROM agents
        UNION SELECT tenant_id FROM runtime_conversations""")


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM platform_ticket_policy WHERE NOT available OR
            allowed_modes<>ARRAY['internal']::text[] OR revision<>1)
        OR EXISTS(SELECT 1 FROM tenant_ticket_settings WHERE mode<>'internal' OR lifecycle<>'enabled'
            OR integration_id IS NOT NULL OR previous_mode IS NOT NULL OR revision<>1)
        THEN RAISE EXCEPTION 'ticket_settings_downgrade_requires_data_export'; END IF;
        END $$""")
    op.execute("DROP TABLE tenant_ticket_settings")
    op.execute("DROP TABLE platform_ticket_policy")
