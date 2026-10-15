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

import time
from uuid import uuid4

import pytest
from sqlalchemy import text
from test_platform_postgres import platform as _platform

from agent_platform.modules.memory.decay import (
    ReinforceService,
    daily_migration,
    enqueue_reinforce_task,
)
from agent_platform.modules.memory.governance import (
    merge_customers,
    scan_overdue_tickets,
    upsert_flag,
)
from agent_platform.modules.memory.service import MemoryService
from agent_platform.platform.persistence.store import DomainError

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
            text(f"INSERT INTO memory_items({cols}) VALUES({marks})"),
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


async def make_conversation(engine, tenant, customer_id):
    cid = uuid4()
    await db_exec(
        engine,
        "INSERT INTO runtime_conversations(id,tenant_id,external_id,customer_id)"
        " VALUES(:id,:t,:e,:c)",
        id=cid,
        t=tenant,
        e=f"conv-{uuid4().hex[:8]}",
        c=customer_id,
    )
    return cid


async def make_ticket(engine, tenant, customer_id, *, due_at_sql="now()-interval '1 day'",
                      status="open"):
    """One ticket row (type row created on demand); due_at via SQL expression."""
    type_id = uuid4()
    await db_exec(
        engine,
        "INSERT INTO ticket_types(id,tenant_id,code,name,enabled,draft_definition,revision,"
        "created_at,updated_at) VALUES(:id,:t,:code,'测试类型',true,'{}',1,now(),now())"
        " ON CONFLICT DO NOTHING",
        id=type_id,
        t=tenant,
        code=f"t{uuid4().hex[:8]}",
    )
    await db_exec(
        engine,
        "INSERT INTO ticket_type_versions(tenant_id,type_id,version,name,definition,created_by)"
        " VALUES(:t,:ty,1,'测试类型','{}','qa')",
        t=tenant,
        ty=type_id,
    )
    ticket_id = uuid4()
    async with engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO tickets(id,tenant_id,title,status,ticket_no,type_id,type_version,"
                f"customer_id,due_at) VALUES(:id,:t,'测试工单',:st,:no,:ty,1,:c,{due_at_sql})"
            ),
            {
                "id": ticket_id,
                "t": tenant,
                "st": status,
                "no": f"TK-{uuid4().hex[:6]}",
                "ty": type_id,
                "c": str(customer_id) if customer_id else None,
            },
        )
    return ticket_id


async def protected_drift_count(engine, tenant):
    rows = await db_rows(
        engine,
        "SELECT count(*)::int AS n FROM memory_items WHERE tenant_id=:t"
        " AND protected <> (source_type='human')",
        t=tenant,
    )
    return rows[0]["n"]


def extract_op(key, content, op="update", item_type="profile", confidence=0.95):
    return {
        "op": op,
        "type": item_type,
        "key": key,
        "content": content,
        "confidence": confidence,
        "reason": "",
    }


# ---------------------------------------------------------------------------
# P4-UT-07/08 protection lifecycle (FR-1, AC-1; R4/R18)
# ---------------------------------------------------------------------------


async def test_p4_ut_07_protected_update_parks_suggested_pending(platform):
    """Extraction update against a protected row must not overwrite; it parks
    a pending ``<key>:suggested`` suggestion instead (FR-1, R4)."""
    _client, services, settings = platform
    memory, engine, tenant = services.platform.memory, services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    item_id = await add_item(engine, tenant, customer_id, "profile", "name", "张三")
    await memory.edit_item(tenant, "operator", item_id, "张叁")

    async with engine.begin() as c:
        counts = await memory.apply_extracted(
            c, tenant, customer_id, [extract_op("name", "张四")], "conv-1", 0.8, 0.5
        )
    assert counts["pending"] == 1 and counts["updated"] == 0

    rows = await items_of(engine, tenant, customer_id)
    base = next(r for r in rows if r["key"] == "name")
    assert base["content"] == "张叁" and base["protected"] is True
    suggestion = next(r for r in rows if r["key"] == "name:suggested")
    assert suggestion["status"] == "pending" and suggestion["content"] == "张四"
    assert suggestion["source_type"] == "extracted"

    # A repeated extraction refreshes the same suggestion in place (R4).
    async with engine.begin() as c:
        counts = await memory.apply_extracted(
            c, tenant, customer_id, [extract_op("name", "张五")], "conv-2", 0.8, 0.5
        )
    assert counts["pending"] == 1
    rows = await items_of(engine, tenant, customer_id, statuses=["pending"])
    assert len(rows) == 1 and rows[0]["content"] == "张五"


async def test_p4_ut_08_release_restores_automatic_management(platform):
    """恢复自动管理: protected=false + source_type='extracted' (R18); later
    extraction updates overwrite normally again (FR-1)."""
    _client, services, settings = platform
    memory, engine, tenant = services.platform.memory, services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    item_id = await add_item(engine, tenant, customer_id, "profile", "name", "张三")
    await memory.edit_item(tenant, "operator", item_id, "张叁")

    await memory.release_item(tenant, "operator", item_id)
    row = (await items_of(engine, tenant, customer_id))[0]
    assert row["protected"] is False and row["source_type"] == "extracted"
    assert row["content"] == "张叁"  # content kept

    async with engine.begin() as c:
        counts = await memory.apply_extracted(
            c, tenant, customer_id, [extract_op("name", "张四")], "conv-1", 0.8, 0.5
        )
    assert counts["updated"] == 1
    row = (await items_of(engine, tenant, customer_id))[0]
    assert row["content"] == "张四" and row["prev_content"] == "张叁"
    audits = await audits_of(engine, tenant, "memory.item.released")
    assert len(audits) == 1
    assert await protected_drift_count(engine, tenant) == 0  # R18 invariant


async def test_p4_ut_09_flag_write_paths_reject_extracted(platform):
    """Entry layers beneath the extractor filter (P0, FR-2): apply_extracted
    drops flag ops; governance.upsert_flag hard-errors on extracted."""
    _client, services, settings = platform
    memory, engine, tenant = services.platform.memory, services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)

    async with engine.begin() as c:
        counts = await memory.apply_extracted(
            c,
            tenant,
            customer_id,
            [extract_op("vip", "高危", item_type="flag")],
            "conv-1",
            0.8,
            0.5,
        )
    assert counts["dropped"] == 1
    assert await items_of(engine, tenant, customer_id) == []

    with pytest.raises(DomainError):
        async with engine.begin() as c:
            await upsert_flag(c, tenant, customer_id, "vip", "x", "extracted")
    with pytest.raises(DomainError):
        async with engine.begin() as c:
            await upsert_flag(c, tenant, customer_id, "not_a_flag", "x", "human")


# ---------------------------------------------------------------------------
# P4-UT-12 merge conflict matrix (AC-6/FR-5, R16)
# ---------------------------------------------------------------------------


