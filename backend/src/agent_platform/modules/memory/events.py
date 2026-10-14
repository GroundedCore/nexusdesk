"""Ticket event -> memory item writer (FR-1).

Runs as ``event_write`` tasks on the shared ``memory_tasks`` queue. The ticket
hooks dispatch a minimal payload (ticket id + event name) inside the ticket's
own transaction; this writer re-reads the ticket's current state at execution
time so a stale payload never writes stale content (R5/R6). Embedding runs
after the database transaction commits: a model failure must never roll back
the memory write itself.
"""

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from agent_platform.modules.memory.service import MemoryService
from agent_platform.platform.persistence.store import one

logger = logging.getLogger(__name__)

EVENT_KIND = "event_write"
EVENTS = ("created", "resolved", "closed", "commitment")

# Long ticket descriptions are summarised for injection and search; 200
# characters keeps the injected section within its budget.
MAX_SUMMARY_CHARS = 200


def summarize(text, limit=MAX_SUMMARY_CHARS):
    """Collapse whitespace and truncate one text for event content (P3-UT-06)."""
    collapsed = " ".join((text or "").split())
    return collapsed if len(collapsed) <= limit else collapsed[:limit].rstrip() + "…"


def event_key(ticket_no, event, log_id=None):
    """The one place that knows the key convention (R5)."""
    if event == "commitment":
        return f"commitment:{log_id}"
    return f"ticket:{ticket_no}:{event}"


def event_content(ticket_no, event, summary):
    """Content templates for the three lifecycle events (FR-1, P3-UT-06)."""
    verbs = {"created": "已创建", "resolved": "已解决", "closed": "已关闭"}
    return f"工单 {ticket_no} {verbs[event]}：{summary}"


class EventWriter:
    """Execute one claimed ``event_write`` task (dispatcher protocol)."""

    def __init__(self, engine, store, settings, memory: MemoryService | None = None):
        self.engine, self.store, self.settings = engine, store, settings
        self.memory = memory or MemoryService(engine, settings)

    async def _ticket(self, c, tenant, ticket_id):
        return await one(
            c,
            """SELECT t.id,t.ticket_no,t.customer_id,d.description,d.resolution
            FROM tickets t JOIN tickets_detail d ON d.ticket_id=t.id AND d.tenant_id=t.tenant_id
            WHERE t.id=:id AND t.tenant_id=:t""",
            id=ticket_id,
            t=tenant,
        )

    async def _customer_exists(self, c, tenant, customer_id):
        try:
            customer_uuid = UUID(str(customer_id))
        except ValueError:
            return False
        return bool(
            await one(
                c,
                "SELECT 1 FROM memory_customers WHERE id=:id AND tenant_id=:t",
                id=customer_uuid,
                t=tenant,
            )
        )

    async def _closed_note(self, c, tenant, ticket_id):
        """The close reason rides in the status_changed process log entry."""
        row = await one(
            c,
            """SELECT content FROM ticket_process_log
            WHERE tenant_id=:t AND ticket_id=:id AND action_type='status_changed'
            AND new_status='closed' ORDER BY id DESC LIMIT 1""",
            t=tenant,
            id=ticket_id,
        )
        return row["content"] if row else None

    async def _commitment_note(self, c, tenant, ticket_id, log_id):
        row = await one(
            c,
            """SELECT content FROM ticket_process_log
            WHERE id=:log AND tenant_id=:t AND ticket_id=:id AND is_commitment""",
            log=log_id,
            t=tenant,
            id=ticket_id,
        )
        return row["content"] if row else None

    async def execute(self, task):
        """Write the event rows for one ticket event. Returns 'done'/'skipped'.

        Embedding happens after the write transaction commits, and per-item
        embedding failures only mark the row — never fail the task.
        """
        payload = task["payload"]
        tenant = task["tenant_id"]
        event = payload.get("event")
        log_id = payload.get("log_id")
        if event not in EVENTS or (event == "commitment") != (log_id is not None):
            logger.warning("event_write task %s has an invalid payload; dropped", task["id"])
            return "skipped"
        written = []
        async with self.engine.begin() as c:
            ticket = await self._ticket(c, tenant, payload.get("ticket_id"))
            # R6: tickets.customer_id is the only dispatch/write criterion; a
            # customerless ticket produces no memory. A ticket that vanished
            # between dispatch and execution is equally a no-op.
            if not ticket or not ticket["customer_id"]:
                return "skipped"
            customer_id = ticket["customer_id"]
            if not await self._customer_exists(c, tenant, customer_id):
                # An unknown customer must not crash the task (R6); the row
                # would fail the FK, so warn and complete normally.
                logger.warning(
                    "event_write: ticket %s references unknown customer %s; skipped",
                    payload.get("ticket_id"),
                    customer_id,
                )
                return "skipped"
            ticket_no = ticket["ticket_no"]
            ttl = None
            if event != "commitment":
                ttl = datetime.now(UTC) + timedelta(days=self.settings.memory_event_ttl_days)
            if event == "commitment":
                note = await self._commitment_note(c, tenant, ticket["id"], log_id)
                if not note or not note.strip():
                    return "skipped"
                content = summarize(note)
                item_id = await self.memory.add_event_item(
                    c,
                    tenant,
                    customer_id,
                    event_key(ticket_no, event, log_id),
                    content,
                    f"{ticket['id']}:{log_id}",
                    expires_at=None,
                )
                if item_id:
                    written.append((item_id, content))
            else:
                if event == "created":
                    summary = summarize(ticket["description"])
                elif event == "resolved":
                    summary = summarize(ticket["resolution"] or ticket["description"])
                else:
                    summary = summarize(
                        await self._closed_note(c, tenant, ticket["id"])
                        or ticket["resolution"]
                        or ticket["description"]
                    )
                item_id = await self.memory.add_event_item(
                    c,
                    tenant,
                    customer_id,
                    event_key(ticket_no, event),
                    event_content(ticket_no, event, summary),
                    str(ticket["id"]),
                    expires_at=ttl,
                )
                if item_id:
                    written.append((item_id, event_content(ticket_no, event, summary)))
                if event == "created":
                    # The in-flight injection row (R5): same summary, same TTL,
                    # expired as soon as the ticket resolves or closes.
                    open_id = await self.memory.add_event_item(
                        c,
                        tenant,
                        customer_id,
                        "open_ticket:" + ticket_no,
                        summary,
                        str(ticket["id"]),
                        expires_at=ttl,
                    )
                    if open_id:
                        written.append((open_id, summary))
                else:
                    await self.memory.expire_open_ticket(c, tenant, customer_id, ticket_no)
        # Embedding is post-commit: a model outage degrades items to
        # keyword-searchable (embedding_failed=true) instead of losing them.
        for item_id, content in written:
            await self.store.embed_and_save(item_id, content)
        return "done"
