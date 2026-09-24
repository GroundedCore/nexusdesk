import itertools
import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChunkingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy: Literal[
        "fixed", "recursive", "hybrid", "delimiter", "page", "table", "faq", "whole", "semantic"
    ] = "hybrid"
    size: int = Field(default=800, ge=100, le=4000)
    overlap: int = Field(default=100, ge=0, le=500)
    delimiter: str = Field(default="\n\n", min_length=1, max_length=100)
    include_headings: bool = True
    rows_per_chunk: int = Field(default=10, ge=1, le=100)
    question_column: int = Field(default=0, ge=0, le=100)
    answer_column: int = Field(default=1, ge=0, le=100)
    sheet_name: str = Field(default="", max_length=100)
    header_row: int = Field(default=1, ge=1, le=10000)
    semantic_profile_id: UUID | None = None
    semantic_profile_version: int = Field(default=1, ge=1)
    semantic_threshold: float = Field(default=0.65, ge=-1, le=1)

    @model_validator(mode="after")
    def overlap_limit(self):
        if self.overlap >= self.size:
            raise ValueError("overlap must be smaller than size")
        if self.strategy == "semantic" and self.semantic_profile_id is None:
            raise ValueError("semantic_embedding_profile_required")
        return self


def split_text(text, policy=None):
    policy = policy or ChunkingPolicy()
    start = 0
    while start < len(text):
        end = min(len(text), start + policy.size)
        if end < len(text) and policy.strategy != "fixed":
            segment = text[start:end]
            separators = ["\n\n", "\n", "。", "！", "？", ". ", "; ", " "]
            for separator in separators:
                position = segment.rfind(separator)
                if position >= policy.size // 2:
                    end = start + position + len(separator)
                    break
        content = text[start:end]
        if content.strip():
            yield {"content": content, "start": start, "end": end}
        if end == len(text):
            break
        start = max(start + 1, end - policy.overlap)


def chunk_document(text, policy=None):
    policy = policy or ChunkingPolicy()
    if policy.strategy == "whole":
        if len(text) > policy.size:
            raise ValueError("whole_document_exceeds_chunk_size")
        return [{"content": text, "start": 0, "end": len(text)}] if text.strip() else []
    if policy.strategy == "delimiter":
        result, offset = [], 0
        for part in text.split(policy.delimiter):
            for chunk in split_text(part, policy):
                result.append(
                    {**chunk, "start": offset + chunk["start"], "end": offset + chunk["end"]}
                )
            offset += len(part) + len(policy.delimiter)
        return result
    if policy.strategy != "hybrid":
        return list(split_text(text, policy))
    # Markdown section boundaries keep overlap within a section.
    boundaries = [
        0,
        *[m.start() for m in re.finditer(r"(?m)^#{1,6}\s+", text) if m.start() > 0],
        len(text),
    ]
    chunks = []
    for left, right in itertools.pairwise(boundaries):
        section = text[left:right]
        heading = section.splitlines()[0] if section.startswith("#") else ""
        for chunk in split_text(section, policy):
            chunks.append(
                {
                    **chunk,
                    "start": left + chunk["start"],
                    "end": left + chunk["end"],
                    "heading": heading,
                }
            )
    return chunks


def chunk_pages(pages, policy=None, progress=None):
    """Keep source locations; repeat table headers and FAQ questions within the size budget."""
    policy = policy or ChunkingPolicy()
    if policy.strategy == "semantic":
        raise ValueError("semantic_chunking_requires_async_gateway")
    result = []
    if policy.strategy == "whole":
        text = "\n\n".join(p["text"] for p in pages)
        return [
            {
                **ch,
                "source": {"blocks": [p.get("source", {"page_num": p["page_num"]}) for p in pages]},
            }
            for ch in chunk_document(text, policy)
        ]
    headings = []
    for page_index, page in enumerate(pages):
        if progress:
            progress(page_index, len(pages))
        source = dict(page.get("source", {"page_num": page["page_num"]}))
        tables = [e for e in page.get("elements", []) if e.get("type") == "table"]
        if policy.sheet_name:
            tables = [e for e in tables if e.get("sheet") == policy.sheet_name]
            if not tables:
                continue
        if policy.strategy in {"table", "faq"} or (
            policy.strategy == "hybrid"
            and tables
            and all(e.get("type") == "table" for e in page.get("elements", []))
        ):
            if not tables:
                raise ValueError("table_or_faq_strategy_requires_table_data")
            for table in tables:
                matrix = [table["header"], *table["rows"]]
                if policy.header_row >= len(matrix):
                    raise ValueError("table_header_has_no_data_rows")
                header = " | ".join(matrix[policy.header_row - 1])
                rows = matrix[policy.header_row :]
                if policy.strategy == "faq":
                    groups = []
                    for i, row in enumerate(rows):
                        if max(policy.question_column, policy.answer_column) >= len(row):
                            raise ValueError("faq_column_out_of_range")
                        groups.append(
                            (
                                i + policy.header_row + 1,
                                "问题：" + row[policy.question_column],
                                row[policy.answer_column],
                            )
                        )
                else:
                    groups = [
                        (
                            i + policy.header_row + 1,
                            header,
                            "\n".join(" | ".join(r) for r in rows[i : i + policy.rows_per_chunk]),
                        )
                        for i in range(0, len(rows), policy.rows_per_chunk)
                    ]
                for row_num, prefix, body in groups:
                    budget = policy.size - len(prefix) - 2
                    if budget < 100:
                        raise ValueError("table_header_or_question_too_long")
                    subpolicy = policy.model_copy(
                        update={"size": budget, "overlap": min(policy.overlap, budget - 1)}
                    )
                    for chunk in split_text(body, subpolicy):
                        result.append(
                            {
                                "content": prefix + "\n\n" + chunk["content"],
                                "source": {
                                    **source,
                                    "sheet": table.get("sheet", ""),
                                    "row_start": row_num,
                                },
                            }
                        )
        else:
            for chunk in chunk_document(page["text"], policy):
                heading = chunk.get("heading", "")
                if heading:
                    depth = len(heading) - len(heading.lstrip("#"))
                    headings = headings[: depth - 1] + [heading]
                prefix = "\n".join(headings) if policy.include_headings else ""
                provenance = {
                    **source,
                    "heading_path": headings.copy(),
                    "start": chunk.get("start"),
                    "end": chunk.get("end"),
                }
                if prefix and not chunk["content"].startswith(prefix):
                    budget = policy.size - len(prefix) - 2
                    if budget < 50:
                        raise ValueError("heading_path_too_long")
                    adjusted = policy.model_copy(
                        update={"size": budget, "overlap": min(policy.overlap, budget - 1)}
                    )
                    for part in split_text(chunk["content"], adjusted):
                        result.append(
                            {"content": prefix + "\n\n" + part["content"], "source": provenance}
                        )
                else:
                    result.append({**chunk, "source": provenance})
    if progress:
        progress(len(pages), len(pages))
    return result
