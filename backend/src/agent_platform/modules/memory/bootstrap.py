"""Assembly of the memory claim loop for both deployment forms.

- quickstart / development: the API process lifespan embeds the loop when
  ``AGENT_EMBEDDED_WORKER=true`` (``embedded_memory_worker``), mirroring how the
  embedded runtime worker is gated; the two-container promise is unchanged.
- production: the standalone ``memory-worker`` container runs
  ``python -m agent_platform.apps.worker.memory`` (``build_memory_worker``),
  decoupled from the runtime worker and horizontally scalable.
"""

from agent_platform.modules.memory.summary import SummaryService
from agent_platform.modules.memory.worker import MemoryWorker


def build_memory_worker(services, settings):
    """Wire a MemoryWorker onto the shared engine and model gateway."""
    engine = services.repository.engine
    return MemoryWorker(
        engine, SummaryService(engine, services.platform.gateway, settings), settings
    )


def embedded_memory_worker(services, settings):
    """Assemble the API-embedded claim loop when the deployment opted in.

    Returns ``None`` unless the embedded worker mode and the summary feature are
    both enabled, so the caller simply skips starting the loop.
    """
    if not (settings.embedded_worker and settings.summary_enabled):
        return None
    return build_memory_worker(services, settings)
