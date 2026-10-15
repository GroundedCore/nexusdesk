"""Phase 4 memory decay: effective confidence, daily migration, reinforcement.

Strategy (03 section 7): compute at query time, migrate state daily — the
effective confidence is never persisted, so the daily pass touches only rows
that actually cross a threshold instead of rewriting the whole table.

- :func:`effective_confidence` is the one pure formula (unit-testable);
  protected rows and flags are immune.
- :func:`injection_filter_sql` is the inline SQL equivalent applied only to
  the injection query (R7): event search keeps Phase 3's "decay reorders,
  never hides" contract; threshold equality is kept (``>=``, P4-UT-11).
- :func:`daily_migration` runs inside the worker's maintenance pass:
  profile below threshold -> ``pending``, preference -> ``expired`` (R8 —
  events never migrate on confidence, their visibility is TTL-managed), then
  capacity eviction down to ``memory_max_items_per_customer`` skipping
  protected rows and flags. Every transition is audited with the
  pre-transition score (R17).
- ``reinforce`` tasks (R11) reset ``last_referenced_at`` and bump
  ``reference_count`` for referenced items — the clock resets, confidence
  never improves (FR-6.2). Enqueued by the runtime repository inside the
  run-finish transaction; dispatched with dispatch-side sampling.
"""

import json
import logging
import math
from datetime import UTC
from uuid import UUID

from agent_platform.platform.persistence.store import audit, many, one

logger = logging.getLogger(__name__)

REINFORCE_KIND = "reinforce"
# One reinforce task carries at most this many distinct item ids (R11).
MAX_REINFORCE_IDS = 200

_LN2 = math.log(2)

# Fallback half-lives (days) for callers without a settings object; mirrors
# the settings defaults.
DEFAULT_HALF_LIVES = {"profile": 365, "preference": 90, "event": 30}


def half_lives(settings):
    """Assemble the per-type half-life table (days) from the three scalars (R9)."""
    return {
        "profile": settings.memory_decay_half_life_days_profile,
        "preference": settings.memory_decay_half_life_days_preference,
        "event": settings.memory_decay_half_life_days_event,
    }


def effective_confidence(confidence, type, protected, last_referenced_at, now, half_lives):
    """Confidence decayed by age since the last reference (FR-6.1).

    Protected rows and flags are immune and keep their stored confidence;
    unknown types are treated as immune as well. Age is clamped at zero so a
    future timestamp never inflates the score. Naive datetimes read as UTC,
    matching ``store.decay_factor``.
    """
    if protected or type == "flag":
        return confidence
    half_life = half_lives.get(type)
    if not half_life:
        return confidence
    if last_referenced_at.tzinfo is None:
        last_referenced_at = last_referenced_at.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    age = max(0.0, (now - last_referenced_at).total_seconds())
    return confidence * math.exp(-_LN2 * age / (half_life * 86400))


def injection_filter_sql(settings):
    """WHERE fragment plus params for the injection query (R7).

    Applied to profile/preference rows only; protected rows always pass and
    the threshold keeps equality (``>= :min_confidence``). The formula is the
    SQL twin of :func:`effective_confidence` so the real-time filter and the
    daily migration agree on every row.
    """
    clause = """ AND (
        type NOT IN ('profile','preference') OR protected
        OR confidence * exp(-ln(2) * EXTRACT(EPOCH FROM (now()-last_referenced_at))
            / (CASE type WHEN 'profile' THEN CAST(:hl_profile AS numeric)
               ELSE CAST(:hl_preference AS numeric) END
               * 86400.0)) >= CAST(:min_confidence AS numeric)
    )"""
    params = {
        "hl_profile": settings.memory_decay_half_life_days_profile,
        "hl_preference": settings.memory_decay_half_life_days_preference,
        "min_confidence": settings.memory_decay_min_confidence,
    }
    return clause, params


# The shared SQL twin of effective_confidence for the daily pass: per-type
# half-life in days, flags excluded by the surrounding WHERE clause. Bind
# params sit inside an untyped CASE, so each branch is CAST explicitly —
# otherwise PostgreSQL's prepare phase infers text (QA D1).
_EFFECTIVE_SQL = """confidence * exp(-ln(2) * EXTRACT(EPOCH FROM (now()-last_referenced_at))
    / (CASE type WHEN 'profile' THEN CAST(:hl_profile AS numeric)
                 WHEN 'preference' THEN CAST(:hl_preference AS numeric)
                 ELSE CAST(:hl_event AS numeric) END * 86400.0))"""


def _half_life_params(settings):
    return {
        "hl_profile": settings.memory_decay_half_life_days_profile,
        "hl_preference": settings.memory_decay_half_life_days_preference,
        "hl_event": settings.memory_decay_half_life_days_event,
    }


async def _migrate_below_threshold(c, tenant, settings, item_type, target, action):
    """Move active rows of one type under the threshold; audit each row (R8)."""
    candidates = await many(
        c,
        f"""SELECT id,customer_id,key,{_EFFECTIVE_SQL} AS effective
        FROM memory_items
        WHERE tenant_id=:t AND type=:type AND status='active' AND NOT protected
        AND {_EFFECTIVE_SQL} < CAST(:min_confidence AS numeric)
        ORDER BY id""",
        t=tenant,
        type=item_type,
        min_confidence=settings.memory_decay_min_confidence,
        **_half_life_params(settings),
    )
    migrated = 0
    for row in candidates:
        updated = await one(
            c,
            """UPDATE memory_items SET status=:status,updated_at=now()
            WHERE id=:id AND status='active' RETURNING id""",
            status=target,
            id=row["id"],
        )
        if not updated:
            continue
        migrated += 1
        await audit(
            c,
            tenant,
            "memory",
            action,
            row["id"],
            customer_id=str(row["customer_id"]),
            type=item_type,
            key=row["key"],
            effective_confidence=round(float(row["effective"]), 6),
        )
    return migrated


