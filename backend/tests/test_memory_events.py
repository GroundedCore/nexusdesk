"""Phase 3 event memory + semantic search — local unit tests (no database).

Case IDs refer to
docs/requirement/agent-memory/phase-3-event-memory-search/04-test-plan.md.
Database-bound cases (store probing, lifecycle, TTL, injection, dual-mode
retrieval) live in test_memory_events_postgres.py; migration checks in
test_memory_event_migrations.py.
"""

from datetime import UTC, datetime, timedelta

import pytest

from agent_platform.modules.memory.store import decay_factor, rank_hits
from agent_platform.settings import Settings

asyncio_mark = pytest.mark.asyncio

NOW = datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# P3-UT-04 decay_factor: one shared function, half-life semantics,
# based on last_referenced_at (the function has no created_at input at all).
# ---------------------------------------------------------------------------


def test_p3_ut_04_decay_factor_half_life_math():
    half_life = 30
    # Freshly referenced: factor 1.0.
    assert decay_factor(NOW, half_life, NOW) == pytest.approx(1.0)
    # Exactly one half-life old: factor exactly 0.5 (exp(-ln2 * 1)).
    one = NOW - timedelta(days=half_life)
    assert decay_factor(one, half_life, NOW) == pytest.approx(0.5)
    # Two half-lives: 0.25 — exponential, not linear.
    two = NOW - timedelta(days=2 * half_life)
    assert decay_factor(two, half_life, NOW) == pytest.approx(0.25)
    # A different half-life config changes the factor per the formula.
    assert decay_factor(one, 60, NOW) == pytest.approx(2**-0.5)


def test_p3_ut_04_decay_factor_naive_datetime_read_as_utc():
    naive = (NOW - timedelta(days=30)).replace(tzinfo=None)
    naive_now = NOW.replace(tzinfo=None)
    assert decay_factor(naive, 30, naive_now) == pytest.approx(0.5)


def test_p3_ut_04_decay_factor_future_reference_clamped_to_zero_age():
    future = NOW + timedelta(days=5)
    assert decay_factor(future, 30, NOW) == pytest.approx(1.0)


def test_p3_ut_04_decay_never_reaches_zero():
    ancient = NOW - timedelta(days=30 * 100)  # 100 half-lives
    factor = decay_factor(ancient, 30, NOW)
    assert 0.0 < factor < 1e-20


# ---------------------------------------------------------------------------
# P3-UT-05 decay only reorders — it never filters rows out.
# ---------------------------------------------------------------------------


def _row(content, key, days_old, relevance):
    # Rows are built relative to the real clock: rank_hits uses datetime.now()
    # internally, so a fixed reference point would drift the score math.
    reference = datetime.now(UTC) - timedelta(days=days_old)
    return {
        "content": content,
        "key": key,
        "updated_at": reference,
        "last_referenced_at": reference,
        "relevance": relevance,
    }


def test_p3_ut_05_rank_hits_keeps_ancient_rows_and_reorders():
    rows = [
        _row("很老的工单事件", "ticket:TK-001:resolved", days_old=300, relevance=0.9),
        _row("最近的工单事件", "ticket:TK-002:resolved", days_old=3, relevance=0.9),
    ]
    hits = rank_hits(rows, half_life_days=30, limit=10)
    # Both rows are returned: decay never hides (visibility assertion).
    assert len(hits) == 2
    # Equal relevance: the recent one ranks first, the ancient one last.
    assert hits[0]["key"] == "ticket:TK-002:resolved"
    assert hits[1]["key"] == "ticket:TK-001:resolved"
    # Scores are relevance * decay, positive even for the ancient row.
    assert hits[0]["score"] > hits[1]["score"] > 0.0
    assert hits[1]["score"] == pytest.approx(0.9 * 2 ** (-300 / 30), rel=1e-3)


def test_p3_ut_05_rank_hits_applies_limit_after_ranking():
    rows = [_row(f"事件{i}", f"k{i}", days_old=i, relevance=1.0) for i in range(8)]
    hits = rank_hits(rows, half_life_days=30, limit=5)
    assert len(hits) == 5
    # Newest first under equal relevance.
    assert [hit["key"] for hit in hits] == ["k0", "k1", "k2", "k3", "k4"]


def test_p3_ut_05_rank_hits_relevance_can_outweigh_decay():
    rows = [
        _row("高相关老事件", "old", days_old=30, relevance=1.0),  # decay 0.5
        _row("低相关新事件", "new", days_old=0, relevance=0.4),  # decay 1.0
    ]
    hits = rank_hits(rows, half_life_days=30, limit=10)
    assert [hit["key"] for hit in hits] == ["old", "new"]


# ---------------------------------------------------------------------------
# P3-CM-01 Phase-3 settings: defaults and AGENT_ env overrides.
# ---------------------------------------------------------------------------


def test_p3_cm_01_event_settings_defaults_and_env_override(monkeypatch):
    settings = Settings(_env_file=None)
    assert settings.memory_event_enabled is True
    assert settings.memory_event_ttl_days == 180
    assert settings.memory_search_limit_max == 10
    assert settings.memory_vector_enabled == "auto"
    assert settings.memory_event_inject_max == 5
    assert settings.memory_search_half_life_days == 30
    assert settings.memory_embedding_profile_id is None
    assert settings.memory_embedding_profile_version is None

    monkeypatch.setenv("AGENT_MEMORY_EVENT_ENABLED", "false")
    monkeypatch.setenv("AGENT_MEMORY_EVENT_TTL_DAYS", "90")
    monkeypatch.setenv("AGENT_MEMORY_SEARCH_LIMIT_MAX", "20")
    monkeypatch.setenv("AGENT_MEMORY_VECTOR_ENABLED", "false")
    monkeypatch.setenv("AGENT_MEMORY_EVENT_INJECT_MAX", "3")
    monkeypatch.setenv("AGENT_MEMORY_SEARCH_HALF_LIFE_DAYS", "14")
    monkeypatch.setenv("AGENT_MEMORY_EMBEDDING_PROFILE_ID", "embed-profile")
    monkeypatch.setenv("AGENT_MEMORY_EMBEDDING_PROFILE_VERSION", "2")
    overridden = Settings(_env_file=None)
    assert overridden.memory_event_enabled is False
    assert overridden.memory_event_ttl_days == 90
    assert overridden.memory_search_limit_max == 20
    assert overridden.memory_vector_enabled == "false"
    assert overridden.memory_event_inject_max == 3
    assert overridden.memory_search_half_life_days == 14
    assert overridden.memory_embedding_profile_id == "embed-profile"
    assert overridden.memory_embedding_profile_version == 2
