"""企业微信应用消息适配器。"""

import hashlib
import hmac
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from agent_platform.integrations.channels.base import (
    DeliveryResult,
    IncomingEnvelope,
    TokenCache,
    WebhookResult,
    require_fields,
    retry_after_seconds,
)
from agent_platform.integrations.channels.crypto import (
    decrypt_wechat_payload,
    wechat_signature,
)
from agent_platform.platform.persistence.store import DomainError


class WeComAdapter:
    provider = "wecom"
    max_message_chars = 2048
    name = "企业微信"
    description = "企业微信应用消息，支持回调验签、文本消息和主动回复。"

    def __init__(self):
        self.tokens = TokenCache()

    def config_fields(self) -> list[dict[str, Any]]:
        return [
            {"name": "corp_id", "label": "企业 ID", "required": True},
            {"name": "agent_id", "label": "应用 AgentId", "required": True},
        ]

    def credential_fields(self) -> list[dict[str, Any]]:
        return [
            {"name": "secret", "label": "应用 Secret", "required": True, "secret": True},
            {"name": "token", "label": "回调 Token", "required": True, "secret": True},
            {
                "name": "encoding_aes_key",
                "label": "回调 EncodingAESKey",
                "required": True,
                "secret": True,
            },
        ]

    def validate(self, config: dict[str, Any], credentials: dict[str, Any]) -> None:
        require_fields(config, ("corp_id", "agent_id"), "config")
        require_fields(credentials, ("secret", "token", "encoding_aes_key"), "credentials")

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
        token = str(credentials["token"])
        aes_key = str(credentials["encoding_aes_key"])
        timestamp = query.get("timestamp", "")
        nonce = query.get("nonce", "")
        signature = query.get("msg_signature") or query.get("signature") or ""
        challenge = query.get("echostr")
        if challenge:
            self._verify(token, timestamp, nonce, challenge, signature)
            return WebhookResult(
                challenge=decrypt_wechat_payload(
                    aes_key,
                    challenge,
                    expected_receive_id=str(config["corp_id"]),
                )
            )

        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            raise DomainError("channel_invalid_webhook_payload", 400) from None
        encrypted = root.findtext("Encrypt")
        if not encrypted:
            raise DomainError("channel_invalid_webhook_payload", 400)
        self._verify(token, timestamp, nonce, encrypted, signature)
        plain = decrypt_wechat_payload(
            aes_key,
            encrypted,
            expected_receive_id=str(config["corp_id"]),
        )
        try:
            message = ET.fromstring(plain)
        except ET.ParseError:
            raise DomainError("channel_invalid_webhook_payload", 400) from None
        message_type = message.findtext("MsgType") or ""
        if message_type != "text":
            raise DomainError("channel_unsupported_message_type", 422)
        content = message.findtext("Content") or ""
        message_id = message.findtext("MsgId") or hashlib.sha256(plain.encode()).hexdigest()[:32]
        user_id = message.findtext("FromUserName") or ""
        chat_id = message.findtext("ChatId")
        if not content.strip() or not user_id:
            raise DomainError("channel_invalid_message", 422)
        conversation_id = chat_id or user_id
        target = {
            "touser": user_id,
            "agentid": str(config["agent_id"]),
        }
        if chat_id:
            target["chat_id"] = chat_id
        return WebhookResult(
            envelope=IncomingEnvelope(
                external_message_id=message_id,
                external_conversation_id=conversation_id,
                external_user_id=user_id,
                external_thread_id=chat_id,
                text=content.strip(),
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
        access_token = await self._access_token(
            str(config["corp_id"]), str(credentials["secret"])
        )
        payload = {
            "touser": str(target.get("touser") or ""),
            "msgtype": "text",
            "agentid": str(config["agent_id"]),
            "text": {"content": content},
            "safe": 0,
        }
        if not payload["touser"]:
            return DeliveryResult(ok=False, error_code="channel_target_missing")
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
                response = await client.post(
                    "https://qyapi.weixin.qq.com/cgi-bin/message/send",
                    params={"access_token": access_token},
                    json=payload,
                )
            if response.status_code == 429:
                return DeliveryResult(
                    ok=False,
                    http_status=429,
                    error_code="wecom_rate_limited",
                    retry_after=retry_after_seconds(dict(response.headers)),
                )
            data = response.json()
            if response.status_code >= 300 or data.get("errcode") not in (0, None):
                return DeliveryResult(
                    ok=False,
                    http_status=response.status_code,
                    error_code="wecom_send_rejected",
                )
            return DeliveryResult(
                ok=True,
                external_message_id=str(data.get("msgid") or "") or None,
                http_status=response.status_code,
            )
        except (httpx.HTTPError, ValueError, TypeError):
            return DeliveryResult(ok=False, error_code="wecom_delivery_error")

    def _verify(
        self, token: str, timestamp: str, nonce: str, encrypted: str, signature: str
    ) -> None:
        expected = wechat_signature(token, timestamp, nonce, encrypted)
        if not signature or not hmac.compare_digest(expected, signature):
            raise DomainError("channel_invalid_signature", 401)

    async def _access_token(self, corp_id: str, secret: str) -> str:
        scope = "wecom:" + corp_id + ":" + hashlib.sha256(secret.encode()).hexdigest()[:16]

        async def loader():
            try:
                async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
                    response = await client.get(
                        "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                        params={"corpid": corp_id, "corpsecret": secret},
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
