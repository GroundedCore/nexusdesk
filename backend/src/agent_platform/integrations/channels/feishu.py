"""飞书开放平台事件与机器人消息适配器。"""

import hashlib
import hmac
import json
from typing import Any

import httpx

from agent_platform.integrations.channels.base import (
    DeliveryResult,
    IncomingEnvelope,
    TokenCache,
    WebhookResult,
    header_value,
    retry_after_seconds,
    require_fields,
)
from agent_platform.integrations.channels.crypto import (
    decrypt_feishu_payload,
    feishu_signature,
)
from agent_platform.platform.persistence.store import DomainError


class FeishuAdapter:
    provider = "feishu"
    max_message_chars = None
    name = "飞书"
    description = "飞书自建应用事件订阅，支持文本消息和机器人主动回复。"

    def __init__(self):
        self.tokens = TokenCache()

    def config_fields(self) -> list[dict[str, Any]]:
        return [{"name": "app_id", "label": "App ID", "required": True}]

    def credential_fields(self) -> list[dict[str, Any]]:
        return [
            {"name": "app_secret", "label": "App Secret", "required": True, "secret": True},
            {
                "name": "verification_token",
                "label": "Verification Token",
                "required": True,
                "secret": True,
            },
            {"name": "encrypt_key", "label": "Encrypt Key", "required": True, "secret": True},
        ]

    def validate(self, config: dict[str, Any], credentials: dict[str, Any]) -> None:
        require_fields(config, ("app_id",), "config")
        require_fields(
            credentials, ("app_secret", "verification_token", "encrypt_key"), "credentials"
        )

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
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise DomainError("channel_invalid_webhook_payload", 400) from None
        self._verify_signature(headers, body, str(credentials["encrypt_key"]))
        if payload.get("encrypt"):
            plain = decrypt_feishu_payload(str(credentials["encrypt_key"]), payload["encrypt"])
            try:
                payload = json.loads(plain)
            except json.JSONDecodeError:
                raise DomainError("channel_invalid_webhook_payload", 400) from None
        expected_token = str(credentials["verification_token"])
        if payload.get("token") and not hmac.compare_digest(
            str(payload["token"]), expected_token
        ):
            raise DomainError("channel_invalid_verification_token", 401)
        if payload.get("type") == "url_verification":
            challenge = payload.get("challenge")
            if not isinstance(challenge, str) or not challenge:
                raise DomainError("channel_invalid_webhook_payload", 400)
            return WebhookResult(challenge=challenge)
        header = payload.get("header") or {}
        event = payload.get("event") or {}
        if header.get("event_type") != "im.message.receive_v1":
            raise DomainError("channel_unsupported_event_type", 422)
        message = event.get("message") or {}
        if message.get("message_type") != "text":
            raise DomainError("channel_unsupported_message_type", 422)
        try:
            content = json.loads(message.get("content") or "{}").get("text") or ""
        except (TypeError, json.JSONDecodeError):
            raise DomainError("channel_invalid_message", 422) from None
        message_id = str(message.get("message_id") or "")
        conversation_id = str(message.get("chat_id") or "")
        sender = event.get("sender") or {}
        sender_id = sender.get("sender_id") or {}
        user_id = sender_id.get("open_id") or sender_id.get("user_id")
        if not content.strip() or not message_id or not conversation_id:
            raise DomainError("channel_invalid_message", 422)
        return WebhookResult(
            envelope=IncomingEnvelope(
                external_message_id=message_id,
                external_conversation_id=conversation_id,
                external_user_id=str(user_id) if user_id else None,
                external_thread_id=str(message.get("thread_id") or "") or None,
                text=content.strip(),
                target={"receive_id": conversation_id, "receive_id_type": "chat_id"},
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
        receive_id = str(target.get("receive_id") or "")
        if not receive_id:
            return DeliveryResult(ok=False, error_code="channel_target_missing")
        access_token = await self._access_token(
            str(config["app_id"]), str(credentials["app_secret"])
        )
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
                response = await client.post(
                    "https://open.feishu.cn/open-apis/im/v1/messages",
                    params={"receive_id_type": str(target.get("receive_id_type") or "chat_id")},
                    headers={"Authorization": "Bearer " + access_token},
                    json={
                        "receive_id": receive_id,
                        "msg_type": "text",
                        "content": json.dumps({"text": content}, ensure_ascii=False),
                    },
                )
            if response.status_code == 429:
                return DeliveryResult(
                    ok=False,
                    http_status=429,
                    error_code="feishu_rate_limited",
                    retry_after=retry_after_seconds(dict(response.headers)),
                )
            data = response.json()
            if response.status_code >= 300 or data.get("code") not in (0, None):
                return DeliveryResult(
                    ok=False,
                    http_status=response.status_code,
                    error_code="feishu_send_rejected",
                )
            message = data.get("data") or {}
            return DeliveryResult(
                ok=True,
                external_message_id=str(message.get("message_id") or "") or None,
                http_status=response.status_code,
            )
        except (httpx.HTTPError, ValueError, TypeError):
            return DeliveryResult(ok=False, error_code="feishu_delivery_error")

    def _verify_signature(self, headers: dict[str, str], body: bytes, encrypt_key: str) -> None:
        signature = header_value(headers, "x-lark-signature")
        timestamp = header_value(headers, "x-lark-request-timestamp")
        nonce = header_value(headers, "x-lark-request-nonce")
        if not signature:
            return
        expected = feishu_signature(timestamp, nonce, encrypt_key, body)
        if not hmac.compare_digest(expected, signature):
            raise DomainError("channel_invalid_signature", 401)

    async def _access_token(self, app_id: str, app_secret: str) -> str:
        scope = "feishu:" + app_id + ":" + hashlib.sha256(app_secret.encode()).hexdigest()[:16]

        async def loader():
            try:
                async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
                    response = await client.post(
                        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                        json={"app_id": app_id, "app_secret": app_secret},
                    )
                data = response.json()
            except (httpx.HTTPError, ValueError, TypeError):
                raise DomainError("channel_access_token_unavailable", 503) from None
            token = str(data.get("tenant_access_token") or "")
            if response.status_code >= 300 or data.get("code") not in (0, None) or not token:
                raise DomainError("channel_access_token_unavailable", 503)
            ttl = max(int(data.get("expire") or 7200) - 120, 60)
            return token, ttl

        return await self.tokens.get(scope, loader)
