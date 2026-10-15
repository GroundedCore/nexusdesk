"""Event memory retrieval: one interface, pgvector and TSVECTOR implementations.

The store is probed once at startup (``build_memory_store``): vector mode
requires the pgvector-created ``memory_items.embedding`` column *and* a
deployment-level embedding profile that resolves to an ``embed`` operation
with dimension 1536 (the migration column width). Any gap degrades to the
TSVECTOR keyword implementation with a warning — that degradation is the
P3-DP-05 "rejection" form: there is no binding-time validation entry point
for a deployment-level setting.

Both implementations share the same filtering (tenant + customer + active +
type='event'), the same ranking contract (``relevance * decay``) and the same
module-level :func:`decay_factor`, so the two modes order identically given
equal relevance. Decay only reorders — it never filters rows out (P3-UT-05).
"""

import logging
import math
from datetime import UTC, datetime
from typing import Protocol, TypedDict
from uuid import UUID

from agent_platform.modules.model_gateway.contracts import EmbedRequest
from agent_platform.platform.persistence.store import many, one

logger = logging.getLogger(__name__)

# Migration 0036 creates memory_items.embedding as vector(1536); a configured
# embedding profile must match that width exactly.
EMBEDDING_DIMENSION = 1536


class MemoryHit(TypedDict):
    content: str
    key: str
    updated_at: datetime
    score: float


class MemoryStore(Protocol):
    def available(self) -> str: ...  # 'vector' | 'text'

    async def embed_and_save(self, item_id: UUID, text: str) -> None: ...

    async def search(
        self, tenant_id: str, customer_id: UUID, query: str, limit: int
    ) -> list[MemoryHit]: ...


def decay_factor(last_referenced_at: datetime, half_life_days: int, now: datetime) -> float:
    """Exponential ranking decay, exp(-ln2 * age / half_life) (P3-UT-04).

    Based on ``last_referenced_at`` rather than ``created_at`` so frequently
    referenced old events keep their rank. The factor is exactly 0.5 at one
    half-life and asymptotically approaches 0 but never reaches it: decay
    reorders, it never hides (P3-UT-05). Naive datetimes are read as UTC.
    """
    if last_referenced_at.tzinfo is None:
        last_referenced_at = last_referenced_at.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    age = max(0.0, (now - last_referenced_at).total_seconds())
    return math.exp(-math.log(2) * age / (half_life_days * 86400))


def rank_hits(rows, half_life_days, limit) -> list[MemoryHit]:
    """Apply the shared decay to raw relevance rows and keep the top ``limit``.

    ``rows`` carry ``content``/``key``/``updated_at``/``last_referenced_at``/
    ``relevance``. Ranking lives in Python so both stores literally share
    :func:`decay_factor`; the candidate set is one customer's active events,
    which the TTL scan keeps small.
    """
    now = datetime.now(UTC)
    hits = [
        MemoryHit(
            content=row["content"],
            key=row["key"],
            updated_at=row["updated_at"],
            score=float(row["relevance"])
            * decay_factor(row["last_referenced_at"], half_life_days, now),
        )
        for row in rows
    ]
    hits.sort(key=lambda hit: hit["score"], reverse=True)
    return hits[:limit]


def escape_like(query: str) -> str:
    """Escape ILIKE wildcards so a user query stays literal."""
    return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# Shared filter: one tenant, one customer, active event items only. This is
# the P0 tenant/customer isolation boundary for every search path.
_TEXT_MATCH = """SELECT key,content,updated_at,last_referenced_at,
    ts_rank(content_tsv, plainto_tsquery('simple', :q)) AS relevance
    FROM memory_items
    WHERE tenant_id=:t AND customer_id=:c AND status='active' AND type='event'
    AND (content_tsv @@ plainto_tsquery('simple', :q)
        OR content ILIKE '%'||:pattern||'%' ESCAPE '\\')"""


