"""Rolling conversation summary: prompt assembly, output validation, execution.

The summary model call always goes through the model gateway so usage lands in
``gateway_calls`` and demo deployments stay zero-cost. Pure helpers
(``build_prompt``/``clean_output``/``trim_dropped``/``render_injection``) are
kept separate from :class:`SummaryService` so they are unit-testable without a
database.
"""

import json
import logging

from agent_platform.modules.model_gateway.contracts import ChatRequest
from agent_platform.platform.persistence.store import audit, execute, one

logger = logging.getLogger(__name__)

SUMMARY_KIND = "conversation_summary"

# Injected summaries are capped at 800 tokens; with the ~2 chars/token estimate
# that is 1600 characters. The tail is kept because recent information wins.
INJECT_MAX_TOKENS = 800
CHARS_PER_TOKEN = 2
INJECTION_HEADER = "【此前对话摘要】"

SUMMARY_PROMPT = """你是客服对话摘要助手。将"此前的对话摘要"与"新被截断的对话片段"合并为一份滚动摘要。

要求：
- 输出不超过 {max_chars} 字符的纯文本段落，不要添加任何前缀或标记；
- 必须保留：客户身份信息与联系方式、客户诉求/问题、关键事实与约束（如过敏史、订单号、故障现象）、已达成的结论、待办与承诺；
- 必须丢弃：寒暄客套、重复表述、工具调用细节与内部指令；
- 若新片段没有增量信息，直接输出原有摘要；
- 只输出摘要正文，不要输出解释。"""


class SummaryError(Exception):
    """A summary attempt failed; the worker decides retry vs. failed."""

    def __init__(self, code, retryable=True):
        self.code, self.retryable = code, retryable
        super().__init__(code)


def build_prompt(old_summary, dropped_messages, max_output_chars):
    """Assemble the chat messages for one rolling-summary call (P1-UT-01/02)."""
    sections = []
    if old_summary:
        sections.append("【此前的对话摘要】\n" + old_summary)
    lines = [
        f"{item['role']}: {item['content']}"
        for item in dropped_messages
        if item.get("role") in ("user", "assistant") and item.get("content")
    ]
    sections.append("【新被截断的对话片段】\n" + "\n".join(lines))
    return [
        {"role": "system", "content": SUMMARY_PROMPT.format(max_chars=max_output_chars)},
        {"role": "user", "content": "\n\n".join(sections)},
    ]


def clean_output(text, max_output_chars):
    """Validate model output: blank is a failure, oversize keeps the tail (P1-UT-03/04)."""
    if not isinstance(text, str) or not text.strip():
        return None
    cleaned = text.strip()
    return cleaned[-max_output_chars:] if len(cleaned) > max_output_chars else cleaned


def trim_dropped(messages, max_input_messages):
    """Only the most recent ``max_input_messages`` dropped messages enter a task (P1-UT-07)."""
    return messages[-max_input_messages:] if max_input_messages else list(messages)


def render_injection(summary, max_tokens=INJECT_MAX_TOKENS):
    """The system message injected after the main prompt (P1-UT-08).

    Oversized summaries keep their tail: recent information takes precedence.
    """
    limit = max_tokens * CHARS_PER_TOKEN
    trimmed = summary[-limit:] if len(summary) > limit else summary
    return f"{INJECTION_HEADER}\n{trimmed}"


def require_binding(binding):
    """Extract the (profile_id, version) pair or fail the task without retry.

    Conversations without an agent binding have no chat profile to summarise
    with; retrying cannot fix that, so the error is non-retryable (P1-UT-09).
    """
    if not binding or not binding.get("profile_id"):
        raise SummaryError("no_chat_profile", retryable=False)
    return binding["profile_id"], binding["profile_version"]


def backoff_seconds(attempts):
    """Exponential retry delay after ``attempts`` failed tries, capped (P1-UT-10)."""
    return min(30 * 2 ** max(0, attempts - 1), 300)


