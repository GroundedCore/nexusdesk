"""Structured Agent classification and audited industry-example backfill."""

from alembic import op

revision = "0013_agent_classification"
down_revision = "0012_agent_deletion"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE agents ADD COLUMN industry TEXT NOT NULL DEFAULT '', ADD COLUMN tags TEXT[] NOT NULL DEFAULT '{}', ADD COLUMN is_example BOOLEAN NOT NULL DEFAULT false"
    )
    op.execute("""UPDATE agents a SET industry=r.details->>'industry',is_example=true,
        tags=CASE
          WHEN r.details->>'scenario' IN ('订单售后','技术故障支持','设备维护与报修','物流异常处理','账号充值与道具异常')
            THEN ARRAY['业务办理','售后支持']
          WHEN r.details->>'scenario' IN ('学员教务服务','预订与改退服务','物业服务与报修','员工制度服务')
            THEN ARRAY['业务办理','知识问答']
          ELSE ARRAY['咨询导购','知识问答'] END
        FROM audit_records r WHERE r.tenant_id=a.tenant_id AND r.resource=CAST(a.id AS text)
          AND r.action='agent.created' AND r.details->>'batch'='industry-cases-v1'
          AND r.details->>'industry' IS NOT NULL""")
    op.execute("CREATE INDEX ix_agents_industry ON agents(tenant_id,industry)")
    op.execute("CREATE INDEX ix_agents_tags ON agents USING gin(tags)")


def downgrade():
    op.execute("DROP INDEX ix_agents_tags")
    op.execute("DROP INDEX ix_agents_industry")
    op.execute("ALTER TABLE agents DROP COLUMN industry, DROP COLUMN tags, DROP COLUMN is_example")
