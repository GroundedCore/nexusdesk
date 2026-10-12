"""Built-in channel adapter registry and management metadata."""

from agent_platform.integrations.channels.dingtalk import DingTalkAdapter
from agent_platform.integrations.channels.discord import DiscordAdapter
from agent_platform.integrations.channels.feishu import FeishuAdapter
from agent_platform.integrations.channels.slack import SlackAdapter
from agent_platform.integrations.channels.telegram import TelegramAdapter
from agent_platform.integrations.channels.wecom import WeComAdapter
from agent_platform.integrations.channels.wecom_kf import WeComKfAdapter
from agent_platform.platform.persistence.store import DomainError

GENERIC_PROVIDER = {
    "id": "generic_webhook",
    "name": "通用 Webhook",
    "description": "可信服务端通过统一 Token、HMAC 和 replies 接口接入。",
    "native": False,
    "config_fields": [],
    "credential_fields": [],
}

_ADAPTERS = {
    adapter.provider: adapter
    for adapter in (
        WeComAdapter(),
        WeComKfAdapter(),
        FeishuAdapter(),
        DingTalkAdapter(),
        SlackAdapter(),
        TelegramAdapter(),
        DiscordAdapter(),
    )
}


def adapter_for(provider: str):
    try:
        return _ADAPTERS[provider]
    except KeyError:
        raise DomainError("channel_provider_unsupported", 422) from None


def chunk_size_for(provider: str) -> int | None:
    adapter = _ADAPTERS.get(provider)
    return adapter.max_message_chars if adapter else None


def bind_token_store(store) -> None:
    """Point every token-using adapter at a shared cross-process cache."""

    for adapter in _ADAPTERS.values():
        cache = getattr(adapter, "tokens", None)
        if cache is not None:
            cache.store = store


def provider_catalog() -> list[dict]:
    items = [GENERIC_PROVIDER]
    for adapter in _ADAPTERS.values():
        items.append(
            {
                "id": adapter.provider,
                "name": adapter.name,
                "description": adapter.description,
                "native": True,
                "config_fields": adapter.config_fields(),
                "credential_fields": adapter.credential_fields(),
            }
        )
    return items
