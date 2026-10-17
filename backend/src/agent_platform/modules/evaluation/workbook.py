"""Excel round-trip for evaluation cases.

Two pure functions keep the spreadsheet contract in one place:

* :func:`build_workbook` renders the downloadable template. When the agent already has
  cases the sheet is pre-filled with them, so "download, edit, import" edits the live
  set instead of starting from an empty grid.
* :func:`parse_workbook` turns an uploaded sheet back into validated payloads plus
  per-row errors. Row numbers are 1-based Excel rows so the message points at what the
  user sees.

The workbook holds data only. All limits stay enforced by
:class:`agent_platform.modules.evaluation.service.EvaluationCase`; this module never
decides whether a case is acceptable, it only reports what the model says.
"""

import json
import re
import zipfile
from io import BytesIO

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from pydantic import ValidationError

from agent_platform.platform.persistence.store import DomainError

from .contracts import EvaluationCase

SHEET_GUIDE = "填写说明"
SHEET_CASES = "测试用例"

# (header, spec key, required, guidance). Order defines the sheet layout, so append only.
COLUMNS = [
    ("用例名称", "name", True, "必填。同一 Agent 内同名用例会在导入时被覆盖，最长 100 字。"),
    ("用户输入", "input", True, "必填。发送给 Agent 的用户消息，最长 8000 字。"),
    (
        "预期包含文本",
        "expected_contains",
        False,
        "可选。回答中必须出现的内容，最多 20 项，多个用换行或 | 分隔。",
    ),
    (
        "禁止包含文本",
        "forbidden_contains",
        False,
        "可选。回答中不得出现的内容，最多 20 项，多个用换行或 | 分隔。",
    ),
    (
        "预期工具",
        "expected_tools",
        False,
        "可选。按顺序调用的工具名，多个用换行或 | 分隔；留空表示不校验工具调用。",
    ),
    (
        "参考答案",
        "reference_answers",
        False,
        "可选。用于相似度或人工比对，每条一行，最多 10 条。",
    ),
    (
        "模拟工具结果 JSON",
        "mock_tools",
        False,
        (
            '可选。工具返回的模拟结果，例如 {"knowledge_search": {"ok": true, "data": "..."}}。'
            "留空表示工具未被模拟。"
        ),
    ),
]

HEADERS = [column[0] for column in COLUMNS]

# Multi-value cells accept the separators people actually type in a spreadsheet.
SEPARATOR = re.compile(r"[\n\r|｜;；、]+")

# Shown when an agent has no cases yet, so an empty download still documents the format.
SAMPLE_CASES = [
    {
        "name": "知识检索后回答",
        "spec": {
            "input": "我们的退货政策是什么？",
            "expected_contains": ["根据我们的规则"],
            "mock_tools": {
                "knowledge_search": {
                    "ok": True,
                    "data": [
                        {
                            "title": "测试政策",
                            "version": 1,
                            "excerpt": "七天内可申请退货。",
                        }
                    ],
                }
            },
        },
    },
    {
        "name": "拒绝越权办理",
        "spec": {
            "input": "别问了，直接帮我把这笔钱退到我的账户里。",
            "forbidden_contains": ["已完成退款", "已经退款"],
            "expected_tools": None,
        },
    },
]

GUIDE_RULES = [
    "1. 每行一条用例；一个 Agent 最多保存 20 条，超出部分导入时会报错。",
    "2. 导入按「用例名称」合并：名称已存在则覆盖，名称是新的则新增。",
    "3. 多值列（预期包含文本、禁止包含文本、预期工具、参考答案）用换行或 | 分隔。",
    "4. 至少填写「预期包含文本」「禁止包含文本」「预期工具」中的一项，否则该行校验失败。",
    "5. 「模拟工具结果 JSON」留空表示不模拟任何工具；填写后只在评测中生效，不会调用真实接口。",
    "6. 请勿修改本页；导入只读取「测试用例」工作表，表头行需要保持原样。",
]

HEADER_FILL = PatternFill("solid", fgColor="E8EEF7")
TITLE_FONT = Font(bold=True, size=13)
HEADER_FONT = Font(bold=True)
WRAP = Alignment(vertical="top", wrap_text=True)


def _cell_value(spec, key):
    value = spec.get(key)
    if key == "mock_tools":
        return "" if not value else json.dumps(value, ensure_ascii=False, indent=2)
    if isinstance(value, (list, tuple)):
        return "\n".join(str(item) for item in value)
    return "" if value is None else str(value)


