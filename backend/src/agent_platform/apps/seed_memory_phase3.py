"""Phase 3 acceptance dataset: event memory and search (事件记忆与搜索).

Seeds the scenarios the Phase 3 acceptance report (A1~A7) exercises so the
console can show them on server startup:

- A1/AC-1  ticket lifecycle events written as ``ticket:{no}:created/resolved/
  closed`` items (``source_type='ticket'``, ``confidence=1.0``, TTL
  ``expires_at``), searchable via the console memory search (content_tsv is a
  stored generated column, so plain INSERTs are immediately searchable);
- A2/AC-2  ``commitment:{log_id}`` items with ``expires_at=NULL`` and
  ``source_ref='{ticket_id}:{log_id}'``;
- A3/AC-3  in-flight ``open_ticket:{no}`` items injected into runs — genuine
  ``memory_injection_log`` rows with ``section='event'`` and ``score=NULL``
  plus the matching ``memory.injected`` event counts;
- A4b      two equally relevant events (3 days vs ~150 days old) so the decay
  ranking puts the recent one first;
- A5/AC-5  an event past TTL soft-``expired`` (searchable nowhere);
- A6/AC-6  resolved/closed tickets whose ``open_ticket:`` row is soft-expired
  and disappears from the 【待跟进事项】 injection;
- R5       one ``memory.event.written`` audit per ticket event with the exact
  details shape emitted by ``memory/events.py``
  (``{event, items_written, pii_masked}``, actor ``memory``, resource = the
  ticket id).

Deterministic uuid5 ids under ``ns("phase3")``; each customer block is
skipped once its identity binding exists, so re-runs never duplicate rows.
"""

from datetime import timedelta

from sqlalchemy import text

from agent_platform.apps.seed_memory_common import (
    current_audit_actor,
    ensure_agent,
    identity_bound,
    insert_audit,
    insert_conversation,
    insert_customer,
    insert_identity,
    insert_item,
    insert_run,
    item,
    ns,
    pairs,
    run_standalone,
    ts,
    uid,
)
from agent_platform.modules.agent_config.service import AgentConfig

NS = ns("phase3")
AGENT_NAME = "事件检索演示助手"
AGENT_DESC = "阶段3事件记忆与搜索验收演示（工单事件写入、待跟进事项注入、事件检索）"
TTL_DAYS = 180  # memory_event_ttl_days default


def ticket_uid(ticket_no: str) -> str:
    """Stand-in ticket id (the real writer stores tickets.id as source_ref)."""
    return uid(NS, "ticket", ticket_no)


async def insert_event_item(conn, tenant, iid, cid, key, content, *,
                            source_ref, created_h, updated_h=None, last_ref_h=None,
                            status="active", source_type="ticket", conf=1.0,
                            expires_at=None, rc=0):
    """One event item with the exact write shape of ``add_event_item`` plus the
    TTL column the common ``insert_item`` helper does not cover."""
    await conn.execute(
        text("""INSERT INTO memory_items(id,tenant_id,customer_id,type,key,content,confidence,status,
                source_type,source_ref,revision,last_referenced_at,reference_count,
                created_at,updated_at,protected,expires_at)
                VALUES(:id,:t,:c,'event',:k,:co,:cf,:st,:sty,:sr,1,:lra,:rc,:ca,:ua,false,:exp)
                ON CONFLICT DO NOTHING"""),
        {"id": iid, "t": tenant, "c": cid, "k": key, "co": content, "cf": conf, "st": status,
         "sty": source_type, "sr": source_ref,
         "lra": ts(last_ref_h if last_ref_h is not None else created_h),
         "rc": rc, "ca": ts(created_h),
         "ua": ts(updated_h if updated_h is not None else created_h),
         "exp": expires_at},
    )


def ttl(created_h: float):
    """``expires_at`` for TTL-managed event rows (commitments pass None)."""
    return ts(created_h) + timedelta(days=TTL_DAYS)


def ev(key, content, created_h, **kw):
    """Event-item spec dict (avoids the C408-flagged ``dict()`` call form)."""
    return {"key": key, "content": content, "created_h": created_h, **kw}


