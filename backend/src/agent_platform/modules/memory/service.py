"""Customer identity resolution, memory item storage and console management.

Phase 2 of agent memory: one customer entity per recognised real-world
customer, identity bindings per source, and profile/preference items with
same-key upsert semantics (``prev_content`` single-level history). Pure
helpers (``trim_items``/``render_memory_injection``/``route_confidence``) are
kept separate from :class:`MemoryService` so they are unit-testable without a
database.
"""

import logging
import re
from datetime import UTC, datetime
from uuid import uuid4

from agent_platform.modules.memory import governance
from agent_platform.modules.memory.decay import (
    DEFAULT_HALF_LIVES,
    effective_confidence,
    half_lives,
    injection_filter_sql,
)
from agent_platform.modules.memory.pii import mask
from agent_platform.platform.persistence.store import (
    DomainError,
    audit,
    execute,
    many,
    one,
    required,
)

logger = logging.getLogger(__name__)

# Rough characters-per-token estimate, same convention as the summary module.
CHARS_PER_TOKEN = 2
# Injected memory is capped at 1000 tokens (FR-3); the item cap comes from
# settings.memory_inject_max_items.
INJECT_MAX_TOKENS = 1000
PROFILE_HEADER = "【客户画像】"
PREFERENCE_HEADER = "【客户偏好】"

# Phase 4 flag memories (FR-2): injected ahead of the profile section under
# their own cap (settings.memory_flag_inject_max), outside the token budget.
FLAG_HEADER = "【服务标记】"
FLAG_LABELS = {"vip": "VIP 客户", "complaint_risk": "投诉风险", "blacklist": "黑名单"}
# Fixed instruction appended whenever a blacklist flag is present (R15).
BLACKLIST_INSTRUCTION = "谨慎服务，涉承诺一律转人工确认"

# Human-protection suggestion rows (R4): an extraction update against a
# protected row becomes a pending "<key>:suggested" item instead of an
# overwrite. The key column is VARCHAR(100); the base key is truncated to 89
# characters so the 10-character suffix always fits (collisions are absorbed
# by the same-key unique constraint, keeping the upsert idempotent).
SUGGESTED_SUFFIX = ":suggested"
SUGGESTED_BASE_MAX = 89

# Phase 3 event items. The in-flight injection keys (open tickets and pending
# commitments) share the items table with plain event records; the key prefix
# is what makes an item appear in the 【待跟进事项】 section.
EVENT_HEADER = "【待跟进事项】"
OPEN_TICKET_PREFIX = "open_ticket:"
COMMITMENT_PREFIX = "commitment:"

# Confidence routing (FR-2): high confidence takes effect immediately, medium
# waits for human confirmation, low is dropped. Thresholds are settings.
def route_confidence(confidence, auto_threshold, pending_threshold):
    if confidence >= auto_threshold:
        return "active"
    if confidence >= pending_threshold:
        return "pending"
    return None


def trim_items(items, max_items, max_tokens=INJECT_MAX_TOKENS):
    """Keep the most recently updated items within count and token budgets (P2-UT-11).

    ``items`` must already be ordered by ``updated_at`` descending. The first
    item is always kept, even when it alone exceeds the token budget.
    """
    budget = max_tokens * CHARS_PER_TOKEN
    kept, used = [], 0
    for item in items[:max_items]:
        line = len(item["key"]) + len(item["content"]) + 4  # "- key: content\n"
        if kept and used + line > budget:
            break
        kept.append(item)
        used += line
    return kept


def render_memory_injection(items):
    """Render active items as the injected system message body (P2-UT-12).

    Empty groups are omitted entirely; ``None`` means "inject nothing".
    """
    groups = {"profile": [], "preference": []}
    for item in items:
        if item["type"] in groups:
            groups[item["type"]].append(item)
    sections = []
    for item_type, header in (("profile", PROFILE_HEADER), ("preference", PREFERENCE_HEADER)):
        if groups[item_type]:
            lines = "\n".join(f"- {item['key']}: {item['content']}" for item in groups[item_type])
            sections.append(f"{header}\n{lines}")
    return "\n\n".join(sections) if sections else None