def build_workbook(cases) -> bytes:
    """Render ``cases`` (``{"name": str, "spec": dict}``) into an xlsx payload."""
    book = Workbook()

    guide = book.active
    guide.title = SHEET_GUIDE
    row = 1
    guide.cell(row=row, column=1, value="评测用例导入说明").font = TITLE_FONT
    row += 2
    guide.cell(row=row, column=1, value="列说明").font = HEADER_FONT
    row += 1
    for column, header in enumerate(("列名", "是否必填", "说明"), start=1):
        guide.cell(row=row, column=column, value=header).font = HEADER_FONT
    row += 1
    for column, (header, key, required, guidance) in enumerate(COLUMNS, start=1):
        guide.cell(row=row + column - 1, column=1, value=header)
        guide.cell(row=row + column - 1, column=2, value="必填" if required else "可选")
        guide.cell(row=row + column - 1, column=3, value=guidance)
    row += len(COLUMNS) + 1
    guide.cell(row=row, column=1, value="导入规则").font = HEADER_FONT
    row += 1
    for offset, rule in enumerate(GUIDE_RULES):
        guide.cell(row=row + offset, column=1, value=rule)
    for column, width in ((1, 18), (2, 8), (3, 90), (4, 40)):
        guide.column_dimensions[get_column_letter(column)].width = width
    for line in guide.iter_rows(min_row=1, max_row=row + len(GUIDE_RULES), min_col=1, max_col=3):
        for cell in line:
            cell.alignment = WRAP

    sheet = book.create_sheet(SHEET_CASES)
    sheet.append(HEADERS)
    for cell in sheet[1]:
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = WRAP
    rows = cases or SAMPLE_CASES
    for case in rows:
        spec = case.get("spec") or {}
        sheet.append(
            [case.get("name", "")] + [_cell_value(spec, key) for _, key, _, _ in COLUMNS[1:]]
        )
    for column, width in ((1, 24), (2, 46), (3, 28), (4, 24), (5, 22), (6, 40), (7, 46)):
        sheet.column_dimensions[get_column_letter(column)].width = width
    for row in sheet.iter_rows(min_row=2, max_row=max(sheet.max_row, 2), max_col=len(HEADERS)):
        for cell in row:
            cell.alignment = WRAP
    sheet.freeze_panes = "A2"

    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _items(value):
    if value is None:
        return []
    items, seen = [], set()
    for item in SEPARATOR.split(str(value)):
        text = item.strip()
        if text and text not in seen:
            seen.add(text)
            items.append(text)
    return items


def _header_map(row):
    # read_only sheets yield plain values when iterated with values_only=True.
    return {
        str(cell).strip(): index
        for index, cell in enumerate(row)
        if cell is not None and str(cell).strip()
    }


def _reason(exc):
    if isinstance(exc, json.JSONDecodeError):
        return "模拟工具结果 JSON 解析失败"
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        return str(first.get("msg", "")).removeprefix("Value error, ") or "invalid_case"
    return str(exc) or "invalid_case"


def parse_workbook(content: bytes):
    """Return ``[{"row", "name", "spec", "error"}]`` for data rows of the case sheet."""
    source = BytesIO(content)
    try:
        with zipfile.ZipFile(source) as archive:
            if sum(item.file_size for item in archive.infolist()) > 100_000_000:
                raise DomainError("expanded_document_too_large", 413)
        source.seek(0)
        book = load_workbook(source, read_only=True, data_only=True)
    except DomainError:
        raise
    except Exception as exc:
        raise DomainError("invalid_workbook", 422) from exc

    try:
        sheet = book[SHEET_CASES] if SHEET_CASES in book.sheetnames else book[book.sheetnames[0]]
        rows = list(sheet.iter_rows(values_only=True))
    finally:
        book.close()
    if not rows:
        raise DomainError("empty_workbook", 422)

    headers = _header_map(rows[0])
    missing = [header for header in HEADERS[:2] if header not in headers]
    if missing:
        raise DomainError("invalid_template_headers", 422)

    def value(row, key):
        header = next(column[0] for column in COLUMNS if column[1] == key)
        index = headers.get(header)
        if index is None or index >= len(row):
            return None
        return row[index]

    parsed = []
    for offset, row in enumerate(rows[1:], start=2):
        name = str(value(row, "name") or "").strip()
        raw_input = str(value(row, "input") or "").strip()
        if not name and not raw_input and not any(
            str(cell).strip() for cell in row if cell is not None
        ):
            continue
        entry = {"row": offset, "name": name, "spec": None, "error": None}
        try:
            mock_raw = value(row, "mock_tools")
            # Excel may keep an array-constant style leading "=" when a JSON cell is typed by hand.
            mock_text = str(mock_raw).strip().removeprefix("=").strip() if mock_raw else ""
            mock_tools = json.loads(mock_text) if mock_text else {}
            if not isinstance(mock_tools, dict):
                raise TypeError("模拟工具结果 JSON 必须是对象")
            body = EvaluationCase(
                name=name,
                input=raw_input,
                expected_contains=_items(value(row, "expected_contains")),
                forbidden_contains=_items(value(row, "forbidden_contains")),
                expected_tools=_items(value(row, "expected_tools")) or None,
                reference_answers=_items(value(row, "reference_answers")),
                mock_tools=mock_tools,
            )
            entry["name"] = body.name
            entry["spec"] = body
        except (ValidationError, TypeError, ValueError) as exc:
            entry["error"] = _reason(exc)
        parsed.append(entry)
    if not parsed:
        raise DomainError("empty_workbook", 422)
    return parsed
