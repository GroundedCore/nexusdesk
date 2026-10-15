"""Phase 4 PII masking unit tests (P4-UT-01).

Pure-function coverage of ``memory.pii.mask``: the four pattern families,
masking shapes, boundary protection against look-alike identifiers and the
hit counter. Database write-path integration (extraction / event / edit /
flag) lives in the PostgreSQL suites.
"""

from agent_platform.modules.memory.pii import mask


def test_phone_masked_keep_first3_last2():
    masked, hits = mask("联系电话 13812345678 随时拨打")
    assert masked == "联系电话 138******78 随时拨打"
    assert hits == 1


def test_id_card_masked_keep_first6_last4():
    masked, hits = mask("身份证号 110101199003074321")
    assert masked == "身份证号 110101********4321"
    assert hits == 1


def test_id_card_trailing_x():
    masked, hits = mask("证件 11010119900307432X")
    assert masked == "证件 110101********432X"
    assert hits == 1


def test_bank_card_masked_keep_last4():
    masked, hits = mask("卡号 6222021001116245718")
    assert masked == "卡号 ***************5718"
    assert hits == 1
    masked, hits = mask("卡号 6222021001116245")
    assert masked == "卡号 ************6245"
    assert hits == 1


def test_email_masked_keep_first_char():
    masked, hits = mask("邮箱 zhang.san@example.com 备用")
    assert masked == "邮箱 z***@example.com 备用"
    assert hits == 1


def test_id_card_runs_before_bank_card():
    # An 18-digit ID number is also a valid 16-19 digit card candidate; the
    # ID pattern must win so the output is the ID shape, counted once.
    masked, hits = mask("110101199003074321")
    assert masked == "110101********4321"
    assert hits == 1


def test_boundaries_protect_longer_digit_runs():
    # 12-digit and 20-digit runs match none of the patterns.
    text = "单号 123456789012 参考 12345678901234567890"
    assert mask(text) == (text, 0)


def test_phone_like_run_inside_digits_untouched():
    # A phone-looking window inside a longer digit run must not mask.
    text = "批次 91381234567801"
    assert mask(text) == (text, 0)


def test_order_numbers_and_plain_text_untouched():
    text = "订单号 TK-123 已补发，工单 2024 关闭"
    assert mask(text) == (text, 0)


def test_multiple_hits_counted():
    masked, hits = mask("手机 13812345678，邮箱 a@b.com，手机 13900001111")
    assert hits == 3
    assert "138******78" in masked and "a***@b.com" in masked and "139******11" in masked


def test_empty_and_non_string_passthrough():
    assert mask("") == ("", 0)
    assert mask(None) == (None, 0)
