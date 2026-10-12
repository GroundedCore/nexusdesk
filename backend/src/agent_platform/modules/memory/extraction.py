"""LLM extraction of customer profile/preference memories (FR-2).

Runs as ``memory_extract`` tasks on the shared ``memory_tasks`` queue, claimed
by the same MemoryWorker loop as Phase 1 summaries. The model call always goes
through the model gateway so usage lands in ``gateway_calls`` (P2-CM-04) and
demo deployments stay zero-cost. Pure helpers (prompt assembly, output
parsing, key validation) are unit-testable without a database.

Defensive posture: model output is untrusted — invalid JSON drops the whole
batch, invalid items are skipped individually, low-confidence items are
dropped. 宁缺毋滥.
"""

import json
import logging
import re

from agent_platform.modules.memory.service import MemoryService
from agent_platform.modules.memory.summary import SummaryError, require_binding
from agent_platform.modules.model_gateway.contracts import ChatRequest
from agent_platform.platform.persistence.store import audit, many, one

logger = logging.getLogger(__name__)

EXTRACT_KIND = "memory_extract"

# Controlled key vocabulary (FR-2). ``custom_*`` extension keys are allowed.
CONTROLLED_KEYS = {
    "profile": {"name", "phone", "email", "account_id", "product_model", "company"},
    "preference": {"language", "style", "address_name", "contact_channel"},
}
KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,99}$")
MAX_CONTENT_CHARS = 500

EXTRACT_PROMPT = """你是客户记忆抽取助手。从客服对话中抽取"客户明确陈述的、跨会话有长期价值"的事实与偏好。

规则：
- 只抽取客户（user）明确陈述的信息；客服（assistant）说的内容不能作为事实来源，除非客户明确确认；
- 每条记忆属于两类之一：profile（身份事实）或 preference（偏好）；
- key 必须从受控词表选择：profile 用 {profile_keys}；preference 用 {preference_keys}；确有词表无法表达的内容，可用 custom_ 开头的扩展键（小写字母、数字、下划线）；
- 不要抽取：一次性的订单号/工单号、临时情绪与寒暄、问题描述本身、任何你不确定的信息；
- 对照"已有记忆"决定操作：新键用 add；同键但内容有实质变化用 update；同键且内容等价用 noop；
- 每条给出 confidence（0~1）：客户明确陈述给 0.9 及以上；需要一定推断的给 0.5~0.8；低于 0.5 的不要输出；
- 只输出 JSON 数组，不要输出任何解释或标记：
  [{{"op":"add|update|noop","type":"profile|preference","key":"...","content":"...","confidence":0.9,"reason":"判断依据"}}]
- 没有可抽取的内容时输出 []。"""


def valid_key(item_type, key):
    """Controlled vocabulary plus ``custom_*`` extensions (P2-UT-10)."""
    if not isinstance(key, str) or not KEY_PATTERN.match(key):
        return False
    if key in CONTROLLED_KEYS.get(item_type, set()):
        return True
    # Extension keys need an actual suffix: bare "custom_" is not a key.
    return key.startswith("custom_") and len(key) > len("custom_")


def build_extraction_prompt(messages, existing_keys):
    """Assemble the chat messages for one extraction call."""
    sections = []
    if existing_keys:
        lines = "\n".join(f"- {item['type']}.{item['key']}" for item in existing_keys)
        sections.append("【已有记忆】\n" + lines)
    lines = [
        f"{item['role']}: {item['content']}"
        for item in messages
        if item.get("role") in ("user", "assistant") and item.get("content")
    ]
    sections.append("【对话内容】\n" + "\n".join(lines))
    prompt = EXTRACT_PROMPT.format(
        profile_keys="/".join(sorted(CONTROLLED_KEYS["profile"])),
        preference_keys="/".join(sorted(CONTROLLED_KEYS["preference"])),
    )
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": "\n\n".join(sections)},
    ]


def validate_item(item):
    """Normalise one model output item; invalid items are skipped (P2-UT-06)."""
    if not isinstance(item, dict):
        return None
    op, item_type = item.get("op"), item.get("type")
    if op not in {"add", "update", "noop"} or item_type not in CONTROLLED_KEYS:
        return None
    key = item.get("key")
    if not valid_key(item_type, key):
        return None
    confidence = item.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return None
    if not 0 <= confidence <= 1:
        return None
    content = item.get("content")
    if op == "noop":
        content = content if isinstance(content, str) else ""
    elif not isinstance(content, str) or not content.strip() or len(content) > MAX_CONTENT_CHARS:
        return None
    return {
        "op": op,
        "type": item_type,
        "key": key,
        "content": content.strip(),
        "confidence": float(confidence),
        "reason": item.get("reason") if isinstance(item.get("reason"), str) else "",
    }


