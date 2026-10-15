"""Phase 4 governance/decay/flag memory — PostgreSQL integration + API tests.

Case IDs refer to
docs/requirement/agent-memory/phase-4-governance/04-test-plan.md (as revised
per 08 R1/R7/R8/R18). Runs only with TEST_DATABASE_URL; skipped locally
without one.

Dual-mode matrix: run the suite twice, once against plain postgres:17
(text mode) and once against pgvector/pgvector:pg17 (vector mode). Decay and
governance are orthogonal to the retrieval mode (08: only P4-IT-18 needs the
embedding column); the suite still runs on both so the decay-aware injection
filter is verified in each mode (P4-IT-10).

Time-travel cases never wait: rows are constructed with direct UPDATEs on
last_referenced_at / expires_at / created_at / due_at, and periodic jobs
(decay migration, capacity eviction, rule scan) are triggered manually for a
single round (04 section 1 principle 4).
"""

from uuid import uuid4

import pytest
from sqlalchemy import text
from test_platform_postgres import platform as _platform

platform = _platform
pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def db_rows(engine, sql, **params):
    async with engine.connect() as c:
        rows = (await c.execute(text(sql), params)).mappings().all()
        return [dict(row) for row in rows]


async def db_exec(engine, sql, **params):
    async with engine.begin() as c:
        await c.execute(text(sql), params)


async def make_customer(engine, tenant, external_key=None, source="channel"):
    customer_id, identity_id = uuid4(), uuid4()
    async with engine.begin() as c:
        await c.execute(
            text("INSERT INTO memory_customers(id,tenant_id) VALUES(:id,:t)"),
            {"id": customer_id, "t": tenant},
        )
        await c.execute(
            text(
                "INSERT INTO memory_identities(id,tenant_id,customer_id,source,external_key)"
                " VALUES(:id,:t,:c,:s,:k)"
            ),
            {
                "id": identity_id,
                "t": tenant,
                "c": customer_id,
                "s": source,
                "k": external_key or f"ext-{uuid4().hex[:8]}",
            },
        )
    return customer_id


async def add_item(engine, tenant, customer_id, item_type, key, content, **overrides):
    """Insert one memory_items row; column overrides go to the INSERT."""
    item_id = uuid4()
    row = {
        "id": item_id,
        "tenant_id": tenant,
        "customer_id": customer_id,
        "type": item_type,
        "key": key,
        "content": content,
        "confidence": 0.9,
        "status": "active",
        "source_type": "extracted",
        "protected": False,
    }
    row.update(overrides)
    cols = ",".join(row)
    marks = ",".join(f":{k}" for k in row)
    async with engine.begin() as c:
        await c.execute(
            text(f"INSERT INTO memory_items({cols}) VALUES({marks})"),  # noqa: S608
            row,
        )
    return item_id


async def items_of(engine, tenant, customer_id, statuses=None):
    sql = (
        "SELECT * FROM memory_items WHERE tenant_id=:t AND customer_id=:c"
        + (" AND status=ANY(:st)" if statuses else "")
        + " ORDER BY created_at,id"
    )
    params = {"t": tenant, "c": customer_id}
    if statuses:
        params["st"] = list(statuses)
    return await db_rows(engine, sql, **params)


async def age_item(engine, item_id, days):
    """Time-travel: backdate last_referenced_at (04 section 2 convention)."""
    await db_exec(
        engine,
        "UPDATE memory_items SET last_referenced_at=now()-make_interval(days => :d)"
        " WHERE id=:id",
        d=days,
        id=item_id,
    )


async def audits_of(engine, tenant, action=None):
    sql = "SELECT * FROM audit_records WHERE tenant_id=:t"
    params = {"t": tenant}
    if action:
        sql += " AND action=:a"
        params["a"] = action
    sql += " ORDER BY created_at,id"
    return await db_rows(engine, sql, **params)
