"""PII masking for memory content (FR-3).

One pure function, :func:`mask`, called at every memory write point before
content lands in the database: extraction (``apply_extracted``), event items
(``add_event_item``), human edits (``edit_item``) and flag notes
(``governance.upsert_flag``) — the R5/R6 ruling; the original value never
persists. The hit count rides inside the enclosing operation's audit record
(``details.pii_masked``) instead of a dedicated audit action, so an audit row
never carries the original value either (C4).

Patterns run in a fixed order with digit boundaries on both sides so
identifiers that merely contain a phone-like run (order numbers, tracking
ids) are left alone. The ID-card pattern must run before the bank-card one:
an 18-digit ID number would otherwise match the 16-19 digit card pattern.
"""

import re

# 11-digit mainland mobile: 1[3-9]xxxxxxxxx -> keep first 3 / last 2.
_PHONE = re.compile(r"(?<!\d)(1[3-9]\d)\d{6}(\d{2})(?!\d)")
# 18-character ID number -> keep first 6 / last 4.
_ID_CARD = re.compile(r"(?<!\d)(\d{6})\d{8}([\dXx]{4})(?!\d)")
# 16-19 digit bank card -> keep the last 4 digits only.
_BANK_CARD = re.compile(r"(?<!\d)\d{12,15}(\d{4})(?!\d)")
# Email -> first character of the local part, then ***@domain.
_EMAIL = re.compile(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")


def _bank_replacement(match):
    return "*" * (len(match.group(0)) - 4) + match.group(1)


def mask(text):
    """Mask PII patterns in ``text``; return ``(masked_text, hit_count)``.

    Pure and dependency-free. Non-string input is returned unchanged with a
    zero count so callers never need a guard.
    """
    if not isinstance(text, str) or not text:
        return text, 0
    hits = 0
    for pattern, replacement in (
        (_ID_CARD, r"\g<1>********\g<2>"),
        (_PHONE, r"\g<1>******\g<2>"),
        (_BANK_CARD, _bank_replacement),
        (_EMAIL, r"\g<1>***@\g<2>"),
    ):
        text, count = pattern.subn(replacement, text)
        hits += count
    return text, hits
