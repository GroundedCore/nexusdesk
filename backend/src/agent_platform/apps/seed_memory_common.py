"""Shared helpers for the memory acceptance seed modules (phases 1-5).

Phase 6 ships its own self-contained seeder (``seed_memory_demo.py``); the
phase 1-5 acceptance datasets live in ``seed_memory_phase1.py`` …
``seed_memory_phase5.py`` and reuse the primitives here so every module
produces identically shaped rows: deterministic uuid5 ids, relative
timestamps anchored at "now", genuine-shaped runs with the standard 7-event
sequence, and audit records attributed to the local admin when present.

Every phase module exposes ``async def seed(runtime, settings) -> bool``
(idempotent — returns True only when it actually inserted) and can be run
standalone (``python -m agent_platform.apps.seed_memory_phaseN``) or through
the combined entrypoint ``seed_memory_acceptance``.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

from sqlalchemy import text

from agent_platform.apps.seed_memory_i18n import T, set_locale
from agent_platform.modules.agent_config.service import AgentConfig, AgentDraft
from agent_platform.modules.agent_runtime.bootstrap import runtime_services
from agent_platform.modules.model_gateway.contracts import Connection, Model, Profile
from agent_platform.platform.persistence.store import one
from agent_platform.settings import Settings

ROOT_NS = UUID("3f8b2a64-1c4a-4e2b-9b7e-7d5c6a1f0001")  # same root as the phase-6 seeder
LOCK_ID = 71432025  # shared advisory lock so seeders never run concurrently

NOW = datetime.now(UTC)


def default_reply() -> str:
    """Canned assistant reply for demo conversations, in the seed locale."""
    return T(zh="[演示模式] Runtime 已收到消息。发送“查询演示”可验证 HTTP 工具链路。",
             en='[Demo mode] Runtime received the message. Send "query demo" to verify the HTTP tool chain.',
             zh_tw="[演示模式] Runtime 已收到訊息。發送「查詢演示」可驗證 HTTP 工具鏈路。",
             hi='[डेमो मोड] Runtime ने संदेश प्राप्त कर लिया है। HTTP टूल चेन सत्यापित करने के लिए "query demo" भेजें।')


def ns(tag: str) -> UUID:
    """Per-phase uuid5 namespace so ids never collide across seed modules."""
    return uuid5(ROOT_NS, f"seed:{tag}")


def uid(namespace: UUID, kind: str, name: str) -> str:
    return str(uuid5(namespace, f"{kind}:{name}"))


def ts(hours: float) -> datetime:
    return NOW - timedelta(hours=hours)


def item(t, k, content, conf=0.9, status="active", st="extracted", sr=None, prot=False,
         rc=0, ch=24, uh=2, prev=None, rev=1):
    """Memory-item spec tuple — same shape as the phase-6 seeder's ``I()``."""
    return {"t": t, "k": k, "content": content, "conf": conf, "status": status, "st": st, "sr": sr,
            "prot": prot, "rc": rc, "ch": ch, "uh": uh, "prev": prev, "rev": rev}


def pairs(messages: list[str]) -> list[dict]:
    """Build a conversation history jsonb from user messages (demo replies)."""
    history = []
    for msg in messages:
        history.append({"role": "user", "content": msg})
        history.append({"role": "assistant", "content": default_reply()})
    return history


async def ensure_demo_profile(p, tenant, actor):
    """Ensure the sample demo model chain; returns (profile, published version)."""
    catalog = p.gateway.catalog

    async def ensure(kind, body):
        rows = await catalog.list(tenant, kind)
        existing = next((r for r in rows if r["name"] == body.name), None)
        return existing or await catalog.save(tenant, actor, kind, body)

    connection = await ensure("connections", Connection(
        name=T(zh="示例模拟连接", en="Sample Demo Connection",
               zh_tw="示例模擬連接", hi="सैंपल डेमो कनेक्शन"), protocol="demo"))
    model = await ensure(
        "models",
        Model(name=T(zh="示例模拟 Chat", en="Sample Demo Chat",
                     zh_tw="示例模擬 Chat", hi="सैंपल डेमो Chat"), connection_id=connection["id"],
              model_name="demo", operations=["chat"], tool_calling=True),
    )
    profile = await ensure("profiles", Profile(
        name=T(zh="示例客服模型方案", en="Sample Support Model Profile",
               zh_tw="示例客服模型方案", hi="सैंपल सपोर्ट मॉडल प्रोफ़ाइल"),
        operation="chat", model_id=model["id"]))
    version = profile["published_version"]
    if not version:
        published = await catalog.publish(tenant, actor, profile["id"], profile["revision"])
        version = published["version"]
    return profile, version


