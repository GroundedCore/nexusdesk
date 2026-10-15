"""Phase 4 decay and flag-rendering unit tests (pure pieces).

Covers the effective_confidence formula (P4-UT-10/11), the injection SQL
fragment shape, flag injection rendering (P4-UT-12), the L3 overlap
tokenizer and the merge conflict matrix. Database-backed migration,
eviction, reinforce and governance behaviour lives in the PostgreSQL suites.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from agent_platform.modules.memory.decay import (
    DEFAULT_HALF_LIVES,
    effective_confidence,
    half_lives,
    injection_filter_sql,
)
from agent_platform.modules.memory.governance import _keep_source
from agent_platform.modules.memory.service import (
    overlap_ids,
    render_flag_injection,
    text_tokens,
)

NOW = datetime(2026, 10, 15, tzinfo=UTC)


def _settings(**overrides):
    base = {
        "memory_decay_half_life_days_profile": 365,
        "memory_decay_half_life_days_preference": 90,
        "memory_decay_half_life_days_event": 30,
        "memory_decay_min_confidence": 0.3,
    }
    return SimpleNamespace(**(base | overrides))


def test_effective_confidence_half_at_one_half_life():
    referenced = NOW - timedelta(days=90)
    value = effective_confidence(0.8, "preference", False, referenced, NOW, DEFAULT_HALF_LIVES)
    assert value == pytest.approx(0.4, rel=1e-9)


def test_effective_confidence_protected_and_flag_immune():
    ancient = NOW - timedelta(days=3650)
    assert effective_confidence(0.7, "profile", True, ancient, NOW, DEFAULT_HALF_LIVES) == 0.7
    assert effective_confidence(1.0, "flag", False, ancient, NOW, DEFAULT_HALF_LIVES) == 1.0


def test_effective_confidence_unknown_type_immune():
    ancient = NOW - timedelta(days=3650)
    assert effective_confidence(0.6, "custom_type", False, ancient, NOW, DEFAULT_HALF_LIVES) == 0.6


def test_effective_confidence_decays_but_never_reaches_zero():
    ancient = NOW - timedelta(days=30 * 100)
    value = effective_confidence(1.0, "event", False, ancient, NOW, DEFAULT_HALF_LIVES)
    assert 0.0 < value < 1e-20


def test_effective_confidence_future_and_naive_timestamps():
    future = NOW + timedelta(days=3)
    assert effective_confidence(0.9, "event", False, future, NOW, DEFAULT_HALF_LIVES) == 0.9
    naive = (NOW - timedelta(days=30)).replace(tzinfo=None)
    value = effective_confidence(1.0, "event", False, naive, NOW.replace(tzinfo=None), DEFAULT_HALF_LIVES)
    assert value == pytest.approx(0.5, rel=1e-9)


def test_half_lives_assembled_from_scalar_settings():
    table = half_lives(_settings(memory_decay_half_life_days_profile=100))
    assert table == {"profile": 100, "preference": 90, "event": 30}


def test_injection_filter_sql_keeps_threshold_equality():
    clause, params = injection_filter_sql(_settings())
    # P4-UT-11: rows exactly at the threshold stay injectable (the bound
    # params are CAST explicitly so the prepare phase cannot infer text).
    assert ">= CAST(:min_confidence AS numeric)" in clause
    assert "protected" in clause
    assert params == {"hl_profile": 365, "hl_preference": 90, "min_confidence": 0.3}


def test_render_flag_injection_none_without_flags():
    assert render_flag_injection([]) is None
    assert render_flag_injection(None) is None


def test_render_flag_injection_labels_and_content():
    body = render_flag_injection(
        [
            {"key": "vip", "content": "优先服务，使用尊称"},
            {"key": "complaint_risk", "content": "避免承诺时限"},
        ]
    )
    assert body.startswith("【服务标记】\n")
    assert "- VIP 客户：优先服务，使用尊称" in body
    assert "- 投诉风险：避免承诺时限" in body
    # No fixed instruction without a blacklist flag.
    assert "谨慎服务" not in body


def test_render_flag_injection_blacklist_appends_fixed_instruction():
    body = render_flag_injection([{"key": "blacklist", "content": "历史恶意投诉"}])
    assert "- 黑名单：历史恶意投诉" in body
    assert body.endswith("- 谨慎服务，涉承诺一律转人工确认")


def test_text_tokens_words_and_cjk_bigrams():
    tokens = text_tokens("订单 TK-123 物流异常")
    assert "tk" in tokens and "123" in tokens
    assert "物流" in tokens and "异常" in tokens and "流异" in tokens
    assert text_tokens("") == set() and text_tokens(None) == set()


def test_overlap_ids_matches_on_content_or_key():
    items = [
        {"id": "a", "key": "name", "content": "张三"},
        {"id": "b", "key": "style", "content": "简洁回答"},
        {"id": "c", "key": "phone", "content": "138******78"},
    ]
    assert overlap_ids(items, "我是张三，请简洁一点") == ["a", "b"]
    assert overlap_ids(items, "") == []
    assert overlap_ids(items, "完全无关的英文 xyzzy") == []


def test_keep_source_matrix_protected_first_then_newer():
    older = datetime(2026, 1, 1, tzinfo=UTC)
    newer = datetime(2026, 2, 1, tzinfo=UTC)
    source = {"protected": True, "updated_at": older}
    target = {"protected": False, "updated_at": newer}
    # Protected beats newer.
    assert _keep_source(source, target) is True
    assert _keep_source(target, source) is False
    # Equal protection: the newer row wins.
    assert _keep_source({"protected": False, "updated_at": newer}, {"protected": False, "updated_at": older}) is True
    assert _keep_source({"protected": False, "updated_at": older}, {"protected": False, "updated_at": newer}) is False
    assert _keep_source({"protected": True, "updated_at": newer}, source) is True
