"""Assembly of the memory claim loop for both deployment forms.

- quickstart / development: the API process lifespan embeds the loop when
  ``AGENT_EMBEDDED_WORKER=true`` (``embedded_memory_worker``), mirroring how the
  embedded runtime worker is gated; the two-container promise is unchanged.
- production: the standalone ``memory-worker`` container runs
  ``python -m agent_platform.apps.worker.memory`` (``build_memory_worker``),
  decoupled from the runtime worker and horizontally scalable.

Phase 2 added ``memory_extract`` tasks and Phase 3 adds ``event_write`` tasks
to the shared queue; the dispatcher routes each claimed row to its service by
``kind``. The deployment shape is unchanged — the existing memory-worker gains
new task kinds purely through code.
"""

from agent_platform.modules.memory.events import EVENT_KIND, EventWriter
from agent_platform.modules.memory.extraction import EXTRACT_KIND, ExtractionService
from agent_platform.modules.memory.service import MemoryService
from agent_platform.modules.memory.store import TsVectorStore
from agent_platform.modules.memory.summary import SummaryService
from agent_platform.modules.memory.worker import MemoryWorker


class MemoryDispatcher:
    """Route claimed tasks to the service that owns their kind."""

    def __init__(self, summary, extraction, events=None):
        self.summary, self.extraction, self.events = summary, extraction, events

    async def execute(self, task):
        if task["kind"] == EXTRACT_KIND:
            return await self.extraction.execute(task)
        if task["kind"] == EVENT_KIND and self.events is not None:
            return await self.events.execute(task)
        return await self.summary.execute(task)


def build_memory_worker(services, settings):
    """Wire a MemoryWorker onto the shared engine and model gateway."""
    engine = services.repository.engine
    gateway = services.platform.gateway
    # runtime_services probes the store at startup; anything assembled without
    # it (tests, ad-hoc wiring) gets the always-safe keyword implementation.
    store = getattr(services.platform, "memory_store", None) or TsVectorStore(engine, settings)
    dispatcher = MemoryDispatcher(
        SummaryService(engine, gateway, settings),
        ExtractionService(engine, gateway, settings, MemoryService(engine, settings)),
        EventWriter(engine, store, settings),
    )
    return MemoryWorker(engine, dispatcher, settings, store=store)


def embedded_memory_worker(services, settings):
    """Assemble the API-embedded claim loop when the deployment opted in.

    Returns ``None`` unless the embedded worker mode and at least one memory
    feature (summary, extraction or events) are enabled, so the caller simply
    skips starting the loop.
    """
    if not (
        settings.embedded_worker
        and (
            settings.summary_enabled
            or settings.memory_extract_enabled
            or settings.memory_event_enabled
        )
    ):
        return None
    return build_memory_worker(services, settings)
