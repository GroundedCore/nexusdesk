"""Seed the memory-visualization demo dataset; safe to run again.

Quickstart boots straight into a console where the customer-memory views
(list stats, 8-tab customer detail, injection records, item lifecycle) have
realistic data to show: four demo customers with curated profile/preference/
event/flag items, pending suggestions, service marks, audit history and — the
Phase 6 centerpiece — genuine-shaped ``memory_injection_log`` rows plus the
matching ``memory.injected`` run events, so every explainability view works
out of the box.

Everything is deterministic (uuid5 ids) and each customer block is skipped
once its identity binding exists, so re-runs never duplicate rows. Runs and
logs are seeded as *results* — the runtime worker is not up while bootstrap
seeds, so conversations cannot be produced live at this stage.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

from sqlalchemy import text

from agent_platform.modules.agent_config.service import AgentConfig, AgentDraft
from agent_platform.modules.agent_runtime.bootstrap import runtime_services
from agent_platform.modules.model_gateway.contracts import Connection, Model, Profile
from agent_platform.platform.persistence.store import one
from agent_platform.settings import Settings

NS = UUID("3f8b2a64-1c4a-4e2b-9b7e-7d5c6a1f0001")  # fixed namespace → deterministic demo ids


def uid(kind: str, name: str) -> str:
    return str(uuid5(NS, f"{kind}:{name}"))


NOW = datetime.now(UTC)


def ts(hours: float) -> datetime:
    return NOW - timedelta(hours=hours)


REPLY = "[演示模式] Runtime 已收到消息。发送“查询演示”可验证 HTTP 工具链路。"
AGENT_NAME = "记忆演示助手"

# Item tuple: (type, key, content, confidence, status, source_type, source_ref,
#              protected, reference_count, created_h, updated_h, prev_content, revision)
def I(t, k, content, conf=0.9, status="active", st="extracted", sr=None, prot=False,
      rc=0, ch=24, uh=2, prev=None, rev=1):
    return {"t": t, "k": k, "content": content, "conf": conf, "status": status, "st": st, "sr": sr,
                "prot": prot, "rc": rc, "ch": ch, "uh": uh, "prev": prev, "rev": rev}


CUSTOMERS = [
    {
        "name": "zhangwei", "external_id": "demo-zhangwei", "created_h": 45 * 24,
        "summary": "客户张伟（星辰科技，金牌年框客户）：跟进物流专线升级工单 TK-20261018 与 Q3 对账单承诺；已确认明年 Q1 追加 15% 采购预算；偏好简洁专业、结论先行的沟通。",
        "items": [
            I("profile", "company", "星辰科技有限公司", .98, st="manual", rc=12, ch=40 * 24, uh=20),
            I("profile", "industry", "跨境电商（主营 3C 配件出海欧美）", .95, sr="conv-demo-001", rc=8, ch=38 * 24, uh=48),
            I("profile", "role", "运营总监，对接采购与售后决策", .92, sr="conv-demo-001", rc=6, ch=35 * 24, uh=72),
            I("profile", "tier", "金牌客户（年框合同）", 1.0, st="manual", prot=True, rc=15, ch=30 * 24, uh=24),
            I("preference", "contact_channel", "工作时间优先微信沟通，紧急事项电话", .9, sr="conv-demo-002", rc=9, ch=28 * 24, uh=24),
            I("preference", "tone", "偏好简洁专业的回复，禁止使用表情符号，数据结论先行", .9, sr="conv-demo-002",
              rc=7, ch=25 * 24, uh=26, prev="偏好简洁专业的回复，少用表情符号", rev=2),
            I("preference", "report", "每周一上午发送上周数据周报", .88, st="manual", rc=5, ch=20 * 24, uh=26),
            I("preference", "budget_hint", "提到明年 Q1 可能追加 15% 采购预算（已电话确认）", .95, sr="conv-demo-004", ch=3 * 24, uh=24),
            I("profile", "new_contact", "新采购经理姓陈，年后到岗（待确认）", .55, status="pending", sr="conv-demo-004", ch=2 * 24, uh=2 * 24),
            I("event", "evt-202609-logistics", "2026-09 订单 #SO-20260912 物流延迟投诉，已补偿 5% 货款并升级专线物流", .95, sr="conv-demo-003", rc=4, ch=32 * 24, uh=32 * 24),
            I("event", "evt-202610-renewal", "2026-10 完成年框续约 ¥360,000，续约窗口每年 12 月", .97, st="manual", rc=3, ch=12 * 24, uh=12 * 24),
            I("event", "open_ticket:TK-20261018", "物流专线升级申请已提交，等待承运商确认排期（预计 10-22 前回复）", .95, st="manual", sr="conv-demo-004", rc=2, ch=26, uh=26),
            I("event", "commitment:2026-10-19", "承诺客户 10-25 前提供 Q3 专属对账单与返利明细", .9, sr="conv-demo-004", rc=1, ch=26, uh=26),
            I("flag", "vip", "重点客户：年框 ¥36 万，续约窗口 2026-12，升级需求 2 小时内响应", 1.0, st="human", prot=True, ch=72, uh=72),
        ],
        "audits": [
            ("memory.extract.applied", None, 600, {"conversation_id": "conv-demo-001", "input_messages": 6, "added": 3, "updated": 0, "pending": 0, "dropped": 0, "pii_masked": 0, "usage": {}}),
            ("memory.flag.upserted", "flag:vip", 72, None),
            ("memory.item.edited", "preference:tone", 26, None),
            ("memory.item.deleted", "preference:report", 27.5, None),
            ("memory.item.restored", "preference:report", 27, None),
            ("memory.item.confirmed", "preference:budget_hint", 24, None),
        ],
        "runs": [
            (34, "你好，我是星辰科技的张伟，想问下上周说的物流专线进展怎么样了？",
             ["flag:vip", "profile:company", "profile:industry", "profile:role", "profile:tier",
              "preference:contact_channel", "preference:tone", "preference:report"]),
            (29, "另外帮我把下周一的周报提前到周日晚上发可以吗？",
             ["flag:vip", "profile:company", "profile:tier", "preference:report", "preference:contact_channel"]),
            (22, "我们明年 Q1 的采购计划定了，追加 15% 预算，帮我记录下。",
             ["flag:vip", "profile:company", "profile:tier", "preference:budget_hint"]),
            (20, "回复的时候直接给我结论就行，别整那些花里胡哨的。",
             ["flag:vip", "preference:tone", "profile:tier"]),
            (6, "我的工单 TK-20261018 现在什么进度？对账单也别忘了。",
             ["flag:vip", "event:open_ticket:TK-20261018", "event:commitment:2026-10-19",
              "profile:company", "profile:tier"]),
            (2, "周报按老样子发就行，另外陈经理到岗后跟我同步下。",
             ["flag:vip", "profile:company", "preference:contact_channel", "preference:tone"]),
        ],
    },
    {
        "name": "lina", "external_id": "demo-lina", "created_h": 25 * 24,
        "summary": None,
        "items": [
            I("profile", "company", "蓝海贸易有限公司", .96, st="manual", rc=5, ch=25 * 24, uh=48),
            I("profile", "industry", "零售连锁（华东 12 家门店）", .9, sr="conv-demo-005", rc=3, ch=24 * 24, uh=72),
            I("preference", "invoice", "需要增值税专用发票，抬头蓝海贸易有限公司", .93, sr="conv-demo-005", rc=6, ch=22 * 24, uh=25),
            I("preference", "callback", "希望售后问题 24 小时内电话回访", .7, status="deleted", sr="conv-demo-006", ch=4 * 24, uh=23),
            I("event", "evt-20261015-quality", "2026-10-15 反馈批次 #B2310 产品外观瑕疵，已安排补寄，待回访确认", .94, sr="conv-demo-006", rc=2, ch=5 * 24, uh=5 * 24),
            I("event", "open_ticket:TK-20261015", "批次 B2310 瑕疵补寄工单：已发货，顺丰 SF1388 待签收回访", .94, sr="conv-demo-006", rc=1, ch=26, uh=4),
            I("flag", "complaint_risk", "近 30 天两次投诉，回复措辞需谨慎，避免承诺交期", 1.0, st="human", prot=True, ch=50, uh=50),
        ],
        "audits": [
            ("memory.extract.applied", None, 550, {"conversation_id": "conv-demo-005", "input_messages": 4, "added": 2, "updated": 0, "pending": 1, "dropped": 0, "pii_masked": 0, "usage": {}}),
            ("memory.flag.upserted", "flag:complaint_risk", 50, None),
            ("memory.item.rejected", "preference:callback", 23, None),
        ],
        "runs": [
            (28, "我是蓝海贸易的李娜，批次 B2310 的补寄发出了吗？",
             ["flag:complaint_risk", "profile:company", "profile:industry", "preference:invoice"]),
            (25, "这次的发票记得开专票，跟上次一样。",
             ["flag:complaint_risk", "preference:invoice", "profile:company"]),
            (21, "处理完麻烦给我回个电话确认下。",
             ["flag:complaint_risk", "profile:company"]),
            (4, "工单 TK-20261015 的补寄件我已经收到了，回访确认一下。",
             ["flag:complaint_risk", "event:open_ticket:TK-20261015", "profile:company"]),
        ],
    },
    {
        "name": "wangqiang", "external_id": "demo-wangqiang", "created_h": 60 * 24,
        "summary": None,
        "items": [
            I("profile", "company", "鑫源电子商行", .8, sr="conv-demo-007", rc=2, ch=60 * 24, uh=10 * 24),
            I("preference", "discount", "多次索要超额折扣（疑似套利，置信度低）", .35, sr="conv-demo-007", rc=1, ch=55 * 24, uh=20 * 24),
            I("event", "evt-202608-dispute", "2026-08 一笔退款纠纷进入平台仲裁", .75, sr="conv-demo-007", rc=2, ch=58 * 24, uh=30 * 24),
            I("flag", "blacklist", "恶意退款仲裁中：禁止自动承诺赔偿与折扣，一律转人工", 1.0, st="human", prot=True, ch=48, uh=48),
        ],
        "audits": [
            ("memory.flag.upserted", "flag:blacklist", 48, None),
        ],
        "runs": [
            (18, "上次那个退款仲裁结果出来没？我要求全额赔偿！",
             ["flag:blacklist", "profile:company"]),
        ],
    },
    {
        "name": "zhaomin", "external_id": "demo-zhaomin", "created_h": 6 * 24,
        "summary": None,
        "items": [
            I("profile", "company", "晨曦文化传媒工作室", .9, sr="conv-demo-008", rc=1, ch=6 * 24, uh=48),
            I("preference", "contact_channel", "偏好邮件沟通，附件用 PDF", .85, sr="conv-demo-008", rc=1, ch=6 * 24, uh=48),
            I("profile", "team_size", "团队约 15 人（待确认）", .5, status="pending", sr="conv-demo-008", ch=5 * 24, uh=5 * 24),
        ],
        "audits": [
            ("memory.extract.applied", None, 130, {"conversation_id": "conv-demo-008", "input_messages": 2, "added": 2, "updated": 0, "pending": 1, "dropped": 0, "pii_masked": 0, "usage": {}}),
        ],
        "runs": [
            (16, "你好，我是晨曦文化的赵敏，想咨询一下企业版报价，麻烦发我邮箱。",
             ["profile:company", "preference:contact_channel"]),
            (3, "我们团队大概 15 个人，后续可能扩容。",
             ["profile:company", "preference:contact_channel"]),
        ],
    },
]


async def ensure_agent(p, tenant, actor):
    """Demo agent with the full memory stack on; reuses the sample demo model."""
    catalog = p.gateway.catalog

    async def ensure(kind, body):
        rows = await catalog.list(tenant, kind)
        existing = next((r for r in rows if r["name"] == body.name), None)
        return existing or await catalog.save(tenant, actor, kind, body)

    connection = await ensure("connections", Connection(name="示例模拟连接", protocol="demo"))
    model = await ensure(
        "models",
        Model(name="示例模拟 Chat", connection_id=connection["id"], model_name="demo",
              operations=["chat"], tool_calling=True),
    )
    profile = await ensure("profiles", Profile(name="示例客服模型方案", operation="chat", model_id=model["id"]))
    version = profile["published_version"]
    if not version:
        published = await catalog.publish(tenant, actor, profile["id"], profile["revision"])
        version = published["version"]
    agents = await p.agents.list(tenant)
    agent = next((a for a in agents if a["name"] == AGENT_NAME), None)
    if not agent:
        agent = await p.agents.create(
            tenant,
            actor,
            AgentDraft(
                name=AGENT_NAME,
                description="客户记忆可视化与注入可解释性演示（记忆读写全开）",
                config=AgentConfig(
                    model_profile_id=profile["id"],
                    model_profile_version=version,
                    system_prompt="你是一名企业售后客服助手。语气自然简洁，先回应客户处境再给方案，一次推进一件事。",
                    tool_names=[],
                    knowledge_base_ids=[],
                ),
            ),
        )
        await p.agents.publish(tenant, actor, agent["id"], agent["draft_revision"])
    return agent


async def seed_customer(c, engine, model_backend, tenant, agent, audit_actor):
    """Insert one customer block; skip entirely once its identity exists."""
    ext = c["external_id"]
    async with engine.connect() as conn:
        bound = await one(
            conn,
            "SELECT id FROM memory_identities WHERE tenant_id=:t AND source='web' AND external_key=:k",
            t=tenant, k=ext,
        )
    if bound:
        return False
    cid = uid("customer", c["name"])
    conv_id = uid("conversation", ext)
    item_ids = {f'{i["t"]}:{i["k"]}': uid("item", f'{c["name"]}:{i["t"]}:{i["k"]}') for i in c["items"]}
    items_by_key = {f'{i["t"]}:{i["k"]}': i for i in c["items"]}
    last_run_h = min(h for h, _, _ in c["runs"])

    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO memory_customers(id,tenant_id,created_at,updated_at) VALUES(:id,:t,:ca,:ua) ON CONFLICT DO NOTHING"),
            {"id": cid, "t": tenant, "ca": ts(c["created_h"]), "ua": ts(last_run_h)},
        )
        await conn.execute(
            text("INSERT INTO memory_identities(id,tenant_id,customer_id,source,external_key,created_at) VALUES(:id,:t,:c,'web',:k,:ca) ON CONFLICT DO NOTHING"),
            {"id": uid("identity", ext), "t": tenant, "c": cid, "k": ext, "ca": ts(c["created_h"])},
        )
        for i in c["items"]:
            iid = item_ids[f'{i["t"]}:{i["k"]}']
            referenced = i["status"] == "active" and any(
                f'{i["t"]}:{i["k"]}' in inj for _, _, inj in c["runs"])
            await conn.execute(
                text("""INSERT INTO memory_items(id,tenant_id,customer_id,type,key,content,confidence,status,
                        source_type,source_ref,prev_content,revision,last_referenced_at,reference_count,
                        created_at,updated_at,protected)
                        VALUES(:id,:t,:c,:ty,:k,:co,:cf,:st,:sty,:sr,:prev,:rev,:lra,:rc,:ca,:ua,:prot)
                        ON CONFLICT DO NOTHING"""),
                {"id": iid, "t": tenant, "c": cid, "ty": i["t"], "k": i["k"], "co": i["content"], "cf": i["conf"],
                     "st": i["status"], "sty": i["st"], "sr": i["sr"], "prev": i["prev"], "rev": i["rev"],
                     "lra": ts(last_run_h) if referenced else ts(i["ch"]), "rc": i["rc"],
                     "ca": ts(i["ch"]), "ua": ts(i["uh"]), "prot": i["prot"]},
            )
        # Conversation with full history and one completed run per message pair.
        history = []
        for _, msg, _ in c["runs"]:
            history.append({"role": "user", "content": msg})
            history.append({"role": "assistant", "content": REPLY})
        await conn.execute(
            text("""INSERT INTO runtime_conversations(id,tenant_id,external_id,history,created_at,mode,agent_id,
                    updated_at,revision,source,summary,customer_id)
                    VALUES(:id,:t,:ext,CAST(:hist AS jsonb),:ca,'bot',:aid,:ua,1,'business',:sum,:cid)
                    ON CONFLICT (tenant_id,external_id) DO NOTHING"""),
            {"id": conv_id, "t": tenant, "ext": ext, "hist": json.dumps(history, ensure_ascii=False),
                 "ca": ts(c["runs"][0][0] + 0.1), "aid": agent["id"], "ua": ts(last_run_h),
                 "sum": c["summary"], "cid": cid},
        )
        for n, (h, msg, injected) in enumerate(c["runs"]):
            run_id = uid("run", f"{ext}:{n}")
            created, started, finished = ts(h), ts(h) + timedelta(milliseconds=30), ts(h) + timedelta(milliseconds=235)
            run_config = {"agent": {"id": str(agent["id"]), "tools": [],
                                    "config": agent["draft"], "version": 1, "is_example": False},
                          "model_name": "", "fingerprint": "0" * 64, "model_backend": model_backend}
            await conn.execute(
                text("""INSERT INTO runtime_runs(id,conversation_id,tenant_id,input,config,status,output,
                        event_seq,queue_expires_at,created_at,started_at,finished_at)
                        VALUES(:id,:cid,:t,:inp,CAST(:cfg AS jsonb),'completed',:out,7,:qexp,:ca,:sa,:fa)
                        ON CONFLICT DO NOTHING"""),
                {"id": run_id, "cid": conv_id, "t": tenant, "inp": msg,
                     "cfg": json.dumps(run_config, ensure_ascii=False, default=str), "out": REPLY,
                     "qexp": created + timedelta(minutes=2), "ca": created, "sa": started, "fa": finished},
            )
            for role, when in (("user", created), ("assistant", finished)):
                await conn.execute(
                    text("INSERT INTO conversation_messages(id,conversation_id,role,content,run_id,created_at) VALUES(:id,:cid,:r,:co,:rid,:ca)"),
                    {"id": uid("message", f"{run_id}:{role}"), "cid": conv_id, "r": role,
                         "co": msg if role == "user" else REPLY, "rid": run_id, "ca": when},
                )
            injected_ids = [item_ids[k] for k in injected if k in item_ids]
            sections = [items_by_key[k]["t"] for k in injected if k in items_by_key]
            counts = {"flags": sections.count("flag"),
                      "items": sections.count("profile") + sections.count("preference"),
                      "events": sections.count("event")}
            events = [
                (1, "run.queued", {}),
                (2, "run.started", {}),
                (3, "model.started", {"round": 1}),
                (4, "model.completed", {"round": 1, "usage": {}, "tool_calls": 0, "duration_ms": 141,
                                        "first_token_ms": 38, "gateway_call_id": uid("gw", run_id)}),
                (5, "memory_extract.queued", {"customer_id": cid}),
                (6, "memory.injected", {**counts, "item_ids": injected_ids}),
                (7, "run.completed", {"output": REPLY, "error_code": None}),
            ]
            for seq, etype, data in events:
                await conn.execute(
                    text("INSERT INTO runtime_events(run_id,seq,type,data,created_at) VALUES(:rid,:seq,:ty,CAST(:d AS jsonb),:ca) ON CONFLICT DO NOTHING"),
                    {"rid": run_id, "seq": seq, "ty": etype, "d": json.dumps(data, ensure_ascii=False), "ca": finished},
                )
            for k in injected:
                if k not in item_ids:
                    continue
                section = items_by_key[k]["t"]
                score = items_by_key[k]["conf"] if section in ("profile", "preference") else None
                await conn.execute(
                    text("INSERT INTO memory_injection_log(tenant_id,run_id,customer_id,item_id,section,score,injected_at) VALUES(:t,:rid,:cid,:iid,:sec,:score,:at)"),
                    {"t": tenant, "rid": run_id, "cid": cid, "iid": item_ids[k], "sec": section, "score": score, "at": finished},
                )
        for action, item_key, hours_ago, details in c["audits"]:
            if details is None:
                i = items_by_key[item_key]
                details = {"key": i["k"], "type": i["t"], "customer_id": cid}
                if action == "memory.flag.upserted":
                    details.update(pii_masked=0, source_ref=audit_actor, source_type="human")
                elif action == "memory.item.edited":
                    details["pii_masked"] = 0
                else:
                    transitions = {"memory.item.deleted": ("active", "deleted"),
                                   "memory.item.restored": ("deleted", "active"),
                                   "memory.item.confirmed": ("pending", "active"),
                                   "memory.item.rejected": ("pending", "deleted")}
                    old, new = transitions[action]
                    details.update(old_status=old, new_status=new)
            resource = item_ids.get(item_key, cid) if item_key else cid
            await conn.execute(
                text("INSERT INTO audit_records(tenant_id,actor,action,resource,details,created_at) VALUES(:t,:actor,:action,:res,CAST(:d AS jsonb),:ca)"),
                {"t": tenant, "actor": audit_actor, "action": action, "res": str(resource),
                     "d": json.dumps(details, ensure_ascii=False), "ca": ts(hours_ago)},
            )
    return True


async def main():
    settings = Settings()
    tenant, actor = settings.tenant_id, "seed"
    async with runtime_services(settings) as runtime:
        p = runtime.platform
        async with p.engine.connect() as lock:
            await lock.execute(text("SELECT pg_advisory_lock(71432025)"))
            try:
                agent = await ensure_agent(p, tenant, actor)
                async with p.engine.connect() as conn:
                    admin = await one(conn, "SELECT user_id FROM local_admin_credentials WHERE tenant_id=:t LIMIT 1", t=tenant)
                audit_actor = f"enterprise:{admin['user_id']}" if admin else actor
                seeded = 0
                for cust in CUSTOMERS:
                    if await seed_customer(cust, p.engine, settings.model_backend, tenant, agent, audit_actor):
                        seeded += 1
                print(f"记忆演示数据就绪（新入库 {seeded}/{len(CUSTOMERS)} 个客户，租户：{tenant}）。")
            finally:
                await lock.execute(text("SELECT pg_advisory_unlock(71432025)"))


if __name__ == "__main__":
    asyncio.run(main())
