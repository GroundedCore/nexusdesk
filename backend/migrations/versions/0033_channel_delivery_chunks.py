"""Chunked outbound delivery and provider cursor persistence."""

from alembic import op

revision = "0033_channel_delivery_chunks"
down_revision = "0032_channel_inbound_outbox"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        ALTER TABLE channels
        ADD COLUMN provider_cursor TEXT
        """
    )
    op.execute(
        """
        ALTER TABLE channel_deliveries
        ADD COLUMN seq INT NOT NULL DEFAULT 0
        """
    )
    op.execute(
        """
        ALTER TABLE channel_deliveries
        DROP CONSTRAINT channel_deliveries_channel_id_message_id_key
        """
    )
    op.execute(
        """
        ALTER TABLE channel_deliveries
        ADD CONSTRAINT channel_deliveries_channel_id_message_id_seq_key
        UNIQUE (channel_id, message_id, seq)
        """
    )


def downgrade():
    op.execute(
        """
        ALTER TABLE channel_deliveries
        DROP CONSTRAINT channel_deliveries_channel_id_message_id_seq_key
        """
    )
    op.execute(
        """
        ALTER TABLE channel_deliveries
        ADD CONSTRAINT channel_deliveries_channel_id_message_id_key
        UNIQUE (channel_id, message_id)
        """
    )
    op.execute(
        """
        ALTER TABLE channel_deliveries
        DROP COLUMN seq
        """
    )
    op.execute(
        """
        ALTER TABLE channels
        DROP COLUMN provider_cursor
        """
    )