async def ensure_agent(p, tenant, actor, name, description, config: AgentConfig):
    """Ensure a published demo agent with the given config; returns the agent row."""
    profile, version = await ensure_demo_profile(p, tenant, actor)
    config.model_profile_id = profile["id"]
    config.model_profile_version = version
    agents = await p.agents.list(tenant)
    agent = next((a for a in agents if a["name"] == name), None)
    if not agent:
        agent = await p.agents.create(
            tenant, actor, AgentDraft(name=name, description=description, config=config))
        await p.agents.publish(tenant, actor, agent["id"], agent["draft_revision"])
    return agent


async def current_audit_actor(p, tenant) -> str:
    async with p.engine.connect() as conn:
        admin = await one(conn, "SELECT user_id FROM local_admin_credentials WHERE tenant_id=:t LIMIT 1", t=tenant)
    return f"enterprise:{admin['user_id']}" if admin else "seed"


async def identity_bound(conn, tenant, source, external_key) -> bool:
    """Idempotency guard: has this identity binding already been seeded?"""
    row = await one(
        conn,
        "SELECT id FROM memory_identities WHERE tenant_id=:t AND source=:s AND external_key=:k",
        t=tenant, s=source, k=external_key,
    )
    return bool(row)


async def insert_customer(conn, tenant, cid, created_h, updated_h):
    await conn.execute(
        text("INSERT INTO memory_customers(id,tenant_id,created_at,updated_at) VALUES(:id,:t,:ca,:ua) ON CONFLICT DO NOTHING"),
        {"id": cid, "t": tenant, "ca": ts(created_h), "ua": ts(updated_h)},
    )


async def insert_identity(conn, tenant, iid, cid, external_key, created_h, source="web"):
    await conn.execute(
        text("INSERT INTO memory_identities(id,tenant_id,customer_id,source,external_key,created_at) VALUES(:id,:t,:c,:s,:k,:ca) ON CONFLICT DO NOTHING"),
        {"id": iid, "t": tenant, "c": cid, "s": source, "k": external_key, "ca": ts(created_h)},
    )


async def insert_item(conn, tenant, iid, cid, spec, last_referenced_h=None):
    """Insert one memory item from an ``item()`` spec dict."""
    await conn.execute(
        text("""INSERT INTO memory_items(id,tenant_id,customer_id,type,key,content,confidence,status,
                source_type,source_ref,prev_content,revision,last_referenced_at,reference_count,
                created_at,updated_at,protected)
                VALUES(:id,:t,:c,:ty,:k,:co,:cf,:st,:sty,:sr,:prev,:rev,:lra,:rc,:ca,:ua,:prot)
                ON CONFLICT DO NOTHING"""),
        {"id": iid, "t": tenant, "c": cid, "ty": spec["t"], "k": spec["k"], "co": spec["content"],
         "cf": spec["conf"], "st": spec["status"], "sty": spec["st"], "sr": spec["sr"],
         "prev": spec["prev"], "rev": spec["rev"],
         "lra": ts(last_referenced_h) if last_referenced_h is not None else ts(spec["ch"]),
         "rc": spec["rc"], "ca": ts(spec["ch"]), "ua": ts(spec["uh"]), "prot": spec["prot"]},
    )


async def insert_conversation(conn, tenant, conv_id, external_id, history, created_h, updated_h,
                              agent_id, summary=None, cid=None, source="business"):
    await conn.execute(
        text("""INSERT INTO runtime_conversations(id,tenant_id,external_id,history,created_at,mode,agent_id,
                updated_at,revision,source,summary,customer_id)
                VALUES(:id,:t,:ext,CAST(:hist AS jsonb),:ca,'bot',:aid,:ua,1,:src,:sum,:cid)
                ON CONFLICT (tenant_id,external_id) DO NOTHING"""),
        {"id": conv_id, "t": tenant, "ext": external_id, "hist": json.dumps(history, ensure_ascii=False),
         "ca": ts(created_h), "aid": agent_id, "ua": ts(updated_h), "src": source, "sum": summary, "cid": cid},
    )


def run_config(agent, model_backend):
    return {"agent": {"id": str(agent["id"]), "tools": [], "config": agent["draft"],
                      "version": 1, "is_example": False},
            "model_name": "", "fingerprint": "0" * 64, "model_backend": model_backend}


def default_events(cid, reply, injected_ids, counts, gateway_call_id):
    """The genuine 7-event run sequence (seq 1-7) with a memory.injected event."""
    return [
        (1, "run.queued", {}),
        (2, "run.started", {}),
        (3, "model.started", {"round": 1}),
        (4, "model.completed", {"round": 1, "usage": {}, "tool_calls": 0, "duration_ms": 141,
                                "first_token_ms": 38, "gateway_call_id": gateway_call_id}),
        (5, "memory_extract.queued", {"customer_id": cid}),
        (6, "memory.injected", {**counts, "item_ids": injected_ids}),
        (7, "run.completed", {"output": reply, "error_code": None}),
    ]