async def test_p4_ut_12_merge_conflict_matrix(platform):
    """protected beats non-protected; same protection level keeps the newer
    row; the loser is physically deleted and listed in the audit detail."""
    _client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    source = await make_customer(engine, tenant)
    target = await make_customer(engine, tenant)

    # Pair 1: source extracted+newer vs target human(protected)+older -> target kept.
    src_name = await add_item(engine, tenant, source, "profile", "name", "源姓名")
    tgt_name = await add_item(
        engine, tenant, target, "profile", "name", "目标姓名",
        source_type="human", protected=True,
    )
    await db_exec(engine, "UPDATE memory_items SET updated_at=now()-interval '1 day' WHERE id=:id",
                  id=tgt_name)
    # Pair 2: both extracted; source newer -> source kept, target row deleted.
    await add_item(engine, tenant, source, "preference", "language", "粤语")
    tgt_lang = await add_item(engine, tenant, target, "preference", "language", "中文")
    await db_exec(engine, "UPDATE memory_items SET updated_at=now()-interval '1 day' WHERE id=:id",
                  id=tgt_lang)

    result = await merge_customers(engine, tenant, "operator", source, target)
    assert sorted((c["type"], c["key"], c["kept"]) for c in result["conflicts"]) == [
        ("preference", "language", "source"),
        ("profile", "name", "target"),
    ]
    rows = await items_of(engine, tenant, target)
    by_key = {r["key"]: r for r in rows}
    assert by_key["name"]["content"] == "目标姓名" and by_key["name"]["protected"] is True
    assert by_key["language"]["content"] == "粤语"
    # losers physically deleted, nothing left on the source customer
    assert await items_of(engine, tenant, source) == []
    remaining = await db_rows(
        engine, "SELECT id FROM memory_items WHERE id=ANY(:ids)",
        ids=[src_name, tgt_lang],
    )
    assert remaining == []
    audits = await audits_of(engine, tenant, "memory.customer.merged")
    assert len(audits) == 1 and len(audits[0]["details"]["conflicts"]) == 2


# ---------------------------------------------------------------------------
# P4-UT-13 reinforcement semantics (FR-6.2, R11)
# ---------------------------------------------------------------------------


async def test_p4_ut_13_reinforce_resets_clock_never_confidence(platform):
    """Reinforce only refreshes last_referenced_at and accrues
    reference_count; base confidence is untouched (FR-6.2)."""
    _client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    item_id = await add_item(engine, tenant, customer_id, "preference", "language", "中文",
                             confidence=0.7)
    await age_item(engine, item_id, 60)

    async with engine.begin() as c:
        task_id = await enqueue_reinforce_task(c, tenant, {item_id: 3})
    assert task_id is not None
    task = (
        await db_rows(engine, "SELECT * FROM memory_tasks WHERE id=:id", id=task_id)
    )[0]
    assert task["kind"] == "reinforce"
    assert task["payload"]["hits"] == {str(item_id): 3}

    result = await ReinforceService(engine, settings).execute(task)
    assert result == "done"
    row = (await items_of(engine, tenant, customer_id))[0]
    assert row["confidence"] == pytest.approx(0.7)  # never improved
    assert row["reference_count"] == 3
    assert row["last_referenced_at"] is not None
    fresh = await db_rows(
        engine,
        "SELECT last_referenced_at > now()-interval '1 minute' AS fresh"
        " FROM memory_items WHERE id=:id",
        id=item_id,
    )
    assert fresh[0]["fresh"] is True


async def test_p4_ut_13_reinforce_payload_capped_at_200_ids(platform):
    _client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    hits = {uuid4(): 1 for _ in range(250)}
    async with engine.begin() as c:
        task_id = await enqueue_reinforce_task(c, tenant, hits)
    task = (await db_rows(engine, "SELECT payload FROM memory_tasks WHERE id=:id", id=task_id))[0]
    assert len(task["payload"]["hits"]) == 200


# ---------------------------------------------------------------------------
# P4-IT-01 human edit survives customer self-correction (AC-1)
# ---------------------------------------------------------------------------


async def test_p4_it_01_human_edit_survives_and_suggestion_reaches_console(platform):
    """AC-1 end to end: human-corrected name is not overwritten when the
    customer later self-reports a different name; the pending suggestion is
    visible in the console queue; confirming it merges onto the human row."""
    _client, services, settings = platform
    memory, engine, tenant = services.platform.memory, services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    item_id = await add_item(engine, tenant, customer_id, "profile", "name", "张三")
    await memory.edit_item(tenant, "operator", item_id, "张叁")

    async with engine.begin() as c:
        await memory.apply_extracted(
            c, tenant, customer_id, [extract_op("name", "李四")], "conv-9", 0.8, 0.5
        )
    queue = await memory.pending_items(tenant)
    suggestion = next(r for r in queue["items"] if r["key"] == "name:suggested")
    assert suggestion["customer_id"] == customer_id

    # Confirm = merge semantics (R4): human row takes the suggested value.
    await memory.confirm_item(tenant, "operator", suggestion["id"])
    rows = await items_of(engine, tenant, customer_id)
    assert len(rows) == 1
    assert rows[0]["content"] == "李四" and rows[0]["prev_content"] == "张叁"
    assert rows[0]["protected"] is True and rows[0]["source_type"] == "human"


# ---------------------------------------------------------------------------
# P4-IT-02 PII end to end (AC-4, FR-3; R5/R6)
# ---------------------------------------------------------------------------


async def test_p4_it_02_pii_masked_at_every_write_point(platform):
    """Extraction, human edit, flag note and event write all persist masked
    content; no plaintext anywhere in the database (incl. prev_content and
    audit payloads); hit counts ride in audit details."""
    _client, services, settings = platform
    memory, engine, tenant = services.platform.memory, services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)

    # 1. extraction write
    async with engine.begin() as c:
        counts = await memory.apply_extracted(
            c,
            tenant,
            customer_id,
            [extract_op("phone", "手机 13812345678", op="add")],
            "conv-1",
            0.8,
            0.5,
        )
    assert counts["pii_masked"] == 1
    row = (await items_of(engine, tenant, customer_id))[0]
    assert "13812345678" not in row["content"] and "138******78" in row["content"]

    # 2. human edit
    await memory.edit_item(tenant, "operator", row["id"], "新号 13911112222")
    row = (await items_of(engine, tenant, customer_id))[0]
    assert "13911112222" not in row["content"] and "139******22" in row["content"]

    # 3. flag note
    await services.platform.memory.upsert_flag(
        tenant, "operator", customer_id, "vip", "回访 13700001111"
    )
    flag = next(r for r in await items_of(engine, tenant, customer_id) if r["type"] == "flag")
    assert "13700001111" not in flag["content"]

    # 4. event write (R6)
    async with engine.begin() as c:
        await memory.add_event_item(
            c, tenant, customer_id, f"ticket:TK-1:created-{uuid4().hex[:6]}",
            "客户来电 13600009999 咨询", "ticket-1",
        )
    event = next(r for r in await items_of(engine, tenant, customer_id) if r["type"] == "event")
    assert "13600009999" not in event["content"]

    # No plaintext anywhere: items (content+prev_content) and audit payloads.
    leaks = await db_rows(
        engine,
        "SELECT count(*)::int AS n FROM memory_items WHERE tenant_id=:t AND ("
        " content ~ '13[6789]\\d{9}' OR prev_content ~ '13[6789]\\d{9}')",
        t=tenant,
    )
    assert leaks[0]["n"] == 0
    audit_leaks = await db_rows(
        engine,
        "SELECT count(*)::int AS n FROM audit_records WHERE tenant_id=:t AND"
        " details::text ~ '13[6789]\\d{9}'",
        t=tenant,
    )
    assert audit_leaks[0]["n"] == 0

    # Hit counts in the audit details (R5), never the original value.
    edited = await audits_of(engine, tenant, "memory.item.edited")
    assert edited[0]["details"]["pii_masked"] == 1
    flagged = await audits_of(engine, tenant, "memory.flag.upserted")
    assert flagged[0]["details"]["pii_masked"] == 1


