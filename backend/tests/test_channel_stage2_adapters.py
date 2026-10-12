import email.utils
import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent_platform.integrations.channels.base import (
    TokenCache,
    IncomingEnvelope,
    chunk_text,
    retry_after_seconds,
)
from agent_platform.integrations.channels.crypto import (
    encrypt_wechat_payload,
    slack_signature,
    wechat_signature,
)
from agent_platform.integrations.channels.discord import DiscordAdapter
from agent_platform.integrations.channels.slack import SlackAdapter
from agent_platform.integrations.channels.telegram import TelegramAdapter
from agent_platform.integrations.channels.wecom_kf import WeComKfAdapter
from agent_platform.platform.persistence.store import DomainError

WECOM_KF_KEY = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ"


def test_chunk_text_splits_on_boundaries():
    assert chunk_text("short", 4000) == ["short"]
    assert chunk_text("a" * 10, 4) == ["aaaa", "aaaa", "aa"]
    assert "".join(chunk_text("a" * 10, 4)) == "a" * 10
    lines = chunk_text("hello\nworld", 6)
    assert "".join(lines) == "hello\nworld"
    assert lines[0] == "hello\n"


def test_retry_after_parsing():
    assert retry_after_seconds({"Retry-After": "12"}) == 12.0
    assert retry_after_seconds({"retry-after": "nonsense"}) is None
    assert retry_after_seconds({}) is None
    future = email.utils.format_datetime(datetime.now(timezone.utc) + timedelta(seconds=30))
    delay = retry_after_seconds({"Retry-After": future})
    assert delay is not None and 25 <= delay <= 31


class _FakeTokenStore:
    def __init__(self):
        self.data: dict[str, tuple[str, float]] = {}

    async def get(self, scope):
        return self.data.get(scope)

    async def put(self, scope, token, expires_at):
        self.data[scope] = (token, expires_at)


@pytest.mark.asyncio
async def test_token_cache_dedupes_within_process():
    calls = {"n": 0}

    async def loader():
        calls["n"] += 1
        return "tok-1", 60

    cache = TokenCache()
    assert await cache.get("wecom:corp", loader) == "tok-1"
    assert await cache.get("wecom:corp", loader) == "tok-1"
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_token_cache_shares_across_processes_via_store():
    store = _FakeTokenStore()
    calls = {"n": 0}

    async def loader():
        calls["n"] += 1
        return "shared-tok", 60

    first = TokenCache()
    first.store = store
    assert await first.get("feishu:app", loader) == "shared-tok"

    # A second "process" must reuse the shared token instead of minting its own.
    second = TokenCache()
    second.store = store
    assert await second.get("feishu:app", loader) == "shared-tok"
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_slack_url_verification_and_signed_message():
    adapter = SlackAdapter()
    credentials = {"signing_secret": "s3cr3t", "bot_token": "xoxb-1"}
    timestamp = str(int(time.time()))

    challenge = json.dumps({"type": "url_verification", "challenge": "chal-1"}).encode()
    result = await adapter.handle_webhook(
        body=challenge,
        query={},
        headers={
            "x-slack-signature": slack_signature("s3cr3t", timestamp, challenge),
            "x-slack-request-timestamp": timestamp,
        },
        config={},
        credentials=credentials,
    )
    assert result.challenge == "chal-1"

    body = json.dumps(
        {
            "type": "event_callback",
            "event": {
                "type": "message",
                "text": "  hi slack  ",
                "channel": "C1",
                "user": "U1",
                "ts": "123.45",
            },
        }
    ).encode()
    result = await adapter.handle_webhook(
        body=body,
        query={},
        headers={
            "x-slack-signature": slack_signature("s3cr3t", timestamp, body),
            "x-slack-request-timestamp": timestamp,
        },
        config={},
        credentials=credentials,
    )
    assert result.envelope is not None
    assert result.envelope.external_conversation_id == "C1"
    assert result.envelope.external_user_id == "U1"
    assert result.envelope.text == "hi slack"
    assert result.envelope.target == {"channel": "C1"}

    with pytest.raises(DomainError):
        await adapter.handle_webhook(
            body=body,
            query={},
            headers={
                "x-slack-signature": slack_signature("wrong", timestamp, body),
                "x-slack-request-timestamp": timestamp,
            },
            config={},
            credentials=credentials,
        )


@pytest.mark.asyncio
async def test_slack_rejects_stale_and_bot_messages():
    adapter = SlackAdapter()
    credentials = {"signing_secret": "s3cr3t", "bot_token": "xoxb-1"}
    stale = str(int(time.time()) - 10_000)
    body = json.dumps({"type": "event_callback", "event": {"type": "message"}}).encode()
    with pytest.raises(DomainError):
        await adapter.handle_webhook(
            body=body,
            query={},
            headers={
                "x-slack-signature": slack_signature("s3cr3t", stale, body),
                "x-slack-request-timestamp": stale,
            },
            config={},
            credentials=credentials,
        )


