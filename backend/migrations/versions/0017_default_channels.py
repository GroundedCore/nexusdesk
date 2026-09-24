"""Seed seven editable, unconfigured provider channels without replacing user data."""

import json
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from alembic import op

from agent_platform.settings import Settings

revision = "0017_default_channels"
down_revision = "0016_gateway_credentials"
branch_labels = None
depends_on = None

# Frozen migration data: future endpoint changes belong in a new migration.
CHANNELS = (
    ("deepseek", "DeepSeek", "https://api.deepseek.com", 8),
    ("doubao", "豆包 (Doubao)", "https://ark.cn-beijing.volces.com/api/v3", 8),
    ("qwen", "千问 (Qwen)", "https://dashscope.aliyuncs.com/compatible-mode/v1", 8),
    ("kimi", "Kimi", "https://api.moonshot.cn/v1", 8),
    ("glm", "智谱 GLM", "https://open.bigmodel.cn/api/paas/v4", 8),
    ("hunyuan", "腾讯混元", "https://api.hunyuan.cloud.tencent.com/v1", 5),
    ("xiaomi", "小米", "https://api.xiaomimimo.com/v1", 8),
)


def upgrade():
    connection = op.get_bind()
    tenants = set(
        connection.execute(
            sa.text("""
        SELECT tenant_id FROM gateway_connections UNION SELECT tenant_id FROM agents
        UNION SELECT tenant_id FROM knowledge_bases
    """)
        ).scalars()
    )
    tenants.add(Settings().tenant_id)
    for tenant in sorted(tenants):
        for key, name, address, concurrency in CHANNELS:
            identifier = uuid5(NAMESPACE_URL, f"agent-platform:{tenant}:default-channel-v1:{key}")
            spec = json.dumps(
                {
                    "name": name,
                    "protocol": "openai_compatible",
                    "base_url": address,
                    "credential_ref": None,
                    "concurrency": concurrency,
                }
            )
            # Include archived rows so a user's removed channel stays removed on reruns.
            connection.execute(
                sa.text("""
                INSERT INTO gateway_connections(id,tenant_id,name,spec,enabled)
                SELECT :id,:tenant,:name,CAST(:spec AS jsonb),false
                WHERE NOT EXISTS (
                    SELECT 1 FROM gateway_connections WHERE tenant_id=:tenant AND
                    (lower(name)=lower(:name) OR rtrim(spec->>'base_url','/')=:address)
                ) ON CONFLICT(id) DO NOTHING
            """),
                {
                    "id": identifier,
                    "tenant": tenant,
                    "name": name,
                    "spec": spec,
                    "address": address,
                },
            )


def downgrade():
    # Data-only migration. Channels may now contain credentials, edits or model references.
    # Preserve all rows instead of removing user-owned configuration on downgrade.
    pass
