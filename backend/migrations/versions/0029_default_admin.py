"""Seed the deployment administrator without overwriting existing credentials."""

from pathlib import Path

from alembic import op
from sqlalchemy import literal

from agent_platform.settings import Settings

revision = "0029_default_admin"
down_revision = "0028_local_admin"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE local_admin_credentials ADD COLUMN must_change_password boolean NOT NULL DEFAULT false"
    )
    tenant = literal(Settings().tenant_id).compile(compile_kwargs={"literal_binds": True})
    op.execute(f"SELECT set_config('nexusdesk.tenant_id', {tenant}, true)")
    op.execute(
        (Path(__file__).resolve().parents[1] / "sql/0029_default_admin.sql").read_text(
            encoding="utf-8"
        )
    )


def downgrade():
    op.execute("ALTER TABLE local_admin_credentials DROP COLUMN must_change_password")
