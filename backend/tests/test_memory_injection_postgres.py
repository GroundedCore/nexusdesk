"""Phase 6 injection explainability — PostgreSQL integration + API tests.

Case IDs refer to
docs/requirement/agent-memory/phase-6-memory-visualization/04-test-plan.md
(P6-IT-*, P6-API-*, plus the database-bound halves of P6-UT-03/04). Runs only
with TEST_DATABASE_URL (CI Backend job); skipped locally without one. Helpers
are shared with the Phase 2/4 suites so fixtures and data construction stay
consistent. Time-travel follows the Phase 4 convention: rows are backdated
with direct UPDATEs and the daily maintenance pass is triggered manually for
a single round (04 section 2).
"""

import json
import re
import time
from uuid import uuid4

import pytest
from sqlalchemy import event
from test_memory_customers_postgres import make_worker
from test_memory_governance_postgres import (
    _bound_conversation,
    _CaptureModel,
    _run_and_capture,
    add_item,
    age_item,
    audits_of,
    db_exec,
    db_rows,
    make_customer,
)
from test_platform_postgres import platform as _platform

from agent_platform.modules.memory.decay import daily_migration
from agent_platform.modules.memory.governance import delete_customer, merge_customers
from agent_platform.platform.persistence.store import DomainError

platform = _platform
pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def log_rows(engine, tenant):
    return await db_rows(
        engine,
        "SELECT * FROM memory_injection_log WHERE tenant_id=:t ORDER BY id",
        t=tenant,
    )


async def injected_events(engine, tenant):
    return await db_rows(
        engine,
        "SELECT e.run_id, e.data FROM runtime_events e JOIN runtime_runs r ON r.id=e.run_id"
        " WHERE e.type='memory.injected' AND r.tenant_id=:t ORDER BY e.run_id",
        t=tenant,
    )


async def only_run(engine, tenant):
    rows = await db_rows(engine, "SELECT * FROM runtime_runs WHERE tenant_id=:t", t=tenant)
    assert len(rows) == 1
    return rows[0]


async def insert_log_row(engine, tenant, customer_id, item_id=None, run_id=None,
                         injected_at_sql="now()"):
    """One manual memory_injection_log row; timestamp via SQL expression."""
    await db_exec(
        engine,
        "INSERT INTO memory_injection_log(tenant_id,run_id,customer_id,item_id,section,"
        f"injected_at) VALUES(:t,:r,:c,:i,'profile',{injected_at_sql})",
        t=tenant,
        r=run_id or uuid4(),
        c=customer_id,
        i=item_id or uuid4(),
    )


async def insert_run(engine, tenant, conversation_id, *, status="completed"):
    """A runtime_runs row with no injection detail (pre-Phase-6 history)."""
    run_id = uuid4()
    await db_exec(
        engine,
        "INSERT INTO runtime_runs(id,conversation_id,tenant_id,input,config,status,"
        "queue_expires_at,finished_at) VALUES(:id,:c,:t,'历史消息','{}',:s,"
        "now()-interval '1 day',now()-interval '2 days')",
        id=run_id,
        c=conversation_id,
        t=tenant,
        s=status,
    )
    return run_id


class SqlSpy:
    """Statement counter/timer registered on the engine's sync side."""

    def __init__(self, needle):
        self.needle = needle
        self.count = 0
        self.durations = []

    def before(self, conn, cursor, statement, parameters, context, executemany):
        if self.needle in statement:
            context._p6_mark = time.perf_counter()

    def after(self, conn, cursor, statement, parameters, context, executemany):
        if self.needle in statement:
            self.count += 1
            self.durations.append(time.perf_counter() - context._p6_mark)


class _Boom(Exception):
    pass


def _fail_on_injection_insert(conn, cursor, statement, parameters, context, executemany):
    if "INSERT INTO memory_injection_log" in statement:
        raise _Boom("injected detail insert failure")


# ---------------------------------------------------------------------------
# P6-IT-01/02 detail persistence and event compatibility (AC-1)
# ---------------------------------------------------------------------------