class BaseStore:
    """Shared clamping and keyword fallback for both implementations."""

    def __init__(self, engine, settings):
        self.engine, self.settings = engine, settings

    def _limit(self, limit) -> int:
        return max(1, min(int(limit), self.settings.memory_search_limit_max))

    async def _text_rows(self, c, tenant_id, customer_id, query):
        # The ILIKE arm backstops Chinese recall: 'simple' does not segment,
        # so a pure tsvector match would miss almost everything (R9).
        return await many(
            c,
            _TEXT_MATCH,
            t=tenant_id,
            c=customer_id,
            q=query,
            pattern=escape_like(query),
        )

    async def _text_search(self, tenant_id, customer_id, query, limit) -> list[MemoryHit]:
        async with self.engine.connect() as c:
            rows = await self._text_rows(c, tenant_id, customer_id, query)
        return rank_hits(rows, self.settings.memory_search_half_life_days, limit)


class TsVectorStore(BaseStore):
    """Keyword fallback: tsvector match with an ILIKE arm for Chinese recall."""

    def available(self) -> str:
        return "text"

    async def embed_and_save(self, item_id: UUID, text: str) -> None:
        return None

    async def search(self, tenant_id, customer_id, query, limit) -> list[MemoryHit]:
        return await self._text_search(tenant_id, customer_id, query, self._limit(limit))


class PgVectorStore(BaseStore):
    """pgvector cosine search over event embeddings produced via the gateway."""

    def __init__(self, engine, gateway, settings):
        super().__init__(engine, settings)
        self.gateway = gateway

    def available(self) -> str:
        return "vector"

    async def _embed(self, item_id, text):
        result = await self.gateway.invoke(
            self.settings.tenant_id,
            self.settings.memory_embedding_profile_id,
            self.settings.memory_embedding_profile_version,
            EmbedRequest(inputs=[{"id": str(item_id), "text": text}]),
            actor="memory",
        )
        return result["payload"]["vectors"][0]["vector"]

    async def embed_and_save(self, item_id: UUID, text: str) -> None:
        """Embed one item's content; failures mark the row, never raise.

        A failed embedding leaves the row fully functional for keyword search
        and the worker's daily retry pass clears ``embedding_failed`` once a
        later attempt succeeds.
        """
        try:
            vector = await self._embed(item_id, text)
        except Exception:  # noqa: BLE001 - per-item degradation, never task failure
            logger.warning("Embedding failed for memory item %s; marked for retry", item_id)
            async with self.engine.begin() as c:
                await one(
                    c,
                    """UPDATE memory_items SET embedding_failed=true
                    WHERE id=:id AND tenant_id=:t RETURNING id""",
                    id=item_id,
                    t=self.settings.tenant_id,
                )
            return
        async with self.engine.begin() as c:
            await one(
                c,
                """UPDATE memory_items SET embedding=CAST(:v AS vector),embedding_failed=false
                WHERE id=:id AND tenant_id=:t RETURNING id""",
                id=item_id,
                t=self.settings.tenant_id,
                v="[" + ",".join(repr(float(x)) for x in vector) + "]",
            )

    async def search(self, tenant_id, customer_id, query, limit) -> list[MemoryHit]:
        limit = self._limit(limit)
        try:
            vector = await self._embed("query", query)
        except Exception:  # noqa: BLE001 - degrade to keyword search, stay useful
            logger.warning(
                "Query embedding failed; memory_search falling back to keyword mode",
            )
            return await self._text_search(tenant_id, customer_id, query, limit)
        async with self.engine.connect() as c:
            rows = await many(
                c,
                """SELECT key,content,updated_at,last_referenced_at,
                1 - (embedding <=> CAST(:v AS vector)) AS relevance
                FROM memory_items
                WHERE tenant_id=:t AND customer_id=:c AND status='active' AND type='event'
                AND embedding IS NOT NULL""",
                t=tenant_id,
                c=customer_id,
                v="[" + ",".join(repr(float(x)) for x in vector) + "]",
            )
        return rank_hits(rows, self.settings.memory_search_half_life_days, limit)


