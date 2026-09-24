"""Trusted service-side context; never bind this dataclass to request bodies."""

from dataclasses import dataclass, field
from uuid import uuid4


@dataclass(frozen=True)
class ExecutionContext:
    tenant_id: str
    actor_id: str
    roles: frozenset[str]
    source: str = "api"
    trace_id: str = field(default_factory=lambda: str(uuid4()))
    conversation_id: str | None = None
    run_id: str | None = None

    def __post_init__(self):
        if not self.tenant_id or not self.actor_id:
            raise ValueError("Trusted tenant and actor are required")