async def test_p6_it_01_run_writes_one_row_per_injected_item(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    profile_id = await add_item(engine, tenant, customer_id, "profile", "name", "张三")
    flag_id = await add_item(
        engine, tenant, customer_id, "flag", "vip", "重点客户", source_type="human"
    )
    event_id = await add_item(
        engine, tenant, customer_id, "event", "open_ticket:TK-1", "物流异常", source_type="ticket"
    )
    # Age the profile by exactly one half-life (365d): the injected score is
    # the SQL-computed effective confidence, i.e. confidence halves.
    await age_item(engine, profile_id, 365)
    conversation_id = await _bound_conversation(engine, tenant, customer_id)

    await _run_and_capture(client, services, settings, conversation_id, _CaptureModel())

    run = await only_run(engine, tenant)
    rows = await log_rows(engine, tenant)
    assert len(rows) == 3
    by_item = {row["item_id"]: row for row in rows}
    assert set(by_item) == {profile_id, flag_id, event_id}
    for row in rows:
        assert row["run_id"] == run["id"]
        assert row["customer_id"] == customer_id
        assert row["tenant_id"] == tenant
        # injected_at sits inside the finish transaction.
        assert run["started_at"] <= row["injected_at"] <= run["finished_at"]
    assert by_item[profile_id]["section"] == "profile"
    assert by_item[flag_id]["section"] == "flag"
    assert by_item[event_id]["section"] == "event"
    # Score rules (P6-UT-02's SQL half): profile/preference carry the decayed
    # effective confidence; flags and events are unranked NULL.
    assert by_item[profile_id]["score"] == pytest.approx(0.45, abs=0.01)
    assert by_item[flag_id]["score"] is None
    assert by_item[event_id]["score"] is None


async def test_p6_it_02_event_counts_and_item_ids_match_detail_rows(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    profile_id = await add_item(engine, tenant, customer_id, "profile", "name", "张三")
    flag_id = await add_item(
        engine, tenant, customer_id, "flag", "vip", "重点客户", source_type="human"
    )
    event_id = await add_item(
        engine, tenant, customer_id, "event", "open_ticket:TK-1", "物流异常", source_type="ticket"
    )
    conversation_id = await _bound_conversation(engine, tenant, customer_id)

    await _run_and_capture(client, services, settings, conversation_id, _CaptureModel())

    events = await injected_events(engine, tenant)
    assert len(events) == 1
    data = events[0]["data"]
    rows = await log_rows(engine, tenant)
    # The Phase 4 count fields keep their semantics: items = profile/preference.
    assert data["items"] == 1 and data["flags"] == 1 and data["events"] == 1
    # ...and every count equals the corresponding detail-row count.
    assert data["items"] == sum(1 for r in rows if r["section"] in ("profile", "preference"))
    assert data["flags"] == sum(1 for r in rows if r["section"] == "flag")
    assert data["events"] == sum(1 for r in rows if r["section"] == "event")
    # The new item_ids list is exactly the detail rows' item set.
    assert set(data["item_ids"]) == {str(profile_id), str(flag_id), str(event_id)}
    assert set(data["item_ids"]) == {str(row["item_id"]) for row in rows}


# ---------------------------------------------------------------------------
# P6-IT-03/04 hot path: one batch insert, P95 ≤ 50ms, same-transaction (AC-9)
# ---------------------------------------------------------------------------


async def test_p6_it_03_batch_insert_is_single_statement_within_budget(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    for i in range(20):
        await add_item(engine, tenant, customer_id, "preference", f"custom_{i:02d}", f"偏好{i}")
    for key in ("vip", "complaint_risk", "blacklist"):
        await add_item(engine, tenant, customer_id, "flag", key, "标记", source_type="human")
    for i in range(5):
        await add_item(
            engine, tenant, customer_id, "event", f"open_ticket:TK-{i}", "处理中",
            source_type="ticket",
        )
    conversation_id = await _bound_conversation(engine, tenant, customer_id)

    spy = SqlSpy("INSERT INTO memory_injection_log")
    event.listen(engine.sync_engine, "before_cursor_execute", spy.before)
    event.listen(engine.sync_engine, "after_cursor_execute", spy.after)
    runs = 10
    try:
        for _ in range(runs):
            await _run_and_capture(client, services, settings, conversation_id, _CaptureModel())
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", spy.before)
        event.remove(engine.sync_engine, "after_cursor_execute", spy.after)

    # 28 detail rows per run (20 preferences + 3 flags + 5 events), all written.
    assert len(await log_rows(engine, tenant)) == runs * 28
    # AC-9: the whole run's detail is ONE batch insert — never per-row writes.
    assert spy.count == runs
    # AC-9: the batch insert's added latency on the finish path stays within
    # the 50ms P95 budget (statement wall time, round trip included).
    ordered = sorted(spy.durations)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    assert p95 <= 0.05, f"batch insert P95 {p95 * 1000:.1f}ms exceeds the 50ms budget"


async def test_p6_it_04_detail_insert_failure_rolls_back_whole_finish(platform):
    _client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    item_id = await add_item(engine, tenant, customer_id, "profile", "name", "张三")
    conversation_id = await _bound_conversation(engine, tenant, customer_id)
    run_id, owner = uuid4(), uuid4()
    await db_exec(
        engine,
        "INSERT INTO runtime_runs(id,conversation_id,tenant_id,input,config,status,"
        "owner,lease_until,queue_expires_at,started_at) VALUES(:id,:c,:t,'你好','{}',"
        "'running',:o,now()+interval '30 seconds',now()+interval '1 hour',now())",
        id=run_id,
        c=conversation_id,
        t=tenant,
        o=owner,
    )
    memory = {
        "injected": {"items": 1, "flags": 0, "events": 0, "item_ids": [str(item_id)]},
        "injection_log": [{"item_id": str(item_id), "section": "profile", "score": 0.9}],
    }

    event.listen(engine.sync_engine, "before_cursor_execute", _fail_on_injection_insert)
    try:
        with pytest.raises(_Boom):
            await services.repository.finish(
                run_id, owner, "completed", output="ok", history=[], memory=memory
            )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _fail_on_injection_insert)

    # No half-committed state: the run row, its events and the detail rows all
    # rolled back together (03 section 1: same transaction).
    run = (await db_rows(engine, "SELECT status FROM runtime_runs WHERE id=:id", id=run_id))[0]
    assert run["status"] == "running"
    assert await log_rows(engine, tenant) == []
    events = await db_rows(
        engine, "SELECT type FROM runtime_events WHERE run_id=:id", id=run_id
    )
    assert events == []


# ---------------------------------------------------------------------------
# P6-IT-05 three query entry points agree; deleted items degrade (AC-4/5/7)
# ---------------------------------------------------------------------------


async def test_p6_it_05_three_entry_points_agree_and_deleted_items_degrade(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    memory = services.platform.memory
    customer_id = await make_customer(engine, tenant)
    item1 = await add_item(engine, tenant, customer_id, "profile", "name", "张三")
    item2 = await add_item(engine, tenant, customer_id, "preference", "style", "简洁")
    conversation_id = await _bound_conversation(engine, tenant, customer_id)
    await _run_and_capture(client, services, settings, conversation_id, _CaptureModel())
    run = await only_run(engine, tenant)

    # Entry 1: run-dimension injection list for the customer.
    listing = await memory.list_injections(tenant, customer_id)
    assert len(listing["items"]) == 1
    entry = listing["items"][0]
    assert entry["run_id"] == run["id"]
    assert entry["item_count"] == 2
    assert entry["conversation_id"] == conversation_id

    # Entry 2: item-level detail of the run (conversation view + drill-down).
    detail = await memory.injection_detail(tenant, run["id"])
    assert detail["run_id"] == str(run["id"])
    assert {row["item_id"] for row in detail["items"]} == {item1, item2}
    for row in detail["items"]:
        assert row["item_deleted"] is False
        assert row["key"] and row["type"] and row["status"] == "active"
        assert row["section"] in ("profile", "preference")
        assert row["score"] == pytest.approx(0.9, abs=0.01)

    # Entry 3: reverse lookup from one item.
    reverse = await memory.item_injections(tenant, item1)
    assert len(reverse["items"]) == 1
    assert reverse["items"][0]["run_id"] == run["id"]
    assert reverse["items"][0]["section"] == "profile"
    assert reverse["items"][0]["conversation_id"] == conversation_id

    # A physically deleted item degrades to a marker; content is never
    # reconstructed (C3) and the other rows are untouched.
    await db_exec(engine, "DELETE FROM memory_items WHERE id=:id", id=item1)
    detail = await memory.injection_detail(tenant, run["id"])
    gone = next(row for row in detail["items"] if row["item_id"] == item1)
    assert gone["item_deleted"] is True
    assert gone["key"] is None and gone["type"] is None and gone["status"] is None
    alive = next(row for row in detail["items"] if row["item_id"] == item2)
    assert alive["item_deleted"] is False and alive["key"] == "style"


# ---------------------------------------------------------------------------
# P6-IT-06/07/08 governance cascades: TTL, delete, merge (AC-10/AC-12, 03 §4)
# ---------------------------------------------------------------------------


async def test_p6_it_06_ttl_pass_purges_only_expired_detail_rows(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    await add_item(engine, tenant, customer_id, "profile", "name", "张三")
    conversation_id = await _bound_conversation(engine, tenant, customer_id)
    await _run_and_capture(client, services, settings, conversation_id, _CaptureModel())
    # Two rows past the 90-day retention, one just inside it.
    await insert_log_row(engine, tenant, customer_id, injected_at_sql="now()-interval '91 days'")
    await insert_log_row(engine, tenant, customer_id, injected_at_sql="now()-interval '100 days'")
    await insert_log_row(engine, tenant, customer_id, injected_at_sql="now()-interval '89 days'")
    assert len(await log_rows(engine, tenant)) == 4

    worker = make_worker(services, settings)
    await worker.maintenance()

    remaining = await log_rows(engine, tenant)
    assert len(remaining) == 2  # the real run's row plus the 89-day row
    cutoff = await db_rows(
        engine,
        "SELECT count(*)::int AS n FROM memory_injection_log"
        " WHERE tenant_id=:t AND injected_at<now()-interval '90 days'",
        t=tenant,
    )
    assert cutoff[0]["n"] == 0


async def test_p6_it_07_customer_delete_cascades_detail_rows(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    await add_item(engine, tenant, customer_id, "profile", "name", "张三")
    conversation_id = await _bound_conversation(engine, tenant, customer_id)
    await _run_and_capture(client, services, settings, conversation_id, _CaptureModel())
    await insert_log_row(engine, tenant, customer_id)  # an older manual row
    assert len(await log_rows(engine, tenant)) == 2

    result = await delete_customer(engine, tenant, "operator", customer_id)
    assert result["injection_logs_deleted"] == 2
    # AC-12: no traceable injection history survives the delete.
    assert await log_rows(engine, tenant) == []
    record = (await audits_of(engine, tenant, "memory.customer.deleted"))[0]
    assert record["details"]["injection_logs_deleted"] == 2
    response = await client.get(f"/api/v1/memory/customers/{customer_id}/injections")
    assert response.status_code == 404


async def test_p6_it_08_merge_repoints_detail_rows_to_target(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    memory = services.platform.memory
    source_id = await make_customer(engine, tenant)
    target_id = await make_customer(engine, tenant)
    await add_item(engine, tenant, source_id, "profile", "name", "张三")
    conversation_id = await _bound_conversation(engine, tenant, source_id)
    await _run_and_capture(client, services, settings, conversation_id, _CaptureModel())
    run = await only_run(engine, tenant)
    assert (await log_rows(engine, tenant))[0]["customer_id"] == source_id

    result = await merge_customers(engine, tenant, "operator", source_id, target_id)
    assert result["injection_logs_moved"] == 1
    # The injection history follows the surviving customer (03 section 4).
    rows = await log_rows(engine, tenant)
    assert len(rows) == 1 and rows[0]["customer_id"] == target_id
    listing = await memory.list_injections(tenant, target_id)
    assert len(listing["items"]) == 1 and listing["items"][0]["run_id"] == run["id"]
    record = (await audits_of(engine, tenant, "memory.customer.merged"))[0]
    assert record["details"]["injection_logs_moved"] == 1
    with pytest.raises(DomainError, match="memory_customer_not_found"):
        await memory.list_injections(tenant, source_id)


# ---------------------------------------------------------------------------
# P6-IT-09 lifecycle timeline; P6-UT-03/04 stats aggregation and diff payload
# ---------------------------------------------------------------------------


async def test_p6_it_09_item_timeline_replays_full_lifecycle(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    memory = services.platform.memory
    customer_id = await make_customer(engine, tenant)
    item_id = await add_item(
        engine, tenant, customer_id, "preference", "style", "简洁", status="pending"
    )
    # 确认 → 注入 → 衰退（preference 800d ≪ threshold → expired) → 恢复 → 编辑.
    await memory.confirm_item(tenant, "operator", item_id)
    conversation_id = await _bound_conversation(engine, tenant, customer_id)
    await _run_and_capture(client, services, settings, conversation_id, _CaptureModel())
    await age_item(engine, item_id, 800)
    assert (await daily_migration(engine, settings))["preference_expired"] == 1
    await memory.restore_item(tenant, "operator", item_id)
    await memory.edit_item(tenant, "operator", item_id, "要简洁")

    payload = await memory.item_timeline(tenant, item_id)
    actions = [row["action"] for row in payload["timeline"]]
    for expected in (
        "memory.item.confirmed",
        "memory.decay_expired",
        "memory.item.restored",
        "memory.item.edited",
    ):
        assert expected in actions
    # Reverse chronological (id DESC), latest first.
    ids = [row["id"] for row in payload["timeline"]]
    assert ids == sorted(ids, reverse=True)
    assert actions.index("memory.item.edited") < actions.index("memory.item.confirmed")
    for row in payload["timeline"]:
        assert row["actor"] and row["resource"] == str(item_id)
    # Full current fields, including the single-level history for the diff view.
    item = payload["item"]
    assert item["content"] == "要简洁" and item["prev_content"] == "简洁"
    assert item["protected"] is True and item["source_type"] == "human"
    assert item["revision"] == 2 and "effective_confidence" in item
    # Injection summary rides alongside (the run above injected the item once).
    assert payload["injection_count"] == 1
    assert payload["last_injected_at"] is not None


async def test_p6_ut_03_customer_memory_stats_matches_direct_counts(platform):
    _client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    memory = services.platform.memory
    c1 = await make_customer(engine, tenant)
    await add_item(engine, tenant, c1, "profile", "name", "张三")
    await add_item(engine, tenant, c1, "profile", "company", "某公司")
    await add_item(engine, tenant, c1, "preference", "style", "简洁", status="pending")
    await add_item(engine, tenant, c1, "event", "open_ticket:TK-1", "处理中", source_type="ticket")
    await add_item(engine, tenant, c1, "flag", "vip", "重点", source_type="human")
    await add_item(engine, tenant, c1, "profile", "old", "旧", status="deleted")
    c2 = await make_customer(engine, tenant)
    await add_item(engine, tenant, c2, "preference", "language", "英文")

    spy = SqlSpy("FROM memory_items")
    event.listen(engine.sync_engine, "before_cursor_execute", spy.before)
    event.listen(engine.sync_engine, "after_cursor_execute", spy.after)
    try:
        stats = await memory.customer_memory_stats(tenant, [c1, c2, uuid4()])
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", spy.before)
        event.remove(engine.sync_engine, "after_cursor_execute", spy.after)
    # One batched GROUP BY for the whole set (FR-2: no N+1).
    assert spy.count == 1

    direct = (
        await db_rows(
            engine,
            "SELECT"
            " count(*) FILTER (WHERE status IN ('active','pending'))::int AS total,"
            " count(*) FILTER (WHERE status='pending')::int AS pending,"
            " count(*) FILTER (WHERE status IN ('active','pending') AND type='profile')::int"
            " AS profile,"
            " count(*) FILTER (WHERE status IN ('active','pending') AND type='preference')::int"
            " AS preference,"
            " count(*) FILTER (WHERE status IN ('active','pending') AND type='event')::int"
            " AS event"
            " FROM memory_items WHERE tenant_id=:t AND customer_id=:c AND status<>'deleted'",
            t=tenant,
            c=c1,
        )
    )[0]
    s1 = stats[c1]
    assert s1["total"] == direct["total"] == 5  # flag counts toward the total
    assert s1["pending"] == direct["pending"] == 1
    assert s1["profile"] == direct["profile"] == 2
    assert s1["preference"] == direct["preference"] == 1
    assert s1["event"] == direct["event"] == 1
    assert s1["flags"] == ["vip"]
    latest = await db_rows(
        engine,
        "SELECT max(updated_at) AS m FROM memory_items"
        " WHERE tenant_id=:t AND customer_id=:c AND status<>'deleted'",
        t=tenant,
        c=c1,
    )
    assert s1["last_updated_at"] == latest[0]["m"]
    # Single-item customer and unknown customer.
    assert stats[c2]["total"] == 1 and stats[c2]["flags"] == []


async def test_p6_ut_04_timeline_payload_exposes_prev_content_for_diff(platform):
    _client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    memory = services.platform.memory
    customer_id = await make_customer(engine, tenant)
    item_id = await add_item(engine, tenant, customer_id, "profile", "name", "张三")

    # Never edited: no diff data (prev_content NULL).
    payload = await memory.item_timeline(tenant, item_id)
    assert payload["item"]["prev_content"] is None
    # After an edit the payload carries both sides of the diff (FR-5).
    await memory.edit_item(tenant, "operator", item_id, "张叁")
    payload = await memory.item_timeline(tenant, item_id)
    assert payload["item"]["prev_content"] == "张三"
    assert payload["item"]["content"] == "张叁"


# ---------------------------------------------------------------------------
# P6-IT-10 audit/injection payloads carry no memory content or PII (原则 3)
# ---------------------------------------------------------------------------


async def test_p6_it_10_audit_and_injection_payloads_carry_no_content_or_pii(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    memory = services.platform.memory
    customer_id = await make_customer(engine, tenant)
    item_id = await add_item(
        engine, tenant, customer_id, "profile", "name", "张三 电话13812345678"
    )
    # Masked write paths (edit/flag) still only audit keys and counts (C4).
    await memory.edit_item(tenant, "operator", item_id, "张四 手机13712345678")
    await memory.upsert_flag(tenant, "operator", customer_id, "vip", "重点客户13900001111")
    await memory.delete_item(tenant, "operator", item_id)
    await memory.restore_item(tenant, "operator", item_id)
    conversation_id = await _bound_conversation(engine, tenant, customer_id)
    await _run_and_capture(client, services, settings, conversation_id, _CaptureModel())
    run = await only_run(engine, tenant)

    blobs = []
    for url in (
        f"/api/v1/memory/customers/{customer_id}/audit",
        f"/api/v1/memory/customers/{customer_id}/injections",
        f"/api/v1/memory/runs/{run['id']}/injections",
        f"/api/v1/memory/items/{item_id}/injections",
    ):
        response = await client.get(url)
        assert response.status_code == 200, response.text
        blobs.append(json.dumps(response.json(), ensure_ascii=False))
    # The audit timeline inside the item-timeline payload (its item block
    # legitimately renders content — that is FR-5's purpose).
    timeline = (await client.get(f"/api/v1/memory/items/{item_id}/timeline")).json()
    blobs.append(json.dumps(timeline["timeline"], ensure_ascii=False))

    for blob in blobs:
        for secret in ("张三", "张四", "13812345678", "13712345678", "13900001111", "重点客户"):
            assert secret not in blob, f"{secret} leaked in {blob[:200]}"
        assert not re.search(r"1[3-9]\d{9}", blob)


# ---------------------------------------------------------------------------
# P6-IT-11 switch degradation (03 配置: 关时退化为仅计数事件)
# ---------------------------------------------------------------------------


async def test_p6_it_11_switch_off_writes_counts_only_and_queries_degrade_empty(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    memory = services.platform.memory
    customer_id = await make_customer(engine, tenant)
    await add_item(engine, tenant, customer_id, "profile", "name", "张三")
    conversation_id = await _bound_conversation(engine, tenant, customer_id)

    settings.memory_injection_log_enabled = False
    await _run_and_capture(client, services, settings, conversation_id, _CaptureModel())

    # Zero detail writes; the counts event is exactly the Phase 4 shape.
    assert await log_rows(engine, tenant) == []
    events = await injected_events(engine, tenant)
    assert len(events) == 1
    assert events[0]["data"]["items"] == 1
    assert events[0]["data"]["flags"] == 0 and events[0]["data"]["events"] == 0
    # Query interfaces degrade to empty, never error.
    assert (await memory.list_injections(tenant, customer_id))["items"] == []
    run = await only_run(engine, tenant)
    response = await client.get(f"/api/v1/memory/runs/{run['id']}/injections")
    assert response.status_code == 200 and response.json()["items"] == []

    # Flipping the switch back on restores detail persistence.
    settings.memory_injection_log_enabled = True
    await _run_and_capture(client, services, settings, conversation_id, _CaptureModel())
    assert len(await log_rows(engine, tenant)) == 1


# ---------------------------------------------------------------------------
# P6-API-01..05 endpoint behaviour
# ---------------------------------------------------------------------------


async def test_p6_api_01_injection_endpoints_reader_ok_unauth_401_limit(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    item_id = await add_item(engine, tenant, customer_id, "profile", "name", "张三")
    conversation_id = await _bound_conversation(engine, tenant, customer_id)
    await _run_and_capture(client, services, settings, conversation_id, _CaptureModel())
    await _run_and_capture(client, services, settings, conversation_id, _CaptureModel())
    run_id = (await injected_events(engine, tenant))[0]["run_id"]

    viewer = {"Authorization": "Bearer platform-viewer"}
    bad = {"Authorization": "Bearer bad"}
    urls = (
        f"/api/v1/memory/customers/{customer_id}/injections",
        f"/api/v1/memory/runs/{run_id}/injections",
        f"/api/v1/memory/items/{item_id}/injections",
        f"/api/v1/memory/items/{item_id}/timeline",
        f"/api/v1/memory/customers/{customer_id}/audit",
    )
    for url in urls:
        # Reader (viewer) reads every Phase 6 view.
        assert (await client.get(url, headers=viewer)).status_code == 200, url
        # Unauthenticated requests are rejected.
        assert (await client.get(url, headers=bad)).status_code == 401, url

    # limit takes effect on the run-dimension list (two runs written above).
    full = (await client.get(f"/api/v1/memory/customers/{customer_id}/injections")).json()
    assert len(full["items"]) == 2
    one = (
        await client.get(f"/api/v1/memory/customers/{customer_id}/injections?limit=1")
    ).json()
    assert len(one["items"]) == 1
    # Newest first.
    assert full["items"][0]["injected_at"] >= full["items"][1]["injected_at"]


async def test_p6_api_02_with_stats_columns_and_constant_query_count(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    pending_customer = await make_customer(engine, tenant)
    await add_item(engine, tenant, pending_customer, "preference", "style", "简洁", status="pending")
    await add_item(engine, tenant, pending_customer, "profile", "name", "张三")
    await add_item(
        engine, tenant, pending_customer, "flag", "vip", "重点", source_type="human"
    )
    for _ in range(11):
        other = await make_customer(engine, tenant)
        await add_item(engine, tenant, other, "preference", "language", "英文")

    spy = SqlSpy("FROM memory_items")
    event.listen(engine.sync_engine, "before_cursor_execute", spy.before)
    event.listen(engine.sync_engine, "after_cursor_execute", spy.after)
    try:
        response = await client.get("/api/v1/memory/customers?with_stats=true&page_size=100")
        assert response.status_code == 200, response.text
        big_page_count = spy.count
        spy.count = 0
        response = await client.get("/api/v1/memory/customers?with_stats=true&page_size=1")
        assert response.status_code == 200, response.text
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", spy.before)
        event.remove(engine.sync_engine, "after_cursor_execute", spy.after)
    # No N+1 (FR-2): page query + one batched stats GROUP BY, independent of
    # the page size.
    assert big_page_count == 2
    assert spy.count == 2

    rows = (
        await client.get("/api/v1/memory/customers?with_stats=true&page_size=100")
    ).json()["items"]
    assert all("memory_stats" in row for row in rows)
    stats = next(row for row in rows if row["id"] == str(pending_customer))["memory_stats"]
    assert stats["pending"] == 1 and stats["total"] == 3
    assert stats["profile"] == 1 and stats["preference"] == 1 and stats["event"] == 0
    assert stats["flags"] == ["vip"] and stats["last_updated_at"]

    # Sort by pending count puts the pending customer first (FR-2).
    sorted_rows = (
        await client.get("/api/v1/memory/customers?with_stats=true&sort=pending&page_size=100")
    ).json()["items"]
    assert sorted_rows[0]["id"] == str(pending_customer)
    # Flag filter keeps only flagged customers.
    flagged = (await client.get("/api/v1/memory/customers?flag=vip")).json()
    assert flagged["total"] == 1 and flagged["items"][0]["id"] == str(pending_customer)


async def test_p6_api_03_timeline_endpoints_return_ordered_payloads(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    memory = services.platform.memory
    customer_id = await make_customer(engine, tenant)
    item_id = await add_item(
        engine, tenant, customer_id, "profile", "name", "张三", status="pending"
    )
    await memory.confirm_item(tenant, "operator", item_id)
    await memory.edit_item(tenant, "operator", item_id, "张叁")

    payload = (await client.get(f"/api/v1/memory/items/{item_id}/timeline")).json()
    assert payload["item"]["id"] == str(item_id)
    actions = [row["action"] for row in payload["timeline"]]
    assert actions == ["memory.item.edited", "memory.item.confirmed"]  # newest first
    # limit clamps the timeline.
    limited = (await client.get(f"/api/v1/memory/items/{item_id}/timeline?limit=1")).json()
    assert len(limited["timeline"]) == 1 and limited["timeline"][0]["action"] == (
        "memory.item.edited"
    )

    # The customer audit timeline carries the same item-level events plus the
    # customer-dimension ones, newest first.
    await memory.upsert_flag(tenant, "operator", customer_id, "vip", "重点")
    audit = (await client.get(f"/api/v1/memory/customers/{customer_id}/audit")).json()
    audit_actions = [row["action"] for row in audit["items"]]
    assert audit_actions[0] == "memory.flag.upserted"
    assert "memory.item.edited" in audit_actions and "memory.item.confirmed" in audit_actions
    ids = [row["id"] for row in audit["items"]]
    assert ids == sorted(ids, reverse=True)


async def test_p6_api_04_cross_tenant_injection_views_are_404(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    customer_id = await make_customer(engine, tenant)
    item_id = await add_item(engine, tenant, customer_id, "profile", "name", "张三")
    conversation_id = await _bound_conversation(engine, tenant, customer_id)
    await _run_and_capture(client, services, settings, conversation_id, _CaptureModel())

    # Another tenant's complete object graph, written directly.
    other_tenant = f"other-{uuid4().hex[:8]}"
    other_customer = uuid4()
    await db_exec(
        engine,
        "INSERT INTO memory_customers(id,tenant_id) VALUES(:id,:t)",
        id=other_customer,
        t=other_tenant,
    )
    other_item = uuid4()
    await db_exec(
        engine,
        "INSERT INTO memory_items(id,tenant_id,customer_id,type,key,content,source_type)"
        " VALUES(:id,:t,:c,'profile','name','外人','human')",
        id=other_item,
        t=other_tenant,
        c=other_customer,
    )
    other_conversation = uuid4()
    await db_exec(
        engine,
        "INSERT INTO runtime_conversations(id,tenant_id,external_id,customer_id)"
        " VALUES(:id,:t,:e,:c)",
        id=other_conversation,
        t=other_tenant,
        e=f"conv-{uuid4().hex[:8]}",
        c=other_customer,
    )
    other_run = await insert_run(engine, other_tenant, other_conversation)
    await db_exec(
        engine,
        "INSERT INTO memory_injection_log(tenant_id,run_id,customer_id,item_id,section)"
        " VALUES(:t,:r,:c,:i,'profile')",
        t=other_tenant,
        r=other_run,
        c=other_customer,
        i=other_item,
    )

    # Every Phase 6 view 404s for the other tenant's ids (AC-11 非功能).
    for url in (
        f"/api/v1/memory/customers/{other_customer}/injections",
        f"/api/v1/memory/customers/{other_customer}/audit",
        f"/api/v1/memory/runs/{other_run}/injections",
        f"/api/v1/memory/items/{other_item}/injections",
        f"/api/v1/memory/items/{other_item}/timeline",
    ):
        assert (await client.get(url)).status_code == 404, url

    # And our own views never surface the other tenant's rows.
    listing = (await client.get(f"/api/v1/memory/customers/{customer_id}/injections")).json()
    assert {row["run_id"] for row in listing["items"]} != {str(other_run)}
    assert all(row["run_id"] != str(other_run) for row in listing["items"])
    reverse = (await client.get(f"/api/v1/memory/items/{item_id}/injections")).json()
    assert all(row["run_id"] != str(other_run) for row in reverse["items"])
    service_rows = await db_rows(
        engine, "SELECT count(*)::int AS n FROM memory_injection_log WHERE tenant_id=:t", t=tenant
    )
    assert service_rows[0]["n"] == 1  # only our own run's row


async def test_p6_api_05_historical_run_without_detail_returns_empty(platform):
    client, services, settings = platform
    engine, tenant = services.repository.engine, settings.tenant_id
    memory = services.platform.memory
    customer_id = await make_customer(engine, tenant)
    conversation_id = await _bound_conversation(engine, tenant, customer_id)
    historical_run = await insert_run(engine, tenant, conversation_id)

    # AC-8: a pre-Phase-6 run has no detail rows — 200 with an empty list.
    response = await client.get(f"/api/v1/memory/runs/{historical_run}/injections")
    assert response.status_code == 200, response.text
    assert response.json() == {"run_id": str(historical_run), "items": []}
    # The customer's run-dimension list is likewise an empty 200.
    response = await client.get(f"/api/v1/memory/customers/{customer_id}/injections")
    assert response.status_code == 200 and response.json()["items"] == []
    # An existing-but-never-injected item reverse-lookup: empty 200 as well.
    item_id = await add_item(engine, tenant, customer_id, "profile", "name", "张三")
    response = await client.get(f"/api/v1/memory/items/{item_id}/injections")
    assert response.status_code == 200 and response.json()["items"] == []
    # Unknown ids are 404, not empty lists.
    assert (await client.get(f"/api/v1/memory/runs/{uuid4()}/injections")).status_code == 404
    with pytest.raises(DomainError, match="memory_item_not_found"):
        await memory.item_injections(tenant, uuid4())