async def test_p4_it_02_pii_switch_off_persists_original(platform):
    """FR-3 switch: with masking explicitly disabled the original text
    persists (new writes only — R21)."""
    _client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    no_mask = MemoryService(
        engine, settings.model_copy(update={"memory_pii_masking_enabled": False})
    )
    customer_id = await make_customer(engine, tenant)
    async with engine.begin() as c:
        counts = await no_mask.apply_extracted(
            c, tenant, customer_id, [extract_op("phone", "13812345678", op="add")], "c1", 0.8, 0.5
        )
    assert counts["pii_masked"] == 0
    row = (await items_of(engine, tenant, customer_id))[0]
    assert row["content"] == "13812345678"


# ---------------------------------------------------------------------------
# P4-IT-05 built-in rule scan (AC-3, FR-2; R12/R14)
# ---------------------------------------------------------------------------


async def test_p4_it_05_rule_scan_marks_clears_and_respects_human_flags(platform):
    """Overdue open ticket -> rule-sourced complaint_risk; 30 clean days ->
    auto-clear; a human flag on the same key is never touched by the rule."""
    _client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    ticket_id = await make_ticket(engine, tenant, customer_id)

    # Mark: one manual scan round (04 section 1 principle 4).
    result = await scan_overdue_tickets(engine, settings)
    assert result["marked"] == 1
    flag = next(r for r in await items_of(engine, tenant, customer_id) if r["type"] == "flag")
    assert flag["key"] == "complaint_risk" and flag["source_type"] == "rule"
    assert flag["status"] == "active" and flag["source_ref"] == str(ticket_id)
    audits = await audits_of(engine, tenant, "memory.flag.upserted")
    assert len(audits) == 1 and audits[0]["details"]["source_type"] == "rule"

    # A second round over unchanged facts is a full no-op (D3 779cca9 +
    # D5 afe2d8f): nothing marked, no repeat UPDATE, no repeat audit — at the
    # 15-minute default interval a noisy scan would distort the FR-7
    # governance counters (~96 audit rows/day/customer).
    result = await scan_overdue_tickets(engine, settings)
    assert result["marked"] == 0
    flags = [r for r in await items_of(engine, tenant, customer_id) if r["type"] == "flag"]
    assert len(flags) == 1
    assert flags[0]["content"] == flag["content"] and flags[0]["source_type"] == "rule"
    assert flags[0]["revision"] == 1
    audits = await audits_of(engine, tenant, "memory.flag.upserted")
    assert len(audits) == 1  # still only the first mark

    # Clear: ticket resolved and due date 31 days back -> no overdue within
    # 30 days -> the rule-sourced flag lifts (AC-3).
    await db_exec(
        engine,
        "UPDATE tickets SET status='resolved', due_at=now()-interval '31 days' WHERE id=:id",
        id=ticket_id,
    )
    result = await scan_overdue_tickets(engine, settings)
    assert result["removed"] == 1
    flag = next(r for r in await items_of(engine, tenant, customer_id) if r["type"] == "flag")
    assert flag["status"] == "deleted"
    audits = await audits_of(engine, tenant, "memory.flag.removed")
    assert len(audits) == 1 and audits[0]["details"]["source"] == "rule"


async def test_p4_it_05_rule_scan_never_touches_human_flag(platform):
    """A human complaint_risk flag survives the scan untouched: rule upserts
    do not clobber it and rule clears skip it (FR-2)."""
    _client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    await services.platform.memory.upsert_flag(
        tenant, "operator", customer_id, "complaint_risk", "人工判定投诉风险"
    )
    # Overdue ticket exists but the human flag must absorb the rule write.
    await make_ticket(engine, tenant, customer_id)
    result = await scan_overdue_tickets(engine, settings)
    assert result["marked"] == 0
    flag = next(r for r in await items_of(engine, tenant, customer_id) if r["type"] == "flag")
    assert flag["content"] == "人工判定投诉风险" and flag["source_type"] == "human"
    # No tickets at all and 30+ quiet days: the human flag still stands.
    await db_exec(engine, "DELETE FROM tickets WHERE tenant_id=:t", t=tenant)
    result = await scan_overdue_tickets(engine, settings)
    assert result["removed"] == 0
    flag = next(r for r in await items_of(engine, tenant, customer_id) if r["type"] == "flag")
    assert flag["status"] == "active"


# ---------------------------------------------------------------------------
# P4-IT-06/07 customer deletion (AC-5, FR-4; R16) + performance
# ---------------------------------------------------------------------------


async def test_p4_it_06_delete_customer_cascades_and_reentry_rebuilds(platform):
    """Delete: items/identities/customer physically gone; conversations kept
    with NULL customer; ticket keeps its string reference; audit carries
    operator and counts only; the same external key re-registers fresh."""
    _client, services, settings = platform
    memory, engine, tenant = services.platform.memory, services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant, external_key="wx-user-1")
    await add_item(engine, tenant, customer_id, "profile", "name", "张三")
    await add_item(engine, tenant, customer_id, "preference", "language", "中文")
    conversation_id = await make_conversation(engine, tenant, customer_id)
    ticket_id = await make_ticket(engine, tenant, customer_id)

    result = await memory.delete_customer(tenant, "operator", customer_id)
    assert result == {"items_deleted": 2, "identities_deleted": 1, "conversations_unlinked": 1}

    assert await items_of(engine, tenant, customer_id) == []
    assert await db_rows(engine, "SELECT 1 AS x FROM memory_identities WHERE tenant_id=:t", t=tenant) == []
    assert await db_rows(engine, "SELECT 1 AS x FROM memory_customers WHERE id=:id", id=customer_id) == []
    conv = await db_rows(engine, "SELECT customer_id FROM runtime_conversations WHERE id=:id",
                         id=conversation_id)
    assert conv[0]["customer_id"] is None  # history kept, link nulled
    ticket = await db_rows(engine, "SELECT customer_id FROM tickets WHERE id=:id", id=ticket_id)
    assert ticket[0]["customer_id"] == str(customer_id)  # string ref kept (R16)

    audits = await audits_of(engine, tenant, "memory.customer.deleted")
    assert len(audits) == 1
    details = audits[0]["details"]
    assert details["items_deleted"] == 2 and details["identities_deleted"] == 1
    assert audits[0]["actor"] == "operator"
    assert "张三" not in str(details) and "中文" not in str(details)  # C2/C4

    # Re-entry with the same external key builds a brand-new customer.
    async with engine.begin() as c:
        new_id = await memory.resolve_customer(c, tenant, "channel", "wx-user-1")
    assert new_id != customer_id
    assert await items_of(engine, tenant, new_id) == []


async def test_p4_it_07_delete_customer_performance(platform):
    """Non-functional: deleting a customer at the 200-item cap stays well
    under the 2s P99 budget (local benchmark, 3 samples)."""
    _client, services, settings = platform
    memory, engine, tenant = services.platform.memory, services.repository.engine, settings.tenant_id
    durations = []
    for _ in range(3):
        customer_id = await make_customer(engine, tenant)
        async with engine.begin() as c:
            for i in range(200):
                await c.execute(
                    text(
                        "INSERT INTO memory_items(id,tenant_id,customer_id,type,key,content,"
                        "confidence,status,source_type) VALUES(:id,:t,:c,'preference',:k,"
                        "'内容',0.9,'active','extracted')"
                    ),
                    {"id": uuid4(), "t": tenant, "c": customer_id, "k": f"custom_k{i}"},
                )
        start = time.perf_counter()
        await memory.delete_customer(tenant, "operator", customer_id)
        durations.append(time.perf_counter() - start)
    assert max(durations) <= 2.0, f"delete took {durations}"


