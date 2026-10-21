"""Seed the phase-1 (conversation summary) acceptance dataset; safe to re-run.

Demonstrates the Phase 1 acceptance scenarios (A1/A4/A5 plus the rolling
state) with genuine-shaped rows:

- 陈静 (demo-p1-chenjing): A1 + rolling — a 13-round conversation whose early
  facts (订单号 A123456, 花生过敏) fell out of the sliding window; two
  ``memory.summary.generated`` audits (first generation, then a rolling
  merge), two ``done`` ``memory_tasks`` rows with merged dropped-message
  payloads, ``summary.queued`` run events on the truncating runs, and the
  current rolling summary on the conversation row. Rounds 12-13 arrived
  after the last generation — the "摘要生成后又有新消息" state.
- 刘洋 (demo-p1-liuyang): A4 — the summary model profile faulted; the task
  exhausted its attempts (``failed``, attempts=2, error
  ``model_provider_unavailable``), a ``memory.summary.failed`` audit row
  exists, the old summary is preserved on the conversation, and every run
  still completed normally (async degradation).
- 孙悦 (demo-p1-sunyue): A5 contrast — a short in-window conversation with
  ``summary`` NULL, no summary tasks, no ``summary.queued`` events and no
  summary audits.

All ids are uuid5-deterministic under the ``phase1`` namespace and the whole
module is skipped once the first identity binding exists, so re-runs never
duplicate rows. No ``pending`` task rows are seeded on purpose: a live
worker would claim them on boot and overwrite the curated summaries.
"""

import json

from sqlalchemy import text

