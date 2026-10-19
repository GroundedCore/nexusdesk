from contextvars import ContextVar
from ipaddress import ip_address

audit_ip: ContextVar[str | None] = ContextVar("audit_ip", default=None)


def _normalized_ip(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    try:
        return str(ip_address(candidate))
    except ValueError:
        return None


def _trusted_proxy(host: str | None) -> bool:
    candidate = _normalized_ip(host)
    if not candidate:
        return False
    parsed = ip_address(candidate)
    return parsed.is_private or parsed.is_loopback


def client_ip(scope) -> str | None:
    """Resolve the caller IP without trusting forwarding headers from public peers."""
    client = scope.get("client")
    peer = _normalized_ip(client[0] if client else None)
    if not _trusted_proxy(peer):
        return peer

    headers: dict[str, str] = {}
    for key, value in scope.get("headers") or []:
        name = key.decode("latin-1").lower()
        if name in {"x-forwarded-for", "x-real-ip"}:
            headers[name] = value.decode("latin-1")

    forwarded = headers.get("x-forwarded-for", "").split(",", 1)[0]
    return (
        _normalized_ip(forwarded)
        or _normalized_ip(headers.get("x-real-ip"))
        or peer
    )


class AuditContextMiddleware:
    """Expose the request IP to audit writes made anywhere in the request.

    Background jobs run without an HTTP scope, so they keep a null IP instead of
    inheriting a previous request context.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        token = audit_ip.set(client_ip(scope))
        try:
            await self.app(scope, receive, send)
        finally:
            audit_ip.reset(token)