# ---------------------------------------------------------------------------
# P4-IT-08 customer merge (AC-6, FR-5; R16)
# ---------------------------------------------------------------------------


async def test_p4_it_08_merge_repoints_deletes_source_and_audits(platform):
    """Full merge: identities and conversations re-point, non-conflict items
    move, the human side of a conflict wins, the source customer disappears
    and the audit carries the full detail."""
    _client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    source = await make_customer(engine, tenant, external_key="wx-dup")
    target = await make_customer(engine, tenant, external_key="app-dup", source="open_platform")
    await add_item(engine, tenant, source, "profile", "name", "抽取姓名")
    await add_item(
        engine, tenant, target, "profile", "name", "人工姓名",
        source_type="human", protected=True,
    )
    await add_item(engine, tenant, source, "preference", "style", "简洁")
    await make_conversation(engine, tenant, source)

    result = await merge_customers(engine, tenant, "operator", source, target)
    assert result["moved"] == 1 and result["identities_moved"] == 1
    assert result["conversations_moved"] == 1
    assert result["conflicts"] == [{"type": "profile", "key": "name", "kept": "target"}]

    assert await db_rows(engine, "SELECT 1 AS x FROM memory_customers WHERE id=:id", id=source) == []
    identities = await db_rows(
        engine, "SELECT customer_id FROM memory_identities WHERE tenant_id=:t", t=tenant
    )
    assert {r["customer_id"] for r in identities} == {target}
    rows = await items_of(engine, tenant, target)
    by_key = {r["key"]: r for r in rows}
    assert by_key["name"]["content"] == "人工姓名"  # human wins (AC-6)
    assert by_key["style"]["content"] == "简洁"
    conv = await db_rows(
        engine, "SELECT customer_id FROM runtime_conversations WHERE tenant_id=:t", t=tenant
    )
    assert conv[0]["customer_id"] == target

    audits = await audits_of(engine, tenant, "memory.customer.merged")
    details = audits[0]["details"]
    assert details["source_customer_id"] == str(source)
    assert details["conflicts"] == [{"type": "profile", "key": "name", "kept": "target"}]
    assert "人工姓名" not in str(details) and "简洁" not in str(details)  # no content


async def test_p4_it_08_merge_rejects_same_customer_and_rolls_back(platform):
    """source == target is refused; a missing target aborts the whole
    transaction with the source customer fully intact."""
    _client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    source = await make_customer(engine, tenant)
    await add_item(engine, tenant, source, "profile", "name", "张三")

    with pytest.raises(DomainError):
        await merge_customers(engine, tenant, "operator", source, source)
    with pytest.raises(DomainError):
        await merge_customers(engine, tenant, "operator", source, uuid4())
    rows = await items_of(engine, tenant, source)
    assert len(rows) == 1 and rows[0]["content"] == "张三"
    assert await db_rows(engine, "SELECT 1 AS x FROM memory_customers WHERE id=:id", id=source)


async def test_p4_it_08_merge_relinks_tickets_and_keeps_memory_flowing(platform):
    """R22: merge rewrites tickets.customer_id (string column, no FK) to the
    target inside the merge transaction and audits tickets_relinked; the
    source's in-flight tickets stay alive afterwards — a later resolved event
    writes to the surviving target and the rule scan flags the target for
    them. Delete deliberately keeps the reference dangling + the "客户已删除"
    degradation instead (R16/R22 scenario split; IT-06 contrast)."""
    _client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    source = await make_customer(engine, tenant)
    target = await make_customer(engine, tenant)
    ticket_id = await make_ticket(engine, tenant, source)  # overdue, open
    await db_exec(
        engine,
        "INSERT INTO tickets_detail(ticket_id,tenant_id,description,resolution)"
        " VALUES(:id,:t,'客户描述','已解决')",
        id=ticket_id,
        t=tenant,
    )

    result = await merge_customers(engine, tenant, "operator", source, target)
    assert result["tickets_relinked"] == 1
    ticket = await db_rows(engine, "SELECT customer_id FROM tickets WHERE id=:id", id=ticket_id)
    assert ticket[0]["customer_id"] == str(target)
    audits = await audits_of(engine, tenant, "memory.customer.merged")
    assert audits[0]["details"]["tickets_relinked"] == 1

    # A later event on the relinked ticket writes to the surviving target
    # (pre-R22 the _customer_exists check silently dropped it: memory loss).
    from test_memory_events_postgres import store_settings

    from agent_platform.modules.memory.events import EventWriter
    from agent_platform.modules.memory.store import TsVectorStore

    store = TsVectorStore(engine, store_settings(settings))
    writer = EventWriter(engine, store, store_settings(settings))
    outcome = await writer.execute(
        {
            "id": uuid4(),
            "tenant_id": tenant,
            "payload": {"event": "resolved", "ticket_id": str(ticket_id)},
        }
    )
    assert outcome == "done"
    rows = await items_of(engine, tenant, target)
    assert any(r["type"] == "event" and r["key"].endswith(":resolved") for r in rows)

    # The rule scan covers the relinked overdue ticket and flags the target.
    result = await scan_overdue_tickets(engine, settings)
    assert result["marked"] == 1
    flags = [r for r in await items_of(engine, tenant, target) if r["type"] == "flag"]
    assert flags[0]["key"] == "complaint_risk" and flags[0]["source_type"] == "rule"


# ---------------------------------------------------------------------------
# P4-IT-09 daily decay migration (AC-8/AC-10/AC-11, FR-6.1/FR-6.4; R8)
# ---------------------------------------------------------------------------


async def test_p4_it_09_daily_migration_routes_by_type_and_audits(platform):
    """One manual decay round: preference 200d -> expired (AC-8), profile
    400d below threshold -> pending (AC-11), protected 500d and flag 500d
    immune (AC-10), event never migrates on confidence (R8); every
    transition audited with the pre-transition score (FR-6.4)."""
    _client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    pref = await add_item(engine, tenant, customer_id, "preference", "language", "中文",
                          confidence=0.9)
    prof = await add_item(engine, tenant, customer_id, "profile", "phone", "138******78",
                          confidence=0.6)
    prot = await add_item(engine, tenant, customer_id, "profile", "name", "张三",
                          source_type="human", protected=True, confidence=0.9)
    flag = await add_item(engine, tenant, customer_id, "flag", "vip", "重点客户",
                          source_type="human", protected=True)
    event = await add_item(engine, tenant, customer_id, "event", "ticket:TK-1:created",
                           "工单事件", confidence=0.9)
    for item_id, days in ((pref, 200), (prof, 400), (prot, 500), (flag, 500), (event, 200)):
        await age_item(engine, item_id, days)

    result = await daily_migration(engine, settings)
    assert result == {"profile_pending": 1, "preference_expired": 1, "evicted": 0}

    rows = {r["key"]: r for r in await items_of(engine, tenant, customer_id)}
    assert rows["language"]["status"] == "expired"  # AC-8: recoverable, not deleted
    assert rows["phone"]["status"] == "pending"  # AC-11: re-confirmation queue
    assert rows["name"]["status"] == "active"  # AC-10: protected immune
    assert rows["vip"]["status"] == "active"  # flag immune
    assert rows["ticket:TK-1:created"]["status"] == "active"  # R8: TTL-managed only

    expired = await audits_of(engine, tenant, "memory.decay_expired")
    assert len(expired) == 1
    # 0.9 * 2^(-200/90) ~= 0.193, recorded before the transition (FR-6.4)
    assert expired[0]["details"]["effective_confidence"] == pytest.approx(0.193, abs=0.01)
    assert expired[0]["details"]["key"] == "language"
    pending = await audits_of(engine, tenant, "memory.decay_pending")
    assert len(pending) == 1
    # 0.6 * 2^(-400/365) ~= 0.281
    assert pending[0]["details"]["effective_confidence"] == pytest.approx(0.281, abs=0.01)

    # Immune rows keep injecting through active_items/active_flags.
    memory = services.platform.memory
    injected = await memory.active_items(tenant, customer_id)
    assert [r["key"] for r in injected] == ["name"]
    flags = await memory.active_flags(tenant, customer_id, 3)
    assert [r["key"] for r in flags] == ["vip"]


