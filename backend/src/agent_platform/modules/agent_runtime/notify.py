"""Transient delta streaming over Postgres LISTEN/NOTIFY.

Deltas are best-effort telemetry: the durable answer always lands in
``runtime_runs.output`` and the event log stays coarse. NOTIFY only accelerates
the internal console's live view, so a missed notification costs latency, never
data. No Redis, no schema change.
"""

import asyncio
import json
from uuid import UUID

import asyncpg

_PREFIX = "runtime_evt_"


def channel(run_id) -> str:
    rid = run_id.hex if isinstance(run_id, UUID) else str(run_id).replace("-", "")
    return _PREFIX + rid


def _dsn(database_url: str) -> str:
    # SQLAlchemy URL -> asyncpg DSN. asyncpg rejects the ``+asyncpg`` dialect marker.
    return database_url.replace("postgresql+asyncpg://", "postgresql://")


class Notifier:
    """Send side (worker): a dedicated connection, reconnected on failure."""

    def __init__(self, database_url: str):
        self.dsn = _dsn(database_url)
        self.conn: asyncpg.Connection | None = None
        self.lock = asyncio.Lock()

    async def send(self, run_id, payload: dict):
        async with self.lock:
            try:
                if self.conn is None or self.conn.is_closed():
                    self.conn = await asyncpg.connect(self.dsn)
                await self.conn.execute(
                    "SELECT pg_notify($1, $2)",
                    channel(run_id),
                    json.dumps(payload, ensure_ascii=False),
                )
            except (asyncpg.PostgresError, OSError):
                # Deltas are best-effort; drop the connection and reconnect next time.
                if self.conn is not None:
                    try:
                        await self.conn.close()
                    except Exception:  # noqa: BLE001 - close is best-effort
                        pass
                self.conn = None

    async def close(self):
        if self.conn is not None:
            await self.conn.close()
            self.conn = None


class Listener:
    """Receive side (API): one dedicated LISTEN connection, many channels.

    A single callback routes every notification to the queues subscribed to its
    channel, so multiple SSE clients can watch the same run on one connection.
    """

    def __init__(self, database_url: str):
        self.dsn = _dsn(database_url)
        self.conn: asyncpg.Connection | None = None
        self.queues: dict[str, set[asyncio.Queue]] = {}
        self.listened: set[str] = set()
        self.lock = asyncio.Lock()

    async def _cb(self, _connection, _pid, ch, payload):
        for queue in list(self.queues.get(ch, ())):
            queue.put_nowait(payload)

    async def subscribe(self, run_id) -> asyncio.Queue:
        queue = asyncio.Queue()
        ch = channel(run_id)
        async with self.lock:
            self.queues.setdefault(ch, set()).add(queue)
            try:
                if self.conn is None or self.conn.is_closed():
                    self.conn = await asyncpg.connect(self.dsn)
                    self.listened.clear()
                    for existing in self.queues:
                        await self.conn.add_listener(existing, self._cb)
                        self.listened.add(existing)
                elif ch not in self.listened:
                    await self.conn.add_listener(ch, self._cb)
                    self.listened.add(ch)
            except (asyncpg.PostgresError, OSError):
                # The 15s safety-net poll still converges; deltas just lag.
                pass
        return queue

    async def unsubscribe(self, run_id, queue):
        ch = channel(run_id)
        async with self.lock:
            queues = self.queues.get(ch)
            if not queues:
                return
            queues.discard(queue)
            if not queues:
                self.queues.pop(ch, None)
                if self.conn is not None and not self.conn.is_closed() and ch in self.listened:
                    try:
                        await self.conn.remove_listener(ch, self._cb)
                    except (asyncpg.PostgresError, OSError):
                        pass
                    self.listened.discard(ch)

    async def close(self):
        if self.conn is not None:
            await self.conn.close()
            self.conn = None
        self.queues.clear()
        self.listened.clear()
