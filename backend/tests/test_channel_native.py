import asyncio
import json
import time

import pytest
from test_platform_postgres import create_agent, execute_next
from test_platform_postgres import platform as _platform

from agent_platform.integrations.channels.base import DeliveryResult, IncomingEnvelope
from agent_platform.integrations.channels.crypto import (
    encrypt_wechat_payload,
    slack_signature,
    wechat_signature,
)
from agent_platform.integrations.channels.registry import adapter_for
from agent_platform.platform.persistence.store import many, one

platform = _platform
pytestmark = pytest.mark.postgres


async def _wait_for_inbound_run(services, channel_id, limit=80):
    event = None
    for _ in range(limit):
        async with services.platform.engine.connect() as connection:
            event = await one(
                connection,
                """SELECT status,run_id,error_code FROM channel_inbound_events
                WHERE channel_id=:channel ORDER BY created_at DESC LIMIT 1""",
                channel=channel_id,
            )
        if event and event["status"] == "processed" and event["run_id"]:
            return event["run_id"]
        if event and event["status"] == "failed":
            raise AssertionError(f"inbound failed: {event['error_code']}")
        await asyncio.sleep(0.1)
    raise AssertionError(f"inbound not processed: {event}")


@pytest.mark.asyncio
async def test_wecom_callback_runs_agent_and_delivers_reply(platform, monkeypatch):
    client, services, settings = platform
    agent, _ = await create_agent(client)
    credentials = {
        "secret": "secret",
        "token": "callback-token",
        "encoding_aes_key": "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ",
    }
    channel = (
        await client.post(
            "/api/v1/channels",
            json={
                "name": "企业微信测试",
                "agent_id": agent["id"],
                "provider": "wecom",
                "config": {"corp_id": "corp-1", "agent_id": "100001"},
                "credentials": credentials,
            },
        )
    ).json()
    xml = (
        "<xml><ToUserName>corp-1</ToUserName><FromUserName>user-1</FromUserName>"
        "<CreateTime>1</CreateTime><MsgType>text</MsgType><Content>hello</Content>"
        "<MsgId>msg-1</MsgId><AgentID>100001</AgentID></xml>"
    )
    encrypted = encrypt_wechat_payload(
        credentials["encoding_aes_key"],
        xml,
        "corp-1",
        random_bytes=b"0123456789abcdef",
    )
    timestamp = "1700000000"
    response = await client.post(
        f"/api/v1/ingress/channels/{channel['id']}/webhook/wecom",
        params={
            "timestamp": timestamp,
            "nonce": "nonce",
            "msg_signature": wechat_signature(
                credentials["token"], timestamp, "nonce", encrypted
            ),
        },
        content=f"<xml><Encrypt><![CDATA[{encrypted}]]></Encrypt></xml>",
    )
    assert response.status_code == 200, response.text
    assert "conversation_id" not in response.text
    await _wait_for_inbound_run(services, channel["id"])

    async def fake_deliver(**_kwargs):
        return DeliveryResult(ok=True, external_message_id="out-1", http_status=200)

    monkeypatch.setattr(adapter_for("wecom"), "deliver", fake_deliver)
    await execute_next(services, settings)
    for _ in range(30):
        async with services.platform.engine.connect() as connection:
            delivery = await one(
                connection,
                """SELECT status,attempts FROM channel_deliveries
                WHERE channel_id=:channel ORDER BY created_at DESC LIMIT 1""",
                channel=channel["id"],
            )
        if delivery and delivery["status"] == "delivered":
            break
        await asyncio.sleep(0.1)
    assert delivery["status"] == "delivered"
    assert delivery["attempts"] == 1


