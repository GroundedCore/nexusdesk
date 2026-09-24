"""Locale-neutral industry and tag identifiers. Preserve unknown custom industries."""

import json

from alembic import op
from sqlalchemy import text

revision = "0014_agent_locale_codes"
down_revision = "0013_agent_classification"
branch_labels = None
depends_on = None

INDUSTRIES = {
    "电商零售": "retail",
    "企业软件（SaaS）": "saas",
    "制造业": "manufacturing",
    "教育培训": "education",
    "酒店文旅": "travel",
    "房产物业": "property",
    "物流运输": "logistics",
    "企业人力资源": "hr",
    "游戏": "gaming",
}
TAGS = {
    "咨询导购": "consulting",
    "售后支持": "after_sales",
    "业务办理": "operations",
    "知识问答": "knowledge",
}


def convert(industries, tags):
    bind = op.get_bind()
    bind.execute(
        text("UPDATE agents SET industry=COALESCE(CAST(:mapping AS jsonb)->>industry,industry)"),
        {"mapping": json.dumps(industries)},
    )
    bind.execute(
        text("""UPDATE agents a SET tags=ARRAY(SELECT COALESCE(CAST(:mapping AS jsonb)->>item,item)
        FROM unnest(a.tags) WITH ORDINALITY AS entries(item,ord) ORDER BY ord)"""),
        {"mapping": json.dumps(tags)},
    )


def upgrade():
    convert(INDUSTRIES, TAGS)


def downgrade():
    convert({v: k for k, v in INDUSTRIES.items()}, {v: k for k, v in TAGS.items()})
