"""Phase 6 injection explainability — local unit tests (no database).

Case IDs refer to
docs/requirement/agent-memory/phase-6-memory-visualization/04-test-plan.md
(P6-UT-*). The database-bound halves (SQL-computed scores, stats aggregation,
timeline/diff payloads, zero-write end to end) live in
test_memory_injection_postgres.py, following the Phase 2 convention of
database-bound "UT" cases in the PG suites.
"""

from uuid import uuid4

import pytest

from agent_platform.modules.agent_runtime.worker import RuntimeWorker
from agent_platform.modules.memory.service import (
    BLACKLIST_INSTRUCTION,
    EVENT_HEADER,
    FLAG_HEADER,
    PREFERENCE_HEADER,
    PROFILE_HEADER,
    render_event_injection,
    render_flag_injection,
    render_memory_injection,
)
from agent_platform.settings import Settings

asyncio_mark = pytest.mark.asyncio


def settings(**overrides):
    return Settings(_env_file=None, **overrides)


# ---------------------------------------------------------------------------
# P6-UT-01 renderers return (text, injected); text is byte-identical to the
# pre-Phase-6 rendering (03 section 1)
# ---------------------------------------------------------------------------


def test_p6_ut_01_memory_injection_text_unchanged_and_entries_listed():
    name_id, lang_id = uuid4(), uuid4()
    text, injected = render_memory_injection(
        [
            {"id": name_id, "type": "profile", "key": "name", "content": "张三",
             "effective_confidence": 0.9},
            {"id": lang_id, "type": "preference", "key": "language", "content": "英文回复",
             "effective_confidence": 0.8},
        ]
    )
    # The exact Phase 2 fixture string (same as test_p5_it_09's expectation).
    assert text == f"{PROFILE_HEADER}\n- name: 张三\n\n{PREFERENCE_HEADER}\n- language: 英文回复"
    # Render order: profile section first, then preference; (id, section, score).
    assert injected == [(name_id, "profile", 0.9), (lang_id, "preference", 0.8)]


def test_p6_ut_01_flag_injection_text_unchanged_and_entries_listed():
    vip_id = uuid4()
    text, injected = render_flag_injection([{"id": vip_id, "key": "vip", "content": "重点客户"}])
    assert text == f"{FLAG_HEADER}\n- VIP 客户：重点客户"
    assert injected == [(vip_id, "flag", None)]
    # The blacklist fixed instruction line is unchanged (R15) and not logged.
    text, injected = render_flag_injection([{"id": vip_id, "key": "blacklist", "content": "恶意"}])
    assert text == f"{FLAG_HEADER}\n- 黑名单：恶意\n- {BLACKLIST_INSTRUCTION}"
    assert injected == [(vip_id, "flag", None)]


def test_p6_ut_01_event_injection_text_unchanged_and_entries_listed():
    ticket_id, commitment_id = uuid4(), uuid4()
    text, injected = render_event_injection(
        [
            {"id": ticket_id, "key": "open_ticket:TK-008", "content": "物流异常补发"},
            {"id": commitment_id, "key": "commitment:17", "content": "48 小时内退款"},
        ]
    )
    assert text == (
        f"{EVENT_HEADER}\n- 工单 TK-008 处理中：物流异常补发\n- 承诺待兑现：48 小时内退款"
    )
    assert injected == [(ticket_id, "event", None), (commitment_id, "event", None)]


# ---------------------------------------------------------------------------
# P6-UT-02 score rules: profile/preference propagate the caller-computed
# effective confidence; flag/event scores are always NULL (03 section 1)
# ---------------------------------------------------------------------------


def test_p6_ut_02_scores_follow_section_rules():
    item_id = uuid4()
    _, injected = render_memory_injection(
        [{"id": item_id, "type": "profile", "key": "name", "content": "张三",
          "effective_confidence": 0.42}]
    )
    assert injected == [(item_id, "profile", 0.42)]
    # A row whose score was never selected (legacy callers) degrades to None.
    _, injected = render_memory_injection(
        [{"id": item_id, "type": "preference", "key": "style", "content": "简洁"}]
    )
    assert injected == [(item_id, "preference", None)]
    _, flag_injected = render_flag_injection([{"id": item_id, "key": "vip", "content": "x"}])
    _, event_injected = render_event_injection(
        [{"id": item_id, "key": "open_ticket:TK-1", "content": "x"}]
    )
    assert flag_injected[0][2] is None and event_injected[0][2] is None


