"""Enterprise integration: version pins, knowledge synchronization and webhooks."""

from alembic import op

revision = "0026_open_integrations"
down_revision = "0025_open_platform"
branch_labels = None
depends_on = None


def upgrade():
    statements = """
    ALTER TABLE open_applications ADD COLUMN agent_versions jsonb NOT NULL DEFAULT '{}', ADD COLUMN knowledge_base_ids uuid[] NOT NULL DEFAULT '{}';
    CREATE TABLE open_synced_documents (
      app_id uuid NOT NULL REFERENCES open_applications(id) ON DELETE CASCADE,
      kb_id uuid NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
      external_id text NOT NULL, document_id uuid NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
      payload_hash text NOT NULL, synced_version integer NOT NULL, deleted boolean NOT NULL DEFAULT false,
      updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(app_id,kb_id,external_id));
    CREATE TABLE open_sync_tasks (
      task_id uuid PRIMARY KEY REFERENCES knowledge_tasks(id) ON DELETE CASCADE,
      app_id uuid NOT NULL REFERENCES open_applications(id) ON DELETE CASCADE,
      kb_id uuid NOT NULL, idempotency_key text NOT NULL, UNIQUE(app_id,idempotency_key));
    CREATE TABLE open_webhooks (
      app_id uuid PRIMARY KEY REFERENCES open_applications(id) ON DELETE CASCADE,
      url text NOT NULL, encrypted_secret text NOT NULL, enabled boolean NOT NULL DEFAULT false,
      revision integer NOT NULL DEFAULT 1, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());
    CREATE TABLE open_deliveries (
      id uuid PRIMARY KEY, app_id uuid NOT NULL REFERENCES open_applications(id) ON DELETE CASCADE,
      event_key text NOT NULL, event_type text NOT NULL, resource_id uuid NOT NULL,
      webhook_revision integer NOT NULL, payload jsonb NOT NULL,
      status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','succeeded','failed','cancelled')),
      attempts integer NOT NULL DEFAULT 0, next_attempt_at timestamptz NOT NULL DEFAULT now(),
      owner uuid, lease_until timestamptz, http_status integer, error_code text,
      created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(app_id,event_key));
    CREATE INDEX open_deliveries_due ON open_deliveries(status,next_attempt_at);
    """
    for statement in statements.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade():
    for table in ["open_deliveries", "open_webhooks", "open_sync_tasks", "open_synced_documents"]:
        op.execute(f"DROP TABLE {table}")
    op.execute(
        "ALTER TABLE open_applications DROP COLUMN agent_versions, DROP COLUMN knowledge_base_ids"
    )
