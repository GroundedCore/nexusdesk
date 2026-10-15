"""Phase 3 memory store unit tests: decay math, shared ranking, clamping.

Database-backed probing and dual-mode search equivalence live in the
PostgreSQL integration suites; these cover the pure pieces (P3-UT-04/05)
plus the R16 half-done-enable probe warning.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from agent_platform.modules.memory import store as store_module
from agent_platform.modules.memory.store import (
    PgVectorStore,
    TsVectorStore,
    decay_factor,
    escape_like,
    rank_hits,
)
from agent_platform.settings import Settings

NOW = datetime(2026, 10, 14, tzinfo=UTC)


def row(key, relevance, age_days):
    referenced = NOW - timedelta(days=age_days)
    return {
        "key": key,
        "content": f"content of {key}",
        "updated_at": referenced,
        "last_referenced_at": referenced,
        "relevance": relevance,
    }


def test_decay_factor_is_exactly_half_at_one_half_life():
    referenced = NOW - timedelta(days=30)
    assert abs(decay_factor(referenced, 30, NOW) - 0.5) < 1e-9


def test_decay_factor_uses_last_referenced_at_and_never_reaches_zero():
    assert decay_factor(NOW, 30, NOW) == 1.0
    ancient = NOW - timedelta(days=30 * 100)
    assert decay_factor(ancient, 30, NOW) > 0.0


def test_decay_factor_ignores_future_timestamps_and_naive_datetimes():
    future = NOW + timedelta(days=3)
    assert decay_factor(future, 30, NOW) == 1.0
    naive = (NOW - timedelta(days=30)).replace(tzinfo=None)
    assert abs(decay_factor(naive, 30, NOW.replace(tzinfo=None)) - 0.5) < 1e-9


def test_rank_hits_orders_by_relevance_times_decay():
    # Equal relevance: the 3-day-old item must outrank the 150-day-old one
    # (AC-4b); stronger relevance can still beat freshness.
    hits = rank_hits(
        [row("old", 1.0, 150), row("recent", 1.0, 3), row("relevant", 1.5, 150)],
        30,
        10,
    )
    assert [hit["key"] for hit in hits] == ["recent", "relevant", "old"]


def test_rank_hits_decays_but_never_hides():
    hits = rank_hits([row("ancient", 0.9, 30 * 100)], 30, 10)
    assert len(hits) == 1
    assert hits[0]["score"] > 0.0


def test_rank_hits_respects_limit():
    hits = rank_hits([row(f"k{i}", 1.0, i) for i in range(10)], 30, 3)
    assert len(hits) == 3
    assert all(
        hits[i]["score"] >= hits[i + 1]["score"] for i in range(len(hits) - 1)
    )


def test_escape_like_makes_wildcards_literal():
    assert escape_like("50%_\\off") == "50\\%\\_\\\\off"


def test_store_modes_and_limit_clamping():
    settings = Settings()
    assert TsVectorStore(None, settings).available() == "text"
    assert PgVectorStore(None, None, settings).available() == "vector"
    store = TsVectorStore(None, settings)
    assert store._limit(5) == 5
    assert store._limit(999) == settings.memory_search_limit_max
    assert store._limit(0) == 1


def _stub_probe(monkeypatch, *, column: bool, extension: bool):
    async def has_column(engine):
        return column

    async def has_extension(engine):
        return extension

    monkeypatch.setattr(store_module, "_has_vector_column", has_column)
    monkeypatch.setattr(store_module, "_has_vector_extension", has_extension)


def test_probe_warns_when_extension_present_but_column_missing(monkeypatch, caplog):
    # R16: manual enable steps half done (extension installed, column DDL not
    # run) must degrade to text with a warning, never pick vector mode.
    _stub_probe(monkeypatch, column=False, extension=True)
    with caplog.at_level(logging.WARNING):
        picked = asyncio.run(store_module.build_memory_store(None, None, Settings()))
    assert picked.available() == "text"
    assert any("manual enable steps incomplete" in r.message for r in caplog.records)


def test_probe_stays_quiet_when_pgvector_entirely_absent(monkeypatch, caplog):
    # The normal no-pgvector deployment degrades silently (auto mode).
    _stub_probe(monkeypatch, column=False, extension=False)
    with caplog.at_level(logging.WARNING):
        picked = asyncio.run(store_module.build_memory_store(None, None, Settings()))
    assert picked.available() == "text"
    assert not caplog.records


def test_probe_failure_degrades_instead_of_crashing(monkeypatch, caplog):
    # D4: a database outage at startup must not crash the lifespan — degrade
    # to keyword mode with a warning; readiness owns the 503 contract.
    async def boom(engine):
        raise ConnectionRefusedError("Connection refused")

    monkeypatch.setattr(store_module, "_has_vector_column", boom)
    with caplog.at_level(logging.WARNING):
        picked = asyncio.run(store_module.build_memory_store(None, None, Settings()))
    assert picked.available() == "text"
    assert any("Memory store probe failed" in r.message for r in caplog.records)