# ---------------------------------------------------------------------------
# P4-IT-10 real-time injection filter (R7; dual-mode)
# ---------------------------------------------------------------------------


async def test_p4_it_10_injection_filter_applies_before_any_daily_run(platform):
    """With no daily task run, a below-threshold profile/preference is
    already excluded from injection (inline SQL filter); the threshold keeps
    equality (>=, P4-UT-11); protected rows always pass. Event retrieval is
    NOT confidence-filtered (R7/R8): visibility stays TTL-managed."""
    _client, services, settings = platform
    memory, engine, tenant = services.platform.memory, services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    await add_item(engine, tenant, customer_id, "profile", "name", "张三", confidence=0.6)
    above = await add_item(engine, tenant, customer_id, "profile", "phone", "x", confidence=0.6)
    below = await add_item(engine, tenant, customer_id, "preference", "language", "中文",
                           confidence=0.6)
    prot = await add_item(engine, tenant, customer_id, "preference", "style", "简洁",
                          source_type="human", protected=True, confidence=0.6)
    await age_item(engine, above, 364)  # 0.6*2^(-364/365) ~= 0.3004: kept
    await age_item(engine, below, 366)  # 0.6*2^(-366/365) ~= 0.2994: filtered
    await age_item(engine, prot, 500)  # protected: immune

    keys = [r["key"] for r in await memory.active_items(tenant, customer_id)]
    assert set(keys) == {"name", "phone", "style"}
    # The filtered row is still active in the table: only the daily task
    # migrates state; the query-time filter just hides it (R7).
    row = next(r for r in await items_of(engine, tenant, customer_id) if r["id"] == below)
    assert row["status"] == "active"

    # Event retrieval ignores the confidence filter entirely (dual-mode).
    from agent_platform.modules.memory.store import (
        PgVectorStore,
        build_memory_store,
    )

    event = await add_item(engine, tenant, customer_id, "event", "ticket:TK-9:created",
                           "物流 维修 工单 事件 内容", confidence=0.6)
    await age_item(engine, event, 500)  # far below any confidence threshold
    store = await build_memory_store(engine, None, settings)
    hits = await store.search(tenant, customer_id, "维修", 5)
    assert any(h["id"] == event for h in hits)  # decay reorders, never hides

    has_embedding = await db_rows(
        engine,
        "SELECT 1 AS x FROM information_schema.columns WHERE table_name='memory_items'"
        " AND column_name='embedding'",
    )
    if has_embedding:
        # Vector mode: same no-confidence-filter contract (R7).
        from test_memory_events_postgres import StubEmbedGateway, set_embedding

        vector = [0.01] * 1536
        await set_embedding(engine, event, vector)
        vec_store = PgVectorStore(engine, StubEmbedGateway(vector), settings)
        hits = await vec_store.search(tenant, customer_id, "维修", 5)
        assert any(h["id"] == event for h in hits)


async def test_p4_it_10_decay_off_keeps_phase3_injection(platform):
    """AC-13/P4-IT-16 service layer: memory_decay_enabled=false removes the
    filter entirely — the injection query is exactly the Phase 3 one."""
    _client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    no_decay = MemoryService(engine, settings.model_copy(update={"memory_decay_enabled": False}))
    customer_id = await make_customer(engine, tenant)
    old = await add_item(engine, tenant, customer_id, "preference", "language", "中文",
                         confidence=0.6)
    await age_item(engine, old, 500)
    keys = [r["key"] for r in await no_decay.active_items(tenant, customer_id)]
    assert keys == ["language"]  # Phase 3 behaviour: no decay filtering


# ---------------------------------------------------------------------------
# P4-IT-11 L1 reinforcement: same-key upsert resets the clock (AC-9)
# ---------------------------------------------------------------------------


async def test_p4_it_11_same_key_upsert_resets_decay_clock(platform):
    """A preference one day from crossing the threshold is mentioned again
    (same-key extraction update): the clock resets and the daily pass leaves
    it active; an unmentioned twin expires (AC-9)."""
    _client, services, settings = platform
    memory, engine, tenant = services.platform.memory, services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    mentioned = await add_item(engine, tenant, customer_id, "preference", "language", "中文",
                               confidence=0.9)
    twin = await add_item(engine, tenant, customer_id, "preference", "style", "简洁",
                          confidence=0.9)
    # 0.9*2^(-144/90) ~= 0.297: just past the ~142.6d crossing point.
    await age_item(engine, mentioned, 144)
    await age_item(engine, twin, 144)

    async with engine.begin() as c:
        counts = await memory.apply_extracted(
            c, tenant, customer_id, [extract_op("language", "中文", item_type="preference")],
            "conv-7", 0.8, 0.5,
        )
    assert counts["updated"] == 1

    result = await daily_migration(engine, settings)
    assert result["preference_expired"] == 1
    rows = {r["key"]: r for r in await items_of(engine, tenant, customer_id)}
    assert rows["language"]["status"] == "active"  # clock reset saved it
    assert rows["style"]["status"] == "expired"  # unmentioned twin crossed


# ---------------------------------------------------------------------------
# P4-IT-12 L2/L3 batch reinforcement (FR-6.2, R11) — execution side
# ---------------------------------------------------------------------------


async def test_p4_it_12_reinforce_task_batches_clock_and_count_updates(platform):
    """One reinforce task refreshes many items in a batch: each id gets its
    clock reset and its reference_count bumped by the collected hits;
    deleted rows are skipped. (Dispatch-side sampling, incl.
    memory_reinforce_sample_rate=0, is asserted with the run-finish path
    once T9 lands.)"""
    _client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    a = await add_item(engine, tenant, customer_id, "profile", "name", "张三")
    b = await add_item(engine, tenant, customer_id, "preference", "language", "中文")
    gone = await add_item(engine, tenant, customer_id, "profile", "phone", "x", status="deleted")
    for item_id in (a, b, gone):
        await age_item(engine, item_id, 30)

    async with engine.begin() as c:
        task_id = await enqueue_reinforce_task(c, tenant, {a: 2, b: 5, gone: 1})
    task = (await db_rows(engine, "SELECT * FROM memory_tasks WHERE id=:id", id=task_id))[0]
    result = await ReinforceService(engine, settings).execute(task)
    assert result == "done"

    rows = {r["key"]: r for r in await items_of(engine, tenant, customer_id)}
    assert rows["name"]["reference_count"] == 2
    assert rows["language"]["reference_count"] == 5
    fresh = await db_rows(
        engine,
        "SELECT count(*)::int AS n FROM memory_items WHERE tenant_id=:t AND status='active'"
        " AND last_referenced_at > now()-interval '1 minute'",
        t=tenant,
    )
    assert fresh[0]["n"] == 2  # both active rows refreshed
    assert rows["phone"]["reference_count"] == 0  # deleted rows skipped


# ---------------------------------------------------------------------------
# P4-IT-14 capacity eviction (AC-12, FR-6.3)
# ---------------------------------------------------------------------------


