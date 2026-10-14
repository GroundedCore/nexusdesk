"""Phase 3 event memory unit tests: content templates, key rules, rendering.

Database-backed lifecycle and linkage behaviour (P3-UT-07/08, P3-IT-01..)
lives in the PostgreSQL integration suites; these cover the pure pieces
(P3-UT-06, injection rendering).
"""

from agent_platform.modules.memory.events import (
    MAX_SUMMARY_CHARS,
    event_content,
    event_key,
    summarize,
)
from agent_platform.modules.memory.service import render_event_injection


def test_summarize_collapses_whitespace_and_truncates():
    assert summarize("  物流\n异常  补发 ") == "物流 异常 补发"
    long_text = "x" * (MAX_SUMMARY_CHARS + 10)
    result = summarize(long_text)
    assert len(result) == MAX_SUMMARY_CHARS + 1
    assert result.endswith("…")
    assert summarize("") == ""
    assert summarize(None) == ""


def test_event_key_conventions():
    assert event_key("TK-1", "created") == "ticket:TK-1:created"
    assert event_key("TK-1", "resolved") == "ticket:TK-1:resolved"
    assert event_key("TK-1", "closed") == "ticket:TK-1:closed"
    assert event_key("TK-1", "commitment", 42) == "commitment:42"


def test_event_content_templates():
    assert event_content("TK-1", "created", "物流异常") == "工单 TK-1 已创建：物流异常"
    assert event_content("TK-1", "resolved", "已补发") == "工单 TK-1 已解决：已补发"
    assert event_content("TK-1", "closed", "不再跟进") == "工单 TK-1 已关闭：不再跟进"


def test_render_event_injection_formats_and_orders():
    items = [
        {"key": "open_ticket:TK-008", "content": "物流异常补发"},
        {"key": "commitment:17", "content": "48 小时内退款"},
    ]
    rendered = render_event_injection(items)
    assert rendered == (
        "【待跟进事项】\n- 工单 TK-008 处理中：物流异常补发\n- 承诺待兑现：48 小时内退款"
    )


def test_render_event_injection_ignores_plain_event_rows_and_empty():
    assert render_event_injection([{"key": "ticket:TK-1:created", "content": "x"}]) is None
    assert render_event_injection([]) is None
