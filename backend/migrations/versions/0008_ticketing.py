"""Typed tickets, JSONB details and append-only business timeline."""

from alembic import op

revision = "0008_ticketing"
down_revision = "0007_vector_index"
branch_labels = None
depends_on = None


def upgrade():
    for table in (
        "tickets",
        "pending_actions",
        "runtime_conversations",
        "runtime_runs",
        "staff_accounts",
    ):
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {table}_tenant_id_key UNIQUE(tenant_id,id)")
    op.execute("""CREATE TABLE ticket_types (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id TEXT NOT NULL,
        code VARCHAR(50) NOT NULL, name VARCHAR(100) NOT NULL,
        enabled BOOLEAN NOT NULL DEFAULT true,
        draft_definition JSONB NOT NULL DEFAULT '{"fields":[]}',
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0), published_version INTEGER,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE(tenant_id,id), UNIQUE(tenant_id,code),
        CHECK(jsonb_typeof(draft_definition)='object'))""")
    op.execute("""CREATE TABLE ticket_type_versions (
        tenant_id TEXT NOT NULL, type_id UUID NOT NULL, version INTEGER NOT NULL CHECK(version>0),
        schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version>0),
        name VARCHAR(100) NOT NULL, definition JSONB NOT NULL CHECK(jsonb_typeof(definition)='object'),
        created_by TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY(tenant_id,type_id,version),
        FOREIGN KEY(tenant_id,type_id) REFERENCES ticket_types(tenant_id,id) ON DELETE CASCADE)""")
    op.execute("""ALTER TABLE ticket_types ADD CONSTRAINT ticket_types_published_fk
        FOREIGN KEY(tenant_id,id,published_version)
        REFERENCES ticket_type_versions(tenant_id,type_id,version) DEFERRABLE INITIALLY DEFERRED""")
    op.execute("""ALTER TABLE tickets
        ADD COLUMN ticket_no VARCHAR(50), ADD COLUMN type_id UUID, ADD COLUMN type_version INTEGER,
        ADD COLUMN source_run_id UUID, ADD COLUMN customer_id VARCHAR(100),
        ADD COLUMN channel VARCHAR(30), ADD COLUMN priority SMALLINT NOT NULL DEFAULT 2,
        ADD COLUMN assignee_id UUID, ADD COLUMN created_by TEXT NOT NULL DEFAULT 'legacy',
        ADD COLUMN due_at TIMESTAMPTZ, ADD COLUMN resolved_at TIMESTAMPTZ,
        ADD COLUMN closed_at TIMESTAMPTZ,
        ADD CONSTRAINT tickets_priority_check CHECK(priority BETWEEN 1 AND 4),
        ADD CONSTRAINT tickets_revision_check CHECK(revision>0),
        ADD CONSTRAINT tickets_status_check CHECK(status IN ('open','in_progress','waiting_customer','resolved','closed')),
        ADD CONSTRAINT tickets_number_key UNIQUE(tenant_id,ticket_no)""")
    op.execute("""INSERT INTO ticket_types(tenant_id,code,name)
        SELECT DISTINCT tenant_id,'general','通用工单' FROM tickets""")
    op.execute("""INSERT INTO ticket_type_versions(tenant_id,type_id,version,name,definition,created_by)
        SELECT tenant_id,id,1,name,draft_definition,'legacy' FROM ticket_types""")
    op.execute("UPDATE ticket_types SET published_version=1")
    op.execute("""UPDATE tickets t SET type_id=k.id,type_version=1,ticket_no='TK-'||replace(t.id::text,'-','')
        FROM ticket_types k WHERE k.tenant_id=t.tenant_id AND k.code='general'""")
    op.execute("""ALTER TABLE tickets
        ALTER COLUMN ticket_no SET NOT NULL, ALTER COLUMN type_id SET NOT NULL,
        ALTER COLUMN type_version SET NOT NULL, ALTER COLUMN conversation_id DROP NOT NULL,
        DROP CONSTRAINT tickets_conversation_id_fkey, DROP CONSTRAINT tickets_action_id_fkey,
        ADD CONSTRAINT tickets_conversation_fk FOREIGN KEY(tenant_id,conversation_id)
            REFERENCES runtime_conversations(tenant_id,id) ON DELETE RESTRICT,
        ADD CONSTRAINT tickets_action_fk FOREIGN KEY(tenant_id,action_id)
            REFERENCES pending_actions(tenant_id,id) ON DELETE RESTRICT,
        ADD CONSTRAINT tickets_run_fk FOREIGN KEY(tenant_id,source_run_id)
            REFERENCES runtime_runs(tenant_id,id) ON DELETE RESTRICT,
        ADD CONSTRAINT tickets_assignee_fk FOREIGN KEY(tenant_id,assignee_id)
            REFERENCES staff_accounts(tenant_id,id) ON DELETE RESTRICT,
        ADD CONSTRAINT tickets_type_fk FOREIGN KEY(tenant_id,type_id,type_version)
            REFERENCES ticket_type_versions(tenant_id,type_id,version)""")
    op.execute("""CREATE TABLE tickets_detail (
        ticket_id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, description TEXT NOT NULL,
        resolution TEXT, custom_fields JSONB NOT NULL DEFAULT '{}',
        CHECK(jsonb_typeof(custom_fields)='object'),
        FOREIGN KEY(tenant_id,ticket_id) REFERENCES tickets(tenant_id,id) ON DELETE CASCADE)""")
    op.execute("""CREATE TABLE ticket_process_log (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        tenant_id TEXT NOT NULL,ticket_id UUID NOT NULL,
        actor_type VARCHAR(20) NOT NULL CHECK(actor_type IN ('staff','customer','agent','system')),
        actor_id TEXT NOT NULL, action_type VARCHAR(30) NOT NULL,
        visibility VARCHAR(20) NOT NULL DEFAULT 'internal' CHECK(visibility IN ('internal','customer')),
        content TEXT, old_status VARCHAR(30),new_status VARCHAR(30),
        old_assignee_id UUID,new_assignee_id UUID,changed_fields TEXT[],source_run_id UUID,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        FOREIGN KEY(tenant_id,ticket_id) REFERENCES tickets(tenant_id,id) ON DELETE CASCADE,
        FOREIGN KEY(tenant_id,source_run_id) REFERENCES runtime_runs(tenant_id,id) ON DELETE RESTRICT,
        CHECK(action_type IN ('created','assigned','status_changed','detail_changed','remark','reply','ai_summary','legacy_note')))""")
    op.execute(
        "INSERT INTO tickets_detail(ticket_id,tenant_id,description) SELECT id,tenant_id,description FROM tickets"
    )
    op.execute("""INSERT INTO ticket_process_log(tenant_id,ticket_id,actor_type,actor_id,action_type,content)
        SELECT tenant_id,id,'system','legacy-import','legacy_note',note FROM tickets WHERE note<>''""")
    op.drop_column("tickets", "description")
    op.drop_column("tickets", "note")
    op.execute("CREATE INDEX tickets_status_page ON tickets(tenant_id,status,created_at,id)")
    op.execute(
        "CREATE INDEX tickets_assignee_page ON tickets(tenant_id,assignee_id,status,updated_at,id)"
    )
    op.execute("CREATE INDEX ticket_process_page ON ticket_process_log(tenant_id,ticket_id,id)")


