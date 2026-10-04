from uuid import uuid4

import pytest

from test_ticket_migrations import migrate, query
from test_ticket_migrations import migration_database as _migration_database

migration_database = _migration_database
pytestmark = pytest.mark.postgres


def test_agent_deletion_upgrade_and_downgrade_guard(migration_database):
    url = migration_database
    migrate(url, "0011_agent_workspace")
    cid = uuid4()
    query(
        url,
        "INSERT INTO runtime_conversations(id,tenant_id,external_id) VALUES($1,'legacy','old')",
        cid,
    )
    migrate(url, "head")
    assert (
        query(url, "SELECT deleted_agent FROM runtime_conversations WHERE id=$1", cid)[0][0] is None
    )
    migrate(url, "0011_agent_workspace", "downgrade")
    migrate(url, "head")
    query(url, "UPDATE runtime_conversations SET mode='closed',deleted_agent='{}' WHERE id=$1", cid)
    failed = migrate(url, "0011_agent_workspace", "downgrade", success=False)
    assert "agent_deletion_downgrade_requires_history_export" in failed.stderr
    assert query(url, "SELECT version_num FROM alembic_version")[0][0] == "0029_default_admin"
    with pytest.raises(Exception, match="check constraint"):
        query(url, "UPDATE runtime_conversations SET mode='bot' WHERE id=$1", cid)