def render_event_injection(items):
    """Render the high-priority event section (FR-3), appended after the
    profile/preference rendering so the injection order stays
    summary → profile/preference → pending events. ``None`` means "inject
    nothing", matching ``render_memory_injection``.
    """
    lines = []
    for item in items:
        key = item["key"]
        if key.startswith(OPEN_TICKET_PREFIX):
            lines.append(f"- 工单 {key[len(OPEN_TICKET_PREFIX):]} 处理中：{item['content']}")
        elif key.startswith(COMMITMENT_PREFIX):
            lines.append(f"- 承诺待兑现：{item['content']}")
    return f"{EVENT_HEADER}\n" + "\n".join(lines) if lines else None


def render_flag_injection(flags):
    """Render the flag section (FR-2), placed ahead of the profile section
    (R15). ``None`` means "inject nothing" — with no flags the message body
    stays byte-identical to Phase 3. A blacklist flag appends the fixed
    instruction line.
    """
    if not flags:
        return None
    lines = [f"- {FLAG_LABELS.get(flag['key'], flag['key'])}：{flag['content']}" for flag in flags]
    if any(flag["key"] == "blacklist" for flag in flags):
        lines.append(f"- {BLACKLIST_INSTRUCTION}")
    return f"{FLAG_HEADER}\n" + "\n".join(lines)


# L3 reference signal (R11): an injected item counts as referenced when its
# key/content shares a token with the current user input. Latin/digit words
# plus CJK bigrams keep the tokenizer language-neutral and unit-testable.
_WORD_TOKEN = re.compile(r"[A-Za-z0-9_]+")
_CJK_CHAR = re.compile(r"[一-鿿]")


def text_tokens(text):
    """Token set for overlap detection: lowercase words plus CJK bigrams."""
    if not text:
        return set()
    tokens = {word.lower() for word in _WORD_TOKEN.findall(text)}
    cjk = "".join(_CJK_CHAR.findall(text))
    tokens.update(cjk[i : i + 2] for i in range(len(cjk) - 1))
    if len(cjk) == 1:
        tokens.add(cjk)
    return tokens


def overlap_ids(items, input_text):
    """Ids of injected items whose key/content overlaps the user input (L3)."""
    query = text_tokens(input_text)
    if not query:
        return []
    return [
        item["id"]
        for item in items
        if item.get("id") and text_tokens(f"{item['key']} {item['content']}") & query
    ]


