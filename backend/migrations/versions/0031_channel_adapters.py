"""Provider-native channel adapters, conversation mappings, and durable delivery."""

from alembic import op

revision = "0031_channel_adapters"
down_revision = "0030_conversation_summary"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        ALTER TABLE channels
        ADD COLUMN provider VARCHAR(40) NOT NULL DEFAULT 'generic_webhook',
        ADD COLUMN config JSONB NOT NULL DEFAULT '{}'::jsonb,
        ADD COLUMN encrypted_credentials TEXT,
        ADD COLUMN last_error_code TEXT,
        ADD COLUMN last_activity_at TIMESTAMPTZ
        """
    )
    op.execute(
        """
        ALTER TABLE channels
        ADD CONSTRAINT channels_provider_format
        CHECK (provider ~ '^[a-z][a-z0-9_]{0,39}$')
        """
    )
    op.execute(
        """
        CREATE TABLE channel_conversations (
            channel_id UUID NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
            conversation_id UUID NOT NULL REFERENCES runtime_conversations(id) ON DELETE CASCADE,
            external_conversation_id TEXT NOT NULL,
            external_user_id TEXT,
            external_thread_id TEXT,
            target JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (channel_id, external_conversation_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE channel_deliveries (
            id UUID PRIMARY KEY,
            channel_id UUID NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
            conversation_id UUID NOT NULL REFERENCES runtime_conversations(id) ON DELETE CASCADE,
            message_id UUID NOT NULL REFERENCES conversation_messages(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            target JSONB NOT NULL DEFAULT '{}'::jsonb,
            status VARCHAR(20) NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'running', 'delivered', 'failed', 'cancelled')),
            attempts INT NOT NULL DEFAULT 0,
            max_attempts INT NOT NULL DEFAULT 5,
            next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            owner UUID,
            lease_until TIMESTAMPTZ,
            external_message_id TEXT,
            http_status INT,
            error_code TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (channel_id, message_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE channel_delivery_attempts (
            id BIGSERIAL PRIMARY KEY,
            delivery_id UUID NOT NULL REFERENCES channel_deliveries(id) ON DELETE CASCADE,
            attempt INT NOT NULL,
            status VARCHAR(20) NOT NULL,
            http_status INT,
            error_code TEXT,
            duration_ms INT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX channel_deliveries_claim
        ON channel_deliveries (status, next_attempt_at)
        WHERE status IN ('pending', 'running')
        """
    )
    op.execute(
        """
        CREATE INDEX channel_conversations_lookup
        ON channel_conversations (conversation_id)
        """
    )


def downgrade():
    op.execute("DROP TABLE channel_delivery_attempts")
    op.execute("DROP TABLE channel_deliveries")
    op.execute("DROP TABLE channel_conversations")
    op.execute("ALTER TABLE channels DROP CONSTRAINT channels_provider_format")
    op.execute(
        """
        ALTER TABLE channels
        DROP COLUMN last_activity_at,
        DROP COLUMN last_error_code,
        DROP COLUMN encrypted_credentials,
        DROP COLUMN config,
        DROP COLUMN provider
        """
    )
