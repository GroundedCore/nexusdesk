"""Phase 4 memory governance: flags, customer deletion/merge, built-in rules.

Flags (FR-2) are memory items with ``type='flag'`` and a controlled key
vocabulary (:data:`FLAG_KEYS`, enforced again by the 0037 CHECK constraint).
Only ``human`` and ``rule`` sources may write flags — an ``extracted`` write
is a hard error, defence in depth on top of the extractor's output filter
(the P0 "flag via extraction" red line). A rule upsert never clobbers a
human-maintained flag, mirroring the extraction protection precedent.

Deletion and merge (FR-4/FR-5) follow the R16 ordering: explicit child-row
deletes first (so the audit carries real counts), conversation links
re-pointed or nulled, then the customer row itself; the FK cascades stay as
the backstop. Audit payloads carry operators, keys and counts only — never
customer content (C2/C4).
"""

import logging
from uuid import UUID

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

# Controlled flag vocabulary (FR-2); the 0037 CHECK constraint repeats this.
FLAG_KEYS = frozenset({"vip", "complaint_risk", "blacklist"})
FLAG_SOURCES = frozenset({"human", "rule"})

# Built-in rule (R12/R14): tickets past due_at without a response flag the
# customer as a complaint risk; 30 days without an overdue ticket lifts the
# rule-sourced flag again (human flags are never touched by the rule).
RULE_FLAG_KEY = "complaint_risk"
RULE_CLEAR_DAYS = 30


def _masking_enabled(settings):
    return getattr(settings, "memory_pii_masking_enabled", True)


async def upsert_flag(
    c, tenant, customer_id, key, content, source_type, source_ref=None, actor="memory", settings=None
):
    """Insert or refresh one flag inside the caller's transaction.

    Returns the flag row id on a real write, or ``None`` when nothing
    changed: a human-maintained flag absorbing a rule write, or an
    idempotent retry whose masked content, source type and source_ref all
    match the active row (D5 — callers counting "new writes", like the
    rule scan's ``marked``, must not count no-ops). Flag notes are free
    text written by staff, so they go through the same PII masking as
    every other write point (R5); the hit count rides in the audit
    details.
    """
    if key not in FLAG_KEYS:
        raise DomainError("memory_flag_key_invalid")
    if source_type not in FLAG_SOURCES:
        # 'extracted' lands here: flags must never come from the model (P0).
        raise DomainError("memory_flag_source_invalid")
    if not isinstance(content, str) or not content.strip():
        raise DomainError("memory_flag_content_required")
    content = content.strip()
    pii_hits = 0
    if _masking_enabled(settings):
        content, pii_hits = mask(content)
    protected = source_type == "human"
    existing = await one(
        c,
        """SELECT id,status,source_type,content,source_ref FROM memory_items
        WHERE tenant_id=:t AND customer_id=:c AND type='flag' AND key=:key""",
        t=tenant,
        c=customer_id,
        key=key,
    )
    if (
        existing
        and existing["status"] == "active"
        and existing["source_type"] == source_type
        and existing["content"] == content
        and (existing["source_ref"] or None) == (source_ref or None)
    ):
        # Idempotent write (QA D3): the rule scan re-runs every interval, so
        # an upsert that would change nothing must not bump the revision or
        # append yet another memory.flag.upserted audit row. None signals
        # the no-op to counting callers (D5).
        return None
    params = {
        "t": tenant,
        "c": customer_id,
        "key": key,
        "content": content,
        "st": source_type,
        "prot": protected,
        "ref": source_ref,
    }
    row = await one(
        c,
        """UPDATE memory_items SET content=:content,prev_content=content,
        confidence=1.0,status='active',source_type=:st,protected=:prot,source_ref=:ref,
        revision=revision+1,updated_at=now()
        WHERE tenant_id=:t AND customer_id=:c AND type='flag' AND key=:key
        AND (source_type<>'human' OR :st='human') RETURNING id""",
        **params,
    )
    if not row:
        row = await one(
            c,
            """INSERT INTO memory_items(tenant_id,customer_id,type,key,content,confidence,
            status,source_type,protected,source_ref)
            VALUES(:t,:c,'flag',:key,:content,1.0,'active',:st,:prot,:ref)
            ON CONFLICT (tenant_id,customer_id,type,key) DO NOTHING RETURNING id""",
            **params,
        )
    if not row:
        # A human flag absorbed a rule write; nothing changed, nothing audited.
        return None
    await audit(
        c,
        tenant,
        actor,
        "memory.flag.upserted",
        row["id"],
        customer_id=str(customer_id),
        key=key,
        source_type=source_type,
        source_ref=source_ref,
        pii_masked=pii_hits,
    )
    return row["id"]


