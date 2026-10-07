import hashlib
import hmac
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from agent_platform.platform.persistence.store import one

from .context import ExecutionContext


@dataclass(frozen=True)
class Principal:
    tenant: str
    role: str
    actor: str

    def execution_context(self):
        return ExecutionContext(self.tenant, self.actor, frozenset({self.role}))


async def principal(request: Request, authorization: Annotated[str | None, Header()] = None):
    settings = request.app.state.settings
    if (authorization or "").startswith("Bearer local_"):
        from agent_platform.platform.persistence.store import DomainError

        from .local_admin import LocalAdmin

        engine = request.app.state.runtime.platform.engine
        try:
            async with engine.connect() as c:
                session = await LocalAdmin(engine, settings.tenant_id).session(c, authorization[7:])
            if session["must_change_password"] and settings.require_password_change:
                raise DomainError("default_password_change_required", 403)
            return Principal(settings.tenant_id, "admin", "enterprise:" + str(session["user_id"]))
        except DomainError as exc:
            raise HTTPException(exc.status, exc.code) from None
    if (authorization or "").startswith("Bearer ssow_"):
        from agent_platform.platform.persistence.store import DomainError

        enterprise = request.app.state.runtime.platform.open_platform.enterprise
        try:
            async with enterprise.engine.connect() as c:
                session = await enterprise.validate_session(c, token=authorization[7:])
            if session["app_id"]:
                raise DomainError("invalid_enterprise_session", 401)
            return Principal(
                settings.tenant_id, session["role"], "enterprise:" + str(session["user_id"])
            )
        except DomainError as exc:
            raise HTTPException(exc.status, exc.code) from None
    for role, token in [
        ("admin", settings.api_token),
        ("operator", settings.operator_api_token),
        ("viewer", settings.viewer_api_token),
    ]:
        if token and hmac.compare_digest(
            (authorization or "").encode(), ("Bearer " + token.get_secret_value()).encode()
        ):
            return Principal(settings.tenant_id, role, role)
    if (authorization or "").startswith("Bearer staff_"):
        digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
        async with request.app.state.runtime.platform.engine.connect() as c:
            staff = await one(
                c,
                "SELECT id,role FROM staff_accounts WHERE tenant_id=:t AND token_hash=:hash AND enabled",
                t=settings.tenant_id,
                hash=digest,
            )
        if staff:
            return Principal(settings.tenant_id, staff["role"], "staff:" + str(staff["id"]))
        raise HTTPException(401, "invalid_api_token")
    if not settings.api_token and settings.environment == "development" and not authorization:
        async with request.app.state.runtime.platform.engine.connect() as c:
            initialized = await one(
                c,
                "SELECT user_id FROM local_admin_credentials WHERE tenant_id=:t",
                t=settings.tenant_id,
            )
        if not initialized:
            return Principal(settings.tenant_id, "admin", "local-development")
    raise HTTPException(401, "invalid_api_token")


def require(*roles):
    def dependency(user: Annotated[Principal, Depends(principal)]):
        if user.role not in roles:
            raise HTTPException(403, "insufficient_role")
        return user

    return dependency


Admin = Annotated[Principal, Depends(require("admin"))]
Operator = Annotated[Principal, Depends(require("admin", "operator"))]
Reader = Annotated[Principal, Depends(principal)]
