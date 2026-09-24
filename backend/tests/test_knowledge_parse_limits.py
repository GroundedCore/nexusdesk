import pytest

from agent_platform.modules.knowledge.parsers import parse_file
from agent_platform.platform.persistence.store import DomainError


def test_large_markdown_and_configured_character_boundary():
    text = "知" * 775684
    assert parse_file("large.md", text.encode())[0]["text"] == text
    assert parse_file("exact.md", text.encode(), max_chars=len(text))
    with pytest.raises(DomainError, match="extracted_text_too_large"):
        parse_file("over.md", text.encode(), max_chars=len(text) - 1)


def test_default_limit_still_bounds_text():
    with pytest.raises(DomainError, match="extracted_text_too_large"):
        parse_file("over.txt", b"x" * 2000001)


def test_table_uses_configured_limit():
    raw = ("header\n" + ("x" * 1000 + "\n") * 210).encode()
    assert parse_file("large.csv", raw)
    with pytest.raises(DomainError, match="extracted_table_too_large"):
        parse_file("large.csv", raw, max_chars=200000)
