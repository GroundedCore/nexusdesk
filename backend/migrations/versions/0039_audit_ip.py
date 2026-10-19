"""Record the caller IP on operation audit entries."""

from alembic import op

revision = "0039_audit_ip"
down_revision = "0038_evaluation_environment"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE audit_records ADD COLUMN ip TEXT")


def downgrade():
    op.execute("ALTER TABLE audit_records DROP COLUMN ip")
