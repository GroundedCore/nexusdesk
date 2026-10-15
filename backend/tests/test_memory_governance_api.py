"""Phase 4 governance memory — API tests (P4-API-01~05).

Case IDs refer to
docs/requirement/agent-memory/phase-4-governance/04-test-plan.md.
Runs only with TEST_DATABASE_URL; skipped locally without one.
"""

from uuid import uuid4

import pytest
from test_memory_governance_postgres import (
    add_item,
    audits_of,
    db_rows,
    items_of,
    make_customer,
)
from test_platform_postgres import platform as _platform

platform = _platform
pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]

VIEWER = {"Authorization": "Bearer platform-viewer"}
OPERATOR = {"Authorization": "Bearer platform-operator"}
ANONYMOUS = {"Authorization": "Bearer no-such-token"}


async def test_p4_api_01_delete_customer_authorization_and_contract(platform):
    """FR-4: operator may delete; viewer 403; anonymous 401; the response
    carries counts only (no customer data); the audit is written; a repeat
    delete is a 404."""
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    await add_item(engine, tenant, customer_id, "profile", "name", "张三")

    path = f"/api/v1/memory/customers/{customer_id}"
    assert (await client.delete(path, headers=ANONYMOUS)).status_code == 401
    assert (await client.delete(path, headers=VIEWER)).status_code == 403

    response = await client.delete(path, headers=OPERATOR)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items_deleted"] == 1 and "张三" not in str(body)
    assert await items_of(engine, tenant, customer_id) == []
    audits = await audits_of(engine, tenant, "memory.customer.deleted")
    assert len(audits) == 1 and audits[0]["actor"] == "operator"

    assert (await client.delete(path, headers=OPERATOR)).status_code == 404


async def test_p4_api_02_merge_customer_contract(platform):
    """FR-5: operator merges; the conflict detail lands in the audit;
    source == target is a 400; viewer 403."""
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    source = await make_customer(engine, tenant)
    target = await make_customer(engine, tenant)
    await add_item(engine, tenant, source, "profile", "name", "抽取姓名")
    await add_item(
        engine, tenant, target, "profile", "name", "人工姓名",
        source_type="human", protected=True,
    )

    path = f"/api/v1/memory/customers/{source}/merge"
    assert (await client.post(path, headers=VIEWER, json={"target_customer_id": str(target)})).status_code == 403
    assert (
        await client.post(path, headers=OPERATOR, json={"target_customer_id": str(source)})
    ).status_code == 400

    response = await client.post(path, headers=OPERATOR, json={"target_customer_id": str(target)})
    assert response.status_code == 200, response.text
    assert response.json()["conflicts"] == [{"type": "profile", "key": "name", "kept": "target"}]
    rows = await items_of(engine, tenant, target)
    assert rows[0]["content"] == "人工姓名"
    audits = await audits_of(engine, tenant, "memory.customer.merged")
    assert len(audits) == 1 and audits[0]["details"]["conflicts"][0]["kept"] == "target"


async def test_p4_api_03_flag_crud_contract(platform):
    """FR-2: PUT/DELETE flags; only vip/complaint_risk/blacklist are legal
    (400 otherwise); viewer is read-only; PII in the note is masked."""
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)

    bad = f"/api/v1/memory/customers/{customer_id}/flags/not_a_flag"
    assert (await client.put(bad, headers=OPERATOR, json={"content": "x"})).status_code == 400

    path = f"/api/v1/memory/customers/{customer_id}/flags/vip"
    assert (await client.put(path, headers=VIEWER, json={"content": "x"})).status_code == 403
    assert (await client.delete(path, headers=VIEWER)).status_code == 403

    response = await client.put(path, headers=OPERATOR, json={"content": "重点客户 13812345678"})
    assert response.status_code == 200, response.text
    flag = next(r for r in await items_of(engine, tenant, customer_id) if r["type"] == "flag")
    assert flag["key"] == "vip" and flag["source_type"] == "human" and flag["protected"] is True
    assert "13812345678" not in flag["content"]  # flag notes are masked too (R5)

    # Read surfaces the flag on the customer detail.
    detail = await client.get(f"/api/v1/memory/customers/{customer_id}", headers=VIEWER)
    assert detail.status_code == 200
    assert any(item["key"] == "vip" for item in detail.json()["items"])

    assert (await client.delete(path, headers=OPERATOR)).status_code == 200
    flag = next(r for r in await items_of(engine, tenant, customer_id) if r["type"] == "flag")
    assert flag["status"] == "deleted"
    audits = await audits_of(engine, tenant, "memory.flag.removed")
    assert len(audits) == 1 and audits[0]["details"]["source"] == "human"


