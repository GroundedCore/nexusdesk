"""Phase 4 governance/decay/flag memory — local unit tests (no database).

Case IDs refer to
docs/requirement/agent-memory/phase-4-governance/04-test-plan.md.
Database-bound cases (protection lifecycle, delete/merge, periodic jobs,
API) live in test_memory_governance_postgres.py; the migration check in
test_memory_governance_migrations.py.

Interface contracts follow 08-implementation-breakdown.md section 3.1:
- ``modules/memory/pii.py::mask(text) -> (masked, hits)`` (T3)
- ``modules/memory/decay.py::effective_confidence(...)`` (T6, R7/R8/R9)
- 11 new scalar settings (T2, R9)
"""

from datetime import UTC, datetime, timedelta

import pytest

from agent_platform.modules.memory.extraction import validate_item
from agent_platform.settings import Settings

NOW = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)


def mask(text):
    """Lazy import: modules/memory/pii.py lands with dev task T3."""
    from agent_platform.modules.memory.pii import mask as _mask

    return _mask(text)


def _days(n):
    return NOW - timedelta(days=n)


# ---------------------------------------------------------------------------
# P4-UT-01~05 PII mask (FR-3, AC-4; contract: 08 section 3.1)
# ---------------------------------------------------------------------------


def test_p4_ut_01_phone_masked_front3_back2():
    masked, hits = mask("联系电话 13812345678 请回拨")
    assert hits == 1
    assert "13812345678" not in masked
    assert "138******78" in masked


def test_p4_ut_02_id_card_masked_front6_back4_including_x_suffix():
    masked, hits = mask("身份证 11010119900307771X 已核验")
    assert hits == 1
    assert "11010119900307771X" not in masked
    assert "110101" in masked and "771X" in masked
    # middle 8 digits must be gone
    assert "19900307" not in masked
    # lowercase x variant is also an ID card
    masked2, hits2 = mask("11010119900307771x")
    assert hits2 == 1
    assert "11010119900307771x" not in masked2


def test_p4_ut_03_bank_card_masked_back4_only():
    for card in ("6222020200113434", "6222020200113434567"):  # 16 and 19 digits
        masked, hits = mask(f"卡号 {card}")
        assert hits == 1
        assert card not in masked
        assert card[-4:] in masked


def test_p4_ut_04_email_masked_local_first_char():
    masked, hits = mask("邮箱 zhangsan@example.com 备用")
    assert hits == 1
    assert "zhangsan@example.com" not in masked
    assert "z***@example.com" in masked


def test_p4_ut_05_false_positive_control_and_hit_count():
    # ticket no / order no / plain numbers must not be masked
    text = "工单 TK-123456 订单 A123 共 10000 件"
    assert mask(text) == (text, 0)
    # phone-like digit run embedded in a longer number: digit boundary guards
    text = "编号11381234567890已登记"
    assert mask(text) == (text, 0)
    # multiple hits are counted
    masked, hits = mask("拨打 13812345678 或 13911112222")
    assert hits == 2
    assert "13812345678" not in masked and "13911112222" not in masked
    # ID card wins over bank card on the same 18-digit run (order per contract)
    masked, hits = mask("110101199003077719")
    assert hits == 1
    assert masked.startswith("110101") and masked.endswith("7719")
    # digit-run boundaries are the only guard: a phone glued to CJK is still
    # masked, a >19 digit run is left alone
    assert mask("手机13812345678")[1] == 1
    assert mask("622202020011343456789") == ("622202020011343456789", 0)


def test_p4_ut_06_pii_switch_default_on_and_env_override(monkeypatch):
    settings = Settings(_env_file=None)
    assert settings.memory_pii_masking_enabled is True
    monkeypatch.setenv("AGENT_MEMORY_PII_MASKING_ENABLED", "false")
    overridden = Settings(_env_file=None)
    assert overridden.memory_pii_masking_enabled is False
    # the pure function itself is switch-agnostic: masking is controlled at
    # the call sites (08 R5); DB behaviour is covered in the postgres suite


# ---------------------------------------------------------------------------
# P4-UT-09 layer 1: extractor output can never carry flag items (FR-2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["vip", "complaint_risk", "blacklist"])
def test_p4_ut_09_extractor_flag_output_filtered(key):
    item = {
        "op": "add",
        "type": "flag",
        "key": key,
        "content": "高危客户",
        "confidence": 0.9,
    }
    assert validate_item(item) is None


# ---------------------------------------------------------------------------
# P4-UT-10/11 decay formula + threshold routing (FR-6.1; R7/R8/R9)
# ---------------------------------------------------------------------------

HALF_LIVES = {"profile": 365, "preference": 90, "event": 30}