def downgrade():
    # Do not silently discard new-model data. A separate export/archive decision is required.
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM tickets WHERE conversation_id IS NULL OR status='waiting_customer'
            OR assignee_id IS NOT NULL OR source_run_id IS NOT NULL OR customer_id IS NOT NULL
            OR channel IS NOT NULL OR priority<>2 OR created_by<>'legacy'
            OR due_at IS NOT NULL OR resolved_at IS NOT NULL OR closed_at IS NOT NULL)
        OR EXISTS(SELECT 1 FROM tickets_detail WHERE custom_fields<>'{}'::jsonb OR resolution IS NOT NULL)
        OR EXISTS(SELECT 1 FROM ticket_types WHERE code<>'general' OR name<>'通用工单' OR NOT enabled
            OR draft_definition<>'{"fields":[]}'::jsonb OR revision<>1 OR published_version<>1)
        OR EXISTS(SELECT 1 FROM ticket_type_versions WHERE version<>1 OR definition<>'{"fields":[]}'::jsonb)
        OR EXISTS(SELECT 1 FROM ticket_process_log WHERE actor_id<>'legacy-import')
        THEN RAISE EXCEPTION 'ticketing_downgrade_requires_data_export'; END IF;
        END $$""")
    op.execute(
        "ALTER TABLE tickets ADD COLUMN description TEXT NOT NULL DEFAULT '', ADD COLUMN note TEXT NOT NULL DEFAULT ''"
    )
    op.execute(
        "UPDATE tickets t SET description=d.description FROM tickets_detail d WHERE d.ticket_id=t.id"
    )
    op.execute("""UPDATE tickets t SET note=COALESCE((SELECT content FROM ticket_process_log l
        WHERE l.ticket_id=t.id AND l.action_type IN ('legacy_note','remark') ORDER BY l.id DESC LIMIT 1),'')""")
    op.execute("ALTER TABLE tickets ALTER COLUMN description DROP DEFAULT")
    op.execute("DROP TABLE ticket_process_log")
    op.execute("DROP TABLE tickets_detail")
    for name in (
        "tickets_conversation_fk",
        "tickets_action_fk",
        "tickets_run_fk",
        "tickets_assignee_fk",
        "tickets_type_fk",
    ):
        op.execute(f"ALTER TABLE tickets DROP CONSTRAINT {name}")
    op.execute("DROP INDEX tickets_status_page")
    op.execute("DROP INDEX tickets_assignee_page")
    for name in (
        "ticket_no",
        "type_id",
        "type_version",
        "source_run_id",
        "customer_id",
        "channel",
        "priority",
        "assignee_id",
        "created_by",
        "due_at",
        "resolved_at",
        "closed_at",
    ):
        op.drop_column("tickets", name)
    op.execute(
        "ALTER TABLE tickets DROP CONSTRAINT tickets_status_check, DROP CONSTRAINT tickets_revision_check"
    )
    op.execute("""ALTER TABLE tickets ALTER COLUMN conversation_id SET NOT NULL,
        ADD CONSTRAINT tickets_conversation_id_fkey FOREIGN KEY(conversation_id)
            REFERENCES runtime_conversations(id) ON DELETE CASCADE,
        ADD CONSTRAINT tickets_action_id_fkey FOREIGN KEY(action_id) REFERENCES pending_actions(id)""")
    op.execute("ALTER TABLE ticket_types DROP CONSTRAINT ticket_types_published_fk")
    op.execute("DROP TABLE ticket_type_versions")
    op.execute("DROP TABLE ticket_types")
    for table in (
        "tickets",
        "pending_actions",
        "runtime_conversations",
        "runtime_runs",
        "staff_accounts",
    ):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_tenant_id_key")