CUSTOMERS = [
    {
        # A1/A2/A4b/A5/A6: full lifecycle ticket + pending commitment + decay pair.
        "name": "chenjing", "external_id": "demo-p3-chenjing", "created_h": 40 * 24,
        "summary": "客户陈静（华宇家电连锁）：工单 TK-20260928 批次 B2310 空调冷媒不足已解决，补偿滤网套装承诺待兑现；历史批次问题可检索。",
        "profile": item("profile", "company", "华宇家电连锁（华北 8 家门店）", .97,
                        st="manual", rc=6, ch=40 * 24, uh=3 * 24),
        "events": [
            # Ticket lifecycle items — the exact keys/content of memory/events.py.
            ev(key="ticket:TK-20260928:created",
                 content="工单 TK-20260928 已创建：客户反馈空调 KFR-35GW（批次 B2310）制冷效果差，要求安排上门检测",
                 created_h=22 * 24, rc=3, expires_at=ttl(22 * 24)),
            ev(key="ticket:TK-20260928:resolved",
                 content="工单 TK-20260928 已解决：上门检测确认批次 B2310 冷媒不足，已现场加注冷媒并补偿延保 6 个月",
                 created_h=3 * 24, rc=4, expires_at=ttl(3 * 24)),
            # AC-6: resolved ticket → in-flight row soft-expired, kept for audit.
            ev(key="open_ticket:TK-20260928",
                 content="空调 KFR-35GW（批次 B2310）制冷效果差，要求安排上门检测",
                 created_h=22 * 24, updated_h=3 * 24, status="expired", rc=2,
                 expires_at=ttl(22 * 24)),
            # AC-2: pending commitment — never expires, source_ref carries log id.
            ev(key="commitment:10001",
                 content="48 小时内寄出补偿的滤网套装，物流单号将以短信同步客户",
                 created_h=3 * 24 - 1, rc=3, expires_at=None,
                 source_ref=f"{ticket_uid('TK-20260928')}:10001"),
            # A4b (old half): equally relevant to “批次 B2310”, referenced ~150d ago.
            ev(key="evt-202606-batch",
                 content="2026-06 历史反馈：批次 B2310 空调遥控器按键失灵，已登记补寄并回访确认",
                 created_h=150 * 24, last_ref_h=150 * 24, rc=1,
                 source_type="extracted", conf=.9, expires_at=None,
                 source_ref="conv-p3-chenjing"),
            # AC-5: past TTL → soft-expired, invisible to search and injection.
            ev(key="ticket:TK-20260310:closed",
                 content="工单 TK-20260310 已关闭：客户确认补寄配件已签收，无其他诉求",
                 created_h=200 * 24, updated_h=10 * 24, status="expired",
                 expires_at=ts(10 * 24)),
        ],
        "runs": [
            # AC-2/AC-3: pending commitment shows up in the 【待跟进事项】 section.
            (26, "你好，我是华宇家电的陈静，工单 TK-20260928 的空调修好了，但说好的滤网套装什么时候寄？",
             ["profile:company", "event:commitment:10001"]),
            # Console search demo: “批次 B2310” hits both the 3-day and 150-day events.
            (2, "另外帮我查下批次 B2310 之前还有没有其他问题记录。",
             ["profile:company", "event:commitment:10001"]),
        ],
        "audits": [
            # One memory.event.written per event task (R5): counts only, actor "memory".
            ("TK-20260928", 22 * 24, {"event": "created", "items_written": 2, "pii_masked": 0}),
            ("TK-20260928", 3 * 24, {"event": "resolved", "items_written": 1, "pii_masked": 0}),
            ("TK-20260928", 3 * 24 - 1, {"event": "commitment", "items_written": 1, "pii_masked": 0}),
        ],
    },
    {
        # A3/AC-3: in-flight ticket + fresh commitment → 待跟进事项 injection.
        "name": "liuyang", "external_id": "demo-p3-liuyang", "created_h": 15 * 24,
        "summary": "客户刘洋（宏达汽配）：在途工单 TK-20261019 订单 SO-8812 刹车片物流滞留，承运商加急中；承诺明早 10 点前电话回复物流轨迹。",
        "profile": item("profile", "company", "宏达汽车配件有限公司", .95,
                        st="manual", rc=4, ch=15 * 24, uh=5),
        "events": [
            ev(key="ticket:TK-20261019:created",
                 content="工单 TK-20261019 已创建：客户反馈订单 SO-8812 一批刹车片物流滞留中转仓 3 天，要求加急并同步轨迹",
                 created_h=2 * 24, rc=2, expires_at=ttl(2 * 24)),
            # AC-3: active in-flight row — the one the injection section renders.
            ev(key="open_ticket:TK-20261019",
                 content="订单 SO-8812 刹车片物流滞留中转仓：承运商已加急处理，预计 48 小时内恢复运输",
                 created_h=2 * 24, updated_h=5, rc=5, expires_at=ttl(2 * 24)),
            ev(key="commitment:10007",
                 content="明天上午 10 点前电话回复加急件最新物流轨迹",
                 created_h=5, rc=2, expires_at=None,
                 source_ref=f"{ticket_uid('TK-20261019')}:10007"),
            ev(key="evt-202609-breakage",
                 content="2026-09 一批火花塞到货破损，已完成退换货并升级防震包装",
                 created_h=35 * 24, rc=1, source_type="extracted", conf=.9,
                 expires_at=None, source_ref="conv-p3-liuyang"),
        ],
        "runs": [
            (5, "我是宏达汽配的刘洋，SO-8812 那批刹车片到底什么时候能到？产线停产待料了！",
             ["profile:company", "event:open_ticket:TK-20261019", "event:commitment:10007"]),
            (1, "物流有新进展麻烦第一时间通知我，电话或者微信都行。",
             ["profile:company", "event:open_ticket:TK-20261019", "event:commitment:10007"]),
        ],
        "audits": [
            ("TK-20261019", 2 * 24, {"event": "created", "items_written": 2, "pii_masked": 0}),
            ("TK-20261019", 5, {"event": "commitment", "items_written": 1, "pii_masked": 0}),
        ],
    },
    {
        # A6/UAT: ticket closed → open_ticket and commitment both expired, the
        # 待跟进事项 section disappears; conclusions stay searchable (AC-1).
        "name": "sunqian", "external_id": "demo-p3-sunqian", "created_h": 20 * 24,
        "summary": None,
        "profile": item("profile", "company", "悦读时光书店", .9,
                        sr="conv-p3-sunqian", rc=2, ch=20 * 24, uh=7 * 24),
        "events": [
            ev(key="ticket:TK-20261010:created",
                 content="工单 TK-20261010 已创建：企业团购 200 册图书到货缺 12 册，客户要求补发",
                 created_h=10 * 24, rc=1, expires_at=ttl(10 * 24)),
            ev(key="ticket:TK-20261010:resolved",
                 content="工单 TK-20261010 已解决：缺册 12 册已补发，顺丰 SF6612 单号已短信同步客户",
                 created_h=8 * 24, rc=2, expires_at=ttl(8 * 24)),
            ev(key="ticket:TK-20261010:closed",
                 content="工单 TK-20261010 已关闭：客户确认补发图书已全部签收，无其他诉求",
                 created_h=7 * 24, rc=1, expires_at=ttl(7 * 24)),
            ev(key="open_ticket:TK-20261010",
                 content="企业团购 200 册图书到货缺 12 册，客户要求补发",
                 created_h=10 * 24, updated_h=8 * 24, status="expired",
                 expires_at=ttl(10 * 24)),
            # 承诺已兑现：工单关闭联动置 expired（UAT 核对项）。
            ev(key="commitment:10012",
                 content="补发图书签收后电话回访确认无缺册",
                 created_h=8 * 24, updated_h=7 * 24, status="expired", expires_at=None,
                 source_ref=f"{ticket_uid('TK-20261010')}:10012"),
        ],
        "runs": [
            # AC-6: no open ticket/commitment left → events count is 0, only the
            # profile section injects; the resolved conclusion stays searchable.
            (7 * 24, "补发的图书都收到了，没问题了，谢谢。",
             ["profile:company"]),
        ],
        "audits": [
            ("TK-20261010", 10 * 24, {"event": "created", "items_written": 2, "pii_masked": 0}),
            ("TK-20261010", 8 * 24, {"event": "resolved", "items_written": 1, "pii_masked": 0}),
            ("TK-20261010", 7 * 24, {"event": "closed", "items_written": 1, "pii_masked": 0}),
        ],
    },
]