def _ec(confidence, item_type, age_days, protected=False):
    from agent_platform.modules.memory.decay import effective_confidence

    return effective_confidence(
        confidence=confidence,
        type=item_type,
        protected=protected,
        last_referenced_at=_days(age_days),
        now=NOW,
        half_lives=HALF_LIVES,
    )


def test_p4_ut_10_age_zero_keeps_confidence():
    assert _ec(0.9, "profile", 0) == pytest.approx(0.9)
    assert _ec(0.9, "preference", 0) == pytest.approx(0.9)
    assert _ec(0.9, "event", 0) == pytest.approx(0.9)


def test_p4_ut_10_half_life_halves_confidence():
    assert _ec(0.8, "profile", 365) == pytest.approx(0.4)
    assert _ec(0.8, "preference", 90) == pytest.approx(0.4)
    assert _ec(0.8, "event", 30) == pytest.approx(0.4)
    # two half-lives quarter it
    assert _ec(0.8, "preference", 180) == pytest.approx(0.2)


def test_p4_ut_10_flag_and_protected_immune():
    assert _ec(0.8, "flag", 500) == pytest.approx(0.8)
    assert _ec(0.8, "profile", 500, protected=True) == pytest.approx(0.8)
    assert _ec(0.8, "preference", 500, protected=True) == pytest.approx(0.8)


def test_p4_ut_11_threshold_boundary():
    # exactly at the threshold the item is retained (>= semantics, R7):
    # confidence 0.6 at one half-life decays to exactly 0.3 and must NOT
    # migrate; below it must. Routing (profile->pending, preference->expired,
    # event excluded per R8) and the inclusive filter are verified
    # behaviorally in the postgres suite (P4-IT-09/P4-IT-10).
    assert _ec(0.6, "preference", 90) == pytest.approx(0.3)
    assert _ec(0.6, "preference", 91) < 0.3
    assert _ec(0.6, "profile", 365) == pytest.approx(0.3)
    assert _ec(0.6, "profile", 366) < 0.3


# ---------------------------------------------------------------------------
# P4-CM-02 settings: 11 new scalars (R9)
# ---------------------------------------------------------------------------


def test_p4_cm_02_governance_settings_defaults_and_env_override(monkeypatch):
    settings = Settings(_env_file=None)
    assert settings.memory_pii_masking_enabled is True
    assert settings.memory_rule_scan_interval_minutes == 15
    assert settings.memory_flag_inject_max == 3
    assert settings.memory_blacklist_auto_handoff is False
    assert settings.memory_decay_enabled is True
    assert settings.memory_decay_half_life_days_profile == 365
    assert settings.memory_decay_half_life_days_preference == 90
    assert settings.memory_decay_half_life_days_event == 30
    assert settings.memory_decay_min_confidence == pytest.approx(0.3)
    assert settings.memory_max_items_per_customer == 200
    assert settings.memory_reinforce_sample_rate == pytest.approx(1.0)

    monkeypatch.setenv("AGENT_MEMORY_PII_MASKING_ENABLED", "false")
    monkeypatch.setenv("AGENT_MEMORY_RULE_SCAN_INTERVAL_MINUTES", "0")
    monkeypatch.setenv("AGENT_MEMORY_FLAG_INJECT_MAX", "5")
    monkeypatch.setenv("AGENT_MEMORY_BLACKLIST_AUTO_HANDOFF", "true")
    monkeypatch.setenv("AGENT_MEMORY_DECAY_ENABLED", "false")
    monkeypatch.setenv("AGENT_MEMORY_DECAY_HALF_LIFE_DAYS_PROFILE", "180")
    monkeypatch.setenv("AGENT_MEMORY_DECAY_HALF_LIFE_DAYS_PREFERENCE", "45")
    monkeypatch.setenv("AGENT_MEMORY_DECAY_HALF_LIFE_DAYS_EVENT", "15")
    monkeypatch.setenv("AGENT_MEMORY_DECAY_MIN_CONFIDENCE", "0.5")
    monkeypatch.setenv("AGENT_MEMORY_MAX_ITEMS_PER_CUSTOMER", "100")
    monkeypatch.setenv("AGENT_MEMORY_REINFORCE_SAMPLE_RATE", "0")
    overridden = Settings(_env_file=None)
    assert overridden.memory_pii_masking_enabled is False
    assert overridden.memory_rule_scan_interval_minutes == 0  # 0 disables rule scan
    assert overridden.memory_flag_inject_max == 5
    assert overridden.memory_blacklist_auto_handoff is True
    assert overridden.memory_decay_enabled is False
    assert overridden.memory_decay_half_life_days_profile == 180
    assert overridden.memory_decay_half_life_days_preference == 45
    assert overridden.memory_decay_half_life_days_event == 15
    assert overridden.memory_decay_min_confidence == pytest.approx(0.5)
    assert overridden.memory_max_items_per_customer == 100
    assert overridden.memory_reinforce_sample_rate == pytest.approx(0.0)
