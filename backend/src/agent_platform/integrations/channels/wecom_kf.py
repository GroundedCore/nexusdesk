"""微信客服（企业微信「微信客服」）适配器。

回调只推送事件通知（Event=kf_msg_or_event），真实消息需带事件里的 Token 调
`cgi-bin/kf/sync_msg` 拉取；因此一次回调若包含多条消息，当前只取第一条客户
文本消息交给会话处理。
"""

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


class WeComKfAdapter:
    provider = "wecom_kf"
    max_message_chars = 2048
    name = "微信客服"
    description = "企业微信「微信客服」回调，同步客户消息并支持客服文本回复。"

    def __init__(self):
        self.tokens = TokenCache()

    def config_fields(self) -> list[dict[str, Any]]:
        return [
            {"name": "corp_id", "label": "企业 ID", "required": True},
            {"name": "open_kfid", "label": "客服账号 OpenKfId", "required": False},
        ]

    def credential_fields(self) -> list[dict[str, Any]]:
        return [
            {"name": "secret", "label": "微信客服 Secret", "required": True, "secret": True},
            {"name": "token", "label": "回调 Token", "required": True, "secret": True},
            {
                "name": "encoding_aes_key",
                "label": "回调 EncodingAESKey",
                "required": True,
                "secret": True,
            },
        ]

    def validate(self, config: dict[str, Any], credentials: dict[str, Any]) -> None:
        require_fields(config, ("corp_id",), "config")
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
        corp_id = str(config["corp_id"])
        timestamp = query.get("timestamp", "")
        nonce = query.get("nonce", "")
        signature = query.get("msg_signature") or query.get("signature") or ""

        challenge = query.get("echostr")
        if challenge:
            self._verify(token, timestamp, nonce, challenge, signature)
            return WebhookResult(
                challenge=decrypt_wechat_payload(
                    aes_key, challenge, expected_receive_id=corp_id
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
        plain = decrypt_wechat_payload(aes_key, encrypted, expected_receive_id=corp_id)
        try:
            message = ET.fromstring(plain)
        except ET.ParseError:
            raise DomainError("channel_invalid_webhook_payload", 400) from None
        if (message.findtext("Event") or "") != "kf_msg_or_event":
            raise DomainError("channel_unsupported_event_type", 422)
        sync_token = message.findtext("Token") or ""
        open_kfid = message.findtext("OpenKfId") or str(config.get("open_kfid") or "")
        if not sync_token:
            raise DomainError("channel_invalid_webhook_payload", 400)
        envelopes, next_cursor = await self._sync_customer_messages(
            corp_id=corp_id,
            secret=str(credentials["secret"]),
            sync_token=sync_token,
            open_kfid=open_kfid,
            cursor=cursor,
        )
        return WebhookResult(envelopes=envelopes, cursor=next_cursor)

    async def deliver(
        self,
        *,
        content: str,
        target: dict[str, Any],
        config: dict[str, Any],
        credentials: dict[str, Any],
    ) -> DeliveryResult:
        open_kfid = str(target.get("open_kfid") or config.get("open_kfid") or "")
        touser = str(target.get("touser") or target.get("external_userid") or "")
        if not open_kfid or not touser:
            return DeliveryResult(ok=False, error_code="channel_target_missing")
        access_token = await self._access_token(str(config["corp_id"]), str(credentials["secret"]))
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
                response = await client.post(
                    "https://qyapi.weixin.qq.com/cgi-bin/kf/send_msg",
                    params={"access_token": access_token},
                    json={
                        "touser": touser,
                        "open_kfid": open_kfid,
                        "msgtype": "text",
                        "text": {"content": content},
                    },
                )
            if response.status_code == 429:
                return DeliveryResult(
                    ok=False,
                    http_status=429,
                    error_code="wecom_kf_rate_limited",
                    retry_after=retry_after_seconds(dict(response.headers)),
                )
            data = response.json()
            if response.status_code >= 300 or data.get("errcode") not in (0, None):
                return DeliveryResult(
                    ok=False,
                    http_status=response.status_code,
                    error_code="wecom_kf_send_rejected",
                )
            return DeliveryResult(
                ok=True,
                external_message_id=str(data.get("msgid") or "") or None,
                http_status=response.status_code,
            )
        except (httpx.HTTPError, ValueError, TypeError):
            return DeliveryResult(ok=False, error_code="wecom_kf_delivery_error")

    async def _sync_customer_messages(
        self,
        *,
        corp_id: str,
        secret: str,
        sync_token: str,
        open_kfid: str,
        cursor: str | None = None,
    ) -> tuple[list[IncomingEnvelope], str | None]:
        access_token = await self._access_token(corp_id, secret)
        payload: dict[str, Any] = {"token": sync_token, "limit": 1000}
        if cursor:
            payload["cursor"] = cursor
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
                response = await client.post(
                    "https://qyapi.weixin.qq.com/cgi-bin/kf/sync_msg",
                    params={"access_token": access_token},
                    json=payload,
                )
            data = response.json()
        except (httpx.HTTPError, ValueError, TypeError):
            raise DomainError("channel_sync_unavailable", 503) from None
        if response.status_code >= 300 or data.get("errcode") not in (0, None):
            raise DomainError("channel_sync_failed", 502)
        envelopes: list[IncomingEnvelope] = []
        for item in data.get("msg_list") or []:
            if item.get("msgtype") != "text":
                continue
            origin = item.get("origin")
            if origin is not None and str(origin) != "3":
                continue
            text = (item.get("text") or {}).get("content") or ""
            user_id = str(item.get("external_userid") or "")
            message_id = str(item.get("msgid") or "")
            message_kfid = str(item.get("open_kfid") or "") or open_kfid
            if not text.strip() or not user_id or not message_id or not message_kfid:
                continue
            envelopes.append(
                IncomingEnvelope(
                    external_message_id=message_id,
                    external_conversation_id=message_kfid + ":" + user_id,
                    external_user_id=user_id,
                    external_thread_id=message_kfid,
                    text=text.strip(),
                    target={"touser": user_id, "open_kfid": message_kfid},
                )
            )
        next_cursor = str(data.get("next_cursor") or "") or None
        return envelopes, next_cursor

    def _verify(
        self, token: str, timestamp: str, nonce: str, encrypted: str, signature: str
    ) -> None:
        expected = wechat_signature(token, timestamp, nonce, encrypted)
        if not signature or not hmac.compare_digest(expected, signature):
            raise DomainError("channel_invalid_signature", 401)

    async def _access_token(self, corp_id: str, secret: str) -> str:
        scope = "wecom_kf:" + corp_id + ":" + hashlib.sha256(secret.encode()).hexdigest()[:16]

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