async def test_p4_it_14_capacity_eviction_soft_expires_lowest_scores(platform):
    """210 active rows (5 protected): one decay round brings the customer to
    the 200 cap; the evicted are the lowest effective_confidence rows,
    soft-expired (FR-6.3, D2 fix verified), protected all survive; the audit
    lists every eviction with its pre-eviction score."""
    _client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    async with engine.begin() as c:
        for i in range(5):
            await c.execute(
                text(
                    "INSERT INTO memory_items(id,tenant_id,customer_id,type,key,content,"
                    "confidence,status,source_type,protected) VALUES(:id,:t,:c,'profile',:k,"
                    "'人工',0.9,'active','human',true)"
                ),
                {"id": uuid4(), "t": tenant, "c": customer_id, "k": f"custom_p{i}"},
            )
        for i in range(205):
            # profile 365d half-life, age 0..204d: all effective > 0.6, so the
            # threshold pass leaves them alone and eviction does the culling.
            await c.execute(
                text(
                    "INSERT INTO memory_items(id,tenant_id,customer_id,type,key,content,"
                    "confidence,status,source_type,last_referenced_at) VALUES(:id,:t,:c,"
                    "'profile',:k,'x',0.9,'active','extracted',now()-make_interval(days => :d))"
                ),
                {"id": uuid4(), "t": tenant, "c": customer_id, "k": f"custom_n{i}", "d": i},
            )

    result = await daily_migration(engine, settings)
    assert result == {"profile_pending": 0, "preference_expired": 0, "evicted": 10}

    rows = await items_of(engine, tenant, customer_id)
    active = [r for r in rows if r["status"] == "active"]
    assert len(active) == 200
    assert sum(1 for r in active if r["protected"]) == 5  # all protected survive
    evicted_keys = sorted(r["key"] for r in rows if r["status"] == "expired")
    assert evicted_keys == [f"custom_n{i}" for i in range(195, 205)]  # lowest scores
    audits = await audits_of(engine, tenant, "memory.evicted")
    assert len(audits) == 10
    assert all("effective_confidence" in a["details"] for a in audits)


# ---------------------------------------------------------------------------
# P4-IT-15 expired restore revives at full strength (FR-6.4, R19)
# ---------------------------------------------------------------------------


async def test_p4_it_15_expired_restore_refreshes_clock_and_stays_active(platform):
    """Restoring an expired row sets it active with a fresh reference clock
    (复活即满血): the very next decay round leaves it alone (R19)."""
    _client, services, settings = platform
    memory, engine, tenant = services.platform.memory, services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    item_id = await add_item(engine, tenant, customer_id, "preference", "language", "中文",
                             confidence=0.9)
    await age_item(engine, item_id, 200)
    await daily_migration(engine, settings)
    row = (await items_of(engine, tenant, customer_id))[0]
    assert row["status"] == "expired"

    await memory.restore_item(tenant, "operator", item_id)
    row = (await items_of(engine, tenant, customer_id))[0]
    assert row["status"] == "active"
    fresh = await db_rows(
        engine,
        "SELECT last_referenced_at > now()-interval '1 minute' AS fresh"
        " FROM memory_items WHERE id=:id",
        id=item_id,
    )
    assert fresh[0]["fresh"] is True

    result = await daily_migration(engine, settings)
    assert result["preference_expired"] == 0
    row = (await items_of(engine, tenant, customer_id))[0]
    assert row["status"] == "active"  # no revive-then-die loop (D2 chain)


# ---------------------------------------------------------------------------
# P4-IT-18 vector mode: embeddings die with their rows (technical design §5)
# ---------------------------------------------------------------------------


async def test_p4_it_18_vector_column_cleared_with_customer_delete(platform):
    """pgvector only: embedding is a plain column, so the physical row delete
    removes the vector with it — no orphaned embeddings remain (SQL check)."""
    _client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    has_embedding = await db_rows(
        engine,
        "SELECT 1 AS x FROM information_schema.columns WHERE table_name='memory_items'"
        " AND column_name='embedding'",
    )
    if not has_embedding:
        pytest.skip("text mode: no embedding column (vector clear verified on pgvector)")
    customer_id = await make_customer(engine, tenant)
    item_id = await add_item(engine, tenant, customer_id, "event", "ticket:TK-1:created", "事件")
    vector = "[" + ",".join(["0.01"] * 1536) + "]"
    await db_exec(engine, "UPDATE memory_items SET embedding=CAST(:v AS vector) WHERE id=:id",
                  v=vector, id=item_id)

    await services.platform.memory.delete_customer(tenant, "operator", customer_id)
    leftovers = await db_rows(
        engine,
        "SELECT count(*)::int AS n FROM memory_items WHERE tenant_id=:t AND embedding IS NOT NULL",
        t=tenant,
    )
    assert leftovers[0]["n"] == 0


# ---------------------------------------------------------------------------
# P4-CM-05 protected invariant (R18)
# ---------------------------------------------------------------------------


async def test_p4_cm_05_protected_invariant_holds_across_governance_paths(platform):
    """protected <=> source_type='human' after edit, release, flag writes,
    decay passes and merges: zero drift rows at every checkpoint."""
    _client, services, settings = platform
    memory, engine, tenant = services.platform.memory, services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    item_id = await add_item(engine, tenant, customer_id, "profile", "name", "张三")
    await memory.edit_item(tenant, "operator", item_id, "张叁")
    assert await protected_drift_count(engine, tenant) == 0
    await memory.release_item(tenant, "operator", item_id)
    assert await protected_drift_count(engine, tenant) == 0
    await memory.upsert_flag(tenant, "operator", customer_id, "vip", "重点客户")
    assert await protected_drift_count(engine, tenant) == 0
    await daily_migration(engine, settings)
    assert await protected_drift_count(engine, tenant) == 0
    source = await make_customer(engine, tenant)
    await add_item(engine, tenant, source, "preference", "style", "简洁")
    await merge_customers(engine, tenant, "operator", source, customer_id)
    assert await protected_drift_count(engine, tenant) == 0


# ---------------------------------------------------------------------------
# P4-IT-03 flag injection (AC-2, FR-2; R15)
# ---------------------------------------------------------------------------


async def _run_and_capture(client, services, settings, conversation_id, model,
                           message="进度怎样了"):
    from test_memory_customers_postgres import claim_and_run

    services.worker.runtime.legacy.model = model
    response = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages", json={"content": message}
    )
    assert response.status_code == 202, response.text
    await claim_and_run(services, settings)
    return response


def _injected_system_text(model):
    return "\n".join(m.content for m in model.seen[0] if m.type == "system")


class _CaptureModel:
    def __init__(self, reply="回答"):
        self.seen = []
        self.reply = reply

    async def ainvoke(self, messages):
        self.seen.append(messages)
        from langchain_core.messages import AIMessage

        return AIMessage(content=self.reply)


async def _bound_conversation(engine, tenant, customer_id):
    conversation_id = uuid4()
    await db_exec(
        engine,
        "INSERT INTO runtime_conversations(id,tenant_id,external_id,customer_id)"
        " VALUES(:id,:t,:e,:c)",
        id=conversation_id,
        t=tenant,
        e=f"conv-{uuid4().hex[:8]}",
        c=customer_id,
    )
    return conversation_id


