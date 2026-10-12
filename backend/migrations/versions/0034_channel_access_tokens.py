"""Shared access-token cache for provider adapters."""

from alembic import op

revision = "0034_channel_access_tokens"
down_revision = "0033_channel_delivery_chunks"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE channel_access_tokens (
            scope TEXT PRIMARY KEY,
            token TEXT NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade():
    op.execute("DROP TABLE channel_access_tokens")