@pytest.mark.asyncio
async def test_webhook_enqueues_inbound_and_dedups(platform):
    client, services, _settings = platform
    agent, _ = await create_agent(client)
    channel = (
        await client.post(
            "/api/v1/channels",
            json={
                "name": "Telegram测试",
                "agent_id": agent["id"],
                "provider": "telegram",
                "config": {},
                "credentials": {"bot_token": "123:abc", "secret_token": "shh"},
            },
        )
    ).json()
    body = json.dumps(
        {
            "update_id": 7,
            "message": {"message_id": 9, "from": {"id": 1}, "chat": {"id": 2}, "text": "hi"},
        }
    ).encode()
    headers = {
        "content-type": "application/json",
        "x-telegram-bot-api-secret-token": "shh",
    }
    url = f"/api/v1/ingress/channels/{channel['id']}/webhook/telegram"
    first = await client.post(url, content=body, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json() == {"ok": True}
    second = await client.post(url, content=body, headers=headers)
    assert second.status_code == 200, second.text
    async with services.platform.engine.connect() as connection:
        counted = await one(
            connection,
            "SELECT count(*) AS n FROM channel_inbound_events WHERE channel_id=:channel",
            channel=channel["id"],
        )
    assert counted["n"] == 1


@pytest.mark.asyncio
async def test_webhook_resolves_tenant_from_channel(platform):
    from uuid import uuid4

    from agent_platform.platform.persistence.store import execute

    client, services, _settings = platform
    agent, _ = await create_agent(client)
    other_channel = str(uuid4())
    async with services.platform.engine.begin() as connection:
        await execute(
            connection,
            """INSERT INTO channels(
                id,tenant_id,name,agent_id,token_hash,provider,config,enabled
            ) VALUES(:id,:t,:name,:aid,:hash,'telegram','{}'::jsonb,true)""",
            id=other_channel,
            t="other-tenant",
            name="跨租户渠道",
            aid=agent["id"],
            hash="x",
        )
    body = json.dumps(
        {
            "update_id": 11,
            "message": {"message_id": 3, "from": {"id": 1}, "chat": {"id": 2}, "text": "hi"},
        }
    ).encode()
    response = await client.post(
        f"/api/v1/ingress/channels/{other_channel}/webhook/telegram",
        content=body,
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 200, response.text
    async with services.platform.engine.connect() as connection:
        event = await one(
            connection,
            "SELECT tenant_id FROM channel_inbound_events WHERE channel_id=:channel",
            channel=other_channel,
        )
    assert event["tenant_id"] == "other-tenant"
    async with services.platform.engine.begin() as connection:
        await execute(
            connection,
            "DELETE FROM channel_inbound_events WHERE channel_id=:channel",
            channel=other_channel,
        )
        await execute(
            connection,
            "DELETE FROM channels WHERE id=:channel",
            channel=other_channel,
        )


@pytest.mark.asyncio
async def test_feishu_url_verification_returns_challenge(platform):
    client, _services, _settings = platform
    agent, _ = await create_agent(client)
    channel = (
        await client.post(
            "/api/v1/channels",
            json={
                "name": "飞书测试",
                "agent_id": agent["id"],
                "provider": "feishu",
                "config": {"app_id": "cli_test"},
                "credentials": {
                    "app_secret": "secret",
                    "verification_token": "vtoken",
                    "encrypt_key": "ekey",
                },
            },
        )
    ).json()
    response = await client.post(
        f"/api/v1/ingress/channels/{channel['id']}/webhook/feishu",
        json={"type": "url_verification", "token": "vtoken", "challenge": "challenge-123"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"challenge": "challenge-123"}


@pytest.mark.asyncio
async def test_channel_providers_endpoint_lists_catalog(platform):
    client, _services, _settings = platform
    response = await client.get("/api/v1/channels/providers")
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()] == [
        "generic_webhook",
        "wecom",
        "wecom_kf",
        "feishu",
        "dingtalk",
        "slack",
        "telegram",
        "discord",
    ]


@pytest.mark.asyncio
async def test_channel_work_notification_wakes_both_loops(platform):
    _client, services, _settings = platform
    channels = services.platform.channels
    channels.delivery_wakeup.clear()
    channels.inbound_wakeup.clear()
    channels._on_work({"k": "delivery"})
    assert channels.delivery_wakeup.is_set()
    assert channels.inbound_wakeup.is_set()


@pytest.mark.asyncio
async def test_channel_pool_spawns_configured_number_of_loops(platform):
    _client, services, _settings = platform
    channels = services.platform.channels
    channels.concurrency = 3
    started = {"n": 0}
    gate = asyncio.Event()

    async def fake_loop():
        started["n"] += 1
        await gate.wait()

    task = asyncio.create_task(channels._serve_pool(fake_loop))
    for _ in range(100):
        if started["n"] >= 3:
            break
        await asyncio.sleep(0.01)
    assert started["n"] == 3
    gate.set()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_rotate_token_only_for_generic_webhook(platform):
    client, _services, _settings = platform
    agent, _ = await create_agent(client)
    generic = (
        await client.post(
            "/api/v1/channels",
            json={"name": "閫氱敤", "agent_id": agent["id"]},
        )
    ).json()
    rotated = await client.post(f"/api/v1/channels/{generic['id']}/rotate-token")
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["token"]

    telegram = (
        await client.post(
            "/api/v1/channels",
            json={
                "name": "Telegram",
                "agent_id": agent["id"],
                "provider": "telegram",
                "credentials": {"bot_token": "123:abc"},
            },
        )
    ).json()
    rejected = await client.post(f"/api/v1/channels/{telegram['id']}/rotate-token")
    assert rejected.status_code == 422, rejected.text


@pytest.mark.asyncio
async def test_wecom_kf_sync_persists_cursor_and_multiple_messages(platform, monkeypatch):
    client, services, _settings = platform
    agent, _ = await create_agent(client)
    key = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ"
    credentials = {"secret": "secret", "token": "callback-token", "encoding_aes_key": key}
    channel = (
        await client.post(
            "/api/v1/channels",
            json={
                "name": "寰俊瀹㈡湇",
                "agent_id": agent["id"],
                "provider": "wecom_kf",
                "config": {"corp_id": "corp-1", "open_kfid": "kf-1"},
                "credentials": credentials,
            },
        )
    ).json()

    def envelope(mid):
        return IncomingEnvelope(
            external_message_id=mid,
            external_conversation_id="kf-1:user-1",
            external_user_id="user-1",
            external_thread_id="kf-1",
            text="hello " + mid,
            target={"touser": "user-1", "open_kfid": "kf-1"},
        )

    captured: dict = {}

    async def fake_sync(**kwargs):
        captured.update(kwargs)
        return [envelope("kf-msg-1"), envelope("kf-msg-2")], "cursor-9"

    monkeypatch.setattr(adapter_for("wecom_kf"), "_sync_customer_messages", fake_sync)
    xml = (
        "<xml><ToUserName>corp-1</ToUserName><Event>kf_msg_or_event</Event>"
        "<Token>sync-token</Token><OpenKfId>kf-1</OpenKfId></xml>"
    )
    encrypted = encrypt_wechat_payload(key, xml, "corp-1", random_bytes=b"0123456789abcdef")
    timestamp = "1700000000"
    response = await client.post(
        f"/api/v1/ingress/channels/{channel['id']}/webhook/wecom_kf",
        params={
            "timestamp": timestamp,
            "nonce": "nonce",
            "msg_signature": wechat_signature("callback-token", timestamp, "nonce", encrypted),
        },
        content=f"<xml><Encrypt><![CDATA[{encrypted}]]></Encrypt></xml>",
    )
    assert response.status_code == 200, response.text
    async with services.platform.engine.connect() as connection:
        counted = await one(
            connection,
            "SELECT count(*) AS n FROM channel_inbound_events WHERE channel_id=:channel",
            channel=channel["id"],
        )
        cursor = await one(
            connection,
            "SELECT provider_cursor FROM channels WHERE id=:channel",
            channel=channel["id"],
        )
    assert counted["n"] == 2
    assert cursor["provider_cursor"] == "cursor-9"


@pytest.mark.asyncio
async def test_outbound_long_message_is_chunked(platform):
    from agent_platform.modules.conversation.service import add_message

    client, services, _settings = platform
    agent, _ = await create_agent(client)
    channel = (
        await client.post(
            "/api/v1/channels",
            json={
                "name": "Telegram",
                "agent_id": agent["id"],
                "provider": "telegram",
                "credentials": {"bot_token": "123:abc"},
            },
        )
    ).json()
    body = json.dumps(
        {
            "update_id": 21,
            "message": {"message_id": 3, "from": {"id": 1}, "chat": {"id": 2}, "text": "hi"},
        }
    ).encode()
    response = await client.post(
        f"/api/v1/ingress/channels/{channel['id']}/webhook/telegram",
        content=body,
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 200, response.text
    await _wait_for_inbound_run(services, channel["id"])
    async with services.platform.engine.connect() as connection:
        mapping = await one(
            connection,
            "SELECT conversation_id FROM channel_conversations WHERE channel_id=:channel",
            channel=channel["id"],
        )
    async with services.platform.engine.begin() as connection:
        await add_message(connection, mapping["conversation_id"], "assistant", "x" * 5000)
    async with services.platform.engine.connect() as connection:
        rows = await many(
            connection,
            """SELECT seq,length(content) AS length FROM channel_deliveries
            WHERE channel_id=:channel ORDER BY seq""",
            channel=channel["id"],
        )
    assert [row["seq"] for row in rows] == [0, 1]
    assert rows[0]["length"] == 4096


@pytest.mark.asyncio
async def test_slack_url_verification_returns_challenge(platform):
    client, _services, _settings = platform
    agent, _ = await create_agent(client)
    channel = (
        await client.post(
            "/api/v1/channels",
            json={
                "name": "Slack测试",
                "agent_id": agent["id"],
                "provider": "slack",
                "config": {},
                "credentials": {"signing_secret": "s3cr3t", "bot_token": "xoxb-1"},
            },
        )
    ).json()
    body = json.dumps({"type": "url_verification", "challenge": "chal-1"}).encode()
    timestamp = str(int(time.time()))
    response = await client.post(
        f"/api/v1/ingress/channels/{channel['id']}/webhook/slack",
        content=body,
        headers={
            "content-type": "application/json",
            "x-slack-signature": slack_signature("s3cr3t", timestamp, body),
            "x-slack-request-timestamp": timestamp,
        },
    )
    assert response.status_code == 200, response.text
    assert response.text == "chal-1"


@pytest.mark.asyncio
async def test_discord_ping_returns_pong(platform):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    client, _services, _settings = platform
    agent, _ = await create_agent(client)
    key = Ed25519PrivateKey.generate()
    public_key = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    ).hex()
    channel = (
        await client.post(
            "/api/v1/channels",
            json={
                "name": "Discord测试",
                "agent_id": agent["id"],
                "provider": "discord",
                "config": {"public_key": public_key},
                "credentials": {"bot_token": "bot-token"},
            },
        )
    ).json()
    body = json.dumps({"type": 1}).encode()
    timestamp = "1700000000"
    response = await client.post(
        f"/api/v1/ingress/channels/{channel['id']}/webhook/discord",
        content=body,
        headers={
            "content-type": "application/json",
            "x-signature-ed25519": key.sign(timestamp.encode() + body).hex(),
            "x-signature-timestamp": timestamp,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"type": 1}


@pytest.mark.asyncio
async def test_feishu_message_runs_agent_and_delivers_reply(platform, monkeypatch):
    client, services, settings = platform
    agent, _ = await create_agent(client)
    channel = (
        await client.post(
            "/api/v1/channels",
            json={
                "name": "飞书测试",
                "agent_id": agent["id"],
                "provider": "feishu",
                "config": {"app_id": "cli_test"},
                "credentials": {
                    "app_secret": "secret",
                    "verification_token": "vtoken",
                    "encrypt_key": "ekey",
                },
            },
        )
    ).json()
    payload = {
        "header": {"event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou-1"}},
            "message": {
                "message_id": "om-1",
                "chat_id": "oc-1",
                "message_type": "text",
                "content": '{"text":"hello"}',
            },
        },
    }
    response = await client.post(
        f"/api/v1/ingress/channels/{channel['id']}/webhook/feishu", json=payload
    )
    assert response.status_code == 200, response.text
    await _wait_for_inbound_run(services, channel["id"])

    async def fake_deliver(**_kwargs):
        return DeliveryResult(ok=True, external_message_id="om-out-1", http_status=200)

    monkeypatch.setattr(adapter_for("feishu"), "deliver", fake_deliver)
    await execute_next(services, settings)
    for _ in range(30):
        async with services.platform.engine.connect() as connection:
            delivery = await one(
                connection,
                """SELECT status,attempts FROM channel_deliveries
                WHERE channel_id=:channel ORDER BY created_at DESC LIMIT 1""",
                channel=channel["id"],
            )
        if delivery and delivery["status"] == "delivered":
            break
        await asyncio.sleep(0.1)
    assert delivery["status"] == "delivered"
    assert delivery["attempts"] == 1