def parse_extraction_output(text):
    """Parse the model output into validated ops.

    Invalid JSON (or a non-array payload) drops the entire batch — partial
    writes from an unparseable response are never risked (P2-UT-05). Code
    fences around the JSON are tolerated; anything else is not.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    cleaned = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1)
    try:
        data = json.loads(cleaned)
    except ValueError:
        return None
    if not isinstance(data, list):
        return None
    return [op for op in (validate_item(item) for item in data) if op is not None]


async def enqueue_extract_task(conn, tenant, conversation_id, customer_id, debounce_seconds):
    """Insert an extraction task unless one exists within the debounce window.

    Per-conversation debounce (FR-2): at most one extraction task per
    ``debounce_seconds`` — the extraction input is the recent message window,
    so a later task naturally covers earlier turns; nothing is merged.
    """
    existing = await one(
        conn,
        """SELECT id FROM memory_tasks
        WHERE tenant_id=:t AND kind=:kind AND payload->>'conversation_id'=:cid
        AND created_at > now() - :debounce * interval '1 second' LIMIT 1""",
        t=tenant,
        kind=EXTRACT_KIND,
        cid=str(conversation_id),
        debounce=debounce_seconds,
    )
    if existing:
        return None
    row = await one(
        conn,
        """INSERT INTO memory_tasks(tenant_id,kind,payload)
        VALUES(:t,:kind,CAST(:payload AS jsonb)) RETURNING id""",
        t=tenant,
        kind=EXTRACT_KIND,
        payload=json.dumps(
            {"conversation_id": str(conversation_id), "customer_id": str(customer_id)}
        ),
    )
    return row["id"]


class ExtractionService:
    def __init__(self, engine, gateway, settings, memory: MemoryService | None = None):
        self.engine, self.gateway, self.settings = engine, gateway, settings
        self.memory = memory or MemoryService(engine, settings)

    async def _profile_binding(self, c, tenant, conversation):
        """Same profile selection as the summary service: the conversation
        agent's published chat profile, nothing else."""
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
        """Run one claimed extraction task. Returns 'done' or 'skipped'."""
        payload = task["payload"]
        tenant = task["tenant_id"]
        cid, customer_id = payload["conversation_id"], payload["customer_id"]
        async with self.engine.begin() as c:
            conversation = await one(
                c,
                """SELECT id,tenant_id,agent_id,mode,source,customer_id
                FROM runtime_conversations WHERE id=:cid AND tenant_id=:t""",
                cid=cid,
                t=tenant,
            )
            # Pollution guards (AC-5): playground conversations, example
            # agents and conversations whose customer link changed since
            # dispatch never produce memory writes.
            if (
                not conversation
                or conversation["source"] == "playground"
                or str(conversation["customer_id"]) != str(customer_id)
            ):
                return "skipped"
            if conversation["agent_id"]:
                agent = await one(
                    c,
                    "SELECT is_example FROM agents WHERE id=:id AND tenant_id=:t",
                    id=conversation["agent_id"],
                    t=tenant,
                )
                if agent and agent["is_example"]:
                    return "skipped"
            binding = await self._profile_binding(c, tenant, conversation)
            messages = await many(
                c,
                """SELECT role,content FROM (SELECT role,content,seq FROM conversation_messages
                WHERE conversation_id=:id ORDER BY seq DESC LIMIT :lim) recent ORDER BY seq""",
                id=cid,
                lim=self.settings.memory_extract_recent_messages,
            )
            existing = await many(
                c,
                """SELECT type,key FROM memory_items
                WHERE tenant_id=:t AND customer_id=:c AND status IN ('active','pending')
                ORDER BY type,key""",
                t=tenant,
                c=customer_id,
            )
        profile_id, profile_version = require_binding(binding)
        prompt = build_extraction_prompt(messages, existing)
        try:
            result = await self.gateway.invoke(
                tenant,
                profile_id,
                profile_version,
                ChatRequest(messages=prompt),
                actor="memory",
            )
        except SummaryError:
            raise
        except Exception as exc:
            raise SummaryError(getattr(exc, "code", "extract_model_error")) from exc
        ops = parse_extraction_output(result["payload"]["content"])
        if ops is None:
            # 宁缺毋滥: an unparseable batch is discarded, not retried forever.
            logger.warning(
                "Memory extraction output was not valid JSON; batch dropped (conversation %s)",
                cid,
            )
            return "done"
        for op in ops:
            if op["reason"]:
                logger.debug("extract %s.%s confidence=%.2f: %s", op["type"], op["key"], op["confidence"], op["reason"])
        async with self.engine.begin() as c:
            counts = await self.memory.apply_extracted(
                c,
                tenant,
                customer_id,
                ops,
                str(cid),
                self.settings.memory_confidence_auto,
                self.settings.memory_confidence_pending,
            )
            await audit(
                c,
                tenant,
                "memory",
                "memory.extract.applied",
                customer_id,
                conversation_id=str(cid),
                input_messages=len(messages),
                **counts,
                usage=result["usage"] or {},
            )
        return "done"
