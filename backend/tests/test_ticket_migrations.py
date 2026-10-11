"""Destructive migration checks run only in randomly named, disposable databases."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

pytestmark = pytest.mark.postgres
BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture
def migration_database():
    url = os.environ.get("MIGRATION_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires a test PostgreSQL role with CREATEDB")
    from urllib.parse import urlsplit, urlunsplit

    url = url.replace("postgresql+asyncpg://", "postgresql://")
    parts = urlsplit(url)
    name = "migration_test_" + uuid4().hex

    async def admin(statement):
        conn = await asyncpg.connect(url)
        try:
            await conn.execute(statement)
        finally:
            await conn.close()

    asyncio.run(admin(f'CREATE DATABASE "{name}"'))
    target = urlunsplit(parts._replace(path="/" + name))
    try:
        yield target
    finally:
        asyncio.run(admin(f'DROP DATABASE "{name}" WITH (FORCE)'))


def migrate(url, revision, direction="upgrade", success=True):
    env = dict(os.environ, AGENT_DATABASE_URL=url.replace("postgresql://", "postgresql+asyncpg://"))
    result = subprocess.run(
        [sys.executable, "-m", "alembic", direction, revision],
        cwd=BACKEND,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if success:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0
    return result


def query(url, sql, *params):
    async def run():
        conn = await asyncpg.connect(url)
        try:
            return await conn.fetch(sql, *params)
        finally:
            await conn.close()

    return asyncio.run(run())


def test_empty_upgrade_downgrade_upgrade(migration_database):
    url = migration_database
    migrate(url, "head")
    assert query(url, "SELECT version_num FROM alembic_version")[0][0] == "0030_conversation_summary"
    migrate(url, "base", "downgrade")
    assert not query(
        url,
        "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename<>'alembic_version'",
    )
    migrate(url, "head")


def test_backfill_constraints_and_downgrade_guards(migration_database):
    url = migration_database
    migrate(url, "0007_vector_index")
    cid, tid, aid = uuid4(), uuid4(), uuid4()
    query(
        url,
        "INSERT INTO runtime_conversations(id,tenant_id,external_id) VALUES($1,'legacy','session')",
        cid,
    )
    query(
        url,
        "INSERT INTO pending_actions(id,tenant_id,conversation_id,kind,payload,dedup_key,expires_at) VALUES($1,'legacy',$2,'create_ticket','{}','key',now()+interval '1 hour')",
        aid,
        cid,
    )
    query(
        url,
        "INSERT INTO tickets(id,tenant_id,conversation_id,action_id,title,description,note) VALUES($1,'legacy',$2,$3,'title','original description','original note')",
        tid,
        cid,
        aid,
    )
    migrate(url, "head")
    row = query(
        url,
        "SELECT t.ticket_no,t.type_version,d.description,d.custom_fields FROM tickets t JOIN tickets_detail d ON d.ticket_id=t.id WHERE t.id=$1",
        tid,
    )[0]
    assert row[0] == "TK-" + tid.hex and row[1] == 1
    assert row[2] == "original description" and row[3] == "{}"
    assert (
        query(url, "SELECT content FROM ticket_process_log WHERE ticket_id=$1", tid)[0][0]
        == "original note"
    )
    assert query(url, "SELECT mode,lifecycle FROM tenant_ticket_settings WHERE tenant_id='legacy'")[
        0
    ][:] == ("internal", "enabled")
    assert not query(
        url,
        "SELECT column_name FROM information_schema.columns WHERE table_name='tickets' AND column_name IN ('description','note')",
    )
    assert not query(url, "SELECT tgname FROM pg_trigger WHERE tgname LIKE 'ticket_legacy_%'")
    # PostgreSQL reports FK enforcement failures as 23503 (foreign_key_violation)
    # even for ON DELETE RESTRICT constraints; 23001 is never emitted here.
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        query(url, "DELETE FROM runtime_conversations WHERE id=$1", cid)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        query(url, "UPDATE tickets_detail SET tenant_id='other' WHERE ticket_id=$1", tid)
    with pytest.raises(asyncpg.CheckViolationError):
        query(url, "UPDATE tickets_detail SET custom_fields='[]' WHERE ticket_id=$1", tid)
    with pytest.raises(asyncpg.CheckViolationError):
        query(url, "UPDATE tickets SET priority=9 WHERE id=$1", tid)
    with pytest.raises(asyncpg.CheckViolationError):
        query(url, "UPDATE tenant_ticket_settings SET mode='external' WHERE tenant_id='legacy'")
    query(url, "UPDATE tenant_ticket_settings SET lifecycle='draining' WHERE tenant_id='legacy'")
    result = migrate(url, "0008_ticketing", "downgrade", success=False)
    assert "ticket_settings_downgrade_requires_data_export" in result.stderr
    query(url, "UPDATE tenant_ticket_settings SET lifecycle='enabled' WHERE tenant_id='legacy'")
    query(
        url,
        "UPDATE tickets_detail SET custom_fields=jsonb_build_object('order_no','1') WHERE ticket_id=$1",
        tid,
    )
    migrate(url, "0008_ticketing", "downgrade")
    result = migrate(url, "0007_vector_index", "downgrade", success=False)
    assert "ticketing_downgrade_requires_data_export" in result.stderr
    query(url, "UPDATE tickets_detail SET custom_fields='{}' WHERE ticket_id=$1", tid)
    migrate(url, "0007_vector_index", "downgrade")
    assert query(url, "SELECT description,note FROM tickets WHERE id=$1", tid)[0][:] == (
        "original description",
        "original note",
    )
    migrate(url, "head")
    assert query(url, "SELECT count(*) FROM tickets_detail")[0][0] == 1
