"""P2-A:環境快照的快照列，支撑评测结果与模型/知识库/记忆版本联动。

Purely additive: adds a nullable ``environment`` JSONB column to
``evaluation_reports``. Historical rows stay NULL and are rendered as
"未知" by the console; new reports always carry the snapshot captured at
run time. The snapshot is frozen with the report so a later model / knowledge
/ memory change never rewrites history.
"""

from alembic import op

revision = "0038_evaluation_environment"
down_revision = "0037_memory_governance"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE evaluation_reports ADD COLUMN environment JSONB")


def downgrade():
    op.execute("ALTER TABLE evaluation_reports DROP COLUMN environment")