class MemoryService:
    def __init__(self, engine, settings=None):
        self.engine, self.settings = engine, settings

    def _masking_enabled(self):
        # Default-on when no settings object is wired (safe direction: the
        # original value never persists).
        return getattr(self.settings, "memory_pii_masking_enabled", True)

    def _decay_enabled(self):
        return getattr(self.settings, "memory_decay_enabled", False)

    async def resolve_customer(self, c, tenant, source, external_key):
        """Resolve (or create) the customer for one external identity (P2-UT-01/02/03).

        Runs inside the caller's transaction so the conversation and its
        customer link commit atomically. Concurrent first-time resolutions
        race on the identity unique key: ``ON CONFLICT DO NOTHING`` never
        aborts the transaction, and the loser re-reads the winner's row.
        """
        row = await one(
            c,
            """SELECT customer_id FROM memory_identities
            WHERE tenant_id=:t AND source=:s AND external_key=:k""",
            t=tenant,
            s=source,
            k=external_key,
        )
        if row:
            return row["customer_id"]
        customer = await one(
            c,
            "INSERT INTO memory_customers(id,tenant_id) VALUES(:id,:t) RETURNING id",
            id=uuid4(),
            t=tenant,
        )
        inserted = await one(
            c,
            """INSERT INTO memory_identities(id,tenant_id,customer_id,source,external_key)
            VALUES(:id,:t,:cid,:s,:k)
            ON CONFLICT (tenant_id,source,external_key) DO NOTHING RETURNING customer_id""",
            id=uuid4(),
            t=tenant,
            cid=customer["id"],
            s=source,
            k=external_key,
        )
        if inserted:
            return customer["id"]
        # Lost the race: the winner committed (or rolled back, in which case
        # our insert would have succeeded). The row must exist now.
        row = await one(
            c,
            """SELECT customer_id FROM memory_identities
            WHERE tenant_id=:t AND source=:s AND external_key=:k""",
            t=tenant,
            s=source,
            k=external_key,
        )
        if not row:
            return customer["id"]
        # Our customer row never got an identity; remove the orphan.
        await execute(
            c,
            "DELETE FROM memory_customers WHERE id=:id",
            id=customer["id"],
        )
        return row["customer_id"]

    async def active_items(self, tenant, customer_id, types=("profile", "preference"), limit=20):
        """Active items for injection, newest first, count-capped by the caller.

        Phase 4 (R7): with decay enabled the profile/preference rows also
        pass the inline effective-confidence threshold; event search never
        gets this filter. With decay off the query is exactly the Phase 3
        one.
        """
        clause, decay_params = "", {}
        if self._decay_enabled():
            clause, decay_params = injection_filter_sql(self.settings)
        async with self.engine.connect() as c:
            return await many(
                c,
                """SELECT id,customer_id,type,key,content,confidence,status,source_type,source_ref,
                prev_content,revision,updated_at FROM memory_items
                WHERE tenant_id=:t AND customer_id=:c AND status='active' AND type=ANY(:types)"""
                + clause
                + " ORDER BY updated_at DESC,id DESC LIMIT :lim",
                t=tenant,
                c=customer_id,
                types=list(types),
                lim=limit,
                **decay_params,
            )

    async def active_flags(self, tenant, customer_id, limit):
        """Active flag items for injection (FR-2), newest first."""
        async with self.engine.connect() as c:
            return await many(
                c,
                """SELECT id,customer_id,type,key,content,source_type,source_ref,updated_at
                FROM memory_items
                WHERE tenant_id=:t AND customer_id=:c AND type='flag' AND status='active'
                ORDER BY updated_at DESC,id DESC LIMIT :lim""",
                t=tenant,
                c=customer_id,
                lim=limit,
            )

    async def injection_items(self, tenant, customer_id, max_items, max_tokens=INJECT_MAX_TOKENS):
        """Service-layer trimming: count and token budgets applied here (FR-3)."""
        items = await self.active_items(tenant, customer_id, limit=max_items)
        return trim_items(items, max_items, max_tokens)

    # ------------------------------------------------------------------
    # Phase 3 event items (FR-1/FR-3/FR-4). Event rows are written by the
    # event_write worker, never by the synchronous ticket path.
    # ------------------------------------------------------------------

    async def add_event_item(
        self, c, tenant, customer_id, key, content, source_ref, expires_at=None,
        clear_embedding=False, pii_hits=None,
    ):
        """Insert or overwrite one event item inside the caller's transaction.

        Same-key upsert semantics mirror extraction: the previous content is
        stashed in ``prev_content`` and human-maintained or deleted rows absorb
        the write (returns ``None``) so staff corrections are never clobbered.
        With ``clear_embedding`` (vector mode only — the column does not exist
        otherwise) a content overwrite also clears the stale embedding so the
        follow-up ``embed_and_save`` regenerates it instead of ranking on the
        previous content's vector.

        Phase 4 (R6): event content is PII-masked here — callers summarize
        first, then this masks, so a truncated half-number cannot slip past
        the patterns. When ``pii_hits`` (a list) is passed, the mask hit
        count is appended so the caller can fold it into its audit details.
        """
        if self._masking_enabled():
            content, hits = mask(content)
            if pii_hits is not None:
                pii_hits.append(hits)
        params = {
            "t": tenant,
            "c": customer_id,
            "key": key,
            "content": content,
            "ref": source_ref,
            "exp": expires_at,
        }
        row = await one(
            c,
            """UPDATE memory_items SET content=:content,prev_content=content,
            confidence=1.0,status='active',source_type='ticket',source_ref=:ref,
            expires_at=:exp,revision=revision+1,last_referenced_at=now(),updated_at=now()
            """
            + (",embedding=NULL,embedding_failed=false\n            " if clear_embedding else "")
            + """WHERE tenant_id=:t AND customer_id=:c AND type='event' AND key=:key
            AND source_type<>'human' AND status<>'deleted' RETURNING id""",
            **params,
        )
        if not row:
            row = await one(
                c,
                """INSERT INTO memory_items(tenant_id,customer_id,type,key,content,confidence,
                status,source_type,source_ref,expires_at)
                VALUES(:t,:c,'event',:key,:content,1.0,'active','ticket',:ref,:exp)
                ON CONFLICT (tenant_id,customer_id,type,key) DO NOTHING RETURNING id""",
                **params,
            )
        return row["id"] if row else None

    async def expire_open_ticket(self, c, tenant, customer_id, ticket_no):
        """Soft-expire the in-flight injection row for one ticket (AC-6).

        The row is kept (not deleted) so the audit trail survives; returns the
        expired row id or ``None`` when there was nothing to expire.
        """
        row = await one(
            c,
            """UPDATE memory_items SET status='expired',updated_at=now()
            WHERE tenant_id=:t AND customer_id=:c AND type='event' AND key=:key
            AND status='active' RETURNING id""",
            t=tenant,
            c=customer_id,
            key=OPEN_TICKET_PREFIX + ticket_no,
        )
        return row["id"] if row else None

    async def pending_event_items(self, tenant, customer_id, limit):
        """In-flight injection items: open tickets and pending commitments,
        newest first (FR-3)."""
        async with self.engine.connect() as c:
            return await many(
                c,
                """SELECT id,type,key,content,updated_at FROM memory_items
                WHERE tenant_id=:t AND customer_id=:c AND type='event' AND status='active'
                AND (key LIKE 'open_ticket:%' OR key LIKE 'commitment:%')
                ORDER BY updated_at DESC,id DESC LIMIT :lim""",
                t=tenant,
                c=customer_id,
                lim=limit,
            )

    async def _upsert_suggestion(self, c, tenant, customer_id, op, content, source_ref):
        """Park an extraction update as a pending suggestion row (FR-1, R4).

        Human-maintained rows are never overwritten by the model; the update
        becomes a ``<key>:suggested`` pending item instead, upserted so a
        repeated extraction refreshes the same suggestion in place (the
        same-key unique constraint keeps it idempotent). A previously
        rejected (deleted) suggestion is not resurrected.
        """
        suggested_key = op["key"][:SUGGESTED_BASE_MAX] + SUGGESTED_SUFFIX
        params = {
            "t": tenant,
            "c": customer_id,
            "type": op["type"],
            "key": suggested_key,
            "content": content,
            "confidence": op["confidence"],
            "ref": source_ref,
        }
        row = await one(
            c,
            """UPDATE memory_items SET content=:content,confidence=:confidence,
            source_type='extracted',source_ref=:ref,updated_at=now()
            WHERE tenant_id=:t AND customer_id=:c AND type=:type AND key=:key
            AND status='pending' RETURNING id""",
            **params,
        )
        if not row:
            row = await one(
                c,
                """INSERT INTO memory_items(tenant_id,customer_id,type,key,content,confidence,
                status,source_type,source_ref)
                VALUES(:t,:c,:type,:key,:content,:confidence,'pending','extracted',:ref)
                ON CONFLICT (tenant_id,customer_id,type,key) DO NOTHING RETURNING id""",
                **params,
            )
        return row

    async def apply_extracted(self, c, tenant, customer_id, ops, source_ref, auto, pending):
        """Apply validated extraction ops inside the caller's transaction.

        - op=add never overwrites: the same-key unique constraint absorbs
          duplicates (P2-UT-09).
        - op=update overwrites content, stashing the old value in
          ``prev_content`` and bumping ``revision``; the shared-key mention
          also refreshes ``last_referenced_at`` (P2-UT-08).
        - Human-edited or deleted rows are protected from extraction updates:
          a human correction must never be silently clobbered by the model.
          Phase 4 (FR-1, R4): an update against a protected row is parked as
          a pending ``:suggested`` item instead of vanishing.
        - Phase 4 (R5): content is PII-masked before it persists; the batch
          hit count rides back in ``counts['pii_masked']`` for the audit.
        """
        counts = {"added": 0, "updated": 0, "pending": 0, "dropped": 0, "noop": 0, "pii_masked": 0}
        for op in ops:
            if op["op"] == "noop":
                counts["noop"] += 1
                continue
            # Defence in depth (P0): only profile/preference may ever be
            # written by extraction; validate_item already enforces this.
            if op["type"] not in ("profile", "preference"):
                counts["dropped"] += 1
                continue
            status = route_confidence(op["confidence"], auto, pending)
            if status is None:
                counts["dropped"] += 1
                continue
            content = op["content"]
            if self._masking_enabled():
                content, hits = mask(content)
                counts["pii_masked"] += hits
            params = {
                "t": tenant,
                "c": customer_id,
                "type": op["type"],
                "key": op["key"],
                "content": content,
                "confidence": op["confidence"],
                "status": status,
                "ref": source_ref,
            }
            if op["op"] == "add":
                row = await one(
                    c,
                    """INSERT INTO memory_items(tenant_id,customer_id,type,key,content,confidence,
                    status,source_type,source_ref)
                    VALUES(:t,:c,:type,:key,:content,:confidence,:status,'extracted',:ref)
                    ON CONFLICT (tenant_id,customer_id,type,key) DO NOTHING RETURNING id""",
                    **params,
                )
            else:  # update: overwrite, or insert when the key is new
                existing = await one(
                    c,
                    """SELECT id,protected,status FROM memory_items
                    WHERE tenant_id=:t AND customer_id=:c AND type=:type AND key=:key""",
                    **{k: params[k] for k in ("t", "c", "type", "key")},
                )
                if existing and existing["protected"] and existing["status"] != "deleted":
                    row = await self._upsert_suggestion(
                        c, tenant, customer_id, op, content, source_ref
                    )
                    counts["pending" if row else "noop"] += 1
                    continue
                row = await one(
                    c,
                    """UPDATE memory_items SET content=:content,prev_content=content,
                    confidence=:confidence,status=:status,source_type='extracted',source_ref=:ref,
                    revision=revision+1,last_referenced_at=now(),updated_at=now()
                    WHERE tenant_id=:t AND customer_id=:c AND type=:type AND key=:key
                    AND source_type<>'human' AND status<>'deleted' RETURNING id""",
                    **params,
                )
                if row:
                    counts["updated"] += 1
                    continue
                row = await one(
                    c,
                    """INSERT INTO memory_items(tenant_id,customer_id,type,key,content,confidence,
                    status,source_type,source_ref)
                    VALUES(:t,:c,:type,:key,:content,:confidence,:status,'extracted',:ref)
                    ON CONFLICT (tenant_id,customer_id,type,key) DO NOTHING RETURNING id""",
                    **params,
                )
            if row:
                counts["added" if status == "active" else "pending"] += 1
            else:  # conflict on add, or a human/deleted row absorbed the update
                counts["noop"] += 1
        return counts

    # ------------------------------------------------------------------
    # Console management (FR-4). Every query is tenant-scoped; mutations
    # write audit records without memory content (keys and counts only).
    # ------------------------------------------------------------------

    async def list_customers(self, tenant, q="", page=1, page_size=20):
        page = max(page, 1)
        page_size = max(1, min(page_size, 100))
        where = "c.tenant_id=:t"
        params = {"t": tenant, "lim": page_size, "off": (page - 1) * page_size}
        if q:
            where += """ AND EXISTS (SELECT 1 FROM memory_identities x
                WHERE x.customer_id=c.id AND x.tenant_id=c.tenant_id AND x.external_key ILIKE :pattern)"""
            params["pattern"] = "%" + q + "%"
        async with self.engine.connect() as c:
            total = await one(
                c,
                "SELECT count(*) AS total FROM memory_customers c WHERE " + where,
                **{k: v for k, v in params.items() if k not in ("lim", "off")},
            )
            items = await many(
                c,
                """SELECT c.id,c.created_at,c.updated_at,
                (SELECT COALESCE(array_agg(i.external_key ORDER BY i.created_at),'{}')
                 FROM memory_identities i WHERE i.customer_id=c.id AND i.tenant_id=c.tenant_id
                ) AS external_keys,
                (SELECT count(*)::int FROM memory_items m
                 WHERE m.customer_id=c.id AND m.tenant_id=c.tenant_id AND m.status<>'deleted'
                ) AS item_count,
                (SELECT max(m.updated_at) FROM memory_items m
                 WHERE m.customer_id=c.id AND m.tenant_id=c.tenant_id AND m.status<>'deleted'
                ) AS last_active_at
                FROM memory_customers c WHERE """
                + where
                + " ORDER BY c.updated_at DESC,c.id DESC LIMIT :lim OFFSET :off",
                **params,
            )
            return {
                "items": items,
                "total": total["total"],
                "page": page,
                "page_size": page_size,
            }

    async def customer_detail(self, tenant, customer_id):
        async with self.engine.connect() as c:
            customer = required(
                await one(
                    c,
                    "SELECT id,created_at,updated_at FROM memory_customers WHERE id=:id AND tenant_id=:t",
                    id=customer_id,
                    t=tenant,
                ),
                "memory_customer_not_found",
            )
            customer["identities"] = await many(
                c,
                """SELECT id,source,external_key,created_at FROM memory_identities
                WHERE customer_id=:id AND tenant_id=:t ORDER BY created_at""",
                id=customer_id,
                t=tenant,
            )
            customer["items"] = await many(
                c,
                """SELECT id,customer_id,type,key,content,confidence,status,source_type,source_ref,
                prev_content,revision,protected,last_referenced_at,reference_count,updated_at
                FROM memory_items
                WHERE customer_id=:id AND tenant_id=:t ORDER BY type,key""",
                id=customer_id,
                t=tenant,
            )
            # Phase 4 (FR-6.4): the console shows the live decayed score; the
            # formula matches the injection filter and the daily migration.
            table = half_lives(self.settings) if self.settings else DEFAULT_HALF_LIVES
            now = datetime.now(UTC)
            for item in customer["items"]:
                item["effective_confidence"] = round(
                    effective_confidence(
                        item["confidence"],
                        item["type"],
                        item["protected"],
                        item["last_referenced_at"],
                        now,
                        table,
                    ),
                    6,
                )
            return customer

    async def pending_items(self, tenant, page=1, page_size=20):
        page = max(page, 1)
        page_size = max(1, min(page_size, 100))
        async with self.engine.connect() as c:
            total = await one(
                c,
                "SELECT count(*) AS total FROM memory_items WHERE tenant_id=:t AND status='pending'",
                t=tenant,
            )
            items = await many(
                c,
                """SELECT m.id,m.customer_id,m.type,m.key,m.content,m.confidence,m.status,
                m.source_type,m.source_ref,m.prev_content,m.revision,m.updated_at,
                (SELECT COALESCE(array_agg(i.external_key ORDER BY i.created_at),'{}')
                 FROM memory_identities i WHERE i.customer_id=m.customer_id AND i.tenant_id=m.tenant_id
                ) AS customer_external_keys
                FROM memory_items m WHERE m.tenant_id=:t AND m.status='pending'
                ORDER BY m.updated_at DESC,m.id DESC LIMIT :lim OFFSET :off""",
                t=tenant,
                lim=page_size,
                off=(page - 1) * page_size,
            )
            return {
                "items": items,
                "total": total["total"],
                "page": page,
                "page_size": page_size,
            }

    async def _item(self, c, tenant, item_id, lock=False):
        return required(
            await one(
                c,
                "SELECT * FROM memory_items WHERE id=:id AND tenant_id=:t"
                + (" FOR UPDATE" if lock else ""),
                id=item_id,
                t=tenant,
            ),
            "memory_item_not_found",
        )

    async def edit_item(self, tenant, actor, item_id, content):
        """Human edit: content replaced, previous value kept in prev_content.

        Phase 4 (R5/R18): the edit goes through PII masking (hit count in the
        audit details) and marks the row ``protected`` alongside
        ``source_type='human'``, keeping the two in lockstep.
        """
        pii_hits = 0
        if self._masking_enabled():
            content, pii_hits = mask(content)
        async with self.engine.begin() as c:
            item = await self._item(c, tenant, item_id, lock=True)
            if item["status"] == "deleted":
                raise DomainError("memory_item_deleted", 409)
            row = await one(
                c,
                """UPDATE memory_items SET content=:content,prev_content=content,
                source_type='human',protected=true,confidence=1.0,source_ref=:actor,
                revision=revision+1,updated_at=now()
                WHERE id=:id AND tenant_id=:t RETURNING *""",
                id=item_id,
                t=tenant,
                content=content,
                actor=actor,
            )
            await audit(
                c,
                tenant,
                actor,
                "memory.item.edited",
                item_id,
                customer_id=str(item["customer_id"]),
                type=item["type"],
                key=item["key"],
                pii_masked=pii_hits,
            )
            return row

    async def release_item(self, tenant, actor, item_id):
        """Release a protected row back to automatic management (FR-1).

        The row keeps its content and status; only the ownership markers flip
        (``protected=false, source_type='extracted'`` — R18's invariant), so
        extraction updates apply to it again and decay treats it normally.
        """
        async with self.engine.begin() as c:
            item = await self._item(c, tenant, item_id, lock=True)
            if item["status"] == "deleted":
                raise DomainError("memory_item_deleted", 409)
            if not item["protected"]:
                raise DomainError("memory_item_not_protected", 409)
            row = await one(
                c,
                """UPDATE memory_items SET protected=false,source_type='extracted',
                updated_at=now() WHERE id=:id AND tenant_id=:t RETURNING *""",
                id=item_id,
                t=tenant,
            )
            await audit(
                c,
                tenant,
                actor,
                "memory.item.released",
                item_id,
                customer_id=str(item["customer_id"]),
                type=item["type"],
                key=item["key"],
            )
            return row

    async def _set_status(
        self, tenant, actor, item_id, action, allowed, target, conflict, repeat_ok=False,
        refresh_on=(),
    ):
        async with self.engine.begin() as c:
            item = await self._item(c, tenant, item_id, lock=True)
            if item["status"] == target:
                # Only deletion may be safely retried; repeating a restore or a
                # pending-queue decision is a conflict, not a no-op.
                if repeat_ok:
                    return item
                raise DomainError(conflict, 409)
            if item["status"] not in allowed:
                raise DomainError(conflict, 409)
            row = await one(
                c,
                "UPDATE memory_items SET status=:status,updated_at=now()"
                # R19: reviving an expired row resets its reference clock so
                # the daily pass does not immediately re-expire it.
                + (",last_referenced_at=now()" if item["status"] in refresh_on else "")
                + " WHERE id=:id AND tenant_id=:t RETURNING *",
                id=item_id,
                t=tenant,
                status=target,
            )
            await audit(
                c,
                tenant,
                actor,
                action,
                item_id,
                customer_id=str(item["customer_id"]),
                type=item["type"],
                key=item["key"],
                old_status=item["status"],
                new_status=target,
            )
            return row

    async def delete_item(self, tenant, actor, item_id):
        return await self._set_status(
            tenant,
            actor,
            item_id,
            "memory.item.deleted",
            {"pending", "active"},
            "deleted",
            "memory_item_not_active",
            repeat_ok=True,
        )

    async def restore_item(self, tenant, actor, item_id):
        # R19: expired rows restore to active as well, with a fresh clock.
        return await self._set_status(
            tenant,
            actor,
            item_id,
            "memory.item.restored",
            {"deleted", "expired"},
            "active",
            "memory_item_not_deleted",
            refresh_on={"expired"},
        )

    async def confirm_item(self, tenant, actor, item_id):
        """Confirm a pending item; refreshes the reference clock (R19).

        R4 merge semantics: confirming a ``:suggested`` row applies the
        suggested content onto the human-maintained base row (prev_content
        stashed, revision bumped, ownership untouched) and physically removes
        the suggestion. When the base row no longer exists the confirm
        degrades to an ordinary activation.
        """
        async with self.engine.begin() as c:
            item = await self._item(c, tenant, item_id, lock=True)
            if item["status"] != "pending":
                raise DomainError("memory_item_not_pending", 409)
            if item["key"].endswith(SUGGESTED_SUFFIX):
                base_key = item["key"][: -len(SUGGESTED_SUFFIX)]
                base = await one(
                    c,
                    """SELECT * FROM memory_items
                    WHERE tenant_id=:t AND customer_id=:c AND type=:type AND key=:key
                    AND status<>'deleted' FOR UPDATE""",
                    t=tenant,
                    c=item["customer_id"],
                    type=item["type"],
                    key=base_key,
                )
                if base:
                    row = await one(
                        c,
                        """UPDATE memory_items SET content=:content,prev_content=:prev,
                        revision=revision+1,last_referenced_at=now(),updated_at=now()
                        WHERE id=:id RETURNING *""",
                        id=base["id"],
                        content=item["content"],
                        prev=base["content"],
                    )
                    await execute(c, "DELETE FROM memory_items WHERE id=:id", id=item["id"])
                    await audit(
                        c,
                        tenant,
                        actor,
                        "memory.item.confirmed",
                        base["id"],
                        customer_id=str(item["customer_id"]),
                        type=item["type"],
                        key=base_key,
                        old_status="pending",
                        new_status="active",
                        merged_from=str(item["id"]),
                    )
                    return row
            row = await one(
                c,
                """UPDATE memory_items SET status='active',last_referenced_at=now(),
                updated_at=now() WHERE id=:id AND tenant_id=:t RETURNING *""",
                id=item_id,
                t=tenant,
            )
            await audit(
                c,
                tenant,
                actor,
                "memory.item.confirmed",
                item_id,
                customer_id=str(item["customer_id"]),
                type=item["type"],
                key=item["key"],
                old_status="pending",
                new_status="active",
            )
            return row

    async def reject_item(self, tenant, actor, item_id):
        return await self._set_status(
            tenant,
            actor,
            item_id,
            "memory.item.rejected",
            {"pending"},
            "deleted",
            "memory_item_not_pending",
        )

    # ------------------------------------------------------------------
    # Phase 4 governance (FR-2/FR-4/FR-5). Thin wrappers owning the
    # transaction boundary; the logic lives in governance.py.
    # ------------------------------------------------------------------

    async def upsert_flag(self, tenant, actor, customer_id, key, content):
        """Create or update one human-sourced flag (FR-2)."""
        async with self.engine.begin() as c:
            required(
                await one(
                    c,
                    "SELECT id FROM memory_customers WHERE id=:id AND tenant_id=:t",
                    id=customer_id,
                    t=tenant,
                ),
                "memory_customer_not_found",
            )
            flag_id = await governance.upsert_flag(
                c,
                tenant,
                customer_id,
                key,
                content,
                "human",
                source_ref=actor,
                actor=actor,
                settings=self.settings,
            )
            return {"id": str(flag_id)}

    async def remove_flag(self, tenant, actor, customer_id, key):
        return await governance.remove_flag(self.engine, tenant, actor, customer_id, key)

    async def delete_customer(self, tenant, actor, customer_id, store=None):
        return await governance.delete_customer(
            self.engine, tenant, actor, customer_id, store=store
        )

    async def merge_customers(self, tenant, actor, source_id, target_id):
        return await governance.merge_customers(self.engine, tenant, actor, source_id, target_id)