from agent_platform.apps.seed_memory_common import (
    REPLY,
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

NS = ns("phase1")
AGENT_NAME = "摘要演示助手"
AGENT_DESC = "阶段1会话摘要验收演示：滚动摘要生成、注入与失败降级（摘要开关开启）"
SYSTEM_PROMPT = "你是一名企业客服助手。结合对话摘要记住客户早期提供的关键信息，回答简洁专业、结论先行。"

# 陈静的滚动摘要：v1（首批截断消息生成）→ v2（滚动合并第二批后的当前值）。
CHEN_V1 = (
    "客户陈静，恒瑞食品贸易有限公司采购主管，负责休闲零食类目选品。"
    "订单号 A123456（2026-10-05 试吃装批次），客户要求跟进进度。"
    "客户本人对花生严重过敏：含花生成分的零食一律排除，推荐前必须确认配料表。"
    "Q4 试吃装采购意向 300 盒，拟走年框合同流程。"
)
CHEN_V2 = (
    "客户陈静，恒瑞食品贸易有限公司采购主管，负责休闲零食类目选品。"
    "订单号 A123456（2026-10-05 试吃装批次），需跟进进度。"
    "客户对花生严重过敏：含花生成分零食一律排除，推荐前必须确认配料表。"
    "Q4 试吃装采购意向 300 盒，走年框合同流程；混合坚果礼盒需确认无花生版本并留两箱样品；"
    "已要求按 300 盒出具含税含运费报价单。"
)
# 刘洋的旧摘要：后续摘要任务失败后被原样保留（A4 降级语义）。
LIU_OLD = (
    "客户刘洋，远洋家居连锁集团售后对接人。"
    "2026-10-12 反馈主卧衣柜门板变形（型号 YG-2205 原木色，订单号 SO-20261008，左侧上门板），"
    "换货工单 TK-20261012 处理中，客户门店急着出样。"
)

CHEN_MSGS = [
    "你好，我是恒瑞食品的陈静，我们 10 月 5 号下的试吃装订单 A123456，麻烦帮我查下进度。",
    "补充一句，这批试吃装是我自己先尝的，我对花生严重过敏，含花生成分的品类千万不要推荐。",
    "我们 Q4 打算采购 300 盒试吃装，走年框合同流程，需要准备哪些材料？",
    "混合坚果礼盒有没有不含花生的版本？有的话帮我留两箱样品。",
    "报价单麻烦按 300 盒的量出一份，要含税含运费。",
    "你们正常发货周期是几天？我们要赶 11 月初的行业展会。",
    "展会现场要摆样品，能不能先单独发 20 盒样品过来？",
    "样品走顺丰到付就行，收货地址我稍后发你。",
    "回复的时候给我列要点就行，先说结论再讲细节，谢谢。",
    "收货地址：杭州市滨江区恒瑞大厦 3 层，陈静收，电话还是那个手机号。",
    "合同流程走到哪一步了？我们法务这周要评审。",
    "报价单我收到了，价格没问题，等合同盖章后回传。",
    "在吗？帮我推荐两款适合展会派发的小包装零食，还是老规矩，注意我的过敏情况。",
]

LIU_MSGS = [
    "你好，我是远洋家居的刘洋，上周送到的主卧衣柜有一块门板变形了，麻烦安排换货。",
    "型号是 YG-2205 原木色，订单号 SO-20261008，变形的是左侧上门板。",
    "换货工单 TK-20261012 建立了吗？我们门店急着出样。",
    "新门板什么时候能发？最好这周发货。",
    "安装师傅到货后 24 小时内能上门吗？需要提前约。",
    "旧门板需要退回吗？还是师傅安装时直接带走？",
    "以后联系我用企业微信，工作时间电话不一定接得到。",
    "工单 TK-20261012 物流显示已发货，德邦 DB7712，大概哪天到？",
    "德邦显示明天派送，麻烦帮忙约后天上午的安装。",
    "安装约好了告诉我一声，我让门店留人。",
]

SUN_MSGS = [
    "你好，我是晴空户外的孙悦，想咨询一下帐篷的批发价，麻烦发份报价到我邮箱。",
    "我们门店大概 80 平米，想先各款拿 5 顶试销。",
    "报价收到了，双人帐那款再便宜 5 块可以吗？可以的话这周就下单。",
]


def counts_for(injected):
    """Section counts for the memory.injected event, mirroring insert_run's default."""
    return {"flags": sum(1 for _, sec, _ in injected if sec == "flag"),
            "items": sum(1 for _, sec, _ in injected if sec in ("profile", "preference")),
            "events": sum(1 for _, sec, _ in injected if sec == "event")}


def events_with_summary(cid, injected_ids, counts, gateway_call_id, dropped):
    """The 8-event run sequence of a truncating run: summary.queued at seq 5.

    Emission order mirrors repository.finish(): summary dispatch first, then
    extraction, then the injection telemetry event, then run completion.
    """
    return [
        (1, "run.queued", {}),
        (2, "run.started", {}),
        (3, "model.started", {"round": 1}),
        (4, "model.completed", {"round": 1, "usage": {}, "tool_calls": 0, "duration_ms": 141,
                                "first_token_ms": 38, "gateway_call_id": gateway_call_id}),
        (5, "summary.queued", {"dropped": dropped}),
        (6, "memory_extract.queued", {"customer_id": cid}),
        (7, "memory.injected", {**counts, "item_ids": injected_ids}),
        (8, "run.completed", {"output": REPLY, "error_code": None}),
    ]


async def insert_summary_task(conn, tenant, task_id, conv_id, dropped_messages, old_summary,
                              status, attempts, error, created_h, updated_h):
    """One memory_tasks row of kind conversation_summary (done or failed)."""
    payload = {"conversation_id": str(conv_id), "dropped_messages": dropped_messages,
               "old_summary": old_summary}
    await conn.execute(
        text("""INSERT INTO memory_tasks(id,tenant_id,kind,status,payload,attempts,max_attempts,
                error,run_after,created_at,updated_at)
                VALUES(:id,:t,'conversation_summary',:st,CAST(:p AS jsonb),:a,2,:err,:ra,:ca,:ua)
                ON CONFLICT DO NOTHING"""),
        {"id": task_id, "t": tenant, "st": status, "p": json.dumps(payload, ensure_ascii=False),
         "a": attempts, "err": error, "ra": ts(created_h), "ca": ts(created_h), "ua": ts(updated_h)},
    )


def dropped_pair(idx, messages):
    """The user/assistant pair of round ``idx`` as a task payload entry list."""
    return [{"role": "user", "content": messages[idx]}, {"role": "assistant", "content": REPLY}]


async def seed_chenjing(conn, tenant, agent, model_backend):
    """A1 + rolling: long conversation, generated summary, queued events, tasks."""
    cid = uid(NS, "customer", "chenjing")
    conv_id = uid(NS, "conversation", "demo-p1-chenjing")
    specs = {
        "profile:company": item("profile", "company", "恒瑞食品贸易有限公司",
                                .98, st="manual", rc=9, ch=15 * 24, uh=30),
        "profile:role": item("profile", "role", "采购主管，负责休闲零食类目选品与供应商对接",
                             .92, sr="demo-p1-chenjing", rc=4, ch=14 * 24, uh=30),
        "preference:allergy": item("preference", "allergy",
                                   "客户对花生严重过敏：含花生成分零食一律排除，推荐前必须确认配料表",
                                   .99, sr="demo-p1-chenjing", rc=11, ch=49, uh=30),
        "preference:tone": item("preference", "tone", "偏好要点式回复，先说结论再讲细节",
                                .9, sr="demo-p1-chenjing", rc=5, ch=32, uh=30),
    }
    item_ids = {k: uid(NS, "item", f"chenjing:{k}") for k in specs}
    # (hours_ago, message, dropped_this_run, injected item keys)
    rounds = [
        (50, CHEN_MSGS[0], 0, ["profile:company"]),
        (49, CHEN_MSGS[1], 0, ["profile:company", "preference:allergy"]),
        (48, CHEN_MSGS[2], 0, ["profile:company"]),
        (47, CHEN_MSGS[3], 0, ["preference:allergy", "profile:company"]),
        (44, CHEN_MSGS[4], 0, ["profile:company"]),
        (40, CHEN_MSGS[5], 0, ["profile:company"]),
        (36, CHEN_MSGS[6], 2, ["profile:company"]),
        (34, CHEN_MSGS[7], 2, ["profile:company"]),
        (32, CHEN_MSGS[8], 2, ["preference:tone", "profile:company"]),
        (30, CHEN_MSGS[9], 2, ["profile:company"]),
        (27, CHEN_MSGS[10], 2, ["profile:company"]),
        (20, CHEN_MSGS[11], 0, ["profile:company", "preference:tone"]),
        (3, CHEN_MSGS[12], 0, ["preference:allergy", "preference:tone",
                               "profile:company", "profile:role"]),
    ]
    await insert_customer(conn, tenant, cid, 15 * 24, 3)
    await insert_identity(conn, tenant, uid(NS, "identity", "demo-p1-chenjing"), cid,
                          "demo-p1-chenjing", 15 * 24)
    for key, spec in specs.items():
        await insert_item(conn, tenant, item_ids[key], cid, spec, last_referenced_h=3)
    await insert_conversation(conn, tenant, conv_id, "demo-p1-chenjing", pairs(CHEN_MSGS),
                              50.1, 3, agent["id"], summary=CHEN_V2, cid=cid)
    for n, (hours, msg, dropped, keys) in enumerate(rounds):
        injected = [(item_ids[k], specs[k]["t"],
                     specs[k]["conf"] if specs[k]["t"] in ("profile", "preference") else None)
                    for k in keys]
        events = None
        if dropped:
            run_id = uid(NS, "run", f"chenjing:{n}")
            events = events_with_summary(cid, [iid for iid, _, _ in injected],
                                         counts_for(injected), uid(NS, "gw", run_id), dropped)
        await insert_run(conn, tenant, NS, f"chenjing:{n}", conv_id, agent, model_backend,
                         msg, hours, cid=cid, injected=injected, events=events)
    # Two done summary tasks: task payloads hold the merged dropped pairs.
    task1, task2 = uid(NS, "task", "chenjing:1"), uid(NS, "task", "chenjing:2")
    await insert_summary_task(conn, tenant, task1, conv_id,
                              [m for i in (0, 1, 2) for m in dropped_pair(i, CHEN_MSGS)],
                              None, "done", 1, None, 32, 31.9)
    await insert_summary_task(conn, tenant, task2, conv_id,
                              [m for i in (3, 4) for m in dropped_pair(i, CHEN_MSGS)],
                              CHEN_V1, "done", 1, None, 27, 26.9)
    await insert_audit(conn, tenant, "memory", "memory.summary.generated", conv_id,
                       {"conversation_id": conv_id, "input_messages": 6, "old_length": 0,
                        "new_length": len(CHEN_V1), "usage": {}}, 31.9)
    await insert_audit(conn, tenant, "memory", "memory.summary.generated", conv_id,
                       {"conversation_id": conv_id, "input_messages": 4,
                        "old_length": len(CHEN_V1), "new_length": len(CHEN_V2), "usage": {}}, 26.9)


async def seed_liuyang(conn, tenant, agent, model_backend):
    """A4: summary model fault — task failed, old summary kept, runs unaffected."""
    cid = uid(NS, "customer", "liuyang")
    conv_id = uid(NS, "conversation", "demo-p1-liuyang")
    specs = {
        "profile:company": item("profile", "company", "远洋家居连锁集团",
                                .97, st="manual", rc=6, ch=10 * 24, uh=30),
        "event:open_ticket:TK-20261012": item(
            "event", "open_ticket:TK-20261012",
            "衣柜门板变形换货工单（型号 YG-2205，订单 SO-20261008）：新门板已发货，"
            "德邦 DB7712 在途，到货后 24 小时内约安装",
            .95, st="manual", sr="demo-p1-liuyang", rc=3, ch=72, uh=8),
        "preference:contact_channel": item("preference", "contact_channel",
                                           "工作时间优先企业微信沟通，电话不一定接得到",
                                           .88, sr="demo-p1-liuyang", rc=2, ch=50, uh=50),
    }
    item_ids = {k: uid(NS, "item", f"liuyang:{k}") for k in specs}
    ticket = "event:open_ticket:TK-20261012"
    rounds = [
        (80, LIU_MSGS[0], 0, ["profile:company"]),
        (78, LIU_MSGS[1], 0, ["profile:company"]),
        (72, LIU_MSGS[2], 0, ["profile:company", ticket]),
        (70, LIU_MSGS[3], 0, [ticket]),
        (60, LIU_MSGS[4], 0, [ticket, "profile:company"]),
        (55, LIU_MSGS[5], 0, [ticket]),
        (50, LIU_MSGS[6], 0, ["preference:contact_channel", "profile:company"]),
        (30, LIU_MSGS[7], 0, [ticket, "profile:company"]),
        (8, LIU_MSGS[8], 2, [ticket, "preference:contact_channel"]),
        (2, LIU_MSGS[9], 0, [ticket, "preference:contact_channel", "profile:company"]),
    ]
    await insert_customer(conn, tenant, cid, 10 * 24, 2)
    await insert_identity(conn, tenant, uid(NS, "identity", "demo-p1-liuyang"), cid,
                          "demo-p1-liuyang", 10 * 24)
    for key, spec in specs.items():
        await insert_item(conn, tenant, item_ids[key], cid, spec, last_referenced_h=2)
    await insert_conversation(conn, tenant, conv_id, "demo-p1-liuyang", pairs(LIU_MSGS),
                              80.1, 2, agent["id"], summary=LIU_OLD, cid=cid)
    for n, (hours, msg, dropped, keys) in enumerate(rounds):
        injected = [(item_ids[k], specs[k]["t"],
                     specs[k]["conf"] if specs[k]["t"] in ("profile", "preference") else None)
                    for k in keys]
        events = None
        if dropped:
            run_id = uid(NS, "run", f"liuyang:{n}")
            events = events_with_summary(cid, [iid for iid, _, _ in injected],
                                         counts_for(injected), uid(NS, "gw", run_id), dropped)
        await insert_run(conn, tenant, NS, f"liuyang:{n}", conv_id, agent, model_backend,
                         msg, hours, cid=cid, injected=injected, events=events)
    # The failed rolling-summary task: two attempts, then permanently failed.
    task_id = uid(NS, "task", "liuyang:1")
    await insert_summary_task(conn, tenant, task_id, conv_id, dropped_pair(2, LIU_MSGS),
                              LIU_OLD, "failed", 2, "model_provider_unavailable", 8, 7.4)
    # The earlier successful generation that produced the preserved summary.
    await insert_audit(conn, tenant, "memory", "memory.summary.generated", conv_id,
                       {"conversation_id": conv_id, "input_messages": 4, "old_length": 0,
                        "new_length": len(LIU_OLD), "usage": {}}, 48)
    await insert_audit(conn, tenant, "memory", "memory.summary.failed", conv_id,
                       {"task_id": task_id, "error": "model_provider_unavailable",
                        "attempts": 2}, 7.4)


async def seed_sunyue(conn, tenant, agent, model_backend):
    """A5 contrast: short in-window conversation, no summary anywhere."""
    cid = uid(NS, "customer", "sunyue")
    conv_id = uid(NS, "conversation", "demo-p1-sunyue")
    specs = {
        "profile:company": item("profile", "company", "晴空户外用品店",
                                .9, sr="demo-p1-sunyue", rc=2, ch=3 * 24, uh=20),
        "preference:contact_channel": item("preference", "contact_channel",
                                           "偏好邮件沟通，报价与合同资料发邮箱",
                                           .85, sr="demo-p1-sunyue", rc=2, ch=26, uh=20),
    }
    item_ids = {k: uid(NS, "item", f"sunyue:{k}") for k in specs}
    rounds = [
        (26, SUN_MSGS[0], ["profile:company", "preference:contact_channel"]),
        (20, SUN_MSGS[1], ["profile:company"]),
        (4, SUN_MSGS[2], ["profile:company", "preference:contact_channel"]),
    ]
    await insert_customer(conn, tenant, cid, 3 * 24, 4)
    await insert_identity(conn, tenant, uid(NS, "identity", "demo-p1-sunyue"), cid,
                          "demo-p1-sunyue", 3 * 24)
    for key, spec in specs.items():
        await insert_item(conn, tenant, item_ids[key], cid, spec, last_referenced_h=4)
    await insert_conversation(conn, tenant, conv_id, "demo-p1-sunyue", pairs(SUN_MSGS),
                              26.1, 4, agent["id"], summary=None, cid=cid)
    for n, (hours, msg, keys) in enumerate(rounds):
        injected = [(item_ids[k], specs[k]["t"],
                     specs[k]["conf"] if specs[k]["t"] in ("profile", "preference") else None)
                    for k in keys]
        await insert_run(conn, tenant, NS, f"sunyue:{n}", conv_id, agent, model_backend,
                         msg, hours, cid=cid, injected=injected)


async def seed(runtime, settings) -> bool:
    """Insert the phase-1 dataset once; return False when already seeded."""
    p = runtime.platform
    tenant = settings.tenant_id
    async with p.engine.connect() as conn:
        if await identity_bound(conn, tenant, "web", "demo-p1-chenjing"):
            return False
    agent = await ensure_agent(p, tenant, "seed", AGENT_NAME, AGENT_DESC,
                               AgentConfig(system_prompt=SYSTEM_PROMPT, tool_names=[],
                                           knowledge_base_ids=[], summary_enabled=True))
    async with p.engine.begin() as conn:
        await seed_chenjing(conn, tenant, agent, settings.model_backend)
        await seed_liuyang(conn, tenant, agent, settings.model_backend)
        await seed_sunyue(conn, tenant, agent, settings.model_backend)
    return True


if __name__ == "__main__":
    run_standalone(seed, "阶段1·会话摘要演示数据")