async def test_p4_it_03_flags_inject_first_and_have_their_own_budget(platform):
    """AC-2: the 【服务标记】 section opens the injection, ahead of profile/
    preference/pending-events; flags do not consume the 20-item profile
    budget; their own cap is configurable (memory_flag_inject_max)."""
    client, services, settings = platform
    memory, engine, tenant = services.platform.memory, services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    # Fill the 20-item profile/preference budget completely.
    for i in range(20):
        await add_item(engine, tenant, customer_id, "preference", f"custom_{i:02d}",
                       f"偏好{i}", confidence=0.9)
    await memory.upsert_flag(tenant, "operator", customer_id, "vip", "VIP客户")
    await memory.upsert_flag(tenant, "operator", customer_id, "complaint_risk", "曾投诉")
    conversation_id = await _bound_conversation(engine, tenant, customer_id)

    model = _CaptureModel()
    await _run_and_capture(client, services, settings, conversation_id, model)
    injected = _injected_system_text(model)
    assert "【服务标记】" in injected and "VIP客户" in injected and "曾投诉" in injected
    # Flag section precedes the preference section.
    assert injected.index("【服务标记】") < injected.index("【客户偏好】")
    # All 20 budget items still inject: flags have their own budget (FR-2).
    for i in range(20):
        assert f"偏好{i}" in injected

    # Independent cap: memory_flag_inject_max=1 keeps only the newest flag.
    services.worker.settings = settings.model_copy(update={"memory_flag_inject_max": 1})
    model2 = _CaptureModel()
    await _run_and_capture(client, services, settings, conversation_id, model2)
    injected2 = _injected_system_text(model2)
    assert injected2.count("：VIP客户") + injected2.count("：曾投诉") == 1  # one of two dropped


async def test_p4_it_03_no_flags_keeps_phase3_message_body(platform):
    """No flags -> render_flag_injection is None and the injected body is
    byte-identical to Phase 3 (regression guard, 08 section 3.1 红线)."""
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    await add_item(engine, tenant, customer_id, "profile", "name", "张三")
    conversation_id = await _bound_conversation(engine, tenant, customer_id)
    model = _CaptureModel()
    await _run_and_capture(client, services, settings, conversation_id, model)
    injected = _injected_system_text(model)
    assert "【服务标记】" not in injected and "张三" in injected


# ---------------------------------------------------------------------------
# P4-IT-04 blacklist behaviour (FR-2; R13/R15)
# ---------------------------------------------------------------------------


async def test_p4_it_04_blacklist_injects_instruction_no_handoff_by_default(platform):
    """blacklist flag: the injection gains the fixed instruction line
    谨慎服务，涉承诺一律转人工确认; with the default switch
    (memory_blacklist_auto_handoff=false) the run proceeds normally."""
    client, services, settings = platform
    memory, engine, tenant = services.platform.memory, services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    await memory.upsert_flag(tenant, "operator", customer_id, "blacklist", "黑名单客户")
    conversation_id = await _bound_conversation(engine, tenant, customer_id)

    model = _CaptureModel()
    await _run_and_capture(client, services, settings, conversation_id, model)
    injected = _injected_system_text(model)
    assert "谨慎服务，涉承诺一律转人工确认" in injected
    # Default switch: the run executed (the model was invoked).
    assert model.seen


async def test_p4_it_04_blacklist_auto_handoff_when_enabled(platform):
    """memory_blacklist_auto_handoff=true: a blacklisted customer's message
    is recorded and handed straight to a human — no run is submitted (R13)."""
    client, services, settings = platform
    memory, engine, tenant = services.platform.memory, services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    await memory.upsert_flag(tenant, "operator", customer_id, "blacklist", "黑名单客户")
    conversation_id = await _bound_conversation(engine, tenant, customer_id)
    # Opt the customer service into auto handoff for this test only.
    services.platform.customer.settings = settings.model_copy(
        update={"memory_blacklist_auto_handoff": True}
    )

    response = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages", json={"content": "在吗"}
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["run"] is None and body["handoff"] is not None

    # No run was submitted for the conversation.
    runs = await db_rows(
        engine, "SELECT count(*)::int AS n FROM runtime_runs WHERE tenant_id=:t", t=tenant
    )
    assert runs[0]["n"] == 0
    # The user message is on record and the conversation waits for a human.
    conv = await db_rows(
        engine, "SELECT mode FROM runtime_conversations WHERE id=:id", id=conversation_id
    )
    assert conv[0]["mode"] == "waiting"
    messages = await db_rows(
        engine,
        "SELECT content FROM conversation_messages WHERE conversation_id=:id AND role='user'",
        id=conversation_id,
    )
    assert any("在吗" in row["content"] for row in messages)
    handoffs = await db_rows(
        engine, "SELECT reason FROM handoffs WHERE conversation_id=:id", id=conversation_id
    )
    assert handoffs[0]["reason"] == "黑名单客户自动转人工"


# ---------------------------------------------------------------------------
# P4-IT-12/13 reinforcement dispatch (FR-6.2; R10/R11)
# ---------------------------------------------------------------------------


async def test_p4_it_12_l3_overlap_enqueues_reinforce_at_run_finish(platform):
    """End to end: an injected item overlapping the user input (L3) becomes
    a reinforce task inside the run-finish transaction; the memory.injected
    runtime event carries the section counts (R10); with
    memory_reinforce_sample_rate=0 nothing is enqueued (dispatch-side
    sampling)."""
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    item_id = await add_item(engine, tenant, customer_id, "preference", "language", "中文")
    conversation_id = await _bound_conversation(engine, tenant, customer_id)

    await _run_and_capture(client, services, settings, conversation_id, _CaptureModel(),
                           message="请用中文回复我")
    tasks = await db_rows(
        engine,
        "SELECT * FROM memory_tasks WHERE tenant_id=:t AND kind='reinforce'",
        t=tenant,
    )
    assert len(tasks) == 1
    assert tasks[0]["payload"]["hits"] == {str(item_id): 1}
    events = await db_rows(
        engine,
        "SELECT e.data FROM runtime_events e JOIN runtime_runs r ON r.id=e.run_id"
        " WHERE e.type='memory.injected' AND r.tenant_id=:t",
        t=tenant,
    )
    assert len(events) == 1
    assert events[0]["data"] == {"items": 1, "flags": 0, "events": 0}

    # Sampling off: a second run enqueues nothing more.
    services.worker.settings = settings.model_copy(update={"memory_reinforce_sample_rate": 0.0})
    await _run_and_capture(client, services, settings, conversation_id, _CaptureModel(),
                           message="请用中文回复我")
    tasks = await db_rows(
        engine,
        "SELECT count(*)::int AS n FROM memory_tasks WHERE tenant_id=:t AND kind='reinforce'",
        t=tenant,
    )
    assert tasks[0]["n"] == 1  # unchanged


async def test_p4_it_13_reinforcement_never_writes_on_the_hot_path(platform):
    """FR-6.2: during run execution the referenced item's row is untouched
    (last_referenced_at/reference_count unchanged right after finish); the
    refresh only happens when the memory worker executes the queued task."""
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    item_id = await add_item(engine, tenant, customer_id, "preference", "language", "中文")
    await age_item(engine, item_id, 10)
    conversation_id = await _bound_conversation(engine, tenant, customer_id)

    await _run_and_capture(client, services, settings, conversation_id, _CaptureModel(),
                           message="请用中文回复我")
    row = (await items_of(engine, tenant, customer_id))[0]
    assert row["reference_count"] == 0  # hot path wrote nothing
    aged = await db_rows(
        engine,
        "SELECT last_referenced_at < now()-interval '1 day' AS aged"
        " FROM memory_items WHERE id=:id",
        id=item_id,
    )
    assert aged[0]["aged"] is True  # still backdated: no synchronous refresh

    # The queued task does the refresh, batched off the hot path.
    from agent_platform.modules.memory.bootstrap import MemoryDispatcher

    dispatcher = MemoryDispatcher(None, None, None, reinforce=ReinforceService(engine, settings))
    task = (
        await db_rows(
            engine,
            "SELECT * FROM memory_tasks WHERE tenant_id=:t AND kind='reinforce'",
            t=tenant,
        )
    )[0]
    assert await dispatcher.execute(task) == "done"
    row = (await items_of(engine, tenant, customer_id))[0]
    assert row["reference_count"] == 1
    fresh = await db_rows(
        engine,
        "SELECT last_referenced_at > now()-interval '1 minute' AS fresh"
        " FROM memory_items WHERE id=:id",
        id=item_id,
    )
    assert fresh[0]["fresh"] is True


