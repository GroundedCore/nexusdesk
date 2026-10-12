"""Discord Interactions 端点适配器。

Discord 没有「普通消息」型 HTTP webhook，普通频道消息需经 Gateway 长连接。
这里接入应用交互端点：PING 心跳应答，斜杠命令文本转会话；回复由投递 worker
以机器人身份主动发到频道。
"""

import json
from typing import Any

import httpx

from agent_platform.integrations.channels.base import (
    DeliveryResult,
    IncomingEnvelope,
    WebhookResult,
    header_value,
    retry_after_seconds,
    require_fields,
)
from agent_platform.integrations.channels.crypto import verify_ed25519
from agent_platform.platform.persistence.store import DomainError

_INTERACTION_PING = 1
_INTERACTION_APPLICATION_COMMAND = 2
_RESPONSE_PONG = 1
_RESPONSE_DEFERRED_CHANNEL_MESSAGE = 5
_OPTION_STRING = 3


class DiscordAdapter:
    provider = "discord"
    name = "Discord"
    max_message_chars = 2000
    description = "Discord 应用交互端点：斜杠命令接入，机器人主动回复频道消息。"

    def config_fields(self) -> list[dict[str, Any]]:
        return [
            {"name": "public_key", "label": "应用 Public Key", "required": True},
            {"name": "application_id", "label": "Application ID", "required": False},
        ]

    def credential_fields(self) -> list[dict[str, Any]]:
        return [
            {"name": "bot_token", "label": "Bot Token", "required": True, "secret": True},
        ]

    def validate(self, config: dict[str, Any], credentials: dict[str, Any]) -> None:
        require_fields(config, ("public_key",), "config")
        require_fields(credentials, ("bot_token",), "credentials")

    async def handle_webhook(
        self,
        *,
        body: bytes,
        query: dict[str, str],
        headers: dict[str, str],
        config: dict[str, Any],
        credentials: dict[str, Any],
        cursor: str | None = None,
    ) -> WebhookResult:
        signature = header_value(headers, "x-signature-ed25519")
        timestamp = header_value(headers, "x-signature-timestamp")
        if not signature or not timestamp:
            raise DomainError("channel_invalid_signature", 401)
        if not verify_ed25519(
            str(config["public_key"]), signature, timestamp.encode() + body
        ):
            raise DomainError("channel_invalid_signature", 401)
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise DomainError("channel_invalid_webhook_payload", 400) from None
        interaction_type = payload.get("type")
        if interaction_type == _INTERACTION_PING:
            return WebhookResult(ack={"type": _RESPONSE_PONG})
        if interaction_type != _INTERACTION_APPLICATION_COMMAND:
            raise DomainError("channel_unsupported_event_type", 422)
        text = _command_text(payload.get("data") or {})
        channel_id = str(payload.get("channel_id") or "")
        interaction_id = str(payload.get("id") or "")
        if not text.strip() or not channel_id or not interaction_id:
            raise DomainError("channel_invalid_message", 422)
        member = payload.get("member") or {}
        user = member.get("user") or payload.get("user") or {}
        return WebhookResult(
            ack={"type": _RESPONSE_DEFERRED_CHANNEL_MESSAGE},
            envelope=IncomingEnvelope(
                external_message_id=interaction_id,
                external_conversation_id=channel_id,
                external_user_id=str(user.get("id")) if user.get("id") else None,
                external_thread_id=None,
                text=text.strip(),
                target={"channel_id": channel_id},
            ),
        )

    async def deliver(
        self,
        *,
        content: str,
        target: dict[str, Any],
        config: dict[str, Any],
        credentials: dict[str, Any],
    ) -> DeliveryResult:
        channel_id = str(target.get("channel_id") or "")
        if not channel_id:
            return DeliveryResult(ok=False, error_code="channel_target_missing")
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
                response = await client.post(
                    f"https://discord.com/api/v10/channels/{channel_id}/messages",
                    headers={"Authorization": "Bot " + str(credentials["bot_token"])},
                    json={"content": content},
                )
            if response.status_code == 429:
                return DeliveryResult(
                    ok=False,
                    http_status=429,
                    error_code="discord_rate_limited",
                    retry_after=retry_after_seconds(dict(response.headers)),
                )
            data = response.json()
            if response.status_code >= 300 or not data.get("id"):
                return DeliveryResult(
                    ok=False,
                    http_status=response.status_code,
                    error_code="discord_send_rejected",
                )
            return DeliveryResult(
                ok=True,
                external_message_id=str(data["id"]),
                http_status=response.status_code,
            )
        except (httpx.HTTPError, ValueError, TypeError):
            return DeliveryResult(ok=False, error_code="discord_delivery_error")


def _command_text(data: dict[str, Any]) -> str:
    for option in data.get("options") or []:
        if option.get("type") == _OPTION_STRING:
            value = option.get("value")
            if isinstance(value, str) and value.strip():
                return value
    return ""
