"""Provider-neutral channel contracts."""

import asyncio
import email.utils
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from agent_platform.platform.persistence.store import DomainError

# Shared LISTEN/NOTIFY channel for channel work (inbound enqueue + outbound
# delivery). Kept here so both the channel service and the conversation service
# can emit the same wakeup without importing each other.
CHANNEL_WORK_CHANNEL = "channel_work"


@dataclass(frozen=True)
class IncomingEnvelope:
    external_message_id: str
    external_conversation_id: str
    external_user_id: str | None
    external_thread_id: str | None
    text: str
    target: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WebhookResult:
    challenge: str | None = None
    envelope: IncomingEnvelope | None = None
    envelopes: list[IncomingEnvelope] = field(default_factory=list)
    ack: dict[str, Any] | None = None
    cursor: str | None = None


@dataclass(frozen=True)
class DeliveryResult:
    ok: bool
    external_message_id: str | None = None
    http_status: int | None = None
    error_code: str | None = None
    retry_after: float | None = None


class ChannelAdapter(Protocol):
    provider: str
    name: str
    description: str
    max_message_chars: int | None

    def config_fields(self) -> list[dict[str, Any]]: ...

    def credential_fields(self) -> list[dict[str, Any]]: ...

    def validate(self, config: dict[str, Any], credentials: dict[str, Any]) -> None: ...

    async def handle_webhook(
        self,
        *,
        body: bytes,
        query: dict[str, str],
        headers: dict[str, str],
        config: dict[str, Any],
        credentials: dict[str, Any],
        cursor: str | None = None,
    ) -> WebhookResult: ...

    async def deliver(
        self,
        *,
        content: str,
        target: dict[str, Any],
        config: dict[str, Any],
        credentials: dict[str, Any],
    ) -> DeliveryResult: ...


def require_fields(values: dict[str, Any], names: tuple[str, ...], area: str) -> None:
    missing = [name for name in names if not str(values.get(name) or "").strip()]
    if missing:
        raise DomainError(f"channel_{area}_missing_{missing[0]}", 422)


def header_value(headers: dict[str, str], name: str) -> str:
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return ""


def retry_after_seconds(headers: dict[str, str]) -> float | None:
    """Parse a Retry-After header given as delay-seconds or an HTTP-date."""

    raw = header_value(headers, "retry-after").strip()
    if not raw:
        return None
    try:
        return max(float(raw), 0.0)
    except ValueError:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max((parsed - datetime.now(UTC)).total_seconds(), 0.0)


def chunk_text(content: str, size: int | None) -> list[str]:
    """Split outbound content into provider-sized chunks on natural boundaries."""

    if not content:
        return [content]
    if not size or size <= 0 or len(content) <= size:
        return [content]
    parts: list[str] = []
    remaining = content
    while len(remaining) > size:
        cut = remaining.rfind("\n", 0, size)
        if cut <= 0:
            cut = remaining.rfind(" ", 0, size)
        if cut <= 0:
            cut = size
        else:
            cut += 1
        parts.append(remaining[:cut])
        remaining = remaining[cut:]
    parts.append(remaining)
    return parts


class TokenCache:
    """Two-level access-token cache: per-process memory plus an optional store.

    ``store`` is injected by the channel service and backed by
    ``channel_access_tokens`` so several replicas share one provider token
    instead of each minting its own. When no store is bound (unit tests,
    standalone adapter use) it degrades to the in-process cache.
    """

    def __init__(self):
        self._memory: dict[str, tuple[str, float]] = {}
        self._lock = asyncio.Lock()
        self.store = None

    async def get(self, key: str, loader):
        """Return a cached token, or ``loader()`` -> ``(token, ttl_seconds)``."""

        now = time.monotonic()
        hit = self._memory.get(key)
        if hit and hit[1] > now:
            return hit[0]
        async with self._lock:
            hit = self._memory.get(key)
            if hit and hit[1] > now:
                return hit[0]
            if self.store is not None:
                shared = await self.store.get(key)
                if shared is not None:
                    token, expires_at = shared
                    remaining = expires_at - time.time()
                    if remaining > 0:
                        self._memory[key] = (token, time.monotonic() + remaining)
                        return token
            token, ttl = await loader()
            self._memory[key] = (token, time.monotonic() + ttl)
            if self.store is not None:
                await self.store.put(key, token, time.time() + ttl)
            return token
