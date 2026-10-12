"""Telegram Bot Webhook 适配器。"""

import hmac
import json
from typing import Any

import httpx

from agent_platform.integrations.channels.base import (
    DeliveryResult,
    IncomingEnvelope,
    WebhookResult,
    header_value,
    require_fields,
    retry_after_seconds,
)
from agent_platform.platform.persistence.store import DomainError


class TelegramAdapter:
    provider = "telegram"
    name = "Telegram"
    max_message_chars = 4096
    description = "Telegram Bot Webhook，支持私聊/群组文本消息与机器人主动回复。"

    def config_fields(self) -> list[dict[str, Any]]:
        return []

    def credential_fields(self) -> list[dict[str, Any]]:
        return [
            {"name": "bot_token", "label": "Bot Token", "required": True, "secret": True},
            {
                "name": "secret_token",
                "label": "Webhook Secret Token",
                "required": False,
                "secret": True,
            },
        ]

    def validate(self, config: dict[str, Any], credentials: dict[str, Any]) -> None:
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
        secret_token = str(credentials.get("secret_token") or "")
        if secret_token:
            supplied = header_value(headers, "x-telegram-bot-api-secret-token")
            if not supplied or not hmac.compare_digest(secret_token, supplied):
                raise DomainError("channel_invalid_signature", 401)
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise DomainError("channel_invalid_webhook_payload", 400) from None
        message = payload.get("message") or payload.get("edited_message") or {}
        text = message.get("text") or message.get("caption") or ""
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        sender = message.get("from") or {}
        message_id = message.get("message_id")
        if not text.strip() or chat_id is None or message_id is None:
            raise DomainError("channel_unsupported_message_type", 422)
        return WebhookResult(
            envelope=IncomingEnvelope(
                external_message_id=f"{chat_id}:{message_id}",
                external_conversation_id=str(chat_id),
                external_user_id=str(sender.get("id")) if sender.get("id") is not None else None,
                external_thread_id=str(message.get("message_thread_id") or "") or None,
                text=text.strip(),
                target={"chat_id": str(chat_id)},
            )
        )

    async def deliver(
        self,
        *,
        content: str,
        target: dict[str, Any],
        config: dict[str, Any],
        credentials: dict[str, Any],
    ) -> DeliveryResult:
        chat_id = str(target.get("chat_id") or "")
        if not chat_id:
            return DeliveryResult(ok=False, error_code="channel_target_missing")
        url = "https://api.telegram.org/bot" + str(credentials["bot_token"]) + "/sendMessage"
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
                response = await client.post(url, json={"chat_id": chat_id, "text": content})
            if response.status_code == 429:
                return DeliveryResult(
                    ok=False,
                    http_status=429,
                    error_code="telegram_rate_limited",
                    retry_after=retry_after_seconds(dict(response.headers)),
                )
            data = response.json()
            if response.status_code >= 300 or not data.get("ok"):
                return DeliveryResult(
                    ok=False,
                    http_status=response.status_code,
                    error_code="telegram_send_rejected",
                )
            result = data.get("result") or {}
            return DeliveryResult(
                ok=True,
                external_message_id=str(result.get("message_id") or "") or None,
                http_status=response.status_code,
            )
        except (httpx.HTTPError, ValueError, TypeError):
            return DeliveryResult(ok=False, error_code="telegram_delivery_error")