async def seed_customer(c, engine, model_backend, tenant, agent) -> bool:
    """Insert one customer block; skip entirely once its identity exists."""
    ext = c["external_id"]
    async with engine.connect() as conn:
        if await identity_bound(conn, tenant, "web", ext):
            return False
    cid = uid(NS, "customer", c["name"])
    conv_id = uid(NS, "conversation", ext)
    profile_id = uid(NS, "item", f'{c["name"]}:profile:company')
    event_ids = {e["key"]: uid(NS, "item", f'{c["name"]}:event:{e["key"]}') for e in c["events"]}
    id_by_label = {"profile:company": profile_id,
                   **{f"event:{k}": v for k, v in event_ids.items()}}

    async with engine.begin() as conn:
        last_run_h = min(h for h, _, _ in c["runs"])
        await insert_customer(conn, tenant, cid, c["created_h"], last_run_h)
        await insert_identity(conn, tenant, uid(NS, "identity", ext), cid, ext, c["created_h"])
        await insert_item(conn, tenant, profile_id, cid, c["profile"], last_referenced_h=last_run_h)
        for e in c["events"]:
            await insert_event_item(
                conn, tenant, event_ids[e["key"]], cid, e["key"], e["content"],
                source_ref=e.get("source_ref") or ticket_uid(
                    e["key"].split(":")[1] if ":" in e["key"] else e["key"]),
                created_h=e["created_h"], updated_h=e.get("updated_h"),
                last_ref_h=e.get("last_ref_h"), status=e.get("status", "active"),
                source_type=e.get("source_type", "ticket"), conf=e.get("conf", 1.0),
                expires_at=e.get("expires_at"), rc=e.get("rc", 0),
            )
        await insert_conversation(
            conn, tenant, conv_id, ext, pairs([msg for _, msg, _ in c["runs"]]),
            c["runs"][0][0] + 0.1, last_run_h, agent["id"], summary=c["summary"], cid=cid,
        )
        for n, (h, msg, injected) in enumerate(c["runs"]):
            await insert_run(
                conn, tenant, NS, f"{ext}:{n}", conv_id, agent, model_backend, msg, h,
                cid=cid,
                injected=[
                    (id_by_label[label],
                     label.split(":")[0],
                     # Event rows carry no ranking score (recency-ordered), so
                     # section='event' logs NULL exactly like the runtime.
                     None if label.startswith("event:") else c["profile"]["conf"])
                    for label in injected
                ],
            )
        for ticket_no, hours_ago, details in c["audits"]:
            await insert_audit(conn, tenant, "memory", "memory.event.written",
                               ticket_uid(ticket_no), details, hours_ago)
    return True


async def seed(runtime, settings) -> bool:
    """Idempotent entrypoint: True only when at least one customer was inserted."""
    p = runtime.platform
    tenant = settings.tenant_id
    actor = await current_audit_actor(p, tenant)
    agent = await ensure_agent(
        p, tenant, actor, AGENT_NAME, AGENT_DESC,
        AgentConfig(
            system_prompt="你是一名企业售后客服助手。优先关注客户的在途工单与未兑现承诺，回答时引用历史工单的处理结论。",
            tool_names=[],
            knowledge_base_ids=[],
        ),
    )
    inserted = False
    for cust in CUSTOMERS:
        if await seed_customer(cust, p.engine, settings.model_backend, tenant, agent):
            inserted = True
    return inserted


if __name__ == "__main__":
    run_standalone(seed, "阶段3事件记忆与搜索演示数据")