@pytest.mark.asyncio
async def test_telegram_secret_and_message_normalization():
    adapter = TelegramAdapter()
    credentials = {"bot_token": "123:abc", "secret_token": "shh"}
    body = json.dumps(
        {
            "update_id": 1,
            "message": {
                "message_id": 5,
                "from": {"id": 42},
                "chat": {"id": 100},
                "text": "hello telegram",
            },
        }
    ).encode()
    result = await adapter.handle_webhook(
        body=body,
        query={},
        headers={"x-telegram-bot-api-secret-token": "shh"},
        config={},
        credentials=credentials,
    )
    assert result.envelope is not None
    assert result.envelope.external_message_id == "100:5"
    assert result.envelope.external_conversation_id == "100"
    assert result.envelope.external_user_id == "42"
    assert result.envelope.target == {"chat_id": "100"}

    with pytest.raises(DomainError):
        await adapter.handle_webhook(
            body=body,
            query={},
            headers={"x-telegram-bot-api-secret-token": "nope"},
            config={},
            credentials=credentials,
        )


def _discord_keypair() -> tuple[Ed25519PrivateKey, str]:
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return key, public.hex()


@pytest.mark.asyncio
async def test_discord_ping_and_command():
    key, public_key = _discord_keypair()
    adapter = DiscordAdapter()
    config = {"public_key": public_key}
    credentials = {"bot_token": "bot-token"}
    timestamp = "1700000000"

    ping = json.dumps({"type": 1}).encode()
    result = await adapter.handle_webhook(
        body=ping,
        query={},
        headers={
            "x-signature-ed25519": key.sign(timestamp.encode() + ping).hex(),
            "x-signature-timestamp": timestamp,
        },
        config=config,
        credentials=credentials,
    )
    assert result.ack == {"type": 1}
    assert result.envelope is None

    command = json.dumps(
        {
            "id": "i-1",
            "type": 2,
            "channel_id": "C9",
            "member": {"user": {"id": "U9"}},
            "data": {
                "name": "ask",
                "options": [{"name": "text", "type": 3, "value": "hello discord"}],
            },
        }
    ).encode()
    result = await adapter.handle_webhook(
        body=command,
        query={},
        headers={
            "x-signature-ed25519": key.sign(timestamp.encode() + command).hex(),
            "x-signature-timestamp": timestamp,
        },
        config=config,
        credentials=credentials,
    )
    assert result.ack == {"type": 5}
    assert result.envelope is not None
    assert result.envelope.external_conversation_id == "C9"
    assert result.envelope.external_user_id == "U9"
    assert result.envelope.text == "hello discord"
    assert result.envelope.target == {"channel_id": "C9"}

    with pytest.raises(DomainError):
        await adapter.handle_webhook(
            body=command,
            query={},
            headers={
                "x-signature-ed25519": "00" * 64,
                "x-signature-timestamp": timestamp,
            },
            config=config,
            credentials=credentials,
        )


@pytest.mark.asyncio
async def test_wecom_kf_challenge_and_event(monkeypatch):
    adapter = WeComKfAdapter()
    credentials = {
        "secret": "secret",
        "token": "callback-token",
        "encoding_aes_key": WECOM_KF_KEY,
    }
    config = {"corp_id": "corp-1"}
    timestamp = "1700000000"
    nonce = "nonce"

    echostr = encrypt_wechat_payload(
        WECOM_KF_KEY, "kf-echo", "corp-1", random_bytes=b"0123456789abcdef"
    )
    result = await adapter.handle_webhook(
        body=b"",
        query={
            "timestamp": timestamp,
            "nonce": nonce,
            "msg_signature": wechat_signature("callback-token", timestamp, nonce, echostr),
            "echostr": echostr,
        },
        headers={},
        config=config,
        credentials=credentials,
    )
    assert result.challenge == "kf-echo"

    async def fake_sync(**_kwargs):
        return (
            [
                IncomingEnvelope(
                    external_message_id="kf-msg-1",
                    external_conversation_id="kf-1:user-1",
                    external_user_id="user-1",
                    external_thread_id="kf-1",
                    text="hello kf",
                    target={"touser": "user-1", "open_kfid": "kf-1"},
                )
            ],
            "cursor-1",
        )

    monkeypatch.setattr(adapter, "_sync_customer_messages", fake_sync)
    xml = (
        "<xml><ToUserName>corp-1</ToUserName><Event>kf_msg_or_event</Event>"
        "<Token>sync-token</Token><OpenKfId>kf-1</OpenKfId></xml>"
    )
    encrypted = encrypt_wechat_payload(
        WECOM_KF_KEY, xml, "corp-1", random_bytes=b"0123456789abcdef"
    )
    body = f"<xml><Encrypt><![CDATA[{encrypted}]]></Encrypt></xml>".encode()
    result = await adapter.handle_webhook(
        body=body,
        query={
            "timestamp": timestamp,
            "nonce": nonce,
            "msg_signature": wechat_signature(
                "callback-token", timestamp, nonce, encrypted
            ),
        },
        headers={},
        config=config,
        credentials=credentials,
    )
    assert result.envelope is None
    assert [e.external_message_id for e in result.envelopes] == ["kf-msg-1"]
    assert result.envelopes[0].text == "hello kf"
    assert result.envelopes[0].target == {"touser": "user-1", "open_kfid": "kf-1"}
    assert result.cursor == "cursor-1"