async def _has_vector_column(engine) -> bool:
    """Whether migration 0036's pgvector probe block created the column."""
    async with engine.connect() as c:
        return bool(
            await one(
                c,
                """SELECT 1 FROM information_schema.columns
                WHERE table_name='memory_items' AND column_name='embedding'""",
            )
        )


async def _has_vector_extension(engine) -> bool:
    """Whether the vector extension itself is installed in this database."""
    async with engine.connect() as c:
        return bool(await one(c, "SELECT 1 FROM pg_extension WHERE extname='vector'"))


async def _embedding_ready(gateway, settings):
    """Check the deployment-level embedding profile (auto mode probe)."""
    profile_id = settings.memory_embedding_profile_id
    version = settings.memory_embedding_profile_version
    if not profile_id or not version:
        return False, "no memory_embedding_profile configured"
    try:
        snapshot = await gateway.catalog.resolve(settings.tenant_id, profile_id, version)
    except Exception as exc:  # noqa: BLE001 - any resolution failure degrades
        return False, f"embedding profile does not resolve ({getattr(exc, 'code', exc)})"
    if snapshot["spec"]["operation"] != "embed":
        return False, "configured profile is not an embed operation"
    bad = [
        route["model"]["embedding_dimension"]
        for route in snapshot["routes"]
        if route["model"]["embedding_dimension"] != EMBEDDING_DIMENSION
    ]
    if bad:
        return False, f"embedding dimension {bad[0]} != {EMBEDDING_DIMENSION}"
    return True, ""


async def build_memory_store(engine, gateway, settings) -> MemoryStore:
    """Probe once at startup and pick the implementation (P3-UT-01/02/03).

    ``memory_vector_enabled`` overrides: ``false`` always picks keyword mode,
    ``true`` picks vector mode whenever the column physically exists (a forced
    vector mode with a broken profile surfaces as per-item embedding_failed
    marks, which the retry pass handles). ``auto`` requires the column plus a
    fully resolving embed profile of the right dimension.

    Probe failures (e.g. database unreachable during startup) degrade to
    keyword mode with a warning instead of crashing the lifespan: the 503
    contract for outages belongs to the readiness endpoint, and vector mode
    is re-probed on the next process start.
    """
    configured = settings.memory_vector_enabled
    if configured == "false":
        return TsVectorStore(engine, settings)
    try:
        if not await _has_vector_column(engine):
            if configured == "true":
                logger.warning(
                    "memory_vector_enabled=true but memory_items.embedding is absent "
                    "(no pgvector at migration time); using keyword mode"
                )
            elif await _has_vector_extension(engine):
                # R16: the manual enable steps were only half done — the
                # extension is installed but the column/index DDL never ran.
                # The probe must not pick vector mode (every query would fail
                # at runtime), so degrade with a loud pointer instead.
                logger.warning(
                    "vector extension is installed but memory_items.embedding is "
                    "missing (manual enable steps incomplete); using keyword mode — "
                    "run the idempotent DDL documented in deploy/README.md to "
                    "enable vector mode"
                )
            return TsVectorStore(engine, settings)
        if configured == "true":
            return PgVectorStore(engine, gateway, settings)
        ready, reason = await _embedding_ready(gateway, settings)
        if not ready:
            logger.warning(
                "Memory vector search unavailable: %s; using TSVECTOR keyword mode",
                reason,
            )
            return TsVectorStore(engine, settings)
        return PgVectorStore(engine, gateway, settings)
    except Exception as exc:  # noqa: BLE001 - outage must not crash startup (D4)
        logger.warning(
            "Memory store probe failed (%s: %s); using keyword mode "
            "until next restart",
            type(exc).__name__,
            exc,
        )
        return TsVectorStore(engine, settings)