async def remove_flag(engine, tenant, actor, customer_id, key, source="human"):
    """Soft-delete one active flag; audit with the removal source (R17).

    Human removal lifts any active flag; rule removal (the built-in scan's
    auto-clear) only ever touches rule-sourced rows.
    """
    if key not in FLAG_KEYS:
        raise DomainError("memory_flag_key_invalid")
    if source not in FLAG_SOURCES:
        raise DomainError("memory_flag_source_invalid")
    async with engine.begin() as c:
        row = await one(
            c,
            """UPDATE memory_items SET status='deleted',updated_at=now()
            WHERE tenant_id=:t AND customer_id=:c AND type='flag' AND key=:key
            AND status='active' AND (:source='human' OR source_type='rule')
            RETURNING id,source_type""",
            t=tenant,
            c=customer_id,
            key=key,
            source=source,
        )
        if not row:
            raise DomainError("memory_flag_not_found", 404)
        await audit(
            c,
            tenant,
            actor,
            "memory.flag.removed",
            row["id"],
            customer_id=str(customer_id),
            key=key,
            source=source,
            flag_source_type=row["source_type"],
        )
        return row["id"]


async def delete_customer(engine, tenant, actor, customer_id, store=None):
    """Delete one customer and every memory row, R16 ordering (FR-4).

    Items and identities go first so the audit carries real counts;
    conversations keep their history with a NULL customer link; tickets keep
    the plain-string customer reference (the console renders "客户已删除").
    The store hook is a no-op on both current implementations (pgvector rows
    disappear with their row); a future Milvus store deletes collection data
    there.
    """
    async with engine.begin() as c:
        required(
            await one(
                c,
                "SELECT id FROM memory_customers WHERE id=:id AND tenant_id=:t FOR UPDATE",
                id=customer_id,
                t=tenant,
            ),
            "memory_customer_not_found",
        )
        items = await many(
            c,
            "DELETE FROM memory_items WHERE tenant_id=:t AND customer_id=:c RETURNING id",
            t=tenant,
            c=customer_id,
        )
        identities = await many(
            c,
            "DELETE FROM memory_identities WHERE tenant_id=:t AND customer_id=:c RETURNING id",
            t=tenant,
            c=customer_id,
        )
        conversations = await many(
            c,
            """UPDATE runtime_conversations SET customer_id=NULL
            WHERE tenant_id=:t AND customer_id=:c RETURNING id""",
            t=tenant,
            c=customer_id,
        )
        await execute(
            c,
            "DELETE FROM memory_customers WHERE id=:id AND tenant_id=:t",
            id=customer_id,
            t=tenant,
        )
        await audit(
            c,
            tenant,
            actor,
            "memory.customer.deleted",
            customer_id,
            items_deleted=len(items),
            identities_deleted=len(identities),
            conversations_unlinked=len(conversations),
        )
    if store is not None:
        await store.delete_customer(customer_id)
    return {
        "items_deleted": len(items),
        "identities_deleted": len(identities),
        "conversations_unlinked": len(conversations),
    }


def _keep_source(source_item, target_item):
    """Conflict matrix (03 section 6): protected first, then the newer row."""
    if source_item["protected"] != target_item["protected"]:
        return source_item["protected"]
    return source_item["updated_at"] > target_item["updated_at"]


async def merge_customers(engine, tenant, actor, source_id, target_id):
    """Merge ``source_id`` into ``target_id`` (FR-5, R16/R22).

    Identities re-point (their unique key is per external key, not per
    customer, so re-pointing cannot collide); active/pending items move
    one-by-one with the conflict matrix applied when the target already holds
    the same (type, key) — the loser is physically deleted and listed in the
    audit. Source rows already deleted/expired stay put and disappear with
    the source customer's cascade. Conversations re-point so the source row
    can be deleted under the plain FK; tickets (VARCHAR link, no FK) re-point
    in the same transaction (R22) so their memory entry point, lifecycle
    events and the rule scan follow the surviving customer.
    """
    if str(source_id) == str(target_id):
        raise DomainError("memory_merge_same_customer")
    async with engine.begin() as c:
        required(
            await one(
                c,
                "SELECT id FROM memory_customers WHERE id=:id AND tenant_id=:t FOR UPDATE",
                id=source_id,
                t=tenant,
            ),
            "memory_customer_not_found",
        )
        required(
            await one(
                c,
                "SELECT id FROM memory_customers WHERE id=:id AND tenant_id=:t FOR UPDATE",
                id=target_id,
                t=tenant,
            ),
            "memory_customer_not_found",
        )
        identities = await many(
            c,
            """UPDATE memory_identities SET customer_id=:target
            WHERE tenant_id=:t AND customer_id=:source RETURNING id""",
            target=target_id,
            t=tenant,
            source=source_id,
        )
        source_items = await many(
            c,
            """SELECT id,type,key,protected,status,updated_at FROM memory_items
            WHERE tenant_id=:t AND customer_id=:source AND status IN ('active','pending')
            ORDER BY updated_at,id""",
            t=tenant,
            source=source_id,
        )
        moved, conflicts = 0, []
        for item in source_items:
            conflict = await one(
                c,
                """SELECT id,protected,updated_at FROM memory_items
                WHERE tenant_id=:t AND customer_id=:target AND type=:type AND key=:key""",
                t=tenant,
                target=target_id,
                type=item["type"],
                key=item["key"],
            )
            if conflict:
                keep_source = _keep_source(item, conflict)
                loser = conflict["id"] if keep_source else item["id"]
                await execute(c, "DELETE FROM memory_items WHERE id=:id", id=loser)
                conflicts.append(
                    {
                        "type": item["type"],
                        "key": item["key"],
                        "kept": "source" if keep_source else "target",
                    }
                )
                if not keep_source:
                    continue
            await execute(
                c,
                "UPDATE memory_items SET customer_id=:target WHERE id=:id",
                target=target_id,
                id=item["id"],
            )
            moved += 1
        conversations = await many(
            c,
            """UPDATE runtime_conversations SET customer_id=:target
            WHERE tenant_id=:t AND customer_id=:source RETURNING id""",
            target=target_id,
            t=tenant,
            source=source_id,
        )
        # R22: merge re-points the source's tickets (VARCHAR, no FK, so a
        # plain string compare in the str(UUID) shape) — otherwise the
        # surviving customer would look "deleted" on old tickets, their
        # lifecycle events would be silently skipped by the writer's
        # customer-exists check, and the rule scan would ignore them.
        # delete_customer deliberately keeps tickets.customer_id untouched:
        # the "客户已删除" degradation is the right semantics there (R16).
        tickets = await many(
            c,
            """UPDATE tickets SET customer_id=:target
            WHERE tenant_id=:t AND customer_id=:source RETURNING id""",
            target=str(target_id),
            t=tenant,
            source=str(source_id),
        )
        await execute(
            c,
            "DELETE FROM memory_customers WHERE id=:id AND tenant_id=:t",
            id=source_id,
            t=tenant,
        )
        await audit(
            c,
            tenant,
            actor,
            "memory.customer.merged",
            target_id,
            source_customer_id=str(source_id),
            moved=moved,
            conflicts=conflicts,
            identities_moved=len(identities),
            conversations_moved=len(conversations),
            tickets_relinked=len(tickets),
        )
        return {
            "moved": moved,
            "conflicts": conflicts,
            "identities_moved": len(identities),
            "conversations_moved": len(conversations),
            "tickets_relinked": len(tickets),
        }


