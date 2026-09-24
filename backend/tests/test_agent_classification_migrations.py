from uuid import uuid4

import pytest
from test_ticket_migrations import migrate, query
from test_ticket_migrations import migration_database as _migration_database

migration_database = _migration_database
pytestmark = pytest.mark.postgres


def test_classification_backfill_uses_audit_not_names(migration_database):
    url = migration_database
    migrate(url, "0012_agent_deletion")
    aid, other = uuid4(), uuid4()
    for identifier, name in [(aid, "已经人工改名"), (other, "[案例] 游戏")]:
        query(
            url,
            "INSERT INTO agents(id,tenant_id,name,draft) VALUES($1,'demo',$2,'{}')",
            identifier,
            name,
        )
    query(
        url,
        """INSERT INTO audit_records(tenant_id,actor,action,resource,details)
        VALUES('demo','seed-industries','agent.created',$1,
        '{"batch":"industry-cases-v1","industry":"游戏","scenario":"账号充值与道具异常"}')""",
        str(aid),
    )
    migrate(url, "head")
    row = query(url, "SELECT industry,tags,is_example,name FROM agents WHERE id=$1", aid)[0]
    assert row[0] == "gaming" and set(row[1]) == {"operations", "after_sales"}
    assert row[2] and row[3] == "已经人工改名"
    assert not query(url, "SELECT is_example FROM agents WHERE id=$1", other)[0][0]
    migrate(url, "0012_agent_deletion", "downgrade")
    assert query(url, "SELECT count(*) FROM agents")[0][0] == 2
    migrate(url, "head")
    assert query(url, "SELECT industry FROM agents WHERE id=$1", aid)[0][0] == "gaming"


def test_locale_codes_preserve_custom_values_and_content(migration_database):
    url = migration_database
    migrate(url, "0013_agent_classification")
    aid = uuid4()
    query(
        url,
        """INSERT INTO agents(id,tenant_id,name,industry,tags,draft)
        VALUES($1,'demo','原始名称','自定义行业',ARRAY['知识问答','自定义标签'],
        '{"system_prompt":"原始中文指令"}')""",
        aid,
    )
    migrate(url, "head")
    row = query(url, "SELECT name,industry,tags,draft FROM agents WHERE id=$1", aid)[0]
    assert row[0] == "原始名称" and row[1] == "自定义行业"
    assert row[2] == ["knowledge", "自定义标签"]
    assert "原始中文指令" in row[3]
    migrate(url, "0013_agent_classification", "downgrade")
    assert query(url, "SELECT tags FROM agents WHERE id=$1", aid)[0][0] == [
        "知识问答",
        "自定义标签",
    ]
