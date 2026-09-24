"""Tool collections and draft workspace, preserving existing published snapshots."""

from alembic import op

revision = "0022_tool_workspace"
down_revision = "0021_tool_credentials"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE tool_collections (
        id UUID PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '', icon TEXT NOT NULL DEFAULT 'api',
        spec JSONB NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
        enabled BOOLEAN NOT NULL DEFAULT true, archived BOOLEAN NOT NULL DEFAULT false,
        auto_origin TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE(tenant_id,id), UNIQUE(tenant_id,auto_origin))""")
    op.execute("ALTER TABLE registered_tools ADD COLUMN collection_id UUID")
    op.execute("ALTER TABLE registered_tools ADD COLUMN display_name TEXT NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE registered_tools ADD COLUMN relative_path TEXT NOT NULL DEFAULT ''")
    op.execute(
        "ALTER TABLE registered_tools ADD COLUMN auth_mode TEXT NOT NULL DEFAULT 'custom' CHECK(auth_mode IN ('inherit','custom','none'))"
    )
    op.execute(
        "ALTER TABLE registered_tools ADD COLUMN workspace_managed BOOLEAN NOT NULL DEFAULT false"
    )
    op.execute("ALTER TABLE registered_tools ADD COLUMN archived BOOLEAN NOT NULL DEFAULT false")
    op.execute(
        "ALTER TABLE registered_tools ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now()"
    )
    op.execute("ALTER TABLE registered_tools ADD COLUMN published_revision INTEGER")
    op.execute("ALTER TABLE registered_tools ADD COLUMN collection_fingerprint TEXT")
    op.execute("ALTER TABLE registered_tools ALTER COLUMN published_version DROP NOT NULL")
    op.execute("ALTER TABLE registered_tools ALTER COLUMN published_version DROP DEFAULT")
    # Origins contain no credentials (already validated by HttpTool). No credentials move.
    op.execute("""INSERT INTO tool_collections(id,tenant_id,name,description,spec,auto_origin)
        SELECT md5(tenant_id || ':' || origin)::uuid, tenant_id, origin,
        '由已有工具整理，原鉴权与发布版本保持不变',
        jsonb_build_object('name','collection','description','Collection','url',origin,
          'parameters',jsonb_build_object('type','object','properties','{}'::jsonb,'additionalProperties',false)),origin
        FROM (SELECT DISTINCT tenant_id,substring(spec->>'url' from '^https?://[^/]+') origin FROM registered_tools) s""")
    op.execute("""UPDATE registered_tools SET
        collection_id=md5(tenant_id || ':' || substring(spec->>'url' from '^https?://[^/]+'))::uuid,
        display_name=name, relative_path=COALESCE(NULLIF(regexp_replace(spec->>'url','^https?://[^/]+',''),''),'/'),
        published_revision=CASE WHEN spec=(SELECT v.spec FROM tool_versions v
          WHERE v.tool_id=registered_tools.id AND v.version=registered_tools.published_version)
          THEN revision ELSE NULL END""")
    op.execute(
        "ALTER TABLE registered_tools ADD FOREIGN KEY (tenant_id,collection_id) REFERENCES tool_collections(tenant_id,id)"
    )
    op.execute("ALTER TABLE tool_credentials ALTER COLUMN tool_id DROP NOT NULL")
    op.execute(
        "ALTER TABLE tool_credentials ADD COLUMN collection_id UUID REFERENCES tool_collections(id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE tool_credentials ADD CHECK ((tool_id IS NULL) <> (collection_id IS NULL))"
    )
    op.execute(
        "CREATE INDEX tool_collection_catalog ON tool_collections(tenant_id,updated_at DESC,id)"
    )
    op.execute("CREATE INDEX tool_collection_members ON registered_tools(tenant_id,collection_id)")


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM registered_tools WHERE workspace_managed)
        OR EXISTS (SELECT 1 FROM tool_credentials WHERE collection_id IS NOT NULL)
        THEN RAISE EXCEPTION 'tool_workspace_downgrade_requires_data_export'; END IF; END $$""")
    op.execute("ALTER TABLE tool_credentials DROP CONSTRAINT tool_credentials_check")
    op.execute("ALTER TABLE tool_credentials DROP COLUMN collection_id")
    op.execute("ALTER TABLE tool_credentials ALTER COLUMN tool_id SET NOT NULL")
    for field in [
        "collection_id",
        "display_name",
        "relative_path",
        "auth_mode",
        "workspace_managed",
        "archived",
        "updated_at",
        "published_revision",
        "collection_fingerprint",
    ]:
        op.execute(f"ALTER TABLE registered_tools DROP COLUMN {field}")
    op.execute("ALTER TABLE registered_tools ALTER COLUMN published_version SET DEFAULT 1")
    op.execute("ALTER TABLE registered_tools ALTER COLUMN published_version SET NOT NULL")
    op.execute("DROP TABLE tool_collections")
