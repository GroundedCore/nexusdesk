from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class ResourceFacts:
    tenant_id: str
    enabled: bool = True
    capability: str | None = None
    allowed_capabilities: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Decision:
    effect: Literal["allow", "deny"]
    reason_code: str
    policy_version: str = "baseline-v1"
    obligations: tuple[str, ...] = ()

    @property
    def allowed(self):
        return self.effect == "allow"
