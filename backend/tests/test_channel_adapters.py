import json

import pytest

from agent_platform.integrations.channels.crypto import (
    encrypt_wechat_payload,
    feishu_signature,
    wechat_signature,
)
from agent_platform.integrations.channels.feishu import FeishuAdapter
from agent_platform.integrations.channels.registry import provider_catalog
from agent_platform.integrations.channels.wecom import WeComAdapter


@pytest.mark.asyncio
async def test_wecom_encrypted_text_callback_is_normalized():
    credentials = {
        "secret": "secret",
        "token": "callback-token",
        "encoding_aes_key": "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ",
    }
    config = {"corp_id": "corp-1", "agent_id": "100001"}
    xml = (
        "<xml><ToUserName>corp-1</ToUserName><FromUserName>user-1</FromUserName>"
        "<CreateTime>1</CreateTime><MsgType>text</MsgType><Content>hello</Content>"
        "<MsgId>msg-1</MsgId><AgentID>100001</AgentID></xml>"
    )
    encrypted = encrypt_wechat_payload(
        credentials["encoding_aes_key"],
        xml,
        config["corp_id"],
        random_bytes=b"0123456789abcdef",
    )
    timestamp = "1700000000"
    nonce = "nonce"
    body = f"<xml><Encrypt><![CDATA[{encrypted}]]></Encrypt></xml>".encode()
    result = await WeComAdapter().handle_webhook(
        body=body,
        query={
            "timestamp": timestamp,
            "nonce": nonce,
            "msg_signature": wechat_signature(
                credentials["token"], timestamp, nonce, encrypted
            ),
        },
        headers={},
        config=config,
        credentials=credentials,
    )
    assert result.envelope is not None
    assert result.envelope.external_message_id == "msg-1"
    assert result.envelope.external_conversation_id == "user-1"
    assert result.envelope.text == "hello"
    assert result.envelope.target == {"touser": "user-1", "agentid": "100001"}


@pytest.mark.asyncio
async def test_feishu_url_verification_and_signed_message():
    credentials = {
        "app_secret": "app-secret",
        "verification_token": "verify-token",
        "encrypt_key": "encrypt-key",
    }
    adapter = FeishuAdapter()
    challenge = json.dumps(
        {"type": "url_verification", "token": "verify-token", "challenge": "ok"}
    ).encode()
    result = await adapter.handle_webhook(
        body=challenge,
        query={},
        headers={},
        config={"app_id": "cli_1"},
        credentials=credentials,
    )
    assert result.challenge == "ok"

    body = json.dumps(
        {
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_id": {"open_id": "ou_1"}},
                "message": {
                    "message_id": "om_1",
                    "chat_id": "oc_1",
                    "message_type": "text",
                    "content": json.dumps({"text": "  hello  "}),
                },
            },
        }
    ).encode()
    timestamp = "1700000000"
    nonce = "nonce"
    result = await adapter.handle_webhook(
        body=body,
        query={},
        headers={
            "x-lark-signature": feishu_signature(
                timestamp, nonce, credentials["encrypt_key"], body
            ),
            "x-lark-request-timestamp": timestamp,
            "x-lark-request-nonce": nonce,
        },
        config={"app_id": "cli_1"},
        credentials=credentials,
    )
    assert result.envelope is not None
    assert result.envelope.external_conversation_id == "oc_1"
    assert result.envelope.external_user_id == "ou_1"
    assert result.envelope.text == "hello"


def test_provider_catalog_contains_first_batch():
    assert [item["id"] for item in provider_catalog()] == [
        "generic_webhook",
        "wecom",
        "wecom_kf",
        "feishu",
        "dingtalk",
        "slack",
        "telegram",
        "discord",
    ]
