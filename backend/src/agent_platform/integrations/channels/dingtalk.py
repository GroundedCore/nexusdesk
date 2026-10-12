"""钉钉事件订阅与机器人消息适配器。"""

import hashlib
import hmac
import json
import time
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from agent_platform.integrations.channels.base import (
    DeliveryResult,
    IncomingEnvelope,
    TokenCache,
    WebhookResult,
    header_value,
    require_fields,
    retry_after_seconds,
)
from agent_platform.integrations.channels.crypto import (
    decrypt_wechat_payload,
    dingtalk_robot_signature,
    wechat_signature,
)
from agent_platform.platform.persistence.store import DomainError


class DingTalkAdapter:
    provider = "dingtalk"
    max_message_chars = None
    name = "钉钉"
    description = "钉钉事件订阅，支持文本消息、机器人回复和企业应用主动发送。"

    def __init__(self):
        self.tokens = TokenCache()

    def config_fields(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "mode",
                "label": "接入模式",
                "required": True,
                "type": "select",
                "options": [
                    {"value": "robot", "label": "群机器人 Webhook"},
                    {"value": "event", "label": "企业内部应用事件"},
                    {"value": "both", "label": "事件 + 群机器人"},
                ],
                "default": "event",
            },
            {"name": "app_key", "label": "App Key", "required": False},
            {"name": "agent_id", "label": "AgentId", "required": False},
        ]

    def credential_fields(self) -> list[dict[str, Any]]:
        return [
            {"name": "app_secret", "label": "App Secret", "required": False, "secret": True},
            {"name": "token", "label": "事件回调 Token", "required": False, "secret": True},
            {
                "name": "encoding_aes_key",
                "label": "事件回调 AES Key",
                "required": False,
                "secret": True,
            },
            {"name": "robot_webhook", "label": "机器人 Webhook", "required": False, "secret": True},
            {"name": "robot_secret", "label": "机器人签名密钥", "required": False, "secret": True},
        ]

    def validate(self, config: dict[str, Any], credentials: dict[str, Any]) -> None:
        mode = str(config.get("mode") or "event")
        if mode not in {"event", "robot", "both"}:
            raise DomainError("channel_invalid_mode", 422)
        if mode in {"event", "both"}:
            require_fields(config, ("app_key", "agent_id"), "config")
            require_fields(credentials, ("app_secret", "token", "encoding_aes_key"), "credentials")
        if mode in {"robot", "both"}:
            require_fields(credentials, ("robot_webhook",), "credentials")

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
        if str(config.get("mode") or "event") == "robot":
            raise DomainError("channel_inbound_not_configured", 404)
        payload = self._decode_payload(
            body=body,
            query=query,
            headers=headers,
            config=config,
            credentials=credentials,
        )
        if isinstance(payload.get("challenge"), str):
            return WebhookResult(challenge=payload["challenge"])
        data = payload.get("Data") or payload.get("data") or {}
        text = data.get("Content") or data.get("content") or data.get("Text") or payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise DomainError("channel_unsupported_message_type", 422)
        message_id = str(
            payload.get("EventId")
            or payload.get("eventId")
            or data.get("MsgId")
            or hashlib.sha256(body).hexdigest()[:32]
        )
        conversation_id = str(
            data.get("ConversationId")
            or data.get("conversationId")
            or data.get("ChatId")
            or data.get("FromUserId")
            or ""
        )
        user_id = str(data.get("FromUserId") or data.get("SenderId") or data.get("userId") or "")
        if not conversation_id:
            raise DomainError("channel_invalid_message", 422)
        target = {"user_id": user_id, "conversation_id": conversation_id}
        return WebhookResult(
            envelope=IncomingEnvelope(
                external_message_id=message_id,
                external_conversation_id=conversation_id,
                external_user_id=user_id or None,
                external_thread_id=None,
                text=text.strip(),
                target=target,
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
        mode = str(config.get("mode") or "event")
        webhook = str(credentials.get("robot_webhook") or "")
        if webhook and mode in {"robot", "both"}:
            return await self._deliver_robot(webhook, content, credentials)
        user_id = str(target.get("user_id") or "")
        if not user_id:
            return DeliveryResult(ok=False, error_code="channel_target_missing")
        try:
            access_token = await self._access_token(
                str(config["app_key"]), str(credentials["app_secret"])
            )
            async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
                response = await client.post(
                    "https://oapi.dingtalk.com/topapi/message/corpconversation/asyncsend_v2",
                    params={"access_token": access_token},
                    json={
                        "agent_id": str(config["agent_id"]),
                        "userid_list": user_id,
                        "msg": {"msgtype": "text", "text": {"content": content}},
                    },
                )
            if response.status_code == 429:
                return DeliveryResult(
                    ok=False,
                    http_status=429,
                    error_code="dingtalk_rate_limited",
                    retry_after=retry_after_seconds(dict(response.headers)),
                )
            data = response.json()
            if response.status_code >= 300 or data.get("errcode") not in (0, None):
                return DeliveryResult(
                    ok=False,
                    http_status=response.status_code,
                    error_code="dingtalk_send_rejected",
                )
            task_id = (data.get("result") or {}).get("task_id")
            return DeliveryResult(
                ok=True,
                external_message_id=str(task_id) if task_id else None,
                http_status=response.status_code,
            )
        except (httpx.HTTPError, ValueError, TypeError):
            return DeliveryResult(ok=False, error_code="dingtalk_delivery_error")

    async def _deliver_robot(
        self, webhook: str, content: str, credentials: dict[str, Any]
    ) -> DeliveryResult:
        url = webhook
        secret = str(credentials.get("robot_secret") or "")
        if secret:
            timestamp = str(int(time.time() * 1000))
            url += ("&" if "?" in url else "?") + "timestamp=" + timestamp
            url += "&sign=" + dingtalk_robot_signature(secret, timestamp)
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
                response = await client.post(
                    url,
                    json={"msgtype": "text", "text": {"content": content}},
                )
            if response.status_code == 429:
                return DeliveryResult(
                    ok=False,
                    http_status=429,
                    error_code="dingtalk_robot_rate_limited",
                    retry_after=retry_after_seconds(dict(response.headers)),
                )
            data = response.json()
            if response.status_code >= 300 or data.get("errcode") not in (0, None):
                return DeliveryResult(
                    ok=False,
                    http_status=response.status_code,
                    error_code="dingtalk_robot_rejected",
                )
            return DeliveryResult(ok=True, http_status=response.status_code)
        except (httpx.HTTPError, ValueError, TypeError):
            return DeliveryResult(ok=False, error_code="dingtalk_delivery_error")

    def _decode_payload(
        self,
        *,
        body: bytes,
        query: dict[str, str],
        headers: dict[str, str],
        config: dict[str, Any],
        credentials: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            payload = json.loads(body) if body else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            try:
                root = ET.fromstring(body)
            except ET.ParseError:
                raise DomainError("channel_invalid_webhook_payload", 400) from None
            payload = {child.tag: child.text or "" for child in root}
        encrypted = payload.get("encrypt") or payload.get("Encrypt")
        if not encrypted:
            return payload
        signature = (
            query.get("msg_signature")
            or query.get("signature")
            or str(payload.get("signature") or "")
            or header_value(headers, "sign")
        )
        timestamp = query.get("timestamp") or str(payload.get("timestamp") or "")
        nonce = query.get("nonce") or str(payload.get("nonce") or "")
        token = str(credentials.get("token") or "")
        if signature and not hmac.compare_digest(
            wechat_signature(token, timestamp, nonce, str(encrypted)), signature
        ):
            raise DomainError("channel_invalid_signature", 401)
        plain = decrypt_wechat_payload(
            str(credentials["encoding_aes_key"]),
            str(encrypted),
            expected_receive_id=str(config.get("corp_id") or "") or None,
        )
        try:
            return json.loads(plain)
        except json.JSONDecodeError:
            try:
                root = ET.fromstring(plain)
            except ET.ParseError:
                raise DomainError("channel_invalid_webhook_payload", 400) from None
            return {child.tag: child.text or "" for child in root}

    async def _access_token(self, app_key: str, app_secret: str) -> str:
        scope = "dingtalk:" + app_key + ":" + hashlib.sha256(app_secret.encode()).hexdigest()[:16]

        async def loader():
            try:
                async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
                    response = await client.get(
                        "https://oapi.dingtalk.com/gettoken",
                        params={"appkey": app_key, "appsecret": app_secret},
                    )
                data = response.json()
            except (httpx.HTTPError, ValueError, TypeError):
                raise DomainError("channel_access_token_unavailable", 503) from None
            token = str(data.get("access_token") or "")
            if response.status_code >= 300 or data.get("errcode") not in (0, None) or not token:
                raise DomainError("channel_access_token_unavailable", 503)
            ttl = max(int(data.get("expires_in") or 7200) - 120, 60)
            return token, ttl

        return await self.tokens.get(scope, loader)
