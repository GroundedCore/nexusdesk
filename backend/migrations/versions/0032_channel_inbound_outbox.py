"""Durable inbound outbox for native channel webhooks (fast ack + retry)."""

from alembic import op

revision = "0032_channel_inbound_outbox"
down_revision = "0031_channel_adapters"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE channel_inbound_events (
            id UUID PRIMARY KEY,
            channel_id UUID NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
            tenant_id TEXT NOT NULL,
            provider VARCHAR(40) NOT NULL,
            external_message_id TEXT NOT NULL,
            external_conversation_id TEXT NOT NULL,
            external_user_id TEXT,
            external_thread_id TEXT,
            content TEXT NOT NULL,
            target JSONB NOT NULL DEFAULT '{}'::jsonb,
            payload_hash TEXT NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'running', 'processed', 'failed')),
            attempts INT NOT NULL DEFAULT 0,
            max_attempts INT NOT NULL DEFAULT 5,
            next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            owner UUID,
            lease_until TIMESTAMPTZ,
            error_code TEXT,
            conversation_id UUID REFERENCES runtime_conversations(id) ON DELETE SET NULL,
            run_id UUID REFERENCES runtime_runs(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            processed_at TIMESTAMPTZ,
            UNIQUE (channel_id, external_message_id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX channel_inbound_events_claim
        ON channel_inbound_events (status, next_attempt_at)
        WHERE status IN ('pending', 'running')
        """
    )
    op.execute(
        """
        CREATE INDEX channel_inbound_events_channel
        ON channel_inbound_events (channel_id, created_at DESC)
        """
    )


def downgrade():
    op.execute("DROP TABLE channel_inbound_events")
