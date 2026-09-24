from agent_platform.platform.identity.context import ExecutionContext
from agent_platform.platform.persistence.store import DomainError

from .contracts import Decision, ResourceFacts


class ExecutionPolicy:
    """Server-side capability checks. Tool names from the model grant no permissions."""

    def __init__(self, registry):
        self.registry = registry

    def authorize(self, context: ExecutionContext, action: str, resource: ResourceFacts):
        roles = {
            "resource.read": {"admin", "operator", "viewer"},
            "configuration.write": {"admin"},
            "service.operate": {"admin", "operator"},
            "business.confirm": {"admin", "operator"},
            "tool.invoke": {"runtime"},
        }
        if context.tenant_id != resource.tenant_id:
            return Decision("deny", "resource_scope_denied")
        if not resource.enabled:
            return Decision("deny", "resource_disabled")
        if not context.roles.intersection(roles.get(action, set())):
            return Decision("deny", "action_not_authorized")
        if action == "tool.invoke" and resource.capability not in resource.allowed_capabilities:
            return Decision("deny", "capability_not_granted")
        obligations = ("verify_business_confirmation",) if action == "business.confirm" else ()
        return Decision("allow", "authorized", obligations=obligations)

    async def check(self, tenant, name, allowed):
        context = ExecutionContext(tenant, "runtime", frozenset({"runtime"}), source="worker")
        facts = ResourceFacts(
            tenant, await self.registry.enabled(tenant, name), name, frozenset(allowed)
        )
        decision = self.authorize(context, "tool.invoke", facts)
        if not decision.allowed:
            raise DomainError("tool_not_allowed", 403)
        return decision

    def check_rounds(self, requested, settings):
        return min(requested, settings.max_model_rounds)