async def enqueue_summary_task(conn, tenant, conversation_id, dropped, old_summary, max_input):
    """Insert or merge a summary task inside the caller's transaction.

    A task that is still pending absorbs the newly dropped messages instead of
    piling up a second row (P1-UT-05). A running task is never disturbed; the
    new messages become a fresh pending task (P1-UT-06).
    """
    dropped = trim_dropped(dropped, max_input)
    if not dropped:
        return None
    existing = await one(
        conn,
        """SELECT id,payload FROM memory_tasks
        WHERE tenant_id=:t AND kind=:kind AND status='pending'
        AND payload->>'conversation_id'=:cid
        ORDER BY created_at LIMIT 1 FOR UPDATE""",
        t=tenant,
        kind=SUMMARY_KIND,
        cid=str(conversation_id),
    )
    if existing:
        payload = existing["payload"]
        merged = trim_dropped([*payload["dropped_messages"], *dropped], max_input)
        await execute(
            conn,
            """UPDATE memory_tasks
            SET payload=CAST(:payload AS jsonb),updated_at=now() WHERE id=:id""",
            id=existing["id"],
            payload=json.dumps({**payload, "dropped_messages": merged}, ensure_ascii=False),
        )
        return existing["id"]
    row = await one(
        conn,
        """INSERT INTO memory_tasks(tenant_id,kind,payload)
        VALUES(:t,:kind,CAST(:payload AS jsonb)) RETURNING id""",
        t=tenant,
        kind=SUMMARY_KIND,
        payload=json.dumps(
            {
                "conversation_id": str(conversation_id),
                "dropped_messages": dropped,
                "old_summary": old_summary,
            },
            ensure_ascii=False,
        ),
    )
    return row["id"]


class SummaryService:
    def __init__(self, engine, gateway, settings):
        self.engine, self.gateway, self.settings = engine, gateway, settings

    async def _profile_binding(self, c, tenant, conversation):
        """The summary call reuses the conversation agent's published chat profile.

        There is no tenant-level default profile concept in the model gateway, so
        conversations without an agent binding degrade to ``no_chat_profile``.
        """
        if not conversation["agent_id"]:
            return None
        return await one(
            c,
            """SELECT v.config->>'model_profile_id' AS profile_id,
            (v.config->>'model_profile_version')::int AS profile_version
            FROM agents a
            JOIN agent_versions v ON v.agent_id=a.id AND v.version=a.published_version
            WHERE a.id=:aid AND a.tenant_id=:t""",
            aid=conversation["agent_id"],
            t=tenant,
        )

    async def execute(self, task):
        """Run one claimed task row. Returns 'done' or 'skipped'; raises SummaryError."""
        payload = task["payload"]
        cid, tenant = payload["conversation_id"], task["tenant_id"]
        dropped = payload.get("dropped_messages") or []
        async with self.engine.begin() as c:
            conversation = await one(
                c,
                """SELECT id,tenant_id,agent_id,mode,summary FROM runtime_conversations
                WHERE id=:cid AND tenant_id=:t""",
                cid=cid,
                t=tenant,
            )
            # A deleted or already closed conversation has no use for a summary;
            # the task completes without touching anything.
            if not conversation or conversation["mode"] == "closed":
                return "skipped"
            binding = await self._profile_binding(c, tenant, conversation)
        profile_id, profile_version = require_binding(binding)
        # The old summary is read at execution time rather than frozen at
        # dispatch, so merged/deferred tasks always merge into the latest text.
        old_summary = conversation["summary"]
        messages = build_prompt(old_summary, dropped, self.settings.summary_max_output_chars)
        try:
            result = await self.gateway.invoke(
                tenant,
                profile_id,
                profile_version,
                ChatRequest(messages=messages),
                actor="memory",
            )
        except SummaryError:
            raise
        except Exception as exc:
            raise SummaryError(getattr(exc, "code", "summary_model_error")) from exc
        summary = clean_output(
            result["payload"]["content"], self.settings.summary_max_output_chars
        )
        if summary is None:
            raise SummaryError("empty_summary")
        async with self.engine.begin() as c:
            # Compare-and-swap on the summary we based the prompt on: a
            # concurrently updated summary is never clobbered, and a conversation
            # closed in the meantime is left alone.
            changed = await one(
                c,
                """UPDATE runtime_conversations SET summary=:summary
                WHERE id=:cid AND tenant_id=:t AND mode<>'closed'
                AND summary IS NOT DISTINCT FROM :expected
                RETURNING id""",
                cid=cid,
                t=tenant,
                summary=summary,
                expected=old_summary,
            )
            if not changed:
                return "skipped"
            await audit(
                c,
                tenant,
                "memory",
                "memory.summary.generated",
                cid,
                conversation_id=str(cid),
                input_messages=len(dropped),
                old_length=len(old_summary or ""),
                new_length=len(summary),
                usage=result["usage"] or {},
            )
        return "done"