async def insert_event(conn, run_id, seq, etype, data, at):
    await conn.execute(
        text("INSERT INTO runtime_events(run_id,seq,type,data,created_at) VALUES(:rid,:seq,:ty,CAST(:d AS jsonb),:ca) ON CONFLICT DO NOTHING"),
        {"rid": run_id, "seq": seq, "ty": etype, "d": json.dumps(data, ensure_ascii=False), "ca": at},
    )


async def insert_injection_log(conn, tenant, run_id, cid, iid, section, score, at):
    await conn.execute(
        text("INSERT INTO memory_injection_log(tenant_id,run_id,customer_id,item_id,section,score,injected_at) VALUES(:t,:rid,:cid,:iid,:sec,:score,:at)"),
        {"t": tenant, "rid": run_id, "cid": cid, "iid": iid, "sec": section, "score": score, "at": at},
    )


async def insert_run(conn, tenant, namespace, run_key, conv_id, agent, model_backend, msg, hours,
                     cid=None, injected=None, events=None, reply_text=None):
    """One completed run + its two messages + events + injection log rows.

    ``injected``: list of (item_id, section, score) logged to memory_injection_log
    and counted in the memory.injected event. ``events``: full (seq, type, data)
    list replacing the default 7-event sequence when given.
    """
    reply = reply_text if reply_text is not None else default_reply()
    run_id = uid(namespace, "run", run_key)
    created, started, finished = ts(hours), ts(hours) + timedelta(milliseconds=30), ts(hours) + timedelta(milliseconds=235)
    await conn.execute(
        text("""INSERT INTO runtime_runs(id,conversation_id,tenant_id,input,config,status,output,
                event_seq,queue_expires_at,created_at,started_at,finished_at)
                VALUES(:id,:cid,:t,:inp,CAST(:cfg AS jsonb),'completed',:out,:eseq,:qexp,:ca,:sa,:fa)
                ON CONFLICT DO NOTHING"""),
        {"id": run_id, "cid": conv_id, "t": tenant, "inp": msg,
         "cfg": json.dumps(run_config(agent, model_backend), ensure_ascii=False, default=str),
         "out": reply, "eseq": len(events) if events else 7,
         "qexp": created + timedelta(minutes=2), "ca": created, "sa": started, "fa": finished},
    )
    for role, when in (("user", created), ("assistant", finished)):
        await conn.execute(
            text("INSERT INTO conversation_messages(id,conversation_id,role,content,run_id,created_at) VALUES(:id,:cid,:r,:co,:rid,:ca)"),
            {"id": uid(namespace, "message", f"{run_id}:{role}"), "cid": conv_id, "r": role,
             "co": msg if role == "user" else reply, "rid": run_id, "ca": when},
        )
    injected = injected or []
    if events is None:
        counts = {"flags": sum(1 for _, sec, _ in injected if sec == "flag"),
                  "items": sum(1 for _, sec, _ in injected if sec in ("profile", "preference")),
                  "events": sum(1 for _, sec, _ in injected if sec == "event")}
        events = default_events(cid, reply, [iid for iid, _, _ in injected], counts,
                                uid(namespace, "gw", run_id))
    for seq, etype, data in events:
        await insert_event(conn, run_id, seq, etype, data, finished)
    for iid, section, score in injected:
        await insert_injection_log(conn, tenant, run_id, cid, iid, section, score, finished)
    return run_id


async def insert_audit(conn, tenant, actor, action, resource_id, details, hours_ago):
    await conn.execute(
        text("INSERT INTO audit_records(tenant_id,actor,action,resource,details,created_at) VALUES(:t,:actor,:action,:res,CAST(:d AS jsonb),:ca)"),
        {"t": tenant, "actor": actor, "action": action, "res": str(resource_id),
         "d": json.dumps(details, ensure_ascii=False), "ca": ts(hours_ago)},
    )


def run_standalone(seed_fn: Callable[..., Awaitable[bool]], label: str | Callable[[], str]):
    """Standalone entrypoint: run one phase's seed under the shared advisory lock.

    ``label`` may be a zero-arg callable returning a T() string — it is
    resolved after ``set_locale`` so standalone runs print in the seed locale.
    """

    async def main():
        settings = Settings()
        tenant = settings.tenant_id
        set_locale(settings)
        async with runtime_services(settings) as runtime, runtime.platform.engine.connect() as lock:
            await lock.execute(text(f"SELECT pg_advisory_lock({LOCK_ID})"))
            try:
                inserted = await seed_fn(runtime, settings)
                text_label = label() if callable(label) else label
                state = (T(zh="已入库", en="seeded", zh_tw="已入庫", hi="सीड हो गया") if inserted
                         else T(zh="已存在，跳过", en="already present, skipped",
                                zh_tw="已存在，略過", hi="पहले से मौजूद, छोड़ा गया"))
                print(f"{text_label}: {state} (tenant: {tenant}).")
            finally:
                await lock.execute(text(f"SELECT pg_advisory_unlock({LOCK_ID})"))

    asyncio.run(main())