async def scan_overdue_tickets(engine, settings):
    """One built-in rule pass (R12/R14): flag overdue-ticket customers.

    Marking: any customer with at least one ticket past ``due_at`` still
    open/in_progress gets a rule-sourced ``complaint_risk`` flag (a human
    flag on the same key is left alone). Clearing: a rule-sourced flag is
    lifted once the customer has no currently overdue ticket and no ticket
    whose due date passed within the last 30 days. Single-tenant, idempotent,
    safe for concurrent replicas.
    """
    tenant = settings.tenant_id
    marked = 0
    async with engine.begin() as c:
        overdue = await many(
            c,
            """SELECT customer_id, count(*)::int AS overdue_count,
            (array_agg(id ORDER BY due_at))[1] AS oldest_ticket
            FROM tickets
            WHERE tenant_id=:t AND due_at<now() AND status IN ('open','in_progress')
            AND customer_id IS NOT NULL
            GROUP BY customer_id""",
            t=tenant,
        )
        for row in overdue:
            try:
                customer_id = UUID(str(row["customer_id"]))
            except ValueError:
                continue
            exists = await one(
                c,
                "SELECT 1 FROM memory_customers WHERE id=:id AND tenant_id=:t",
                id=customer_id,
                t=tenant,
            )
            if not exists:
                continue
            content = f"超时未响应工单 {row['overdue_count']} 条，请优先跟进"
            flag_id = await upsert_flag(
                c,
                tenant,
                customer_id,
                RULE_FLAG_KEY,
                content,
                "rule",
                source_ref=str(row["oldest_ticket"]),
                actor="memory",
                settings=settings,
            )
            if flag_id:
                marked += 1
        removed = await many(
            c,
            """UPDATE memory_items SET status='deleted',updated_at=now()
            WHERE tenant_id=:t AND type='flag' AND key=:key AND source_type='rule'
            AND status='active'
            AND NOT EXISTS (
                SELECT 1 FROM tickets tk
                WHERE tk.tenant_id=:t AND tk.customer_id=memory_items.customer_id::text
                AND tk.due_at<now() AND tk.status IN ('open','in_progress'))
            AND NOT EXISTS (
                SELECT 1 FROM tickets tk
                WHERE tk.tenant_id=:t AND tk.customer_id=memory_items.customer_id::text
                AND tk.due_at<now() AND tk.due_at>now()-:days*interval '1 day')
            RETURNING id,customer_id""",
            t=tenant,
            key=RULE_FLAG_KEY,
            days=RULE_CLEAR_DAYS,
        )
        for row in removed:
            await audit(
                c,
                tenant,
                "memory",
                "memory.flag.removed",
                row["id"],
                customer_id=str(row["customer_id"]),
                key=RULE_FLAG_KEY,
                source="rule",
                flag_source_type="rule",
            )
    if marked or removed:
        logger.info(
            "Rule scan: %d overdue customers flagged, %d rule flags cleared",
            marked,
            len(removed),
        )
    return {"overdue_customers": len(overdue), "marked": marked, "removed": len(removed)}