async def test_p4_api_04_release_and_expired_restore_contract(platform):
    """FR-1/FR-6.4: release flips the ownership markers; restore revives an
    expired row with a fresh clock; viewer 403 on both."""
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    protected_item = await add_item(
        engine, tenant, customer_id, "profile", "name", "张三",
        source_type="human", protected=True,
    )
    expired_item = await add_item(
        engine, tenant, customer_id, "preference", "language", "中文", status="expired",
    )

    release = f"/api/v1/memory/items/{protected_item}/release"
    restore = f"/api/v1/memory/items/{expired_item}/restore"
    assert (await client.post(release, headers=VIEWER)).status_code == 403
    assert (await client.post(restore, headers=VIEWER)).status_code == 403

    assert (await client.post(release, headers=OPERATOR)).status_code == 200
    row = next(r for r in await items_of(engine, tenant, customer_id) if r["id"] == protected_item)
    assert row["protected"] is False and row["source_type"] == "extracted"

    assert (await client.post(restore, headers=OPERATOR)).status_code == 200
    row = next(r for r in await items_of(engine, tenant, customer_id) if r["id"] == expired_item)
    assert row["status"] == "active"
    fresh = await db_rows(
        engine,
        "SELECT last_referenced_at > now()-interval '1 minute' AS fresh"
        " FROM memory_items WHERE id=:id",
        id=expired_item,
    )
    assert fresh[0]["fresh"] is True


async def test_p4_api_05_cross_tenant_is_unreachable(platform):
    """Non-functional: every governance endpoint 404s across tenants — no
    cross-tenant reads or mutations (P0 red line). Mirrors the P2 isolation
    precedent: a foreign-tenant customer row is invisible to this tenant's
    API, and service calls under a foreign tenant id raise DomainError."""
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    item_id = await add_item(engine, tenant, customer_id, "profile", "name", "张三")

    outsider_id = uuid4()
    async with engine.begin() as c:
        from sqlalchemy import text

        await c.execute(
            text("INSERT INTO memory_customers(id,tenant_id) VALUES(:id,'other-tenant')"),
            {"id": outsider_id},
        )
    # Every new governance verb 404s on the foreign customer (FR-4/FR-5/FR-2).
    assert (await client.delete(f"/api/v1/memory/customers/{outsider_id}", headers=OPERATOR)).status_code == 404
    assert (
        await client.post(
            f"/api/v1/memory/customers/{outsider_id}/merge",
            headers=OPERATOR,
            json={"target_customer_id": str(customer_id)},
        )
    ).status_code == 404
    assert (
        await client.put(
            f"/api/v1/memory/customers/{outsider_id}/flags/vip",
            headers=OPERATOR,
            json={"content": "x"},
        )
    ).status_code == 404
    assert (
        await client.delete(f"/api/v1/memory/customers/{outsider_id}/flags/vip", headers=OPERATOR)
    ).status_code == 404
    # Merging INTO a foreign target is equally unreachable.
    assert (
        await client.post(
            f"/api/v1/memory/customers/{customer_id}/merge",
            headers=OPERATOR,
            json={"target_customer_id": str(outsider_id)},
        )
    ).status_code == 404

    # Service-level cross-tenant writes raise DomainError and change nothing.
    from agent_platform.platform.persistence.store import DomainError

    memory = services.platform.memory
    with pytest.raises(DomainError):
        await memory.customer_detail("other-tenant", customer_id)
    with pytest.raises(DomainError):
        await memory.delete_customer("other-tenant", "intruder", customer_id)
    with pytest.raises(DomainError):
        await memory.merge_customers("other-tenant", "intruder", customer_id, uuid4())
    with pytest.raises(DomainError):
        await memory.release_item("other-tenant", "intruder", item_id)
    rows = await items_of(engine, tenant, customer_id)
    assert len(rows) == 1 and rows[0]["content"] == "张三"
    # This tenant's own customer is unaffected by the foreign row's existence.
    assert (await client.get(f"/api/v1/memory/customers/{customer_id}", headers=VIEWER)).status_code == 200
