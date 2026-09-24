from uuid import uuid4

import pytest
from test_ticket_migrations import migrate, query
from test_ticket_migrations import migration_database as _migration_database

migration_database = _migration_database
pytestmark = pytest.mark.postgres


def test_workspace_upgrade_preserves_legacy_conversations(migration_database):
    url = migration_database
    migrate(url, "0010_gateway_governance")
    cid = uuid4()
    query(
        url,
        "INSERT INTO runtime_conversations(id,tenant_id,external_id) VALUES($1,'legacy','old')",
        cid,
    )
    migrate(url, "head")
    assert (
        query(url, "SELECT source FROM runtime_conversations WHERE id=$1", cid)[0][0] == "business"
    )
    with pytest.raises(Exception, match="check constraint"):
        query(url, "UPDATE runtime_conversations SET source='invalid' WHERE id=$1", cid)
    migrate(url, "0010_gateway_governance", "downgrade")
    assert (
        query(url, "SELECT external_id FROM runtime_conversations WHERE id=$1", cid)[0][0] == "old"
    )
    migrate(url, "head")
