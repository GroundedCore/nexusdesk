"""Slack Events API 适配器。"""

import hmac
import json
import time
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
from agent_platform.integrations.channels.crypto import slack_signature
from agent_platform.platform.persistence.store import DomainError


class SlackAdapter:
    provider = "slack"
    name = "Slack"
    max_message_chars = 4000
    description = "Slack Events API 事件订阅，支持频道文本消息与机器人主动回复。"

    def config_fields(self) -> list[dict[str, Any]]:
        return [{"name": "app_id", "label": "App ID", "required": False}]

    def credential_fields(self) -> list[dict[str, Any]]:
        return [
            {"name": "signing_secret", "label": "Signing Secret", "required": True, "secret": True},
            {"name": "bot_token", "label": "Bot User OAuth Token", "required": True, "secret": True},
        ]

    def validate(self, config: dict[str, Any], credentials: dict[str, Any]) -> None:
        require_fields(credentials, ("signing_secret", "bot_token"), "credentials")

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
        self._verify(headers, body, str(credentials["signing_secret"]))
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise DomainError("channel_invalid_webhook_payload", 400) from None
        if payload.get("type") == "url_verification":
            challenge = payload.get("challenge")
            if not isinstance(challenge, str) or not challenge:
                raise DomainError("channel_invalid_webhook_payload", 400)
            return WebhookResult(challenge=challenge)
        if payload.get("type") != "event_callback":
            raise DomainError("channel_unsupported_event_type", 422)
        event = payload.get("event") or {}
        if event.get("type") != "message" or event.get("subtype") or event.get("bot_id"):
            raise DomainError("channel_unsupported_event_type", 422)
        text = event.get("text") or ""
        channel = str(event.get("channel") or "")
        user = str(event.get("user") or "")
        ts = str(event.get("ts") or "")
        message_id = str(event.get("client_msg_id") or ts or payload.get("event_id") or "")
        if not text.strip() or not channel or not message_id:
            raise DomainError("channel_invalid_message", 422)
        return WebhookResult(
            envelope=IncomingEnvelope(
                external_message_id=message_id,
                external_conversation_id=channel,
                external_user_id=user or None,
                external_thread_id=str(event.get("thread_ts") or "") or None,
                text=text.strip(),
                target={"channel": channel},
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
        channel = str(target.get("channel") or "")
        if not channel:
            return DeliveryResult(ok=False, error_code="channel_target_missing")
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
                response = await client.post(
                    "https://slack.com/api/chat.postMessage",
                    headers={"Authorization": "Bearer " + str(credentials["bot_token"])},
                    json={"channel": channel, "text": content},
                )
            if response.status_code == 429:
                return DeliveryResult(
                    ok=False,
                    http_status=429,
                    error_code="slack_rate_limited",
                    retry_after=retry_after_seconds(dict(response.headers)),
                )
            data = response.json()
            if response.status_code >= 300 or not data.get("ok"):
                return DeliveryResult(
                    ok=False,
                    http_status=response.status_code,
                    error_code="slack_send_rejected",
                )
            return DeliveryResult(
                ok=True,
                external_message_id=str(data.get("ts") or "") or None,
                http_status=response.status_code,
            )
        except (httpx.HTTPError, ValueError, TypeError):
            return DeliveryResult(ok=False, error_code="slack_delivery_error")

    def _verify(self, headers: dict[str, str], body: bytes, signing_secret: str) -> None:
        signature = header_value(headers, "x-slack-signature")
        timestamp = header_value(headers, "x-slack-request-timestamp")
        if not signature or not timestamp:
            raise DomainError("channel_invalid_signature", 401)
        try:
            if abs(time.time() - int(timestamp)) > 300:
                raise DomainError("channel_stale_signature", 401)
        except ValueError:
            raise DomainError("channel_invalid_signature", 401) from None
        expected = slack_signature(signing_secret, timestamp, body)
        if not hmac.compare_digest(expected, signature):
            raise DomainError("channel_invalid_signature", 401)
