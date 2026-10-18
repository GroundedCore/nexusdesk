"""Phase 5 agent memory config — local unit tests (no database).

Case IDs refer to docs/requirement/agent-memory/phase-5-agent-memory-config/04-test-plan.md
(P5-UT-01..11). Database-bound behaviour (extraction task payloads, real
injection queries, review end-to-end) lives in test_memory_config_postgres.py.
"""

import pytest
from pydantic import ValidationError

from agent_platform.modules.agent_config.memory import (
    MEMORY_TYPES,
    resolve_agent_memory,
    resolve_memory_config,
)
from agent_platform.modules.agent_config.service import AgentConfig
from agent_platform.modules.agent_runtime.worker import RuntimeWorker
from agent_platform.modules.memory.service import route_confidence
from agent_platform.settings import Settings

asyncio_mark = pytest.mark.asyncio


def settings(**overrides):
    return Settings(_env_file=None, **overrides)


def cfg(**overrides):
    return AgentConfig(system_prompt="x", **overrides)


# ---------------------------------------------------------------------------
# resolve_memory_config: gate combinations and numeric caps (P5-UT-01..07)
# ---------------------------------------------------------------------------


def test_ut_01_defaults_resolve_everything_on():
    effective = resolve_memory_config(cfg(), settings())
    assert effective.summary is True
    assert effective.read is True
    assert effective.write is True
    assert effective.types == frozenset(MEMORY_TYPES)
    assert effective.search is True
    assert effective.write_mode == "auto"
    assert effective.inject_max_items == 20
    assert effective.search_limit == 5
    assert effective.blacklist_auto_handoff is False


@pytest.mark.parametrize(
    "override,field",
    [
        ({"summary_enabled": False}, "summary"),
        ({"memory_inject_enabled": False}, "read"),
        # memory_search is read-side, so the read kill switch gates it too
        # (D1): platform off means fully off at runtime (AC-6).
        ({"memory_inject_enabled": False}, "search"),
        ({"memory_extract_enabled": False}, "write"),
    ],
)
def test_ut_02_platform_off_wins_over_agent_on(override, field):
    # Agent config is all-default (on); a platform kill switch must stay off.
    effective = resolve_memory_config(cfg(), settings(**override))
    assert getattr(effective, field) is False


@pytest.mark.parametrize(
    "agent_override,field",
    [
        ({"summary_enabled": False}, "summary"),
        ({"memory_read_enabled": False}, "read"),
        ({"memory_write": False}, "write"),
        ({"memory_search_enabled": False}, "search"),
    ],
)
def test_ut_03_agent_tightens_with_platform_on(agent_override, field):
    effective = resolve_memory_config(cfg(**agent_override), settings())
    assert getattr(effective, field) is False


@pytest.mark.parametrize(
    "agent_max,expected",
    [(999, 20), (100, 20), (50, 20), (5, 5), (None, 20)],
)
def test_ut_04a_inject_items_capped_at_platform_max(agent_max, expected):
    # AC-7: an above-ceiling value (999) is saved and truncated at resolve time.
    effective = resolve_memory_config(cfg(memory_inject_max_items=agent_max), settings())
    assert effective.inject_max_items == expected


@pytest.mark.parametrize(
    "agent_limit,expected",
    [(20, 5), (2, 2), (None, 5)],
)
def test_ut_04b_search_limit_capped_at_platform_max(agent_limit, expected):
    # D2: settings.memory_search_limit (default 5) is both default and ceiling.
    effective = resolve_memory_config(cfg(memory_search_limit=agent_limit), settings())
    assert effective.search_limit == expected


@pytest.mark.parametrize(
    "platform,agent,expected",
    [
        (False, True, True),
        (True, False, True),
        (False, False, False),
        (True, True, True),
    ],
)
def test_ut_05_blacklist_handoff_is_or_semantics(platform, agent, expected):
    effective = resolve_memory_config(
        cfg(memory_blacklist_auto_handoff=agent),
        settings(memory_blacklist_auto_handoff=platform),
    )
    assert effective.blacklist_auto_handoff is expected


def test_ut_06_master_switch_kills_read_write_search():
    effective = resolve_memory_config(
        cfg(memory_enabled=False, memory_read_enabled=True, memory_write=True, memory_search_enabled=True),
        settings(),
    )
    assert effective.read is False
    assert effective.write is False
    assert effective.search is False


def test_ut_07_search_depends_on_read():
    effective = resolve_memory_config(
        cfg(memory_read_enabled=False, memory_search_enabled=True), settings()
    )
    assert effective.search is False


# ---------------------------------------------------------------------------
# pydantic validation and legacy snapshots (P5-UT-08/09)
# ---------------------------------------------------------------------------


def test_ut_08a_empty_types_rejected():
    with pytest.raises(ValidationError):
        cfg(memory_types=[])


def test_ut_08b_invalid_enum_values_rejected():
    with pytest.raises(ValidationError):
        cfg(memory_types=["flag"])  # flag is platform-governed, never an option
    with pytest.raises(ValidationError):
        cfg(memory_write_mode="manual")


def test_ut_08c_out_of_range_numeric_limits_rejected():
    # Only the lower bound is rejected; above-ceiling values are accepted and
    # truncated to the platform ceiling at resolve time (AC-7).
    for bad in (0, -1):
        with pytest.raises(ValidationError):
            cfg(memory_inject_max_items=bad)
        with pytest.raises(ValidationError):
            cfg(memory_search_limit=bad)
    assert cfg(memory_inject_max_items=999).memory_inject_max_items == 999
    assert cfg(memory_search_limit=999).memory_search_limit == 999


