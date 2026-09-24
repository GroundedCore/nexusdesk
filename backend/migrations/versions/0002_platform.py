"""Agent management, knowledge, conversations and customer service modules."""

from alembic import op

revision = "0002_platform"
down_revision = "0001_runtime"
branch_labels = None
depends_on = None

SQL = [
    """CREATE TABLE agents (
        id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '', draft JSONB NOT NULL,
        draft_revision INTEGER NOT NULL DEFAULT 1, published_version INTEGER,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE agent_versions (
        agent_id UUID REFERENCES agents(id), version INTEGER NOT NULL, config JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(agent_id,version))""",
    """CREATE TABLE registered_tools (
        id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL, spec JSONB NOT NULL,
        enabled BOOLEAN NOT NULL DEFAULT true, revision INTEGER NOT NULL DEFAULT 1,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(tenant_id,name))""",
    """CREATE TABLE knowledge_bases (
        id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL,
        enabled BOOLEAN NOT NULL DEFAULT true, created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE knowledge_documents (
        id UUID PRIMARY KEY, kb_id UUID NOT NULL REFERENCES knowledge_bases(id),
        title TEXT NOT NULL, current_version INTEGER NOT NULL DEFAULT 1,
        enabled BOOLEAN NOT NULL DEFAULT true, created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE knowledge_versions (
        document_id UUID REFERENCES knowledge_documents(id), version INTEGER NOT NULL,
        content TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY(document_id,version))""",
    """CREATE TABLE knowledge_chunks (
        id UUID PRIMARY KEY, document_id UUID NOT NULL REFERENCES knowledge_documents(id),
        version INTEGER NOT NULL, ordinal INTEGER NOT NULL, content TEXT NOT NULL,
        terms TSVECTOR NOT NULL, UNIQUE(document_id,version,ordinal))""",
    "CREATE INDEX knowledge_terms ON knowledge_chunks USING gin(terms)",
    "ALTER TABLE runtime_conversations ADD COLUMN mode TEXT NOT NULL DEFAULT 'bot' CHECK(mode IN ('bot','waiting','human','closed'))",
    "ALTER TABLE runtime_conversations ADD COLUMN agent_id UUID REFERENCES agents(id)",
    "ALTER TABLE runtime_conversations ADD COLUMN assigned_to TEXT",
    "ALTER TABLE runtime_conversations ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now()",
    """CREATE TABLE conversation_messages (
        seq BIGSERIAL PRIMARY KEY, id UUID NOT NULL UNIQUE,
        conversation_id UUID NOT NULL REFERENCES runtime_conversations(id) ON DELETE CASCADE,
        role TEXT NOT NULL CHECK(role IN ('user','assistant','human','system')),
        content TEXT NOT NULL, run_id UUID REFERENCES runtime_runs(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    "CREATE INDEX conversation_message_order ON conversation_messages(conversation_id,seq)",
    """CREATE TABLE handoffs (
        id UUID PRIMARY KEY, tenant_id TEXT NOT NULL,
        conversation_id UUID NOT NULL REFERENCES runtime_conversations(id) ON DELETE CASCADE,
        reason TEXT NOT NULL, summary TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'waiting',
        assignee TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), closed_at TIMESTAMPTZ)""",
    "CREATE UNIQUE INDEX one_open_handoff ON handoffs(conversation_id) WHERE status IN ('waiting','active')",
    """CREATE TABLE pending_actions (
        id UUID PRIMARY KEY, tenant_id TEXT NOT NULL,
        conversation_id UUID NOT NULL REFERENCES runtime_conversations(id) ON DELETE CASCADE,
        kind TEXT NOT NULL, payload JSONB NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
        dedup_key TEXT NOT NULL, expires_at TIMESTAMPTZ NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(tenant_id,dedup_key))""",
    """CREATE TABLE tickets (
        id UUID PRIMARY KEY, tenant_id TEXT NOT NULL,
        conversation_id UUID NOT NULL REFERENCES runtime_conversations(id) ON DELETE CASCADE,
        action_id UUID UNIQUE REFERENCES pending_actions(id), title TEXT NOT NULL,
        description TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
        note TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 1,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE channels (
        id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL,
        agent_id UUID NOT NULL REFERENCES agents(id), token_hash TEXT NOT NULL,
        enabled BOOLEAN NOT NULL DEFAULT true, created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE channel_receipts (
        channel_id UUID REFERENCES channels(id), message_id TEXT NOT NULL,
        payload_hash TEXT NOT NULL, conversation_id UUID NOT NULL REFERENCES runtime_conversations(id) ON DELETE CASCADE,
        run_id UUID REFERENCES runtime_runs(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(channel_id,message_id))""",
    """CREATE TABLE audit_records (
        id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL, actor TEXT NOT NULL,
        action TEXT NOT NULL, resource TEXT NOT NULL, details JSONB NOT NULL DEFAULT '{}',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE evaluation_cases (
        id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, agent_id UUID NOT NULL REFERENCES agents(id),
        name TEXT NOT NULL, spec JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE evaluation_reports (
        id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, agent_id UUID NOT NULL REFERENCES agents(id),
        agent_version INTEGER NOT NULL, results JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    "CREATE INDEX agent_tenant ON agents(tenant_id)",
    "CREATE INDEX audit_tenant ON audit_records(tenant_id,id)",
]


def upgrade():
    for statement in SQL:
        op.execute(statement)


def downgrade():
    for name in [
        "evaluation_reports",
        "evaluation_cases",
        "audit_records",
        "channel_receipts",
        "channels",
        "tickets",
        "pending_actions",
        "handoffs",
        "conversation_messages",
    ]:
        op.drop_table(name)
    for name in ["updated_at", "assigned_to", "agent_id", "mode"]:
        op.drop_column("runtime_conversations", name)
    for name in [
        "knowledge_chunks",
        "knowledge_versions",
        "knowledge_documents",
        "knowledge_bases",
        "registered_tools",
        "agent_versions",
        "agents",
    ]:
        op.drop_table(name)