async def _evict_over_capacity(c, tenant, settings):
    """Evict the lowest-scoring active rows above the per-customer cap.

    Eviction is a soft expiry (FR-6.3): rows land in ``expired`` so the
    console can restore them, and an expired restore refreshes the reference
    clock (R19) — no evict-restore-re-evict loop. Protected rows and flags
    never evict (R8 double guard); a customer whose overflow consists only
    of protected rows simply stays over the cap.
    """
    limit = settings.memory_max_items_per_customer
    over = await many(
        c,
        """SELECT customer_id, count(*)::int AS n FROM memory_items
        WHERE tenant_id=:t AND status='active'
        GROUP BY tenant_id,customer_id HAVING count(*)>:lim ORDER BY customer_id""",
        t=tenant,
        lim=limit,
    )
    evicted = 0
    for group in over:
        candidates = await many(
            c,
            f"""SELECT id,key,type,{_EFFECTIVE_SQL} AS effective
            FROM memory_items
            WHERE tenant_id=:t AND customer_id=:c AND status='active'
            AND NOT protected AND type<>'flag'
            ORDER BY {_EFFECTIVE_SQL} ASC, updated_at ASC, id ASC""",
            t=tenant,
            c=group["customer_id"],
            **_half_life_params(settings),
        )
        for row in candidates[: group["n"] - limit]:
            updated = await one(
                c,
                """UPDATE memory_items SET status='expired',updated_at=now()
                WHERE id=:id AND status='active' RETURNING id""",
                id=row["id"],
            )
            if not updated:
                continue
            evicted += 1
            await audit(
                c,
                tenant,
                "memory",
                "memory.evicted",
                row["id"],
                customer_id=str(group["customer_id"]),
                type=row["type"],
                key=row["key"],
                effective_confidence=round(float(row["effective"]), 6),
            )
    return evicted


async def daily_migration(engine, settings):
    """One decay pass: threshold migrations plus capacity eviction (R8/R14).

    Runs inside the worker's maintenance window alongside the TTL sweep;
    gated by ``memory_decay_enabled`` at the call site. Single-tenant and
    idempotent, so concurrent replicas may run it safely.
    """
    tenant = settings.tenant_id
    async with engine.begin() as c:
        profile = await _migrate_below_threshold(
            c, tenant, settings, "profile", "pending", "memory.decay_pending"
        )
        preference = await _migrate_below_threshold(
            c, tenant, settings, "preference", "expired", "memory.decay_expired"
        )
        evicted = await _evict_over_capacity(c, tenant, settings)
    if profile or preference or evicted:
        logger.info(
            "Decay pass: %d profile->pending, %d preference->expired, %d evicted",
            profile,
            preference,
            evicted,
        )
    return {"profile_pending": profile, "preference_expired": preference, "evicted": evicted}


async def enqueue_reinforce_task(conn, tenant, hits):
    """Insert one ``reinforce`` task inside the caller's transaction (R11).

    ``hits`` maps item id -> reference count collected during the run; the
    payload is capped at :data:`MAX_REINFORCE_IDS` ids. Returns the task id,
    or ``None`` when there is nothing to record.
    """
    if not hits:
        return None
    capped = list(hits.items())[:MAX_REINFORCE_IDS]
    row = await one(
        conn,
        """INSERT INTO memory_tasks(tenant_id,kind,payload)
        VALUES(:t,:kind,CAST(:payload AS jsonb)) RETURNING id""",
        t=tenant,
        kind=REINFORCE_KIND,
        payload=json.dumps({"hits": {str(k): int(v) for k, v in capped}}),
    )
    return row["id"]


class ReinforceService:
    """Execute one claimed ``reinforce`` task (dispatcher protocol).

    One UPDATE per referenced id, tenant-scoped and skipping deleted rows:
    the reference clock resets and the counter accrues, confidence never
    improves (FR-6.2). Sampling happens at dispatch time, never here.
    """

    def __init__(self, engine, settings):
        self.engine, self.settings = engine, settings

    async def execute(self, task):
        hits = (task["payload"] or {}).get("hits")
        if not isinstance(hits, dict):
            logger.warning("reinforce task %s has an invalid payload; dropped", task["id"])
            return "skipped"
        updated = 0
        async with self.engine.begin() as c:
            for raw_id, count in list(hits.items())[:MAX_REINFORCE_IDS]:
                try:
                    item_id = UUID(str(raw_id))
                    count = max(1, min(int(count), 1000))
                except (ValueError, TypeError):
                    continue
                row = await one(
                    c,
                    """UPDATE memory_items SET last_referenced_at=now(),
                    reference_count=reference_count+:n
                    WHERE id=:id AND tenant_id=:t AND status<>'deleted' RETURNING id""",
                    id=item_id,
                    t=task["tenant_id"],
                    n=count,
                )
                if row:
                    updated += 1
        if updated:
            logger.info("reinforce task %s refreshed %d items", task["id"], updated)
        return "done"