class _FakeMemory:
    """Injection-source double: one scored profile row, no events/flags."""

    def __init__(self, item_id, score):
        self.item = {
            "id": item_id,
            "type": "profile",
            "key": "name",
            "content": "张三",
            "effective_confidence": score,
        }

    async def injection_items(self, tenant, customer_id, max_items, types=None, **kw):
        return [self.item]

    async def pending_event_items(self, tenant, customer_id, limit):
        return []

    async def active_flags(self, tenant, customer_id, limit):
        return []


@asyncio_mark
async def test_p6_ut_02_worker_collects_log_entries_and_switch_gate():
    item_id = uuid4()
    worker = RuntimeWorker(None, None, settings(), "fp", memory=_FakeMemory(item_id, 0.8999999))
    row = {
        "id": str(uuid4()),
        "config": {"agent": {"config": {}}},
        "tenant_id": "t",
        "customer_id": str(uuid4()),
        "input": "你好",
    }
    assert await worker._customer_memory(row) == f"{PROFILE_HEADER}\n- name: 张三"
    # The collected entries are JSON-ready: string ids, section, rounded float score.
    assert row["_memory_injection_log"] == [
        {"item_id": str(item_id), "section": "profile", "score": 0.9}
    ]
    assert row["_memory_injected"]["item_ids"] == [str(item_id)]

    # Switch on (default): the detail rows ride to finish() alongside the event.
    context = worker._memory_context(row, [])
    assert context["injection_log"] == row["_memory_injection_log"]
    assert context["injected"]["items"] == 1

    # Switch off (C5 degradation): the counts event survives, the detail rows
    # are dropped before they ever reach the repository.
    off = RuntimeWorker(None, None, settings(memory_injection_log_enabled=False), "fp")
    context = off._memory_context(row, [])
    assert "injection_log" not in context
    assert context["injected"]["items"] == 1


# ---------------------------------------------------------------------------
# P6-UT-05 empty injection: nothing rendered, nothing collected, nothing to
# write (03 section 1: 无注入场景零写入零开销)
# ---------------------------------------------------------------------------


def test_p6_ut_05_empty_renderers_log_nothing():
    assert render_memory_injection([]) == (None, [])
    assert render_flag_injection([]) == (None, [])
    assert render_flag_injection(None) == (None, [])
    assert render_event_injection([]) == (None, [])
    # Plain event rows (no open ticket / commitment) are neither rendered nor logged.
    assert render_event_injection([{"id": uuid4(), "key": "ticket:TK-1:created", "content": "x"}]) == (
        None,
        [],
    )


@asyncio_mark
async def test_p6_ut_05_no_injection_leaves_no_log_context():
    async def assert_clean(worker, row):
        assert await worker._customer_memory(row) is None
        assert "_memory_injection_log" not in row and "_memory_injected" not in row
        context = worker._memory_context(row, [])
        assert context is None or "injection_log" not in context
        assert context is None or "injected" not in context

    def base_row(**extra):
        return {
            "id": str(uuid4()),
            "config": {"agent": {"config": {}}},
            "tenant_id": "t",
            "input": "你好",
            **extra,
        }

    # No memory service wired at all.
    await assert_clean(RuntimeWorker(None, None, settings(), "fp", memory=None), base_row())
    # Run not bound to a customer.
    await assert_clean(
        RuntimeWorker(None, None, settings(), "fp", memory=_FakeMemory(uuid4(), 0.9)), base_row()
    )
    # Agent read switch off: the lookup never happens.
    await assert_clean(
        RuntimeWorker(None, None, settings(memory_inject_enabled=False), "fp",
                      memory=_FakeMemory(uuid4(), 0.9)),
        base_row(customer_id=str(uuid4())),
    )


@asyncio_mark
async def test_p6_ut_05_customer_without_items_collects_nothing():
    class Empty:
        async def injection_items(self, *args, **kw):
            return []

        async def pending_event_items(self, *args, **kw):
            return []

        async def active_flags(self, *args, **kw):
            return []

    worker = RuntimeWorker(None, None, settings(), "fp", memory=Empty())
    row = {
        "id": str(uuid4()),
        "config": {"agent": {"config": {}}},
        "tenant_id": "t",
        "customer_id": str(uuid4()),
        "input": "你好",
    }
    assert await worker._customer_memory(row) is None
    assert "_memory_injection_log" not in row and "_memory_injected" not in row