# ---------------------------------------------------------------------------
# P4-IT-16 decay switch parity with Phase 3 (AC-13; R14)
# ---------------------------------------------------------------------------


async def test_p4_it_16_decay_disabled_skips_maintenance_migration(platform):
    """AC-13 at the worker layer: with memory_decay_enabled=false the
    maintenance pass is exactly the Phase 3 one (TTL only) — no threshold
    migration, no eviction — while the enabled worker migrates."""
    _client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    old = await add_item(engine, tenant, customer_id, "preference", "language", "中文",
                         confidence=0.9)
    await age_item(engine, old, 200)  # below threshold: would expire under decay

    from agent_platform.modules.memory.worker import MemoryWorker

    off = MemoryWorker(engine, service=None,
                       settings=settings.model_copy(update={"memory_decay_enabled": False}),
                       store=None)
    await off.maintenance()
    row = (await items_of(engine, tenant, customer_id))[0]
    assert row["status"] == "active"  # Phase 3 behaviour: untouched

    on = MemoryWorker(engine, service=None,
                      settings=settings.model_copy(update={"memory_decay_enabled": True}),
                      store=None)
    await on.maintenance()
    row = (await items_of(engine, tenant, customer_id))[0]
    assert row["status"] == "expired"


async def test_p4_it_16_decay_disabled_gates_reinforce_dispatch(platform):
    """R23(b): with memory_decay_enabled=false the dispatch side enqueues no
    reinforce task and the L3 path never refreshes last_referenced_at —
    reinforcement would shift the Phase 3 retrieval ordering (decay_factor
    keys off that column), so AC-13 parity requires the gate. L1 (same-key
    upsert refreshing the clock) is Phase 2 behaviour and stays ungated."""
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    item_id = await add_item(engine, tenant, customer_id, "preference", "language", "中文")
    await age_item(engine, item_id, 10)
    conversation_id = await _bound_conversation(engine, tenant, customer_id)

    services.worker.settings = settings.model_copy(update={"memory_decay_enabled": False})
    await _run_and_capture(client, services, settings, conversation_id, _CaptureModel(),
                           message="请用中文回复我")
    tasks = await db_rows(
        engine,
        "SELECT count(*)::int AS n FROM memory_tasks WHERE tenant_id=:t AND kind='reinforce'",
        t=tenant,
    )
    assert tasks[0]["n"] == 0  # dispatch gated off
    aged = await db_rows(
        engine,
        "SELECT last_referenced_at < now()-interval '1 day' AS aged"
        " FROM memory_items WHERE id=:id",
        id=item_id,
    )
    assert aged[0]["aged"] is True  # L3 never touched the clock

    # L1 stays ungated: a same-key extraction upsert refreshes the clock even
    # with decay off (Phase 2 behaviour, outside the R23(b) gate).
    decay_off = MemoryService(
        engine, settings.model_copy(update={"memory_decay_enabled": False})
    )
    async with engine.begin() as c:
        await decay_off.apply_extracted(
            c, tenant, customer_id,
            [extract_op("language", "中文", op="update", item_type="preference")],
            "conv-l1", 0.9, 0.5,
        )
    fresh = await db_rows(
        engine,
        "SELECT last_referenced_at > now()-interval '1 minute' AS fresh"
        " FROM memory_items WHERE id=:id",
        id=item_id,
    )
    assert fresh[0]["fresh"] is True


# ---------------------------------------------------------------------------
# P4-IT-17 observability memory summary (AC-7, FR-7; R20)
# ---------------------------------------------------------------------------


async def test_p4_it_17_memory_summary_aggregates_constructed_data(platform):
    """Construct a known dataset, then memory_summary() agrees with it:
    extraction volume from audit details, pending backlog, governance
    counters, decay counters, reinforcement queue stats, PII hits."""
    client, services, settings = platform
    memory, engine, tenant = services.platform.memory, services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)

    # Extraction: 1 added + 1 pii hit, audited the way the extraction service
    # persists it (apply_extracted counts ride the audit details).
    async with engine.begin() as c:
        counts = await memory.apply_extracted(
            c, tenant, customer_id,
            [extract_op("phone", "13812345678", op="add")], "conv-1", 0.8, 0.5,
        )
        from agent_platform.platform.persistence.store import audit

        await audit(c, tenant, "memory", "memory.extract.applied", customer_id, **counts)

    # Governance: one flag write, one human edit.
    await memory.upsert_flag(tenant, "operator", customer_id, "vip", "重点")
    phone_item = next(r for r in await items_of(engine, tenant, customer_id)
                      if r["key"] == "phone")
    await memory.edit_item(tenant, "operator", phone_item["id"], "新号码")
    # Backlog: one pending row.
    await add_item(engine, tenant, customer_id, "profile", "company", "公司", status="pending")
    # Decay: one preference expires.
    old = await add_item(engine, tenant, customer_id, "preference", "style", "简洁",
                         confidence=0.9)
    await age_item(engine, old, 200)
    await daily_migration(engine, settings)
    # Reinforcement: one done task refreshing one id.
    async with engine.begin() as c:
        task_id = await enqueue_reinforce_task(c, tenant, {phone_item["id"]: 2})
    task = (await db_rows(engine, "SELECT * FROM memory_tasks WHERE id=:id", id=task_id))[0]
    await ReinforceService(engine, settings).execute(task)
    await db_exec(engine, "UPDATE memory_tasks SET status='done' WHERE id=:id", id=task_id)

    summary = await services.platform.observability.memory_summary(tenant)
    today = [d for d in summary["extraction_daily"] if d["runs"] == 1]
    assert today and today[0]["added"] == 1 and today[0]["pii_masked"] == 1
    assert summary["backlog"]["pending"] >= 1
    governance = {row["action"]: row["n"] for row in summary["governance"]}
    assert governance["memory.flag.upserted"] == 1
    assert governance["memory.item.edited"] == 1
    assert summary["pii_masked"] >= 1
    assert summary["decay"]["decay_expired"] == 1
    reinforce = summary["reinforce"]
    assert reinforce["tasks_done"] == 1 and reinforce["ids_refreshed"] == 1

    # HTTP surface (D4 regression guard: the reinforce aggregation once 500'd
    # this endpoint on every call): reader auth suffices, anonymous is 401,
    # and the body mirrors the service-level shape.
    response = await client.get(
        "/api/v1/observability/memory", headers={"Authorization": "Bearer platform-viewer"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pii_masked"] == summary["pii_masked"]
    assert body["decay"]["decay_expired"] == 1
    assert body["reinforce"]["tasks_done"] == 1
    assert (
        await client.get(
            "/api/v1/observability/memory", headers={"Authorization": "Bearer no-such-token"}
        )
    ).status_code == 401