def test_ut_09_legacy_three_key_snapshot_deserializes_with_defaults():
    legacy = AgentConfig.model_validate(
        {"system_prompt": "x", "summary_enabled": True, "memory_enabled": True, "memory_write": True}
    )
    assert legacy.memory_read_enabled is True
    assert legacy.memory_types == list(MEMORY_TYPES)
    assert legacy.memory_search_enabled is True
    assert legacy.memory_write_mode == "auto"
    assert legacy.memory_inject_max_items is None
    assert legacy.memory_search_limit is None
    assert legacy.memory_blacklist_auto_handoff is False
    # The resolved effective config is bit-identical to a fresh default agent.
    assert resolve_memory_config(legacy, settings()) == resolve_memory_config(cfg(), settings())


# ---------------------------------------------------------------------------
# review routing and type semantics (P5-UT-10/11)
# ---------------------------------------------------------------------------


def test_ut_10_review_routes_everything_pending():
    auto, pending = 0.8, 0.5
    assert route_confidence(0.95, auto, pending, "review") == "pending"
    assert route_confidence(0.1, auto, pending, "review") == "pending"
    # Default auto mode keeps the Phase 2 thresholds.
    assert route_confidence(0.95, auto, pending) == "active"
    assert route_confidence(0.49, auto, pending) is None


def test_ut_11_types_are_normalised_and_flag_is_never_an_option():
    effective = resolve_memory_config(cfg(memory_types=["profile"]), settings())
    assert effective.types == frozenset({"profile"})
    assert "flag" not in effective.types
    assert "flag" not in MEMORY_TYPES


def test_ut_11b_partial_snapshot_dict_coerces_like_a_full_config():
    # A legacy/partial config dict (no system_prompt) resolves to default-on.
    assert resolve_agent_memory({}, settings()) == resolve_agent_memory(None, settings())
    tightened = resolve_agent_memory({"memory_read_enabled": False}, settings())
    assert tightened.read is False and tightened.search is False


# ---------------------------------------------------------------------------
# injection gate and extraction dispatch gate (C-4), no database
# ---------------------------------------------------------------------------


class _FakeMemory:
    def __init__(self):
        self.calls = {}

    async def injection_items(self, tenant, customer_id, max_items, types=None, **kw):
        self.calls["injection"] = (max_items, tuple(types) if types else None)
        return [{"id": id(self), "type": "profile", "key": "name", "content": "张三"}]

    async def pending_event_items(self, tenant, customer_id, limit):
        self.calls["events_limit"] = limit
        return []

    async def active_flags(self, tenant, customer_id, limit):
        self.calls["flags_limit"] = limit
        return []


def make_worker(overrides=None, fake=None):
    return RuntimeWorker(None, None, settings(**(overrides or {})), "fp", memory=fake or _FakeMemory())


@asyncio_mark
async def test_customer_memory_read_off_injects_nothing():
    worker = make_worker({"memory_inject_enabled": False})
    row = {"config": {"agent": {"config": {}}}, "tenant_id": "t", "customer_id": "c", "id": "r"}
    assert await worker._customer_memory(row) is None


@asyncio_mark
async def test_customer_memory_filters_types_and_caps_items():
    fake = _FakeMemory()
    worker = make_worker(fake=fake)
    row = {
        "config": {"agent": {"config": {"memory_types": ["profile"], "memory_inject_max_items": 3}}},
        "tenant_id": "t",
        "customer_id": "c",
        "id": "r",
        "input": "你好",
    }
    body = await worker._customer_memory(row)
    # Profile/preference inject the allowed subset only, capped to the agent value.
    assert fake.calls["injection"] == (3, ("profile",))
    # event is excluded from the types, so the pending-events query is skipped.
    assert "events_limit" not in fake.calls
    assert "【客户画像】" in body


@asyncio_mark
async def test_customer_memory_event_gate_follows_types():
    fake = _FakeMemory()
    worker = make_worker(fake=fake)
    row = {
        "config": {"agent": {"config": {"memory_types": ["profile", "preference", "event"]}}},
        "tenant_id": "t",
        "customer_id": "c",
        "id": "r",
        "input": "你好",
    }
    await worker._customer_memory(row)
    # Default agent: min(platform inject cap 20, platform event ceiling 5) = 5,
    # which keeps the Phase 3 behaviour bit-identical.
    assert fake.calls["events_limit"] == 5


@asyncio_mark
async def test_customer_memory_event_cap_tightens_with_agent_limit():
    # FR-1: the agent-level inject cap covers event items too.
    fake = _FakeMemory()
    worker = make_worker(fake=fake)
    row = {
        "config": {
            "agent": {"config": {"memory_types": ["profile", "event"], "memory_inject_max_items": 3}}
        },
        "tenant_id": "t",
        "customer_id": "c",
        "id": "r",
        "input": "你好",
    }
    await worker._customer_memory(row)
    assert fake.calls["events_limit"] == 3


def test_memory_context_snapshots_write_mode_and_types():
    worker = make_worker()
    row = {
        "config": {"agent": {"config": {"memory_write_mode": "review", "memory_types": ["profile"]}}}
    }
    context = worker._memory_context(row, [])
    assert context["extract"] is True
    assert context["write_mode"] == "review"
    assert context["types"] == ["profile"]


def test_memory_context_master_off_dispatches_no_extract():
    worker = make_worker()
    row = {"config": {"agent": {"config": {"memory_enabled": False}}}}
    context = worker._memory_context(row, [{"role": "user", "content": "x"}])
    assert "extract" not in context