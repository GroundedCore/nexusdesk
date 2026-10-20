"""P0 fixed-role permission catalog.

The existing ``Admin``/``Operator``/``Reader`` dependencies remain a
compatibility layer while routes migrate to permission checks. New access
control APIs use these codes directly.
"""

ROLE_NAMES = ("admin", "operator", "viewer")

PERMISSIONS = {
    "member.read": "View organization members",
    "member.manage": "Create, update, enable, and disable members",
    "role.read": "View roles and permission mappings",
    "role.manage": "Manage roles and permission mappings",
    "session.self": "View and revoke the current user's sessions",
    "session.read": "View member sessions",
    "session.revoke": "Revoke member sessions",
    "credential.read": "View service credentials",
    "credential.manage": "Create and disable service credentials",
    "conversation.read": "View conversations",
    "conversation.write": "Reply to conversations",
    "handoff.manage": "Claim, resume, and close human handoffs",
    "ticket.manage": "Process service tickets",
    "agent.read": "View agents",
    "agent.manage": "Configure agents",
    "agent.publish": "Publish or archive agents",
    "knowledge.read": "View knowledge bases and documents",
    "knowledge.manage": "Edit knowledge bases and documents",
    "knowledge.publish": "Publish knowledge",
    "model.manage": "Configure model gateway resources",
    "tool.manage": "Configure tools",
    "channel.manage": "Configure customer channels",
    "evaluation.read": "View evaluations",
    "evaluation.run": "Run evaluations",
    "observability.read": "View runtime observability",
    "audit.read": "View operation audit",
}

_OPERATOR = {
    "session.self",
    "conversation.read",
    "conversation.write",
    "handoff.manage",
    "ticket.manage",
    "agent.read",
    "knowledge.read",
    "evaluation.read",
    "observability.read",
}

_VIEWER = {
    "session.self",
    "conversation.read",
    "agent.read",
    "knowledge.read",
    "evaluation.read",
    "observability.read",
}

ROLE_PERMISSIONS = {
    "admin": frozenset(PERMISSIONS),
    "operator": frozenset(_OPERATOR),
    "viewer": frozenset(_VIEWER),
}


def permissions_for(role: str | None) -> frozenset[str]:
    return ROLE_PERMISSIONS.get(role or "", frozenset())


def role_catalog() -> list[dict]:
    return [
        {"name": role, "permissions": sorted(ROLE_PERMISSIONS[role])}
        for role in ROLE_NAMES
    ]
