import hashlib
import hmac
import json
import time
from uuid import uuid4

import httpx
import pytest
from test_model_gateway import setup as setup_model
from test_platform_postgres import conversation, create_agent
from test_platform_postgres import platform as _platform_fixture

from agent_platform.modules.evaluation.scoring import JudgeResult, ProfileRef, verdict
from agent_platform.modules.knowledge.chunking import ChunkingPolicy, chunk_document
from agent_platform.modules.knowledge.vector_store import VectorIndex

platform = _platform_fixture


def test_chunk_boundaries_preserve_source_and_heading():
    text = "# One\n" + ("alpha " * 70) + "\n# Two\n" + ("beta " * 70)
    chunks = chunk_document(text, ChunkingPolicy(size=100, overlap=10))
    assert all(
        text[c["start"] : c["end"]] == c["content"] and len(c["content"]) <= 100 for c in chunks
    )
    assert all(not ("# One" in c["content"] and "# Two" in c["content"]) for c in chunks)
    assert {c["heading"] for c in chunks} == {"# One", "# Two"}


def test_judge_cannot_override_hard_failure():
    assert verdict(False, judge={"score": 5}) == "failed"
    assert verdict(True, judge={"score": 5}) == "needs_review"
    assert verdict(True, judge_error="timeout") == "error"
    with pytest.raises(ValueError):
        JudgeResult.model_validate(
            {"correctness": {"status": "scored", "score": 6, "reason": "bad"}}
        )


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_named_staff_cannot_reply_as_another_assignee(platform):
    client, _, _ = platform
    alice = (await client.post("/api/v1/staff", json={"name": "Alice"})).json()
    bob = (await client.post("/api/v1/staff", json={"name": "Bob"})).json()
    ah = {"Authorization": "Bearer " + alice["token"]}
    bh = {"Authorization": "Bearer " + bob["token"]}
    conv = await conversation(client)
    handoff = (
        await client.post(f"/api/v1/conversations/{conv['id']}/handoffs", json={"reason": "help"})
    ).json()
    result = await client.post(
        f"/api/v1/handoffs/{handoff['id']}/transition",
        json={"operation": "claim", "revision": 1},
        headers=ah,
    )
    assert result.status_code == 200, result.text
    assert result.json()["assignee"] == "staff:" + alice["id"]
    assert (
        await client.post(
            f"/api/v1/conversations/{conv['id']}/human-replies",
            json={"content": "wrong"},
            headers=bh,
        )
    ).status_code == 403
    assert (
        await client.post(
            f"/api/v1/conversations/{conv['id']}/human-replies",
            json={"content": "hello"},
            headers=ah,
        )
    ).status_code == 200
    await client.patch("/api/v1/staff/" + alice["id"], json={"enabled": False})
    assert (await client.get("/api/v1/me", headers=ah)).status_code == 401
    assert alice["token"] not in (await client.get("/api/v1/staff")).text


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_conversation_transition_revision(platform):
    client, _, _ = platform
    conv = await conversation(client)
    path = f"/api/v1/conversations/{conv['id']}/transition"
    assert (await client.post(path, json={"operation": "close", "revision": 1})).status_code == 200
    assert (await client.post(path, json={"operation": "reopen", "revision": 1})).status_code == 409
    assert (await client.post(path, json={"operation": "reopen", "revision": 2})).status_code == 200


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_channel_signature_and_token_rotation(platform, monkeypatch):
    client, _, _ = platform
    agent, _ = await create_agent(client)
    monkeypatch.setenv("AGENT_CHANNEL_SECRET_TEST", "test-secret")
    channel = (
        await client.post(
            "/api/v1/channels",
            json={
                "name": "signed",
                "agent_id": agent["id"],
                "signing_secret_ref": "AGENT_CHANNEL_SECRET_TEST",
            },
        )
    ).json()
    path = f"/api/v1/ingress/channels/{channel['id']}/messages"
    data = json.dumps({"message_id": "m1", "session_id": "s1", "text": "hello"}).encode()
    timestamp = str(int(time.time()))
    signature = hmac.new(
        b"test-secret", timestamp.encode() + b"." + data, hashlib.sha256
    ).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Channel-Token": channel["token"],
        "X-Channel-Timestamp": timestamp,
        "X-Channel-Signature": signature,
    }
    assert (
        await client.post(path, content=data, headers={**headers, "X-Channel-Signature": "bad"})
    ).status_code == 401
    assert (await client.post(path, content=data, headers=headers)).status_code == 202
    assert (
        await client.post(path, content=data, headers={**headers, "X-Channel-Timestamp": "1"})
    ).status_code == 401
    assert (await client.post(f"/api/v1/channels/{channel['id']}/rotate-token")).status_code == 200
    assert (await client.post(path, content=data, headers=headers)).status_code == 401


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_document_queue_deduplication_pages_and_cancel(platform):
    client, services, settings = platform
    base = (await client.post("/api/v1/knowledge-bases", json={"name": "files"})).json()
    url = f"/api/v1/knowledge-bases/{base['id']}/uploads?filename=policy.md"
    content = "# Service\n退货政策：七天内申请。".encode()
    first = await client.post(url, content=content)
    assert first.status_code == 202, first.text
    assert (await client.post(url, content=content)).json()["id"] == first.json()["id"]
    assert await services.platform.ingestion.run_next(settings.tenant_id)
    rows = (await client.get(f"/api/v1/knowledge-bases/{base['id']}/jobs")).json()
    assert rows[0]["status"] == "completed", rows
    pages = (await client.get(f"/api/v1/knowledge-jobs/{rows[0]['id']}/pages")).json()
    assert pages[0]["page_num"] == 1
    assert (
        await client.get("/api/v1/knowledge/search", params={"q": "退货", "kb": base["id"]})
    ).json()
    other = (await client.post(url, content=b"cancel me")).json()
    await client.post(f"/api/v1/knowledge-jobs/{other['id']}/cancel")
    assert not await services.platform.ingestion.run_next(settings.tenant_id)
    assert (
        await client.post(
            f"/api/v1/knowledge-bases/{base['id']}/uploads?filename=file.caj", content=b"not text"
        )
    ).status_code == 503


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_milvus_contract_and_postfilter(platform):
    client, services, settings = platform
    _, _, profile = await setup_model(client, "embed")
    base = (await client.post("/api/v1/knowledge-bases", json={"name": "vectors"})).json()
    doc = (
        await client.post(
            f"/api/v1/knowledge-bases/{base['id']}/documents",
            json={"title": "terms", "content": "退款规则"},
        )
    ).json()
    ids = []

    def provider(request):
        body = json.loads(request.content)
        if request.url.path.endswith("/upsert"):
            ids.extend(x["id"] for x in body["data"])
            assert all(x["tenant_id"] == settings.tenant_id for x in body["data"])
        if request.url.path.endswith("/search"):
            assert settings.tenant_id in body["filter"]
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": [
                        {"id": ids[0], "distance": 0.8},
                        {"id": str(uuid4()), "distance": 0.9},
                    ],
                },
            )
        return httpx.Response(200, json={"code": 0, "data": {}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as mock:
        index = VectorIndex(
            services.platform.engine,
            services.platform.gateway,
            settings.model_copy(update={"milvus_url": "http://localhost:19530"}),
            mock,
        )
        result = await index.build(
            settings.tenant_id, "test", base["id"], ProfileRef(id=profile["id"], version=1)
        )
        assert result["chunks"] == 1
        assert len(await index.search(settings.tenant_id, base["id"], "退款")) == 1
        await client.patch(f"/api/v1/documents/{doc['id']}", json={"enabled": False})
        assert await index.search(settings.tenant_id, base["id"], "退款") == []


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_gateway_metrics_do_not_mix_units(platform):
    client, _, _ = platform
    response = await client.get("/api/v1/observability/gateways")
    assert response.status_code == 200, response.text
    assert response.json()["known_usage"] == []


@pytest.mark.asyncio
async def test_judge_schema_and_evidence_contract():
    from agent_platform.modules.evaluation.scoring import Scorer
    from agent_platform.platform.persistence.store import DomainError

    dimension = {
        "status": "scored",
        "score": 4,
        "reason": "Matches supplied reference",
        "evidence_refs": ["reference:0"],
    }
    payload = {
        key: dict(dimension) for key in ("correctness", "groundedness", "completeness", "clarity")
    }
    profile = ProfileRef(id=uuid4(), version=3)

    class Gateway:
        async def invoke(self, tenant, pid, version, request):
            assert (tenant, pid, version) == ("test", profile.id, 3)
            assert not request.tools
            return {
                "call_id": uuid4(),
                "payload": {"content": json.dumps(payload), "tool_calls": []},
            }

    scorer = Scorer(Gateway())
    result = await scorer.judge("test", profile, "delivery?", "48 hours", {}, ["48 hours"])
    assert result["analysis_only"] and result["version"] == 3
    payload["correctness"]["evidence_refs"] = ["invented-source"]
    with pytest.raises(DomainError, match="judge_invalid_evidence_reference"):
        await scorer.judge("test", profile, "delivery?", "48 hours", {}, ["48 hours"])


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_ticket_new_storage_and_lifecycle(platform):
    from sqlalchemy import text

    from agent_platform.modules.customer_service.service import TicketProposal

    client, services, settings = platform
    conv = await conversation(client)
    proposal = await services.platform.customer.propose(
        settings.tenant_id,
        conv["id"],
        uuid4(),
        TicketProposal(title="Repair", description="Screen broken"),
    )
    response = await client.post(
        f"/api/v1/actions/{proposal['action_id']}/decision", json={"approve": True}
    )
    assert response.status_code == 200, response.text
    ticket = response.json()
    url = f"/api/v1/tickets/{ticket['id']}"

    async def update(status, note):
        nonlocal ticket
        reply = await client.patch(
            url, json={"status": status, "note": note, "revision": ticket["revision"]}
        )
        if reply.status_code == 200:
            ticket = reply.json()
        return reply

    assert (await update("in_progress", "Investigating")).status_code == 200
    assert (await update("waiting_customer", "Need photo")).status_code == 200
    assert (await update("in_progress", "Photo received")).status_code == 200
    assert (await update("resolved", "")).status_code == 400
    assert (await update("resolved", "Screen replaced")).status_code == 200
    assert ticket["resolution"] == "Screen replaced" and ticket["resolved_at"]
    assert (await update("closed", "Customer confirmed")).status_code == 200
    assert ticket["closed_at"]
    assert (await update("in_progress", "reopen")).status_code == 409
    async with services.platform.engine.connect() as c:
        params = {"id": ticket["id"], "t": settings.tenant_id}
        row = (
            await c.execute(
                text(
                    "SELECT description,custom_fields FROM tickets_detail WHERE ticket_id=:id AND tenant_id=:t"
                ),
                params,
            )
        ).one()
        assert row.description == "Screen broken" and row.custom_fields == {}
        logs = (
            await c.execute(
                text(
                    "SELECT actor_id,action_type FROM ticket_process_log WHERE ticket_id=:id AND tenant_id=:t ORDER BY id"
                ),
                params,
            )
        ).all()
        assert logs[0].action_type == "created"
        assert sum(log.action_type == "status_changed" for log in logs) == 5
        assert all(log.actor_id != "legacy-writer" for log in logs)
